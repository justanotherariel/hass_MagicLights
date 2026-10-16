"""One select per room and zone: its scenes, Off and Manual."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    OPTION_MANUAL,
    OPTION_OFF,
    SIGNAL_CONFIG,
    SIGNAL_STATUS,
    SIGNAL_TARGETS,
)
from .data import MagicLightsConfigEntry, MagicLightsData
from .dto import scene_summary, suggested_entity_id
from .models import Scene, Target, TargetId, TargetStatus

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MagicLightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    targets = data.coordinator.targets
    added: set[TargetId] = set()

    @callback
    def add(target_ids: Iterable[TargetId]) -> None:
        new = [t for t in target_ids if t in targets and t not in added]
        added.update(new)
        devices = dr.async_get(hass)
        async_add_entities(
            TargetSceneSelect(
                data,
                targets[t],
                place=devices.async_get_device(identifiers={(DOMAIN, t)}) is None,
            )
            for t in new
        )

    @callback
    def targets_changed(new: frozenset[TargetId], removed: frozenset[TargetId]) -> None:
        add(sorted(new))
        entities = er.async_get(hass)
        devices = dr.async_get(hass)
        for target_id in removed:
            added.discard(target_id)
            if entity_id := entities.async_get_entity_id(
                Platform.SELECT, DOMAIN, target_id
            ):
                entities.async_remove(entity_id)
            if device := devices.async_get_device(identifiers={(DOMAIN, target_id)}):
                devices.async_remove_device(device.id)

    add(list(targets))
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_TARGETS, targets_changed)
    )


def _current_option(status: TargetStatus) -> str:
    if status.state == "scene" and status.scene_id is not None:
        return status.scene_name or OPTION_MANUAL
    if status.state == "off":
        return OPTION_OFF
    # Manual, mixed, and drafts being tried.
    return OPTION_MANUAL


class TargetSceneSelect(SelectEntity):
    """The scene running on a target; services act through it."""

    _attr_has_entity_name = True
    _attr_translation_key = "scene"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset(
        {"lights", "parts", "manual", "scenes", "hidden_scenes", "elsewhere"}
    )

    def __init__(self, data: MagicLightsData, target: Target, *, place: bool) -> None:
        self._data = data
        self._coordinator = data.coordinator
        self._target_id = target.id
        self._device_name = target.name
        self._attr_unique_id = target.id
        # The registry keeps it stable across renames.
        self.entity_id = suggested_entity_id(target)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, target.id)},
            name=target.name,
            manufacturer="Magic Lights",
            model="Room" if target.kind == "room" else "Zone",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._menu: list[Scene] | None = None
        self._summaries: list[dict[str, Any]] = []
        self._hidden: list[dict[str, Any]] = []
        self._shown: tuple[Any, ...] | None = None
        self._place = place
        self._refresh()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_STATUS, self._on_status)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_CONFIG, self._on_config)
        )
        if self._place:
            self._place_device()

    def _place_device(self) -> None:
        """Put a new room device into its area, unless it came back with one."""
        target = self._coordinator.targets.get(self._target_id)
        entry = self.registry_entry
        if target is None or target.area_id is None or entry is None:
            return
        devices = dr.async_get(self.hass)
        if (
            entry.device_id is not None
            and (device := devices.async_get(entry.device_id)) is not None
            and device.area_id is None
        ):
            devices.async_update_device(device.id, area_id=target.area_id)

    @callback
    def _on_status(self, target_ids: frozenset[TargetId]) -> None:
        if self._target_id in target_ids and self._refresh():
            self.async_write_ha_state()

    @callback
    def _on_config(self) -> None:
        self._menu = None
        if self._refresh():
            self.async_write_ha_state()

    def _refresh(self) -> bool:
        """Recompute options and attributes; return whether they changed."""
        coordinator = self._coordinator
        if (target := coordinator.targets.get(self._target_id)) is None:
            return False  # being removed
        if target.name != self._device_name and self.hass is not None:
            self._rename_device(target.name)
        if self._menu is None:
            # Only config and effect changes alter the menu.
            registry = self._data.registry
            self._menu = coordinator.scenes_for(self._target_id)
            self._summaries = [scene_summary(s, registry) for s in self._menu]
            shown = {s.public_id for s in self._menu}
            self._hidden = [
                scene_summary(s, registry)
                for s in coordinator.scenes_for(self._target_id, include_hidden=True)
                if s.public_id not in shown
            ]
        status = coordinator.status(self._target_id)
        names = [scene.name for scene in self._menu]
        current = _current_option(status)
        # The running scene stays selectable even when it is not in the menu
        # (a zone scene seen from a room, a hidden scene started by a service).
        extra = [] if current in (OPTION_OFF, OPTION_MANUAL, *names) else [current]
        attributes = status.to_dict() | {
            "target_id": target.id,
            "kind": target.kind,
            "scenes": self._summaries,
            # Cards may show scenes the menu leaves out, and play them.
            "hidden_scenes": self._hidden,
        }
        shown = (
            [OPTION_OFF, *names, *extra, OPTION_MANUAL],
            current,
            attributes,
            bool(target.lights),
        )
        if shown == self._shown:
            return False
        self._shown = shown
        (
            self._attr_options,
            self._attr_current_option,
            self._attr_extra_state_attributes,
            self._attr_available,
        ) = shown
        return True

    def _rename_device(self, name: str) -> None:
        self._device_name = name
        devices = dr.async_get(self.hass)
        if device := devices.async_get_device(identifiers={(DOMAIN, self._target_id)}):
            devices.async_update_device(device.id, name=name)

    async def async_select_option(self, option: str) -> None:
        coordinator = self._coordinator
        context = self._context
        if option == OPTION_OFF:
            coordinator.turn_off(self._target_id, context=context)
        elif option == OPTION_MANUAL:
            coordinator.release(self._target_id, context=context)
        else:
            coordinator.activate(
                self._target_id,
                coordinator.find_scene(self._target_id, option),
                context=context,
            )

    # ---- entity services (services.py); HA sets self._context to the call's

    async def async_activate_scene(
        self,
        scene: str,
        brightness: float | None = None,
        dynamic: bool | None = None,
        transition: float | None = None,
    ) -> None:
        coordinator = self._coordinator
        coordinator.activate(
            self._target_id,
            coordinator.find_scene(self._target_id, scene),
            brightness=brightness,
            dynamic=dynamic,
            transition=transition,
            context=self._context,
        )

    async def async_turn_off(self, transition: float | None = None) -> None:
        self._coordinator.turn_off(
            self._target_id, transition=transition, context=self._context
        )

    async def async_toggle_scene(
        self,
        scene: str,
        brightness: float | None = None,
        dynamic: bool | None = None,
    ) -> None:
        coordinator = self._coordinator
        found = coordinator.find_scene(self._target_id, scene)
        status = coordinator.status(self._target_id)
        if (
            status.state == "scene"
            and status.via is None
            and status.scene_id == found.public_id
        ):
            coordinator.turn_off(self._target_id, context=self._context)
        else:
            coordinator.activate(
                self._target_id,
                found,
                brightness=brightness,
                dynamic=dynamic,
                context=self._context,
            )

    async def async_next_scene(self, cycle: bool = True) -> None:
        self._coordinator.next_scene(self._target_id, 1, cycle, context=self._context)

    async def async_previous_scene(self, cycle: bool = True) -> None:
        self._coordinator.next_scene(self._target_id, -1, cycle, context=self._context)

    async def async_adjust_brightness(
        self, step: float | None = None, brightness: float | None = None
    ) -> None:
        self._coordinator.adjust_brightness(
            self._target_id, value=brightness, step=step, context=self._context
        )

    async def async_set_dynamic(self, dynamic: bool) -> None:
        self._coordinator.set_dynamic(self._target_id, dynamic, context=self._context)

    async def async_release(self) -> None:
        self._coordinator.release(self._target_id, context=self._context)

    async def async_reclaim(self, light: str | None = None) -> None:
        self._coordinator.reclaim(self._target_id, light, context=self._context)
