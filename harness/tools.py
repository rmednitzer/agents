"""Known-tool catalog for the harness.

A ToolCatalog is the set of tool names a runtime can expose for a run:
locally-defined tools plus tools advertised by the MCP servers a
workload declares. It is the authority a skill's ``allowed-tools``
declaration is validated against (BL-012, ADR 0007): a skill that
pre-approves a tool the harness cannot provide is a configuration
error, and surfacing it when the registry is built beats discovering it
when the tool is first called mid-run.

The catalog is intentionally minimal: a set of names with set algebra.
It does not carry schemas (ToolSpec already does); validation only needs
identity.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from harness.mcp import ToolSpec

__all__ = ["ToolCatalog"]


class ToolCatalog:
    """An immutable, hashable set of known tool names.

    Construct from bare names via ``from_names`` or from ToolSpec objects
    via ``from_specs``. Catalogs compose with ``merge`` so a runtime can
    union its locally-defined tools with each MCP server's advertised
    tool list before validating skill declarations against the whole.
    """

    __slots__ = ("_names",)

    def __init__(self, names: Iterable[str] = ()) -> None:
        self._names: frozenset[str] = frozenset(names)

    @classmethod
    def from_names(cls, names: Iterable[str]) -> ToolCatalog:
        """Build a catalog from an iterable of tool names."""
        return cls(names)

    @classmethod
    def from_specs(cls, specs: Iterable[ToolSpec]) -> ToolCatalog:
        """Build a catalog from ToolSpec objects, keyed by ``spec.name``."""
        return cls(spec.name for spec in specs)

    def names(self) -> frozenset[str]:
        """Return the underlying set of known tool names."""
        return self._names

    def merge(self, other: ToolCatalog) -> ToolCatalog:
        """Return a new catalog containing names from both catalogs."""
        return ToolCatalog(self._names | other._names)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._names

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._names))

    def __len__(self) -> int:
        return len(self._names)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ToolCatalog) and other._names == self._names

    def __hash__(self) -> int:
        return hash(self._names)

    def __repr__(self) -> str:
        return f"ToolCatalog({sorted(self._names)!r})"
