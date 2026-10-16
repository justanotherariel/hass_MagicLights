"""Contexts that mark state changes and service calls as ours.

A marker inside the 26-character context id makes recognition stateless.
The id keeps a valid ULID timestamp prefix and its length.
"""

from __future__ import annotations

from homeassistant.core import Context
from homeassistant.util.ulid import ulid_now

MARKER = ":mgl:"
_ULID_TIME_LENGTH = 10


def create_context(parent: Context | None = None) -> Context:
    """Return a marked context, a child of `parent` so the logbook can attribute it."""
    ulid = ulid_now()
    marked = ulid[:_ULID_TIME_LENGTH] + MARKER + ulid[-11:]
    if parent is None:
        return Context(id=marked)
    return Context(id=marked, user_id=parent.user_id, parent_id=parent.id)


def is_ours(context: Context | None) -> bool:
    return context is not None and MARKER in context.id
