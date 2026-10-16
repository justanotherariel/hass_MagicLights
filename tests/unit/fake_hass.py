"""A HomeAssistant stand-in over HA's real registry entry types (no HA boot).

Only the surface that lights.py, buses.py and native/ read is provided:
`hass.data` with entity/device/area registries and the loader caches, states,
an event bus, services and config entries. Mutations fire the same events HA
fires, synchronously.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import pathlib
from types import MappingProxyType
from typing import Any

import attr

from homeassistant import loader
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, State
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.util.dt import utcnow


class FakeEntityRegistry:
    def __init__(self, hass: FakeHass) -> None:
        self.entities = er.EntityRegistryItems(hass)

    def async_get(self, entity_id_or_uuid: str) -> er.RegistryEntry | None:
        return self.entities.get(entity_id_or_uuid) or self.entities.get_entry(
            entity_id_or_uuid
        )

    def async_get_entity_id(
        self, domain: str, platform: str, unique_id: str
    ) -> str | None:
        return self.entities.get_entity_id((domain, platform, unique_id))


class FakeDeviceRegistry:
    def __init__(self) -> None:
        self.devices: dict[str, dr.DeviceEntry] = {}

    def async_get(self, device_id: str) -> dr.DeviceEntry | None:
        return self.devices.get(device_id)

    def async_get_devices_for_composite_device_id(
        self, device_id: str
    ) -> list[dr.DeviceEntry]:
        return []


class FakeAreaRegistry:
    def __init__(self) -> None:
        self.areas: dict[str, ar.AreaEntry] = {}

    def async_get_area(self, area_id: str) -> ar.AreaEntry | None:
        return self.areas.get(area_id)

    def async_list_areas(self) -> Iterable[ar.AreaEntry]:
        return self.areas.values()


class FakeStates:
    def __init__(self) -> None:
        self.states: dict[str, State] = {}

    def get(self, entity_id: str) -> State | None:
        return self.states.get(entity_id)

    def async_entity_ids(self, domain_filter: str | None = None) -> list[str]:
        return [
            e
            for e in self.states
            if domain_filter is None or e.startswith(f"{domain_filter}.")
        ]


class FakeBus:
    def __init__(self) -> None:
        self.listeners: list[tuple[str, Callable[[Event], Any], Callable | None]] = []

    def async_listen(
        self,
        event_type: str,
        listener: Callable[[Event], Any],
        event_filter: Callable[[Any], bool] | None = None,
    ) -> Callable[[], None]:
        item = (event_type, listener, event_filter)
        self.listeners.append(item)
        return lambda: self.listeners.remove(item)

    def fire(self, event_type: str, data: Mapping[str, Any]) -> None:
        for kind, listener, event_filter in list(self.listeners):
            if kind == event_type and (event_filter is None or event_filter(data)):
                listener(Event(event_type, dict(data)))


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], bool, Context | None]] = []
        # Raised by the "integration" for calls to these entities.
        self.errors: dict[str, Exception] = {}

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        blocking: bool = False,
        context: Context | None = None,
    ) -> None:
        data = dict(service_data or {})
        self.calls.append((domain, service, data, blocking, context))
        # Like HA, only a blocking call hands the handler's error to the caller.
        if blocking and (err := self.errors.get(data.get(ATTR_ENTITY_ID))):
            raise err


class FakeConfigEntries:
    def __init__(self) -> None:
        self.entries: dict[str, ConfigEntry] = {}

    def async_get_entry(self, entry_id: str) -> ConfigEntry | None:
        return self.entries.get(entry_id)


class FakeHass:
    def __init__(self) -> None:
        self.states = FakeStates()
        self.bus = FakeBus()
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()
        self.ent_reg = FakeEntityRegistry(self)
        self.dev_reg = FakeDeviceRegistry()
        self.area_reg = FakeAreaRegistry()
        self.data: dict[str, Any] = {
            er.DATA_REGISTRY: self.ent_reg,
            dr.DATA_REGISTRY: self.dev_reg,
            ar.DATA_REGISTRY: self.area_reg,
            loader.DATA_COMPONENTS: {},
            loader.DATA_INTEGRATIONS: {},
            loader.DATA_MISSING_PLATFORMS: {},
            loader.DATA_PRELOAD_PLATFORMS: [],
        }

    # ---- builders

    def add_config_entry(
        self,
        domain: str,
        title: str = "",
        *,
        unique_id: str | None = None,
        data: Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> ConfigEntry:
        entry = ConfigEntry(
            data=data or {},
            discovery_keys=MappingProxyType({}),
            domain=domain,
            minor_version=1,
            options=options or {},
            source="user",
            subentries_data=None,
            title=title or domain,
            unique_id=unique_id,
            version=1,
        )
        self.config_entries.entries[entry.entry_id] = entry
        return entry

    def add_integration(
        self, domain: str, *, iot_class: str, integration_type: str = "hub"
    ) -> None:
        self.data[loader.DATA_INTEGRATIONS][domain] = loader.Integration(
            self,  # type: ignore[arg-type]
            f"homeassistant.components.{domain}",
            pathlib.Path(domain),
            {
                "domain": domain,
                "name": domain,
                "iot_class": iot_class,
                "integration_type": integration_type,
            },
        )

    def add_device(
        self,
        entry: ConfigEntry | None = None,
        *,
        identifiers: Iterable[tuple[str, str]] = (),
        connections: Iterable[tuple[str, str]] = (),
        via: dr.DeviceEntry | None = None,
        name: str | None = None,
        area_id: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
    ) -> dr.DeviceEntry:
        device = dr.DeviceEntry(
            config_entry_id=entry.entry_id if entry else "none",
            identifiers=set(identifiers),
            connections=set(connections),
            via_device_id=via.id if via else None,
            name=name,
            area_id=area_id,
            manufacturer=manufacturer,
            model=model,
        )
        self.dev_reg.devices[device.id] = device
        return device

    def add_entity(
        self,
        entity_id: str,
        platform: str,
        unique_id: str | None = None,
        *,
        entry: ConfigEntry | None = None,
        device: dr.DeviceEntry | None = None,
        capabilities: Mapping[str, Any] | None = None,
        supported_features: int = 0,
        **kw: Any,
    ) -> er.RegistryEntry:
        values: dict[str, Any] = {
            "config_entry_id": entry.entry_id if entry else None,
            "area_id": None,
            "disabled_by": None,
            "entity_category": None,
            "has_entity_name": False,
            "hidden_by": None,
            "name": None,
            "options": None,
            "original_name": None,
            "translation_key": None,
        }
        values.update(kw)
        entity = er.RegistryEntry(
            entity_id=entity_id,
            unique_id=unique_id or entity_id,
            platform=platform,
            capabilities=capabilities,
            config_subentry_id=None,
            created_at=utcnow(),
            device_id=device.id if device else None,
            id=None,
            object_id_base=None,
            original_device_class=None,
            original_icon=None,
            suggested_object_id=None,
            supported_features=supported_features,
            unit_of_measurement=None,
            **values,
        )
        self.ent_reg.entities[entity_id] = entity
        self.bus.fire(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            {"action": "create", "entity_id": entity_id},
        )
        return entity

    def add_area(self, area_id: str, name: str, icon: str | None = None) -> None:
        self.area_reg.areas[area_id] = ar.AreaEntry(
            name=name,
            aliases=set(),
            floor_id=None,
            humidity_entity_id=None,
            icon=icon,
            id=area_id,
            picture=None,
            temperature_entity_id=None,
        )
        self.bus.fire(
            ar.EVENT_AREA_REGISTRY_UPDATED, {"action": "create", "area_id": area_id}
        )

    # ---- mutations

    def update_entity(self, entity_id: str, /, **changes: Any) -> er.RegistryEntry:
        old = self.ent_reg.entities[entity_id]
        new = attr.evolve(old, **changes)
        data: dict[str, Any] = {
            "action": "update",
            "entity_id": new.entity_id,
            "changes": {k: getattr(old, k) for k in changes},
        }
        if new.entity_id != entity_id:
            del self.ent_reg.entities[entity_id]
            data["old_entity_id"] = entity_id
        self.ent_reg.entities[new.entity_id] = new
        self.bus.fire(er.EVENT_ENTITY_REGISTRY_UPDATED, data)
        return new

    def remove_entity(self, entity_id: str) -> None:
        del self.ent_reg.entities[entity_id]
        self.bus.fire(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            {"action": "remove", "entity_id": entity_id},
        )

    def update_device(self, device: dr.DeviceEntry, **changes: Any) -> dr.DeviceEntry:
        new = attr.evolve(device, **changes)
        self.dev_reg.devices[new.id] = new
        self.bus.fire(
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            {
                "action": "update",
                "device_id": new.id,
                "changes": {k: getattr(device, k) for k in changes},
            },
        )
        return new

    def update_area(self, area_id: str, **changes: Any) -> None:
        old = self.area_reg.areas[area_id]
        self.area_reg.areas[area_id] = ar.AreaEntry(
            **{
                "name": old.name,
                "aliases": old.aliases,
                "floor_id": old.floor_id,
                "humidity_entity_id": old.humidity_entity_id,
                "icon": old.icon,
                "id": old.id,
                "picture": old.picture,
                "temperature_entity_id": old.temperature_entity_id,
                **changes,
            }
        )
        self.bus.fire(
            ar.EVENT_AREA_REGISTRY_UPDATED, {"action": "update", "area_id": area_id}
        )

    def remove_area(self, area_id: str) -> None:
        del self.area_reg.areas[area_id]
        self.bus.fire(
            ar.EVENT_AREA_REGISTRY_UPDATED, {"action": "remove", "area_id": area_id}
        )

    def set_state(
        self, entity_id: str, state: str, attributes: Mapping[str, Any] | None = None
    ) -> State:
        old = self.states.states.get(entity_id)
        new = State(entity_id, state, attributes)
        self.states.states[entity_id] = new
        self.bus.fire(
            EVENT_STATE_CHANGED,
            {"entity_id": entity_id, "old_state": old, "new_state": new},
        )
        return new

    def remove_state(self, entity_id: str) -> None:
        old = self.states.states.pop(entity_id)
        self.bus.fire(
            EVENT_STATE_CHANGED,
            {"entity_id": entity_id, "old_state": old, "new_state": None},
        )
