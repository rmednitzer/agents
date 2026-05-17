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
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = ["RecoveryDirective", "RecoveryHandler", "RecoveryOutcome"]

# What the enforcement loop should do after a soft violation's handler
# ran. ``continue`` is the L1 behaviour (emit-and-continue, the run
# proceeds unchanged). The others let a handler drive control flow
# (BL-102); they only take effect on the postcondition stage, where the
# output is in hand:
#  - ``retry``: re-invoke the runtime once and re-validate the output.
#  - ``substitute``: replace the output with ``RecoveryOutcome.replacement``.
#  - ``escalate``: raise PostconditionViolation despite the soft severity.
RecoveryDirective = Literal["continue", "retry", "substitute", "escalate"]


@dataclass(frozen=True)
class RecoveryOutcome:
    """What a handler did.

    Attributes:
        action: Short, audit-friendly description of the remediation
            taken (e.g. "retried with backoff", "substituted default").
        recovered: The handler's own signal that it believes the
            condition was remediated. Recorded for audit; it does not
            change the soft-continue control flow.
        directive: What the enforcement loop should do next (BL-102).
            Defaults to ``"continue"`` so an existing handler that does
            not set it preserves the exact L1 emit-and-continue path.
            ``retry`` / ``substitute`` / ``escalate`` are honoured only
            on the postcondition stage (the only stage with an output to
            act on); on other stages a non-continue directive is recorded
            but the run still continues, as before.
        replacement: The output to use when ``directive == "substitute"``.
            Ignored otherwise. Must validate against the workload's
            output model or the substitution is rejected and the run
            continues with the original output.
    """

    action: str
    recovered: bool = True
    directive: RecoveryDirective = "continue"
    replacement: Any = None


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
