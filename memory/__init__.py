"""agents memory: namespace-bound key-value stores.

Public API surface. See docs/adr/0004-memory-namespace-contracts.md.

A Namespace is constructed once per workload (typically declared in the
WorkloadManifest in Phase 4). A MemoryStore is bound to a Namespace at
construction; cross-namespace access is impossible without explicit
construction of additional stores.

Workloads serialize their own data; the store handles raw bytes.
"""

from memory.errors import MemoryError, NamespaceViolation
from memory.inmemory import InMemoryStore
from memory.store import (
    BatchMemoryStore,
    CASMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SweepableStore,
)
from memory.sweep import TTLSweeper
from memory.types import Namespace
from memory.validators import (
    KEY_MAX_LENGTH,
    NAMESPACE_NAME_PATTERN,
    validate_key,
    validate_namespace_name,
)

__all__ = [
    "KEY_MAX_LENGTH",
    "NAMESPACE_NAME_PATTERN",
    "BatchMemoryStore",
    "CASMemoryStore",
    "ContentAddressableStore",
    "InMemoryStore",
    "MemoryError",
    "MemoryStore",
    "Namespace",
    "NamespaceViolation",
    "ScannableStore",
    "SweepableStore",
    "TTLSweeper",
    "validate_key",
    "validate_namespace_name",
]
