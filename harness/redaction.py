"""Secret and PII redaction for harness events (BL-134).

Harness events carry free-form caller data: ``GovernanceViolated`` and
``ApprovalRequested`` serialize raw tool ``arguments``, and the
violation events carry a ``state_snapshot``. Without scrubbing, a tool
call that passes an API key, bearer token, or password is written to
every sink (JSONL, OTel) in plaintext.

``Redactor`` scrubs a single event; ``RedactingSink`` wraps any
``EventSink`` so redaction happens once, at the emit boundary, for every
downstream sink. Both are additive: no existing sink behaviour changes
unless a caller opts in by wrapping it.

Redaction is structural, not semantic: a key whose name looks sensitive
has its value replaced with the placeholder, a value that matches a
high-confidence secret shape is replaced with the placeholder, and an
over-long scalar is clamped. The key is kept (so audit still shows the
field existed) with its value masked. It reduces accidental leakage; it
is not a guarantee against a caller that deliberately hides a secret in
an unrecognised field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from harness.events import HarnessEvent
from harness.sinks import EventSink

__all__ = [
    "RedactingSink",
    "Redactor",
]

# Field/argument names that should never carry a value to a sink.
_DEFAULT_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|apikey|authorization|"
    r"auth[_-]?token|credential|client[_-]?secret|private[_-]?key|"
    r"access[_-]?key|secret[_-]?key|session[_-]?(id|token)|cookie)"
)

# High-confidence secret shapes (precise to avoid scrubbing prose).
_DEFAULT_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM private key
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}=*"),  # Authorization: Bearer
    re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9]{16,}"),  # provider secret keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
)


@dataclass(frozen=True)
class Redactor:
    """Returns a copy of an event with sensitive content removed.

    ``key_pattern`` matches argument/field names whose value is always
    dropped. ``value_patterns`` match value strings that are dropped
    regardless of their key. ``max_value_chars`` clamps any longer
    scalar string (defends log-flooding and trims embedded blobs).
    ``max_depth`` (`BL-200`) caps the recursion into nested containers
    so a cyclic or pathologically nested payload (e.g., a workload's
    ``state_snapshot`` that carries a self-referential dict) cannot
    crash the audit path with ``RecursionError``; an over-deep
    container is replaced with the placeholder. Defaults match
    typical event-payload depth (small, finite) and the
    audit-path-must-not-crash stance of `BL-167`.
    """

    key_pattern: re.Pattern[str] = _DEFAULT_KEY_PATTERN
    value_patterns: tuple[re.Pattern[str], ...] = _DEFAULT_VALUE_PATTERNS
    max_value_chars: int = 4096
    max_depth: int = 64
    placeholder: str = "[REDACTED]"

    def redact(self, event: HarnessEvent) -> HarnessEvent:
        """Return a new event with every field scrubbed.

        Every field is walked, not only ``dict``-valued ones: a
        secret-shaped or over-long value in a top-level string or list
        field is scrubbed too, so the safety net does not depend on the
        payload being nested under a dict. Frozen events are not
        mutated; a ``model_copy`` carrying scrubbed values is returned.
        An event with no sensitive content is returned unchanged (the
        same object, no copy), since scrubbing is a strict no-op there.
        """
        update: dict[str, Any] = {}
        for name in type(event).model_fields:
            value = getattr(event, name)
            scrubbed = self._scrub(value, depth=0)
            if scrubbed != value:
                update[name] = scrubbed
        if not update:
            return event
        return event.model_copy(update=update)

    def _scrub(self, value: Any, *, depth: int) -> Any:
        # Recursion cap (BL-200, audit path must not crash): an
        # over-deep container is replaced with the placeholder. Cycles
        # (a dict that holds a reference to itself) eventually exceed
        # depth, so the cap also covers cycle detection without a
        # separate visited set.
        if depth > self.max_depth:
            return self.placeholder
        if isinstance(value, dict):
            return {k: self._scrub_member(k, v, depth=depth + 1) for k, v in value.items()}
        if isinstance(value, list | tuple):
            # Rebuild as a plain list/tuple. A tuple subclass such as a
            # namedtuple has an arity-sensitive constructor, so
            # type(value)(<generator>) would raise; degrading to the
            # builtin is safe for a sink-bound sanitised copy.
            items = [self._scrub(v, depth=depth + 1) for v in value]
            return tuple(items) if isinstance(value, tuple) else items
        if isinstance(value, set | frozenset):
            scrubbed = [self._scrub(v, depth=depth + 1) for v in value]
            return frozenset(scrubbed) if isinstance(value, frozenset) else set(scrubbed)
        return self._scrub_scalar(value)

    def _scrub_member(self, key: Any, value: Any, *, depth: int) -> Any:
        if isinstance(key, str) and self.key_pattern.search(key):
            return self.placeholder
        return self._scrub(value, depth=depth)

    def _scrub_scalar(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if any(p.search(value) for p in self.value_patterns):
            return self.placeholder
        if len(value) > self.max_value_chars:
            return value[: self.max_value_chars] + "...[clamped]"
        return value


@dataclass(frozen=True)
class RedactingSink:
    """Wraps a sink so every event is redacted before it is emitted.

    Place this at the outermost layer (e.g. wrap a ``MultiSink``) so a
    single redaction pass protects every downstream sink.
    """

    inner: EventSink
    redactor: Redactor = field(default_factory=Redactor)

    def emit(self, event: HarnessEvent) -> None:
        self.inner.emit(self.redactor.redact(event))
