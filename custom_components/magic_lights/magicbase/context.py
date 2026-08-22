"""Tagged Context creation and recognition.

Instead of remembering every Context handed out, a marker is encoded
directly into the 26-character context id (the adaptive-lighting
approach). Recognition is stateless: any context whose id contains
":mgl:" was created by this integration. No bookkeeping, no cleanup,
and it survives restarts.

The id must stay exactly 26 characters and start with a valid ULID
timestamp, because the recorder stores context ids as binary ULIDs
since HA 2023.4.
"""

import ulid_transform
from homeassistant.core import Context

MARKER = ":mgl:"

_ULID_TIME_LENGTH = 10


def create_context() -> Context:
    """Create a Context whose id marks it as ours.

    Layout: 10-char ULID timestamp + ":mgl:" + random ULID tail.
    """
    ulid = ulid_transform.ulid_now()
    return Context(id=ulid[:_ULID_TIME_LENGTH] + MARKER + ulid[-11:])


def is_our_context(context: Context | None) -> bool:
    """Return True if the context was created by this integration."""
    return context is not None and MARKER in context.id
