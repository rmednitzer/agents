"""Run-provenance records for completed contract runs (BL-185).

A ``RunRecord`` is a small, schema-versioned, self-attesting artifact
emitted at the terminal point of a ``run_under_contract`` call. It binds
the run to the *content digest of the contract that actually enforced
it*, computed in-process at enforcement time (not reconstructed from
version-control history after the fact).

This is the agents-repo analogue of the provenance discipline in the
sibling ``sentinel`` corpus, with two deliberate divergences from how
``sentinel`` does it, each a documented lesson rather than a copy:

1. ``sentinel/.github/scripts/check_provenance.py`` resolves the
   producing config's hash from ``git log -1`` on the artifact's path,
   so a later fix PR "advances the anchor" and the attestation only
   proves internal consistency at some commit, not the config in force
   at generation time. Here the digest is taken from the live
   ``Contract`` object inside the enforcement loop, so it is bound to
   what actually ran. There is no git round-trip and nothing to
   re-stamp.
2. ``sentinel``'s consistency CI routes window-overlap / coverage
   findings into warnings and exits zero, so the "blocking" claim in
   its docs is not enforced. ``verify_run_record`` and
   ``scripts/check_run_records.py`` return violations as hard errors
   only; there is no warn-and-pass tier.

``RunRecord`` is additive: it is produced only when a caller passes
``record_sink=`` to ``run_under_contract`` (ADR 0007 / ADR 0012).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.contract import Contract

__all__ = [
    "RUN_RECORD_SCHEMA_VERSION",
    "SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS",
    "RunOutcome",
    "RunRecord",
    "contract_digest",
    "verify_run_record",
]

RUN_RECORD_SCHEMA_VERSION = "1.0.0"
"""Schema version stamped on freshly produced records.

There is one shape today, so the offline gate validates every
supported record against the current ``RunRecord`` model. This set is
the *extension point*, not yet a dispatcher: when the shape changes,
bump this constant, keep the prior value in
``SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS``, and have the gate select a
per-version validator (the structure ``sentinel``'s
``validate_artifacts.py`` reaches once it has more than one schema).
Until a v2 exists, building that dispatch would be speculative.
"""

SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0.0"})
"""Every schema version a record may legitimately declare.

``schema_version`` has a default so producers (the enforcement loop)
need not pass it; an omitted field is therefore the current version,
both for the model and for the offline gate, which validates the model
before checking this set so the two agree.
"""

RunOutcome = Literal[
    "completed",
    "paused",
    "precondition",
    "invariant",
    "postcondition",
    "governance",
    "budget",
    "approval_denied",
    "output_invalid",
]
"""Terminal outcome of a run.

``completed`` is a clean return; ``paused`` is an approval interruption
(a ``ResumableState``, not a terminal success); ``output_invalid`` is a
runtime result that fails to parse into the workload's output model;
the rest name the hard violation / budget exception that ended the run.
The vocabulary matches ``evaluation.dataset.TrajectoryOutcome`` plus
``paused`` and ``output_invalid`` so a recorded corpus and the
trajectory gate speak the same terms.
"""


def _predicate_surface(predicates: Any) -> list[list[str]]:
    """Order-independent ``[name, severity]`` pairs for a predicate list.

    Sorted by ``(name, severity)`` so the digest reflects a contract's
    behavioural *surface*, not the incidental order predicates were
    appended in. Two contracts that enforce the same obligations with
    the same severities digest identically.
    """
    return sorted([p.name, str(p.severity)] for p in predicates)


def contract_digest(contract: Contract[Any, Any]) -> str:
    """Return the SHA-256 hex digest of a contract's behavioural surface.

    The digest covers the contract identity (name, version) and the
    name+severity of every precondition, invariant, postcondition, and
    governance predicate, plus the sorted ``approval_required`` tool
    list. It deliberately does NOT cover predicate *implementations*:
    two builds with the same declared obligations attest equal, and a
    changed obligation set (an added/removed/re-severitied predicate, a
    new approval-gated tool) changes the digest. This is the in-process
    counterpart of ``sentinel``'s ``codebook_hash``; it is computed from
    the live object, so it cannot be re-stamped after the fact.
    """
    canonical = {
        "name": contract.name,
        "version": contract.version,
        "preconditions": _predicate_surface(contract.preconditions),
        "invariants": _predicate_surface(contract.invariants),
        "postconditions": _predicate_surface(contract.postconditions),
        "governance": _predicate_surface(contract.governance),
        "approval_required": sorted(contract.approval_required),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RunRecord(BaseModel):
    """A self-attesting record of one completed contract run.

    Immutable. Stamped with the schema version it was written under and
    the digest of the contract that enforced it, so a persisted corpus
    of records can be re-validated offline (``scripts/check_run_records``)
    against the contracts that should have produced them.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = RUN_RECORD_SCHEMA_VERSION
    run_id: str
    """The run's trace id (correlates with the emitted HarnessEvents)."""
    workload: str
    contract_name: str
    contract_version: str
    contract_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    """SHA-256 (64 lowercase hex chars) of the enforcing contract's
    surface at enforcement time. The shape is enforced by the model, so
    a non-digest value fails validation independent of any registry."""
    outcome: RunOutcome
    started_at: str
    """ISO 8601 UTC; matches ``ContractStarted.timestamp``."""
    completed_at: str
    """ISO 8601 UTC; the terminal instant of the run."""
    duration_ms: float = Field(ge=0.0)


def verify_run_record(record: RunRecord, contract: Contract[Any, Any]) -> list[str]:
    """Return a list of provenance violations for ``record``.

    Empty list means the record is sound. Every entry is a hard error:
    there is no warn-and-pass tier (the explicit divergence from
    ``sentinel``'s consistency CI, whose overlap/coverage findings are
    warnings that still exit zero). Checks:

    - the declared ``schema_version`` is one this build supports;
    - the stamped ``contract_digest`` equals the live digest of
      ``contract`` (the run was enforced by the contract it claims);
    - identity fields agree with ``contract``.
    """
    errors: list[str] = []
    if record.schema_version not in SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS:
        errors.append(
            f"schema_version {record.schema_version!r} not in supported set "
            f"{sorted(SUPPORTED_RUN_RECORD_SCHEMA_VERSIONS)}"
        )
    if record.workload != contract.name:
        errors.append(f"workload {record.workload!r} does not match contract {contract.name!r}")
    if record.contract_name != contract.name:
        errors.append(
            f"contract_name {record.contract_name!r} does not match contract {contract.name!r}"
        )
    if record.contract_version != contract.version:
        errors.append(
            f"contract_version {record.contract_version!r} does not match "
            f"contract {contract.version!r}"
        )
    expected = contract_digest(contract)
    if record.contract_digest != expected:
        errors.append(
            f"contract_digest {record.contract_digest} does not match the "
            f"live digest {expected} of contract {contract.name!r} "
            f"v{contract.version} (the run was not enforced by this "
            f"contract surface)"
        )
    return errors
