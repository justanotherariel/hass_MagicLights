"""The engine's seam to Home Assistant."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterable
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.sun import get_astral_observer
from homeassistant.util import dt as dt_util

from ..const import DOMAIN, EVENT_MAGIC_LIGHTS, SIGNAL_STATUS, SIGNAL_TARGETS
from ..context import create_context
from ..models import HaLight, SunLocation, TargetId


class HassHost:
    """Host backed by a running HomeAssistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._dirty: set[TargetId] = set()
        self._flush: asyncio.Handle | None = None

    def now(self) -> datetime:
        return dt_util.utcnow()

    def monotonic(self) -> float:
        return self._hass.loop.time()

    def call_later(self, delay: float, cb: Callable[[], None]) -> Callable[[], None]:
        return self._hass.loop.call_later(max(0.0, delay), cb).cancel

    def state(self, entity_id: str) -> HaLight | None:
        if (state := self._hass.states.get(entity_id)) is None:
            return None
        return HaLight.from_state(state)

    def sun(self) -> SunLocation:
        # Built per call: cheap, and always follows core config updates.
        return SunLocation(
            get_astral_observer(self._hass), dt_util.get_default_time_zone()
        )

    def emit(self, event: dict[str, Any]) -> None:
        self._hass.bus.async_fire(EVENT_MAGIC_LIGHTS, event, context=create_context())

    def status_changed(self, target_ids: Iterable[TargetId]) -> None:
        self._dirty.update(target_ids)
        if self._dirty and self._flush is None:
            self._flush = self._hass.loop.call_soon(self._send_status)

    @callback
    def _send_status(self) -> None:
        target_ids = frozenset(self._dirty)
        self._dirty.clear()
        self._flush = None
        async_dispatcher_send(self._hass, SIGNAL_STATUS, target_ids)

    def targets_changed(
        self, added: Iterable[TargetId], removed: Iterable[TargetId]
    ) -> None:
        async_dispatcher_send(
            self._hass, SIGNAL_TARGETS, frozenset(added), frozenset(removed)
        )

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        # Not eager: a light that writes its state inside the service call
        # must not re-enter the scheduler while it is draining.
        self._hass.async_create_task(coro, f"{DOMAIN} send", eager_start=False)
