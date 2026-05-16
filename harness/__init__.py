"""agents harness: contracts, enforcement, events, runtime adapters.

Public API surface. Workloads import from `harness` only; they should not
reach into submodules. See CLAUDE.md and docs/adr/ for architecture.
"""

from harness.contract import (
    Contract,
    FunctionPredicate,
    Predicate,
    Severity,
    predicate,
)
from harness.enforcement import run_under_contract
from harness.errors import (
    ApprovalDenied,
    BudgetExceeded,
    GovernanceViolation,
    HarnessError,
    InvariantViolation,
    PostconditionViolation,
    PreconditionViolation,
)
from harness.events import (
    ApprovalDenied as ApprovalDeniedEvent,
)
from harness.events import (
    ApprovalGranted,
    ApprovalRequested,
    BudgetExceededEvent,
    ContractCompleted,
    ContractStarted,
    GovernanceViolated,
    HarnessEvent,
    InvariantViolated,
    PostconditionViolated,
    PreconditionViolated,
    SkillDispatched,
)
from harness.interruption import (
    ActionRecord,
    ApprovalInterruption,
    Interruption,
    ResumableState,
)
from harness.runtime import PydanticAIRuntime, Runtime
from harness.sinks import EventSink, JsonlSink, MemorySink, MultiSink, NullSink

__all__ = [
    "ActionRecord",
    "ApprovalDenied",
    "ApprovalDeniedEvent",
    "ApprovalGranted",
    "ApprovalInterruption",
    "ApprovalRequested",
    "BudgetExceeded",
    "BudgetExceededEvent",
    "Contract",
    "ContractCompleted",
    "ContractStarted",
    "EventSink",
    "FunctionPredicate",
    "GovernanceViolated",
    "GovernanceViolation",
    "HarnessError",
    "HarnessEvent",
    "Interruption",
    "InvariantViolated",
    "InvariantViolation",
    "JsonlSink",
    "MemorySink",
    "MultiSink",
    "NullSink",
    "PostconditionViolated",
    "PostconditionViolation",
    "PreconditionViolated",
    "PreconditionViolation",
    "Predicate",
    "PydanticAIRuntime",
    "ResumableState",
    "Runtime",
    "Severity",
    "SkillDispatched",
    "predicate",
    "run_under_contract",
]
