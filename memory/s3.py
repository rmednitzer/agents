"""S3Store: blob / cold-storage MemoryStore adapter (BL-032, ADR 0007).

``boto3`` is an optional dependency, imported lazily. Install:
``pip install 'agents[aws]'``.

Semantics deviation (documented per ADR 0004):

- S3 has no native per-object TTL (only coarse, bucket-level lifecycle
  rules). Expiry is stored in object metadata (``x-amz-meta-expires-at``)
  and enforced lazily on access and by ``sweep_expired`` -- the same
  lazy model as InMemoryStore, not server-side eviction.
- S3 is read-after-write consistent for new keys but overwrite/delete
  visibility and LIST can briefly lag. Callers needing strict
  monotonicity should use Redis/SQLite/DynamoDB instead. This is the
  cold-storage / audit-pack backend.
- No CAS (S3 has no atomic compare-and-set on object content), so
  CASMemoryStore is intentionally not implemented.

``BoundedS3Store`` (``BL-225``) is the opt-in subclass that adds
``BoundedSweepableStore`` via a per-object ``insertion-order``
metadata attribute (nanosecond wall-clock at write time), closing the
S3 half of ``BL-135`` (the size-bound half) for the cold-storage
backend. The bare ``S3Store`` keeps its prior design unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from typing import Any

from memory._audit import MemoryAudit
from memory._expiry import is_expired
from memory.types import Namespace
from memory.validators import validate_key

__all__ = ["BoundedS3Store", "S3Store"]

_EXPIRES_META = "expires-at"
_SEQ_META = "insertion-order"


def _safe_float(v: str | None) -> float | None:
    """Parse a float from untrusted S3 user metadata.

    Returns ``None`` if the value is missing, not parseable as a
    float, or not finite (NaN, +inf, -inf). The non-finite rejection
    closes the BL-159 / BL-205 / BL-221 NaN-bypass class on the
    metadata-read trust boundary (tenth audit, BL-226): a corrupted
    or hand-written ``x-amz-meta-expires-at = "nan"`` would otherwise
    sail through ``float()`` and then through ``now > exp`` (NaN
    comparisons are always ``False``), permanently masking the object
    from lazy / sweep / capacity expiry. Treating non-finite and
    unparseable as ``None`` is the safest cold-storage default
    (consistent with "no TTL recorded": the object stays live, an
    operator can re-write it with valid metadata).
    """
    if v is None:
        return None
    try:
        parsed = float(v)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _safe_int(v: str | None) -> int:
    """Parse an int from untrusted S3 user metadata.

    Returns ``0`` if the value is missing or not parseable as an int.
    Zero matches the BL-225 legacy-migration default (an object
    written by a bare ``S3Store`` has no ``insertion-order`` metadata
    and is treated as the oldest entry, evicting first); a corrupted
    metadata value cannot crash the eviction scan past the documented
    ``S3Store`` exception contract (tenth audit, BL-226 class of
    BL-201 / BL-215 / BL-217 untrusted-input parsing).
    """
    if v is None:
        return 0
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


class S3Store:
    """Cold-storage MemoryStore backed by an S3 bucket."""

    name: str = "s3"

    def __init__(
        self,
        namespace: Namespace,
        bucket: str,
        *,
        client: Any | None = None,
        prefix: str | None = None,
        sink: Any | None = None,
        base_event_fields: dict[str, Any] | None = None,
    ) -> None:
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ImportError(
                    "S3Store requires the 'aws' extra: pip install 'agents[aws]'"
                ) from exc
            client = boto3.client("s3")
        self._s3 = client
        self._bucket = bucket
        self._namespace = namespace
        self._prefix = f"{prefix or namespace.name}/"
        self._audit = MemoryAudit(namespace.name, sink, base_event_fields)

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    def _okey(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def _ttl(self, ttl_seconds: float | None) -> float | None:
        # Delegate to Namespace.resolve_ttl (BL-197).
        return self._namespace.resolve_ttl(ttl_seconds)

    def _get_live(self, key: str) -> bytes | None:
        """Return value if present and unexpired; delete it if expired.

        Only an object-level not-found is a miss. Other ClientErrors
        (AccessDenied, throttling, transient outages, and
        ``NoSuchBucket`` -- a misconfigured/deleted bucket is a backend
        failure, not an absent key) propagate so an outage is not
        silently reported as "key absent".
        """
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._okey(key))
        except self._s3.exceptions.NoSuchKey:
            return None
        except self._s3.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        exp = _safe_float(obj.get("Metadata", {}).get(_EXPIRES_META))
        if is_expired(time.time(), exp):
            self._s3.delete_object(Bucket=self._bucket, Key=self._okey(key))
            return None
        body = obj["Body"].read()
        return bytes(body)

    def _head_metadata(self, s3_key: str) -> dict[str, str] | None:
        """HEAD an object by full S3 key, returning its user metadata.

        Returns ``None`` if the object is not found. A not-found result
        means the object was deleted between the LIST that produced
        ``s3_key`` and this HEAD: S3's documented concurrent-access /
        eventual-consistency window (another writer, a concurrent
        ``read`` lazy-expiry delete, or a concurrent ``sweep_expired``
        run). HeadObject returns HTTP 404 (``NoSuchKey``) for a missing
        key, so without this guard a single concurrently-deleted object
        would raise out of the per-object HEAD loop and crash the whole
        ``sweep_expired`` / ``evict_to_capacity`` scan (``BL-229``,
        eleventh audit; the BL-170 S3-listing-robustness class on the
        HEAD-during-scan boundary).

        Treating not-found as "gone, skip it" matches the not-found
        handling ``_get_live`` already applies on the read path. Any
        other ``ClientError`` (throttle, AccessDenied, outage,
        ``NoSuchBucket``) propagates, so a backend failure is never
        silently reported as an absent object: the same
        propagate-the-real-error stance as ``_get_live`` (and the
        deliberately narrow scope that leaves the "should the parent
        sweep be best-effort for transient errors too?" question of the
        ADR 0020 revisit trigger open, rather than swallowing real
        backend errors here).
        """
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=s3_key)
        except self._s3.exceptions.NoSuchKey:
            return None
        except self._s3.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        metadata: dict[str, str] = head.get("Metadata", {})
        return metadata

    def _head_live(self, s3_key: str) -> bool:
        """Liveness of an object by HEAD (no body), for the listing path.

        ``list_keys`` / ``scan`` need only the key and its liveness, so a
        metadata-only HEAD avoids the full-body ``GetObject`` that
        ``_get_live`` issues per listed object just to read the
        ``expires-at`` metadata and discard the body (BL-260, fifteenth
        audit). A concurrently-deleted object (HEAD 404 -> ``None``) is
        treated as gone, the same not-found stance as ``_get_live``.
        Expiry uses the same ``_safe_float(_EXPIRES_META)`` / ``is_expired``
        path; an expired object is excluded from the listing but NOT
        lazily deleted here, so the listing stays a pure read and avoids a
        per-item DELETE inside the scan loop (the BL-233 containment
        concern); the read and sweep paths own reclamation.
        """
        md = self._head_metadata(s3_key)
        if md is None:
            return False
        return not is_expired(time.time(), _safe_float(md.get(_EXPIRES_META)))

    # boto3 is synchronous; every blocking call is offloaded to a worker
    # thread so an asyncio workload's event loop is never stalled by S3
    # network I/O. Audit emission stays on the loop (fast, in-memory).

    async def read(self, key: str) -> bytes | None:
        validate_key(key)
        value = await asyncio.to_thread(self._get_live, key)
        self._audit.read(key, hit=value is not None)
        return value

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        metadata: dict[str, str] = {}
        if ttl is not None:
            metadata[_EXPIRES_META] = str(time.time() + ttl)
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=self._okey(key),
            Body=value,
            Metadata=metadata,
        )
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)

    def _delete_sync(self, key: str) -> bool:
        existed = self._get_live(key) is not None
        self._s3.delete_object(Bucket=self._bucket, Key=self._okey(key))
        return existed

    async def delete(self, key: str) -> None:
        validate_key(key)
        existed = await asyncio.to_thread(self._delete_sync, key)
        self._audit.delete(key, existed=existed)

    def _all_live_keys(self, prefix: str = "") -> list[str]:
        # Push ``prefix`` into the S3 LIST (server-side), so listing a
        # subtree no longer pulls the whole namespace and HEAD/GETs every
        # object just to discard most of them (BL-161).
        full_prefix = f"{self._prefix}{prefix}"
        keys: list[str] = []
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": full_prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            for item in resp.get("Contents", []):
                short = item["Key"][len(self._prefix) :]
                if self._head_live(item["Key"]):
                    keys.append(short)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._all_live_keys, prefix)

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        for k in keys:
            validate_key(k)
        out: list[bytes | None] = await asyncio.to_thread(lambda: [self._get_live(k) for k in keys])
        for k, v in zip(keys, out, strict=True):
            self._audit.read(k, hit=v is not None)
        return out

    async def mset(self, items: dict[str, bytes], *, ttl_seconds: float | None = None) -> None:
        for k, v in items.items():
            await self.write(k, v, ttl_seconds=ttl_seconds)

    async def mdelete(self, keys: list[str]) -> None:
        for k in keys:
            await self.delete(k)

    def _scan_sync(self, cursor: str, prefix: str, count: int) -> tuple[str, list[str]]:
        # Exclude expired-but-unswept keys so scan() agrees with
        # read()/list_keys(). A raw S3 page can be entirely expired keys
        # while more live keys exist behind a continuation token;
        # returning ("", []) there would wrongly signal exhaustion
        # (audit B1). Page internally until at least one live key is
        # collected or the listing truly ends, then return the cursor.
        keys: list[str] = []
        token = cursor
        next_cursor = ""
        while True:
            kw: dict[str, Any] = {
                "Bucket": self._bucket,
                "Prefix": f"{self._prefix}{prefix}",
                "MaxKeys": count,
            }
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            keys.extend(
                item["Key"][len(self._prefix) :]
                for item in resp.get("Contents", [])
                if self._head_live(item["Key"])
            )
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken", "")
                next_cursor = token
            else:
                next_cursor = ""
            if keys or not next_cursor:
                break
        return next_cursor, sorted(keys)

    async def scan(
        self, *, cursor: str = "", prefix: str = "", count: int = 100
    ) -> tuple[str, list[str]]:
        if count <= 0:
            return "", []
        return await asyncio.to_thread(self._scan_sync, cursor, prefix, count)

    async def write_content(self, value: bytes, *, ttl_seconds: float | None = None) -> str:
        key = hashlib.sha256(value).hexdigest()
        await self.write(key, value, ttl_seconds=ttl_seconds)
        return key

    def _sweep_sync(self) -> int:
        removed = 0
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": self._prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            for item in resp.get("Contents", []):
                md = self._head_metadata(item["Key"])
                if md is None:
                    # Deleted between LIST and HEAD (BL-229): already
                    # gone, nothing to sweep. The DELETE below is S3-
                    # idempotent so it needs no equivalent guard.
                    continue
                exp = _safe_float(md.get(_EXPIRES_META))
                if not is_expired(time.time(), exp):
                    continue
                # BL-233: contain a per-object DELETE failure (the
                # BL-227 fan-out-containment class on the sibling sweep
                # path, answering the "should the parent sweep be
                # best-effort for transient errors too?" question the
                # BL-229 _head_metadata scope left open). A transient
                # backend error (SlowDown / throttle, a network blip) on
                # one expired object must not abort the whole pass and
                # leave every later expired object in the listing
                # un-swept for the cycle; the failed object stays alive
                # and the next TTLSweeper interval retries it (the
                # BL-199 resilience contract, extended one level down).
                # The HEAD above stays fail-loud, so an un-inspectable
                # object (real AccessDenied / NoSuchBucket) still
                # surfaces; only the idempotent DELETE action is
                # best-effort, exactly as evict_to_capacity already is.
                # Catches Exception so BaseException (KeyboardInterrupt /
                # SystemExit / asyncio.CancelledError) still propagates
                # per the BL-165 / BL-223 invariant.
                try:
                    self._s3.delete_object(Bucket=self._bucket, Key=item["Key"])
                except Exception:
                    continue
                removed += 1
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return removed

    async def sweep_expired(self) -> int:
        return await asyncio.to_thread(self._sweep_sync)


class BoundedS3Store(S3Store):
    """S3Store + per-object ``insertion-order`` metadata for size-bound
    eviction (``BL-225``, ``BL-135`` size-bound on S3).

    S3 counterpart to ``BL-213`` (SQLite), ``BL-214`` (Redis), and
    ``BL-224`` (DynamoDB), closing the S3 half of ``BL-135`` (the
    size-bound half) for the cold-storage backend. The bare
    ``S3Store`` keeps its prior design unchanged: no per-write
    overhead and no ``insertion-order`` metadata stamp. The subclass
    is the explicit opt-in (same pattern as ``BoundedRedisStore`` /
    ``BoundedDynamoDBStore``).

    The adapter stamps an ``insertion-order`` user-metadata attribute
    on every PUT, set to ``time.time_ns()`` at write time.
    Eviction LISTs the namespace prefix, HEADs each object to read
    the ``insertion-order`` and ``expires-at`` attributes, filters
    out expired-but-unswept objects (the BL-195 read-vs-listing
    parity in S3 form), sorts the live set by
    ``(insertion-order, key)`` ascending, and DELETEs the oldest
    ``overflow`` objects in one parallelised thread call. No
    auxiliary object is needed: every data object carries its own
    ordering attribute, so there is no auxiliary-index staleness
    problem (the rewrite-shifts-to-newest semantic is automatic --
    a fresh PUT overwrites the previous metadata stamp).

    Cost model: every write costs the same one ``PutObject`` round
    trip as the bare ``S3Store`` (the small extra metadata bytes are
    well under S3's 2 KiB user-metadata cap). Eviction costs one
    LIST followed by one HEAD per object plus the DELETE calls,
    matching the parent ``sweep_expired`` shape (LIST +
    HEAD-per-object + DELETE per expired). Use ``S3Store`` directly
    when the size-bound is not needed; the bare class avoids the
    metadata stamp.

    Eviction order: by ``insertion-order`` ASC (nanosecond
    wall-clock at write time), with key-ascending tie-break for
    same-nanosecond writes (resolves both the rare nanosecond
    collision and the migration case where multiple legacy items
    share ``insertion-order = 0``). A re-write of an existing key
    gets a fresh ``insertion-order``, so the rewritten key orders
    as *newest*, matching the SQLite ``INSERT OR REPLACE``
    semantic (``BL-213``), the Redis ZADD-rescore semantic
    (``BL-214``), and the DynamoDB seq-replacement semantic
    (``BL-224``); diverges from the ``BL-212`` InMemoryStore
    first-write FIFO.

    Clock skew: ``time.time_ns()`` is wall-clock based. Multi-
    writer deployments with clock skew can re-order writes across
    writers (a writer with a fast clock orders newer than a writer
    with a slow clock for back-to-back writes). The ``BL-214``
    (Redis) and ``BL-224`` (DynamoDB) adapters use server-side
    atomic counters to avoid this; S3 has no equivalent primitive
    (conditional writes are recent and not universally available),
    so the cold-storage adapter uses wall-clock with nanosecond
    precision and accepts the multi-writer divergence in line with
    the existing S3Store eventual-consistency contract. Single-
    writer deployments stay strictly monotonic on any platform
    whose ``time.time_ns()`` advances per call (Linux and macOS;
    Windows resolution varies by platform).

    Migration: a pre-existing object written via a bare ``S3Store``
    has no ``insertion-order`` user-metadata attribute.
    ``evict_to_capacity`` treats such objects as
    ``insertion-order = 0`` (oldest), so they evict first until
    rewritten. A subsequent write via ``BoundedS3Store`` stamps a
    fresh ``insertion-order`` and the key moves to the newest
    position. Same migration shape as ``BoundedDynamoDBStore`` for
    ``seq`` (``LIMITATIONS.md`` L17 class extension).
    """

    name: str = "s3-bounded"

    async def write(self, key: str, value: bytes, *, ttl_seconds: float | None = None) -> None:
        validate_key(key)
        ttl = self._ttl(ttl_seconds)
        metadata: dict[str, str] = {_SEQ_META: str(time.time_ns())}
        if ttl is not None:
            metadata[_EXPIRES_META] = str(time.time() + ttl)
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=self._okey(key),
            Body=value,
            Metadata=metadata,
        )
        self._audit.write(key, value_bytes=len(value), ttl_seconds=ttl)

    def _collect_live_sync(self) -> list[tuple[int, str]]:
        """LIST the namespace prefix and HEAD each object to read
        ``insertion-order`` and ``expires-at`` metadata. Returns the
        live (non-expired) set as ``(seq, short_key)`` pairs.

        Expired-but-unswept objects are excluded so the cap does not
        double-evict a live key while the dead object still occupies
        the nominal slot (the BL-195 read-vs-listing parity in S3
        form). A legacy object without ``insertion-order`` is
        treated as ``seq = 0`` (oldest, evicts first) per the
        migration contract; ``_safe_int`` / ``_safe_float`` apply the
        BL-226 trust-boundary parsing so a corrupted metadata value
        does not crash the scan past the documented exception
        contract.
        """
        entries: list[tuple[int, str]] = []
        now = time.time()
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": self._prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            for item in resp.get("Contents", []):
                md = self._head_metadata(item["Key"])
                if md is None:
                    # Deleted between LIST and HEAD (BL-229): not part
                    # of the live set, so it cannot count toward the
                    # capacity cap. BL-227 contained the per-key DELETE
                    # in evict_to_capacity but not this collect-phase
                    # HEAD, so a concurrently-deleted object still
                    # crashed the whole eviction scan until this guard.
                    continue
                exp = _safe_float(md.get(_EXPIRES_META))
                if is_expired(now, exp):
                    continue
                seq = _safe_int(md.get(_SEQ_META))
                short = item["Key"][len(self._prefix) :]
                entries.append((seq, short))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return entries

    async def evict_to_capacity(self, max_keys: int) -> int:
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        entries = await asyncio.to_thread(self._collect_live_sync)
        overflow = len(entries) - max_keys
        if overflow <= 0:
            return 0
        # Sort by (seq, key) ascending. The secondary key sort is a
        # deterministic tie-break for the migration case where
        # multiple legacy items share seq=0 and for the rare
        # same-nanosecond collision on a fast host (parallel to the
        # BL-224 ``entries.sort(key=lambda e: (e[0], e[1]))``).
        entries.sort(key=lambda e: (e[0], e[1]))
        to_evict = [k for _seq, k in entries[:overflow]]

        def _delete_all() -> list[str]:
            # Per-item failure containment (BL-227, the BL-222 /
            # BL-223 audit-vs-raise parity class): a transient S3
            # error on key K must not lose the audit for keys
            # already deleted. The failed key stays alive (the size
            # cap may not be fully met this cycle); the next
            # TTLSweeper interval retries it. Catches ``Exception``
            # so ``BaseException`` (KeyboardInterrupt, SystemExit,
            # asyncio.CancelledError) still propagates per the
            # BL-165 / BL-223 invariant.
            deleted: list[str] = []
            for k in to_evict:
                try:
                    self._s3.delete_object(Bucket=self._bucket, Key=self._okey(k))
                    deleted.append(k)
                except Exception:
                    continue
            return deleted

        actually_deleted = await asyncio.to_thread(_delete_all)
        for k in actually_deleted:
            self._audit.delete(k, existed=True)
        return len(actually_deleted)
