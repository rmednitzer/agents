"""Ninth-audit harness fix: regression tests for `BL-223` (ADR 0019).

`BL-223` (MultiSink per-sink failure containment): `MultiSink.emit`
iterated its wrapped sinks and called ``sink.emit(event)`` without any
exception containment. A single failing sink (a flaky OTel exporter, a
disk-full ``JsonlSink``, a third-party sink with a transient network
error) raised out of the fan-out loop, so every downstream sink in the
``MultiSink`` was skipped for that event. The BL-202 / BL-167
audit-vs-raise parity invariant ("every state-affecting raise has a
matching audit event") was broken: an enforcement-loop ``emit`` of a
``BudgetExceededEvent`` or ``GovernanceViolated`` could be lost across
the OTLP sink because the local JsonlSink happened to fail first.

BL-222 (eighth audit) fixed the same class on the ``MultiDispatcher``
ensemble side: per-member failure must not poison the surviving
members' contributions. BL-223 is the dual on the audit fan-out side:
per-sink failure must not poison the surviving sinks' delivery of the
event. The fix catches ``Exception`` per sink and continues iterating;
``BaseException`` (KeyboardInterrupt, SystemExit, asyncio.CancelledError)
still propagates so terminal signals are not swallowed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from harness.events import ContractStarted, HarnessEvent
from harness.sinks import MemorySink, MultiSink


class _FailingSink:
    """Test double: every emit raises ``RuntimeError``."""

    def __init__(self, message: str = "sink down") -> None:
        self._message = message
        self.calls = 0

    def emit(self, event: HarnessEvent) -> None:
        self.calls += 1
        raise RuntimeError(self._message)


class _BaseExceptionSink:
    """Test double: every emit raises ``KeyboardInterrupt``.

    Confirms that a non-``Exception`` ``BaseException`` (terminal
    signals, asyncio cancellation) is NOT contained: those are
    authoritative and must reach the caller, parity with the
    runtime's "do not reinterpret cancellation as a pause"
    invariant (BL-165).
    """

    def emit(self, event: HarnessEvent) -> None:
        raise KeyboardInterrupt("terminate")


def _event() -> ContractStarted:
    return ContractStarted(
        timestamp=datetime.now(UTC),
        workload="w",
        contract="c",
        contract_version="0.1.0",
        trace_id="t",
        span_id="s",
    )


def test_multi_sink_continues_past_failing_sink() -> None:
    """A failing middle sink does not block downstream sinks.

    Before BL-223 the second sink raised, the third never saw the
    event, and the audit pipeline lost the row in the third sink.
    """
    first = MemorySink()
    failing = _FailingSink()
    last = MemorySink()
    multi = MultiSink(first, failing, last)
    event = _event()
    multi.emit(event)
    # Every healthy sink received the event.
    assert len(first.events) == 1
    assert len(last.events) == 1
    assert first.events[0] is event
    assert last.events[0] is event
    # The failing sink was tried (the contract is "try every sink",
    # not "skip on first failure").
    assert failing.calls == 1


def test_multi_sink_continues_past_first_sink_failing() -> None:
    """A failing FIRST sink does not block subsequent sinks."""
    failing = _FailingSink()
    healthy = MemorySink()
    multi = MultiSink(failing, healthy)
    multi.emit(_event())
    assert len(healthy.events) == 1
    assert failing.calls == 1


def test_multi_sink_all_failing_does_not_raise() -> None:
    """When every sink fails, the contract is still "emit returns
    cleanly": the caller (enforcement loop, budget tracker, guard)
    must not be aborted by audit-pipeline failures.

    This is the parallel of BL-222's "all-fail returns empty"
    contract on the dispatcher side: containment is the rule,
    propagation is not.
    """
    multi = MultiSink(_FailingSink(), _FailingSink(), _FailingSink())
    multi.emit(_event())  # no exception escapes


def test_multi_sink_propagates_base_exception() -> None:
    """A ``BaseException`` (KeyboardInterrupt, SystemExit, asyncio.
    CancelledError) is NOT contained: those are authoritative
    termination signals (BL-165 class) and must reach the caller.

    This is the same boundary the runtime's BL-165 fix walks on
    the cancellation side: ``BaseException`` propagates;
    ``Exception`` is contained.
    """
    healthy = MemorySink()
    multi = MultiSink(healthy, _BaseExceptionSink())
    with pytest.raises(KeyboardInterrupt):
        multi.emit(_event())
    # The pre-failure sink still saw the event (containment of
    # ``BaseException`` would mask termination; we only need to
    # confirm the in-order siblings are not skipped *before* the
    # terminal signal reaches them).
    assert len(healthy.events) == 1


def test_multi_sink_happy_path_unchanged() -> None:
    """No-failure fan-out is byte-for-byte the prior behaviour:
    every sink receives the same event in declaration order.
    """
    sinks = [MemorySink(), MemorySink(), MemorySink()]
    multi = MultiSink(*sinks)
    event = _event()
    multi.emit(event)
    for sink in sinks:
        assert len(sink.events) == 1
        assert sink.events[0] is event


def test_multi_sink_empty_fan_out_unchanged() -> None:
    """A MultiSink with zero wrapped sinks is a no-op (the L1
    behaviour). The containment fix must not invent an exception
    where there is no sink to fail.
    """
    MultiSink().emit(_event())  # no exception


def test_multi_sink_failure_does_not_corrupt_inner_state() -> None:
    """The healthy sinks' state after a fan-out matches what they
    would see in a single ``emit`` call (no out-of-order delivery,
    no double-emit, no skip).
    """
    a = MemorySink()
    b = MemorySink()
    multi = MultiSink(a, _FailingSink(), b)
    e1 = _event()
    e2 = _event()
    e3 = _event()
    multi.emit(e1)
    multi.emit(e2)
    multi.emit(e3)
    assert list(a.events) == [e1, e2, e3]
    assert list(b.events) == [e1, e2, e3]
