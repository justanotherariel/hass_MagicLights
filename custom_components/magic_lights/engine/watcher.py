"""Light service calls and state changes in, coordinator calls out."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_BRIGHTNESS_STEP,
    ATTR_BRIGHTNESS_STEP_PCT,
    ATTR_COLOR_NAME,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_FLASH,
    ATTR_HS_COLOR,
    ATTR_PROFILE,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_GROUP_ENTITIES,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    ENTITY_MATCH_ALL,
    EVENT_CALL_SERVICE,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from ..context import is_ours
from ..models import HaLight, LightIndex, LightsChange

if TYPE_CHECKING:
    from .coordinator import Coordinator

SERVICES = frozenset({SERVICE_TURN_ON, SERVICE_TURN_OFF, SERVICE_TOGGLE})
# A turn_on carrying any of these sets the light, not just switches it on.
ATTR_KEYS = frozenset(
    {
        ATTR_BRIGHTNESS,
        ATTR_BRIGHTNESS_PCT,
        ATTR_BRIGHTNESS_STEP,
        ATTR_BRIGHTNESS_STEP_PCT,
        ATTR_COLOR_NAME,
        ATTR_COLOR_TEMP_KELVIN,
        ATTR_HS_COLOR,
        ATTR_RGB_COLOR,
        ATTR_RGBW_COLOR,
        ATTR_RGBWW_COLOR,
        ATTR_XY_COLOR,
        ATTR_WHITE,
        ATTR_EFFECT,
        ATTR_FLASH,
        ATTR_PROFILE,
    }
)


@callback
def _is_light_call(data: Mapping[str, Any]) -> bool:
    return data.get(ATTR_DOMAIN) == LIGHT_DOMAIN and data.get(ATTR_SERVICE) in SERVICES


def _ha_light(state: Any) -> HaLight | None:
    return None if state is None else HaLight.from_state(state)


def _members(value: Any) -> tuple[str, ...]:
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(v for v in value if isinstance(v, str))
    return ()


class Watcher:
    """Feeds foreign light calls and tracked light states to the coordinator."""

    def __init__(
        self, hass: HomeAssistant, coordinator: Coordinator, lights: LightIndex
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._lights = lights
        self._unsubs: list[Callable[[], None]] = []
        self._untrack: Callable[[], None] | None = None
        self._tracked: frozenset[str] = frozenset()

    @callback
    def start(self) -> None:
        if self._unsubs:
            return
        self._unsubs = [
            self._hass.bus.async_listen(
                EVENT_CALL_SERVICE, self._on_call, event_filter=_is_light_call
            ),
            self._lights.subscribe(self._on_lights_change),
        ]
        self._track()

    @callback
    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._untrack is not None:
            self._untrack()
            self._untrack = None
        self._tracked = frozenset()

    @callback
    def _on_lights_change(self, change: LightsChange) -> None:
        self._track()

    def _track(self) -> None:
        entity_ids = frozenset(self._lights.lights)
        if entity_ids == self._tracked and self._untrack is not None:
            return
        if self._untrack is not None:
            self._untrack()
        self._tracked = entity_ids
        self._untrack = async_track_state_change_event(
            self._hass, entity_ids, self._on_state
        )

    @callback
    def _on_call(self, event: Event[Mapping[str, Any]]) -> None:
        if is_ours(event.context):
            return
        data = event.data.get(ATTR_SERVICE_DATA) or {}
        bare = not (data.keys() & ATTR_KEYS)
        for service, entity_ids in self.referenced(data, event.data[ATTR_SERVICE]):
            self._coordinator.on_foreign_call(entity_ids, service, bare)

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        data = event.data
        self._coordinator.on_light_state(
            data["entity_id"],
            _ha_light(data["old_state"]),
            _ha_light(data["new_state"]),
        )

    def referenced(
        self, service_data: Mapping[str, Any], service: str
    ) -> list[tuple[str, set[str]]]:
        """Tracked lights a call targets, through areas, devices, labels and groups.

        Returns them by the service each light gets: a group toggles all its
        members by its own state, so a toggle of an "on" group turns them off.
        """
        tracked = self._lights.lights.keys()
        if service_data.get(ATTR_ENTITY_ID) in (ENTITY_MATCH_ALL, [ENTITY_MATCH_ALL]):
            return [(service, set(tracked))]
        selected = async_extract_referenced_entity_ids(
            self._hass, TargetSelection(dict(service_data)), expand_group=True
        )
        calls: dict[str, set[str]] = {}
        for entity_id in selected.referenced | selected.indirectly_referenced:
            members = self._group_members(entity_id)
            judged = service
            if (
                service == SERVICE_TOGGLE
                and members
                and entity_id in selected.referenced
            ):
                state = self._hass.states.get(entity_id)
                if state is None or state.state == STATE_UNAVAILABLE:
                    continue
                judged = (
                    SERVICE_TURN_OFF if state.state == STATE_ON else SERVICE_TURN_ON
                )
            calls.setdefault(judged, set()).update(({entity_id} | members) & tracked)
        return [(judged, ids) for judged, ids in calls.items() if ids]

    def _group_members(self, entity_id: str) -> set[str]:
        """Members of an integration group (HA light group, Hue/Z2M group).

        HA does not expand these for us: they list members in attributes.
        """
        found: set[str] = set()
        todo = [entity_id]
        while todo:
            if (state := self._hass.states.get(todo.pop())) is None:
                continue
            attrs = state.attributes
            for member in (
                *_members(attrs.get(ATTR_GROUP_ENTITIES)),
                *_members(attrs.get(ATTR_ENTITY_ID)),
            ):
                if member != entity_id and member not in found:
                    found.add(member)
                    todo.append(member)
        return found
