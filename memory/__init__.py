"""agents memory: namespace-bound key-value stores.

Public API surface. See docs/adr/0004-memory-namespace-contracts.md.

A Namespace is constructed once per workload (typically declared in the
WorkloadManifest in Phase 4). A MemoryStore is bound to a Namespace at
construction; cross-namespace access is impossible without explicit
construction of additional stores.

Workloads serialize their own data; the store handles raw bytes.
"""

from memory.acl import (
    AccessPolicy,
    ACLStore,
    AttributeACL,
    AttributeRule,
    Operation,
    RoleACL,
    wrap_acl,
)
from memory.compaction import (
    CompactionResult,
    MemoryCompactor,
    Summarizer,
    TruncatingSummarizer,
)
from memory.dynamodb import BoundedDynamoDBStore, DynamoDBStore
from memory.encryption import (
    EncryptedStore,
    EnvKeyProvider,
    FileKeyProvider,
    KeyProvider,
    RotatingKeyProvider,
    StaticKeyProvider,
    VersionedKeyProvider,
    wrap_encrypted,
)
from memory.errors import AccessDenied, MemoryError, NamespaceViolation
from memory.inmemory import InMemoryStore
from memory.redis import BoundedRedisStore, RedisStore
from memory.s3 import BoundedS3Store, S3Store
from memory.semantic import Embedder, InMemorySemanticStore
from memory.sqlite import SQLiteStore
from memory.store import (
    BatchMemoryStore,
    BoundedSweepableStore,
    CASMemoryStore,
    ContentAddressableStore,
    MemoryStore,
    ScannableStore,
    SemanticHit,
    SemanticMemoryStore,
    SweepableStore,
    TransactionalMemoryStore,
    TxnDelete,
    TxnWrite,
    VersionedMemoryStore,
)
from memory.sweep import TTLSweeper
from memory.tiering import TieredMemoryStore
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
    "ACLStore",
    "AccessDenied",
    "AccessPolicy",
    "AttributeACL",
    "AttributeRule",
    "BatchMemoryStore",
    "BoundedDynamoDBStore",
    "BoundedRedisStore",
    "BoundedS3Store",
    "BoundedSweepableStore",
    "CASMemoryStore",
    "CompactionResult",
    "ContentAddressableStore",
    "DynamoDBStore",
    "Embedder",
    "EncryptedStore",
    "EnvKeyProvider",
    "FileKeyProvider",
    "InMemorySemanticStore",
    "InMemoryStore",
    "KeyProvider",
    "MemoryCompactor",
    "MemoryError",
    "MemoryStore",
    "Namespace",
    "NamespaceViolation",
    "Operation",
    "RedisStore",
    "RoleACL",
    "RotatingKeyProvider",
    "S3Store",
    "SQLiteStore",
    "ScannableStore",
    "SemanticHit",
    "SemanticMemoryStore",
    "StaticKeyProvider",
    "Summarizer",
    "SweepableStore",
    "TTLSweeper",
    "TieredMemoryStore",
    "TransactionalMemoryStore",
    "TruncatingSummarizer",
    "TxnDelete",
    "TxnWrite",
    "VersionedKeyProvider",
    "VersionedMemoryStore",
    "validate_key",
    "validate_namespace_name",
    "wrap_acl",
    "wrap_encrypted",
]
