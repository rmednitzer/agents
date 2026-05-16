"""Behavioral contracts for the harness.

A Contract declares obligations of a workload at four points:

- preconditions: must hold on input state before the workload runs
- invariants: must hold throughout the run (checked against observable state)
- postconditions: must hold on output state after the workload runs
- governance: must hold on each proposed action (wired in Phase 2)

Each obligation is a Predicate with a Severity. Hard violations halt the run
via an exception in harness.errors; soft violations emit a Violation event
to the configured EventSink and the run continues.

The Predicate Protocol is generic over the state type so contracts can be
parameterized by Pydantic input/output models for type-safe predicates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Contract",
    "FunctionPredicate",
    "Predicate",
    "Severity",
    "predicate",
]


class Severity(StrEnum):
    """Severity of a predicate violation.

    HARD violations halt the run via an exception in harness.errors.
    SOFT violations emit a Violation event and the run continues.
    """

    HARD = "hard"
    SOFT = "soft"


@runtime_checkable
class Predicate[StateT](Protocol):
    """A boolean condition over a state, with a severity.

    Implementations carry a stable name (for event logging and audit) and
    a severity. The __call__ returns True if the state satisfies the
    condition, False otherwise. Predicates must be side-effect-free.

    The name and severity attributes are read-only by Protocol; concrete
    implementations may store them as frozen dataclass fields, property
    getters, or class variables.
    """

    @property
    def name(self) -> str: ...

    @property
    def severity(self) -> Severity: ...

    def __call__(self, state: StateT) -> bool: ...


@dataclass(frozen=True)
class FunctionPredicate[StateT]:
    """A Predicate backed by a callable.

    Use the `predicate` decorator to construct these from plain functions.
    For predicates that need internal state, implement the Predicate Protocol
    directly with a class.
    """

    name: str
    severity: Severity
    fn: Callable[[StateT], bool]

    def __call__(self, state: StateT) -> bool:
        return self.fn(state)


def predicate[StateT](
    *, name: str, severity: Severity = Severity.HARD
) -> Callable[[Callable[[StateT], bool]], FunctionPredicate[StateT]]:
    """Decorator that turns a function into a FunctionPredicate.

    Args:
        name: Stable name used in event logging and audit. Should be unique
            within a Contract's predicate set.
        severity: HARD (halt on violation) or SOFT (log and continue).

    Returns:
        A decorator wrapping a `Callable[[StateT], bool]` into a
        `FunctionPredicate[StateT]` carrying the given name and severity.

    Example:
        @predicate(name="input_non_empty", severity=Severity.HARD)
        def input_non_empty(state: MyInput) -> bool:
            return bool(state.query)
    """

    def decorator(fn: Callable[[StateT], bool]) -> FunctionPredicate[StateT]:
        return FunctionPredicate(name=name, severity=severity, fn=fn)

    return decorator


@dataclass(frozen=True)
class Contract[InputT, OutputT]:
    """Behavioral contract for a workload.

    Attributes:
        name: Stable identifier, typically the workload name.
        version: Semantic version of the contract.
        preconditions: Predicates over the input, validated before runtime.
        invariants: Predicates over observable state, validated during run.
        postconditions: Predicates over the output, validated after runtime.
        governance: Predicates over individual proposed actions. Wired to
            the runtime in Phase 2; declared here so contracts are forward
            compatible.
        approval_required: Tool names that require human-in-the-loop
            approval before invocation. The interruption flow is in
            harness.interruption; live wiring lands with Phase 2.
    """

    name: str
    version: str
    preconditions: list[Predicate[InputT]] = field(default_factory=list)
    invariants: list[Predicate[Any]] = field(default_factory=list)
    postconditions: list[Predicate[OutputT]] = field(default_factory=list)
    governance: list[Predicate[Any]] = field(default_factory=list)
    approval_required: list[str] = field(default_factory=list)
