"""Namespace type for memory operations.

A Namespace owns a keyspace within a MemoryStore. Each store is bound
to a single Namespace at construction, so cross-namespace access is
impossible by construction. The Namespace also carries a default
retention policy that the store applies to writes without explicit TTL.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, model_validator

from memory.validators import validate_namespace_name

__all__ = ["Namespace"]


class Namespace(BaseModel):
    """A memory namespace.

    Attributes:
        name: Namespace identifier. Validated against
            NAMESPACE_NAME_PATTERN at construction.
        workload: Name of the workload that owns this namespace. Used
            for event correlation and audit; not enforced at the store
            level (the store is already namespace-bound).
        retention_seconds: Default TTL applied to writes that do not
            specify an explicit ttl_seconds. None means writes persist
            until deleted. Must be a finite positive float when set
            (`BL-197`): NaN / +inf are rejected at the construction
            boundary so an anomalous TTL cannot silently propagate to
            ``expires_at = now + NaN/inf`` and disable expiration via
            the BL-195 helpers (which preserved the prior bug-for-bug
            non-finite-is-live semantics; this validator is the longer-
            term fix the Copilot review on PR #51 suggested).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    workload: str
    retention_seconds: float | None = None

    @model_validator(mode="after")
    def _check_name(self) -> Namespace:
        validate_namespace_name(self.name)
        if self.retention_seconds is not None:
            if not math.isfinite(self.retention_seconds):
                raise ValueError(
                    "retention_seconds must be finite when set "
                    "(NaN / +inf would silently disable expiration via "
                    "the BL-195 expiry helpers)"
                )
            if self.retention_seconds <= 0:
                raise ValueError("retention_seconds must be positive when set")
        return self

    def resolve_ttl(self, ttl_seconds: float | None) -> float | None:
        """Resolve an explicit per-call ``ttl_seconds`` against this
        namespace's default ``retention_seconds`` (`BL-197`).

        Centralises the five-way duplication of
        ``_ttl`` / ``_effective_ttl`` previously copied across each
        adapter, with the same per-call validation the constructor
        applies to ``retention_seconds``: an explicit ``ttl_seconds``
        must be ``None`` (fall through to the namespace default) or a
        finite positive float. NaN / +inf raises ``ValueError`` at the
        adapter's resolver call site (still a load-of-the-write
        boundary, not mid-store), matching the constructor's stance.
        """
        if ttl_seconds is None:
            return self.retention_seconds
        if not math.isfinite(ttl_seconds):
            raise ValueError(
                "ttl_seconds must be finite when set "
                "(NaN / +inf would silently disable expiration via "
                "the BL-195 expiry helpers)"
            )
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive when set")
        return ttl_seconds
