"""agents harness: contracts, enforcement, events, runtime adapters.

Public API surface. Workloads import from `harness` only; they should not
reach into submodules. See CLAUDE.md and docs/adr/ for architecture.
"""

from harness.budgets import ActionBudget, BudgetKind, BudgetTracker
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
from harness.guard import (
    GuardDecision,
    GuardResponse,
    HarnessToolGuard,
    ProposedAction,
    ToolGuard,
)
from harness.interruption import (
    ActionRecord,
    ApprovalInterruption,
    Interruption,
    ResumableState,
)
from harness.mcp import (
    MCPHandle,
    MCPLifecycle,
    MCPServerSpec,
    MCPTransport,
    ToolSpec,
)
from harness.runtime import PydanticAIRuntime, Runtime
from harness.sinks import EventSink, JsonlSink, MemorySink, MultiSink, NullSink

__all__ = [
    "ActionBudget",
    "ActionRecord",
    "ApprovalDenied",
    "ApprovalDeniedEvent",
    "ApprovalGranted",
    "ApprovalInterruption",
    "ApprovalRequested",
    "BudgetExceeded",
    "BudgetExceededEvent",
    "BudgetKind",
    "BudgetTracker",
    "Contract",
    "ContractCompleted",
    "ContractStarted",
    "EventSink",
    "FunctionPredicate",
    "GovernanceViolated",
    "GovernanceViolation",
    "GuardDecision",
    "GuardResponse",
    "HarnessError",
    "HarnessEvent",
    "HarnessToolGuard",
    "Interruption",
    "InvariantViolated",
    "InvariantViolation",
    "JsonlSink",
    "MCPHandle",
    "MCPLifecycle",
    "MCPServerSpec",
    "MCPTransport",
    "MemorySink",
    "MultiSink",
    "NullSink",
    "PostconditionViolated",
    "PostconditionViolation",
    "PreconditionViolated",
    "PreconditionViolation",
    "Predicate",
    "ProposedAction",
    "PydanticAIRuntime",
    "ResumableState",
    "Runtime",
    "Severity",
    "SkillDispatched",
    "ToolGuard",
    "ToolSpec",
    "predicate",
    "run_under_contract",
]
