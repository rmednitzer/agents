"""Tests for harness.contract."""

from __future__ import annotations

from pydantic import BaseModel

from harness.contract import (
    Contract,
    FunctionPredicate,
    Predicate,
    Severity,
    predicate,
)


class _Input(BaseModel):
    query: str


class _Output(BaseModel):
    text: str


def test_severity_values() -> None:
    assert Severity.HARD == "hard"
    assert Severity.SOFT == "soft"


def test_predicate_decorator_constructs_function_predicate() -> None:
    @predicate(name="non_empty", severity=Severity.HARD)
    def non_empty(state: _Input) -> bool:
        return bool(state.query)

    assert isinstance(non_empty, FunctionPredicate)
    assert non_empty.name == "non_empty"
    assert non_empty.severity == Severity.HARD
    assert non_empty(_Input(query="hello")) is True
    assert non_empty(_Input(query="")) is False


def test_predicate_decorator_default_severity_hard() -> None:
    @predicate(name="x")
    def p(state: _Input) -> bool:
        return True

    assert p.severity == Severity.HARD


def test_function_predicate_conforms_to_protocol() -> None:
    @predicate(name="p", severity=Severity.SOFT)
    def p(state: _Input) -> bool:
        return True

    assert isinstance(p, Predicate)


def test_contract_construction() -> None:
    @predicate(name="pre", severity=Severity.HARD)
    def pre(state: _Input) -> bool:
        return bool(state.query)

    @predicate(name="post", severity=Severity.SOFT)
    def post(state: _Output) -> bool:
        return len(state.text) > 0

    contract: Contract[_Input, _Output] = Contract(
        name="example",
        version="0.1.0",
        preconditions=[pre],
        postconditions=[post],
        approval_required=["risky_tool"],
    )
    assert contract.name == "example"
    assert contract.version == "0.1.0"
    assert len(contract.preconditions) == 1
    assert len(contract.postconditions) == 1
    assert contract.approval_required == ["risky_tool"]


def test_contract_is_frozen() -> None:
    contract: Contract[_Input, _Output] = Contract(name="x", version="0.1.0")
    try:
        contract.name = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Contract should be frozen")
