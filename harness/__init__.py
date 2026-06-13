"""agents harness: contracts, enforcement, events, runtime adapters.

Public API surface. Workloads import from `harness` only; they should not
reach into submodules. See CLAUDE.md and docs/adr/ for architecture.
"""

from harness.anthropic_api import (
    AnthropicBatchProcessor,
    BatchRequest,
    BatchResult,
    BatchStatus,
    cache_control_system,
)
from harness.authority import (
    AuthorityTier,
    MappingRollbackPlanner,
    MappingTierClassifier,
    RollbackPlanner,
    TierClassifier,
)
from harness.budgets import ActionBudget, BudgetKind, BudgetTracker
from harness.composition import compose_contracts
from harness.contract import (
    Contract,
    FunctionPredicate,
    Predicate,
    Severity,
    predicate,
)
from harness.drift import DriftMonitor, jensen_shannon_divergence
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
    AccessDenied as AccessDeniedEvent,
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
    DispatchObserved,
    DriftThresholdCrossed,
    GovernanceViolated,
    HarnessEvent,
    InvariantViolated,
    MemoryDelete,
    MemoryRead,
    MemoryWrite,
    PostconditionViolated,
    PreconditionViolated,
    RecoveryApplied,
    SkillDispatched,
)
from harness.evidence import EvidenceContext, EvidenceHook, EvidenceRecord, RecordingEvidenceHook
from harness.fallback import FallbackChain, default_should_descend
from harness.freshness import Refusal, is_stale, require_fresh
from harness.grounding import grounding_predicate, ungrounded_citations
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
from harness.openai_api import (
    OpenAIBatchProcessor,
    OpenAIBatchRequest,
    OpenAIBatchResult,
    OpenAIBatchStatus,
)
from harness.otel import OTelSink
from harness.provenance import (
    RUN_RECORD_SCHEMA_VERSION,
    RunOutcome,
    RunRecord,
    contract_digest,
    record_invariant_violations,
    verify_run_record,
)
from harness.recovery import RecoveryDirective, RecoveryHandler, RecoveryOutcome
from harness.redaction import RedactingSink, Redactor
from harness.runtime import PydanticAIRuntime, RetryPolicy, Runtime
from harness.sinks import EventSink, JsonlSink, MemorySink, MultiSink, NullSink
from harness.tools import ToolCatalog

__all__ = [
    "RUN_RECORD_SCHEMA_VERSION",
    "AccessDeniedEvent",
    "ActionBudget",
    "ActionRecord",
    "AnthropicBatchProcessor",
    "ApprovalDenied",
    "ApprovalDeniedEvent",
    "ApprovalGranted",
    "ApprovalInterruption",
    "ApprovalRequested",
    "AuthorityTier",
    "BatchRequest",
    "BatchResult",
    "BatchStatus",
    "BudgetExceeded",
    "BudgetExceededEvent",
    "BudgetKind",
    "BudgetTracker",
    "Contract",
    "ContractCompleted",
    "ContractStarted",
    "DispatchObserved",
    "DriftMonitor",
    "DriftThresholdCrossed",
    "EventSink",
    "EvidenceContext",
    "EvidenceHook",
    "EvidenceRecord",
    "FallbackChain",
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
    "MappingRollbackPlanner",
    "MappingTierClassifier",
    "MemoryDelete",
    "MemoryRead",
    "MemorySink",
    "MemoryWrite",
    "MultiSink",
    "NullSink",
    "OTelSink",
    "OpenAIBatchProcessor",
    "OpenAIBatchRequest",
    "OpenAIBatchResult",
    "OpenAIBatchStatus",
    "PostconditionViolated",
    "PostconditionViolation",
    "PreconditionViolated",
    "PreconditionViolation",
    "Predicate",
    "ProposedAction",
    "PydanticAIRuntime",
    "RecordingEvidenceHook",
    "RecoveryApplied",
    "RecoveryDirective",
    "RecoveryHandler",
    "RecoveryOutcome",
    "RedactingSink",
    "Redactor",
    "Refusal",
    "ResumableState",
    "RetryPolicy",
    "RollbackPlanner",
    "RunOutcome",
    "RunRecord",
    "Runtime",
    "Severity",
    "SkillDispatched",
    "TierClassifier",
    "ToolCatalog",
    "ToolGuard",
    "ToolSpec",
    "cache_control_system",
    "compose_contracts",
    "contract_digest",
    "default_should_descend",
    "grounding_predicate",
    "is_stale",
    "jensen_shannon_divergence",
    "predicate",
    "record_invariant_violations",
    "require_fresh",
    "run_under_contract",
    "ungrounded_citations",
    "verify_run_record",
]
