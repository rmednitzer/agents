"""Recovery handlers for soft violations (BL-061; the R in P,I,G,R).

ADR 0002 modelled contracts on the Bhardwaj (P, I, G, R) tuple but L1
shipped only flag-and-emit for soft violations -- R was unspecified.
A RecoveryHandler is the recovery action: when a SOFT predicate fails,
the enforcement loop invokes the handler registered for that predicate
name (if any), records a RecoveryApplied event, and -- because the
violation is soft -- continues. Recovery is observational + remedial,
not control flow: a soft violation never halts, with or without a
handler. Hard violations still raise; recovery does not mask them.

The surface is opt-in and additive: ``run_under_contract`` takes an
optional ``recovery`` mapping; omitting it preserves L1 behaviour
exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = ["RecoveryHandler", "RecoveryOutcome"]


@dataclass(frozen=True)
class RecoveryOutcome:
    """What a handler did.

    Attributes:
        action: Short, audit-friendly description of the remediation
            taken (e.g. "retried with backoff", "substituted default").
        recovered: The handler's own signal that it believes the
            condition was remediated. Recorded for audit; it does not
            change the soft-continue control flow.
    """

    action: str
    recovered: bool = True


@runtime_checkable
class RecoveryHandler(Protocol):
    """Remediation for a named soft predicate.

    ``stage`` is "precondition" | "invariant" | "postcondition".
    ``state`` is the same state object the predicate saw (input, output,
    or invariant state). Handlers must be side-effect-aware: they run
    inside the enforced run and their effects are the workload's
    concern, not the harness's.
    """

    async def recover(
        self,
        *,
        predicate: str,
        stage: str,
        state: Any,
    ) -> RecoveryOutcome: ...
