"""Fifteenth audit, BL-263: EvidenceContext.arguments is read-only at runtime.

``EvidenceContext.arguments`` is typed ``Mapping`` (read-only) and the
context is public harness surface, but ``_with_evidence`` previously
handed the hook a plain ``dict`` it could mutate. The framework now wraps
the capture snapshot in ``MappingProxyType`` so a hook cannot mutate it,
making the documented read-only typing true at runtime (the Copilot review
of PR #123). The shallow-snapshot semantics (BL-253) are unchanged: a
later mutation of the live argument dict is still not observed.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest

from harness.authority import AuthorityTier
from harness.runtime import _GateResult, _with_evidence


class _CapturingHook:
    def __init__(self) -> None:
        self.context: Any = None

    async def before(self, context: Any) -> Any:
        self.context = context
        return context

    async def after(self, token: Any, *, error: BaseException | None = None) -> None:
        pass


async def _capture(arguments: dict[str, Any]) -> _CapturingHook:
    hook = _CapturingHook()

    async def _run() -> str:
        return "ok"

    gate = _GateResult(soft=None, tier=AuthorityTier.IRREVERSIBLE, rollback_plan=None)
    result = await _with_evidence(
        hook, gate, tool="delete_data", arguments=arguments, tool_call_id=None, run=_run
    )
    assert result == "ok"
    return hook


async def test_evidence_context_arguments_reject_mutation() -> None:
    hook = await _capture({"target": "prod"})
    assert isinstance(hook.context.arguments, MappingProxyType)
    assert hook.context.arguments == {"target": "prod"}
    with pytest.raises(TypeError):
        hook.context.arguments["target"] = "staging"
    with pytest.raises(TypeError):
        del hook.context.arguments["target"]


async def test_evidence_context_snapshot_isolated_from_later_live_mutation() -> None:
    live = {"target": "prod"}
    hook = await _capture(live)
    # The read-only proxy wraps a shallow copy (BL-253), not the live dict,
    # so a later mutation of the live argument dict is not observed.
    live["target"] = "staging"
    assert hook.context.arguments["target"] == "prod"
