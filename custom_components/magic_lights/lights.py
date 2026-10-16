"""Discover the lights and build the room and zone targets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
import dataclasses
from typing import Any, Final, Literal

from homeassistant.components.light import (
    ATTR_EFFECT_LIST,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_GROUP_ENTITIES,
    ATTR_RESTORED,
    ATTR_SUPPORTED_FEATURES,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from .buses import BUS_DEFAULTS, BusKind, async_classify, bus_kind
from .const import DOMAIN
from .models import (
    Bus,
    Caps,
    Config,
    Driver,
    Light,
    LightsChange,
    Target,
    TargetId,
    TargetOptions,
)
from .native import HaDriver

GROUP_PLATFORM: Final = "group"
_GROUP_FLAGS: Final = ("is_hue_group", "is_deconz_group")
# State attributes that feed a Light; a change to any of them re-reads it.
# `restored` drops once the integration adds the entity, which is when its
# capabilities and bus become authoritative.
_LIGHT_ATTRS: Final = (
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_EFFECT_LIST,
    ATTR_SUPPORTED_FEATURES,
    ATTR_FRIENDLY_NAME,
    ATTR_ENTITY_ID,
    ATTR_GROUP_ENTITIES,
    *_GROUP_FLAGS,
    ATTR_RESTORED,
)


def _is_group(attrs: Mapping[str, Any]) -> bool:
    return (
        ATTR_ENTITY_ID in attrs
        or ATTR_GROUP_ENTITIES in attrs
        or any(attrs.get(flag) for flag in _GROUP_FLAGS)
    )


def _is_group_entry(entry: er.RegistryEntry) -> bool:
    """Whether the registry marks a group, even while its state lacks the flags."""
    return (
        entry.platform == GROUP_PLATFORM
        or (entry.platform == "hue" and entry.translation_key == "hue_grouped_light")
        or (entry.platform == "deconz" and "-/groups/" in entry.unique_id)
    )


def _signature(state: State | None) -> tuple[Any, ...] | None:
    if state is None:
        return None
    return tuple(state.attributes.get(key) for key in _LIGHT_ATTRS)


@callback
def _light_state_filter(data: EventStateChangedData) -> bool:
    return data["entity_id"].startswith("light.") and _signature(
        data["old_state"]
    ) != _signature(data["new_state"])


@callback
def _light_entry_filter(data: Mapping[str, Any]) -> bool:
    return data["entity_id"].startswith("light.") or (
        data.get("old_entity_id") or ""
    ).startswith("light.")


class LightIndexImpl:
    """Tracked lights and the targets built from areas and zones."""

    def __init__(
        self, hass: HomeAssistant, config: Config, drivers: Mapping[str, Driver]
    ) -> None:
        self._hass = hass
        self._config = config
        self._drivers = drivers
        # Updated in place, so references held by others stay current.
        self.lights: dict[str, Light] = {}
        self.targets: dict[TargetId, Target] = {}
        self._targets_of: dict[str, set[TargetId]] = {}
        # Seen with group attributes; HA drops those from unavailable states.
        self._groups: set[str] = set()
        self._subscribers: list[Callable[[LightsChange], None]] = []

    # ---- LightIndex protocol

    def targets_of(self, entity_id: str) -> set[TargetId]:
        return set(self._targets_of.get(entity_id, ()))

    def expand(self, items: Literal["all"] | Sequence[str], target: Target) -> set[str]:
        """Resolve layer lights ("all", "light.x", "room:<area_id>") in a target."""
        members = set(target.lights)
        if items == "all":
            return members
        out: set[str] = set()
        for item in items:
            if item.startswith("room:"):
                if (room := self.targets.get(item)) is not None:
                    out.update(room.lights)
            else:
                out.add(item)
        return out & members

    def subscribe(self, cb: Callable[[LightsChange], None]) -> Callable[[], None]:
        self._subscribers.append(cb)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._subscribers.remove(cb)

        return unsubscribe

    # ---- lifecycle

    @callback
    def rebuild(self) -> None:
        """Rediscover every light and target."""
        ent_reg = er.async_get(self._hass)
        entity_ids = {
            entry.entity_id
            for entry in ent_reg.entities.values()
            if entry.domain == LIGHT_DOMAIN
        }
        entity_ids.update(self._hass.states.async_entity_ids(LIGHT_DOMAIN))
        self._commit(
            {
                entity_id: light
                for entity_id in entity_ids
                if (light := self._light(entity_id)) is not None
            }
        )

    @callback
    def apply_config(self, config: Config) -> None:
        """Take new zones, target options and settings."""
        self._config = config
        self.rebuild()

    @callback
    def async_listen(self) -> CALLBACK_TYPE:
        """Follow registry and light state changes; returns the unsubscribe."""
        bus = self._hass.bus
        unsubs = [
            bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                self._on_entity_registry,
                event_filter=_light_entry_filter,
            ),
            bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._on_device_registry
            ),
            bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, self._on_area_registry),
            bus.async_listen(
                EVENT_STATE_CHANGED, self._on_state, event_filter=_light_state_filter
            ),
        ]

        @callback
        def unsubscribe() -> None:
            for unsub in unsubs:
                unsub()

        return unsubscribe

    # ---- event handlers

    @callback
    def _on_entity_registry(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        data = event.data
        self._update([data["entity_id"], *filter(None, [data.get("old_entity_id")])])

    @callback
    def _on_device_registry(
        self, event: Event[dr.EventDeviceRegistryUpdatedData]
    ) -> None:
        if event.data["action"] == "create":
            return
        device_id = event.data["device_id"]
        if on_device := [
            light.entity_id
            for light in self.lights.values()
            if light.device_id == device_id
        ]:
            self._update(on_device)

    @callback
    def _on_area_registry(self, event: Event[ar.EventAreaRegistryUpdatedData]) -> None:
        if event.data["action"] in ("update", "remove") and (
            f"room:{event.data['area_id']}" in self.targets
        ):
            self._update(())

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        self._update([event.data["entity_id"]])

    # ---- building

    def _update(self, entity_ids: Iterable[str]) -> None:
        lights = dict(self.lights)
        for entity_id in entity_ids:
            if (light := self._light(entity_id)) is None:
                lights.pop(entity_id, None)
            else:
                lights[entity_id] = light
        self._commit(lights)

    def _light(self, entity_id: str) -> Light | None:
        """Return the light with its natural bus, or None if it is not tracked."""
        hass = self._hass
        entry = er.async_get(hass).async_get(entity_id)
        state = hass.states.get(entity_id)
        if entry is None and state is None:
            return None
        if entry is not None and (
            entry.disabled_by is not None
            or entry.entity_category is not None
            or _is_group_entry(entry)
        ):
            return None
        if state is not None and state.state != STATE_UNAVAILABLE:
            if _is_group(state.attributes):
                self._groups.add(entity_id)
            else:
                self._groups.discard(entity_id)
        if entity_id in self._groups:
            return None
        if (caps := self._detect(entity_id)) is None:
            return None
        area_id = device_id = None
        if entry is not None:
            area_id, device_id = entry.area_id, entry.device_id
            if area_id is None and device_id is not None:
                device = dr.async_get(hass).async_get(device_id)
                area_id = device.area_id if device is not None else None
        friendly = state.attributes.get(ATTR_FRIENDLY_NAME) if state else None
        if friendly:
            name = str(friendly)
        else:
            name = (entry and er.async_get_full_entity_name(hass, entry)) or entity_id
        return Light(
            entity_id=entity_id,
            name=name,
            area_id=area_id,
            device_id=device_id,
            bus=async_classify(hass, entity_id),
            caps=caps,
        )

    def _detect(self, entity_id: str) -> Caps | None:
        if not self._config.settings.native_effects:
            caps = self._drivers[HaDriver.key].detect(self._hass, entity_id)
            # Plain colour commands only: light_effect emulates or falls back.
            return caps and dataclasses.replace(
                caps, effects=frozenset(), gradient_points=0
            )
        # The HA driver detects every light, so it decides last.
        for driver in sorted(
            self._drivers.values(), key=lambda d: d.key == HaDriver.key
        ):
            if (caps := driver.detect(self._hass, entity_id)) is not None:
                return caps
        return None

    def _with_bus_settings(self, lights: Mapping[str, Light]) -> dict[str, Light]:
        """Apply `light_buses` re-homing and per-bus limit overrides."""
        settings = self._config.settings
        known = {light.bus.id: light.bus for light in lights.values()}
        out = {}
        for entity_id, light in sorted(lights.items()):
            bus = light.bus
            if (bus_id := settings.light_buses.get(entity_id)) and bus_id != bus.id:
                kind = bus_kind(bus_id)
                bus = known.get(bus_id) or Bus(bus_id, kind, bus_id, BUS_DEFAULTS[kind])
            defaults = BUS_DEFAULTS.get(bus.kind, BUS_DEFAULTS[BusKind.UNKNOWN])
            limits = dataclasses.replace(defaults, **settings.buses.get(bus.id, {}))
            if bus != light.bus or limits != bus.limits:
                light = dataclasses.replace(
                    light, bus=dataclasses.replace(bus, limits=limits)
                )
            out[entity_id] = light
        return out

    def _targets(self, lights: Mapping[str, Light]) -> dict[TargetId, Target]:
        config = self._config
        ent_reg = er.async_get(self._hass)
        natural: dict[str, set[str]] = defaultdict(set)
        for light in lights.values():
            if light.area_id is not None:
                natural[light.area_id].add(light.entity_id)
        rooms: list[Target] = []
        for area in ar.async_get(self._hass).async_list_areas():
            target_id = f"room:{area.id}"
            # A room that lost its lights keeps its select until the area goes.
            if area.id not in natural and not ent_reg.async_get_entity_id(
                Platform.SELECT, DOMAIN, target_id
            ):
                continue
            options = config.targets.get(target_id, TargetOptions())
            members = (natural[area.id] | set(options.extra_lights)) & lights.keys()
            members -= set(options.excluded_lights)
            rooms.append(
                Target(
                    id=target_id,
                    kind="room",
                    name=area.name,
                    icon=area.icon,
                    area_id=area.id,
                    rooms=(area.id,),
                    lights=tuple(sorted(members)),
                    options=options,
                )
            )
        room_lights = {room.area_id: room.lights for room in rooms}
        zones: list[Target] = []
        for zone in config.zones.values():
            target_id = f"zone:{zone.id}"
            zone_rooms = tuple(
                area_id for area_id in zone.rooms if area_id in room_lights
            )
            members = {e for area_id in zone_rooms for e in room_lights[area_id]}
            members |= set(zone.lights) & lights.keys()
            zones.append(
                Target(
                    id=target_id,
                    kind="zone",
                    name=zone.name,
                    icon=zone.icon,
                    area_id=None,
                    rooms=zone_rooms,
                    lights=tuple(sorted(members)),
                    options=config.targets.get(target_id, TargetOptions()),
                )
            )

        def order(target: Target) -> tuple[str, str]:
            return target.name.casefold(), target.id

        return {
            target.id: target
            for target in (*sorted(rooms, key=order), *sorted(zones, key=order))
        }

    def _commit(self, lights: Mapping[str, Light]) -> None:
        lights = self._with_bus_settings(lights)
        targets = self._targets(lights)
        old_lights, old_targets = self.lights, self.targets
        change = LightsChange(
            added_targets=frozenset(targets.keys() - old_targets.keys()),
            removed_targets=frozenset(old_targets.keys() - targets.keys()),
            changed_targets=frozenset(
                target_id
                for target_id in targets.keys() & old_targets.keys()
                if targets[target_id] != old_targets[target_id]
            ),
            changed_lights=frozenset(
                entity_id
                for entity_id in lights.keys() | old_lights.keys()
                if lights.get(entity_id) != old_lights.get(entity_id)
            ),
        )
        if not (
            change.added_targets
            or change.removed_targets
            or change.changed_targets
            or change.changed_lights
        ):
            return
        self.lights.clear()
        self.lights.update(lights)
        self.targets.clear()
        self.targets.update(targets)
        self._targets_of.clear()
        for target in targets.values():
            for entity_id in target.lights:
                self._targets_of.setdefault(entity_id, set()).add(target.id)
        for cb in list(self._subscribers):
            cb(change)
