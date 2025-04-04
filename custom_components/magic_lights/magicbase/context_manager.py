import logging

from homeassistant.core import Context

_LOGGER = logging.getLogger(__name__)


# TODO: Periodically check and delete old context entries.

class ContextManager:
    _created_contexts: list[str] = None

    def __init__(self) -> None:
        self._created_contexts: list[Context] = []

    def create_context(self) -> Context:
        context = Context()
        self._created_contexts.append(context)
        return context

    def context_ours(self, context: Context, remove: bool = True):
        for c_context in self._created_contexts:
            if c_context.id == context.id:
                if remove:
                    self._created_contexts.remove(context)
                return True
        return False
