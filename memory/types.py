"""Namespace type for memory operations.

A Namespace owns a keyspace within a MemoryStore. Each store is bound
to a single Namespace at construction, so cross-namespace access is
impossible by construction. The Namespace also carries a default
retention policy that the store applies to writes without explicit TTL.
"""

from __future__ import annotations

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
            until deleted.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    workload: str
    retention_seconds: float | None = None

    @model_validator(mode="after")
    def _check_name(self) -> Namespace:
        validate_namespace_name(self.name)
        if self.retention_seconds is not None and self.retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive when set")
        return self
