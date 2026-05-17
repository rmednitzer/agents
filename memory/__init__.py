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
from memory.dynamodb import DynamoDBStore
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
from memory.redis import RedisStore
from memory.s3 import S3Store
from memory.sqlite import SQLiteStore
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
    "ACLStore",
    "AccessDenied",
    "AccessPolicy",
    "AttributeACL",
    "AttributeRule",
    "BatchMemoryStore",
    "CASMemoryStore",
    "ContentAddressableStore",
    "DynamoDBStore",
    "EncryptedStore",
    "EnvKeyProvider",
    "FileKeyProvider",
    "InMemoryStore",
    "KeyProvider",
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
    "StaticKeyProvider",
    "SweepableStore",
    "TTLSweeper",
    "VersionedKeyProvider",
    "validate_key",
    "validate_namespace_name",
    "wrap_acl",
    "wrap_encrypted",
]
