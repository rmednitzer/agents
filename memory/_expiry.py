"""Expiry-boundary predicate helpers for memory adapters (BL-195).

A single source of truth for the read-vs-CAS-vs-listing-vs-sweep
expiry boundary. The fault-class history (the prior pointwise fixes
this consolidation collapses):

- ``BL-157`` (ADR 0010): DynamoDB CAS / TTL float-seconds boundary.
- ``BL-168`` (ADR 0010): SQLite ``sweep_expired`` strict ``<`` vs
  ``read``'s ``>``.
- ``BL-177`` (ADR 0011): DynamoDB ``compare_and_set`` match-branch and
  ``compare_and_delete`` boundary.
- ``BL-188`` (ADR 0013): InMemory / SQLite ``list_keys`` / ``scan``
  boundary parity with ``read`` / ``sweep_expired``.
- ``BL-180`` (ADR 0014): DynamoDB versioned/transactional condition
  expressions inherit the same ``exp >= :now`` live boundary.

Five pointwise fixes against one underlying invariant. Each time, the
risk was that an entry at the exact expiry instant disagreed across
``read`` / ``mget`` / CAS / ``read_versioned`` / ``list_keys`` /
``scan`` / ``sweep_expired`` / the in-line condition. This module
codifies the invariant so a future adapter cannot reintroduce the
class by mistake.

## Contract

An entry with absolute expiry ``expires_at: float | None`` is *live*
at wall-clock instant ``now: float`` iff::

    expires_at is None or now <= expires_at

The boundary is **inclusive at the instant** ``now == expires_at``: a
key that ``read`` still returns is still in ``list_keys`` / ``scan``,
is still CAS-modifiable, and is not yet swept by ``sweep_expired``.
The negation is *expired*: the entry has an expiry AND the wall-clock
is strictly past it.

## SQL counterpart

``SQLiteStore`` uses these helpers for the Python-side predicates and
expresses the same invariant in SQL where it touches the DB directly:

- Live:    ``expires_at IS NULL OR :now <= expires_at``
- Expired: ``expires_at IS NOT NULL AND expires_at < :now`` (the
  ``sweep_expired`` form; matches the negation of ``is_live``).

## DynamoDB counterpart

``DynamoDBStore`` condition expressions encode the same invariant:

- Live:    ``attribute_not_exists(exp) OR exp >= :now``
- Expired: ``attribute_exists(exp) AND exp < :now`` (the
  ``compare_and_set`` create-or-overwrite-expired form).

Adapters MAY embed these expressions literally (one canonical form per
backend); the docstring above is the binding documentation that
``is_live(now, exp)`` and the DSL forms agree at every instant,
including the boundary ``now == exp``.
"""

from __future__ import annotations

__all__ = ["is_expired", "is_live"]


def is_live(now: float, expires_at: float | None) -> bool:
    """Return ``True`` iff an entry with absolute expiry ``expires_at`` is
    live at wall-clock ``now``.

    See the module docstring for the invariant and the SQL / DynamoDB
    counterparts. A ``None`` expiry means "no expiry, always live".
    The boundary is inclusive at ``now == expires_at`` (the entry is
    still live at that exact instant).
    """
    return expires_at is None or now <= expires_at


def is_expired(now: float, expires_at: float | None) -> bool:
    """Return ``True`` iff an entry with absolute expiry ``expires_at`` is
    expired at wall-clock ``now``.

    Negation of :func:`is_live`. An entry with no expiry is never
    expired.
    """
    return not is_live(now, expires_at)
