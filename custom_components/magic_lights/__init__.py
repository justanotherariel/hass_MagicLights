"""Magic Lights: curated, dynamic and adaptive scenes for rooms and zones."""

from __future__ import annotations

import dataclasses
from functools import partial

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN, SIGNAL_CONFIG, SIGNAL_EFFECTS
from .data import MagicLightsConfigEntry, MagicLightsData
from .effects import async_get_registry
from .engine import create_engine
from .lights import LightIndexImpl, light_areas
from .migrate_v3 import remap_runtime
from .models import Config, LightsChange
from .native import build_drivers
from .panel import async_register_panel, async_register_static, async_unregister_panel
from .presets import load_presets
from .services import async_setup_services
from .store import ConfigStore, RuntimeStoreImpl
from .websocket import async_register_websocket

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    await async_register_static(hass, str(integration.version))
    async_register_websocket(hass)
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MagicLightsConfigEntry) -> bool:
    registry = async_get_registry(hass)
    presets = await hass.async_add_executor_job(load_presets, registry)
    store = ConfigStore(hass, registry, presets, partial(light_areas, hass))
    config = await store.async_load_config() or Config()
    runtime = RuntimeStoreImpl(hass)
    snapshot = await runtime.load()
    if store.remap and snapshot is not None:
        # Running scenes follow their merge; saved before anything can end them.
        snapshot = remap_runtime(snapshot, store.remap)
        await runtime.save(snapshot)
    integration = await async_get_integration(hass, DOMAIN)

    # Registries are loaded here; light states may not be yet.
    drivers = build_drivers(hass)
    lights = LightIndexImpl(hass, config, drivers)
    lights.rebuild()
    coordinator = create_engine(
        hass, lights, registry, drivers, config, presets, runtime
    )
    # Before the select platform, so the selects are born with this status.
    coordinator.restore_ownership(snapshot)
    data = entry.runtime_data = MagicLightsData(
        coordinator,
        lights,
        store,
        runtime,
        registry,
        presets,
        str(integration.version),
    )

    @callback
    def _light_renamed(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        old, new = event.data["old_entity_id"], event.data["entity_id"]
        coordinator.rename_light(old, new)
        config = coordinator.config
        if (renamed := _rename_light(config, old, new)) != config:
            data.apply_config(hass, renamed)
            entry.async_create_task(hass, store.async_save_config(renamed))

    # Ahead of the index's own listener, so the new id is never read under
    # the old config or ownership (an excluded or manual lamp would be claimed).
    entry.async_on_unload(
        hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            _light_renamed,
            event_filter=_is_light_rename,
        )
    )
    entry.async_on_unload(lights.async_listen())

    @callback
    def _lights_changed(change: LightsChange) -> None:
        # Targets changed: the panel's copy of the config is stale.
        if change.changed_targets:
            async_dispatcher_send(hass, SIGNAL_CONFIG)

    entry.async_on_unload(lights.subscribe(_lights_changed))

    @callback
    def _effects_changed() -> None:
        coordinator.apply_config(coordinator.config)
        async_dispatcher_send(hass, SIGNAL_CONFIG)  # new swatches

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_EFFECTS, _effects_changed)
    )

    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SELECT])
    await async_register_panel(hass, data.version)

    @callback
    def _start(hass: HomeAssistant) -> None:
        coordinator.start()

    entry.async_on_unload(async_at_started(hass, _start))

    async def _stop(event: Event) -> None:
        coordinator.shutdown()
        await runtime.flush()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: MagicLightsConfigEntry
) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.SELECT]):
        return False
    data = entry.runtime_data
    data.coordinator.shutdown()
    await data.runtime.flush()
    async_unregister_panel(hass)
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: MagicLightsConfigEntry
) -> None:
    """Forget what ran; rooms, zones and scenes stay for the next entry."""
    await RuntimeStoreImpl(hass).remove()


@callback
def _is_light_rename(data: er.EventEntityRegistryUpdatedData) -> bool:
    return (
        data["action"] == "update"
        and "old_entity_id" in data
        and data["entity_id"].startswith(f"{Platform.LIGHT}.")
    )


def _rename_light(config: Config, old: str, new: str) -> Config:
    """Return the config with every reference to light `old` pointing to `new`."""

    def ids(entity_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(new if e == old else e for e in entity_ids)

    replace = dataclasses.replace
    return replace(
        config,
        zones={
            zone_id: replace(zone, lights=ids(zone.lights))
            for zone_id, zone in config.zones.items()
        },
        targets={
            target_id: replace(
                options,
                excluded_lights=ids(options.excluded_lights),
                extra_lights=ids(options.extra_lights),
            )
            for target_id, options in config.targets.items()
        },
        scenes={
            scene_id: replace(
                scene,
                layers=tuple(
                    layer
                    if layer.lights == "all"
                    else replace(layer, lights=ids(layer.lights))
                    for layer in scene.layers
                ),
            )
            for scene_id, scene in config.scenes.items()
        },
        settings=replace(
            config.settings,
            light_buses={
                new if e == old else e: bus_id
                for e, bus_id in config.settings.light_buses.items()
            },
        ),
    )
