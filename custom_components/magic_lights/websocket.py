"""Websocket commands of the panel (magic_lights/*)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import dataclasses
from functools import partial
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_CONFIG, SIGNAL_EFFECTS, SIGNAL_TARGETS
from .data import MagicLightsData
from .dto import bus_infos, light_info, scene_info, target_info
from .models import (
    Config,
    Scene,
    Settings,
    TargetId,
    TargetOptions,
    Zone,
    new_zone_id,
)
from .presets import palettes_from_presets
from .validation import (
    OPTIONS_SCHEMA,
    SETTINGS_SCHEMA,
    ZONE_SCHEMA,
    ConfigError,
    validate_scene,
)

_LOCATED = frozenset({"invalid", "invalid_params", "unknown_effect"})

ADJUST = vol.Schema(
    {
        vol.Optional("brightness"): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=100)
        ),
        vol.Optional("dynamic"): bool,
    }
)


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    for handler in (
        ws_config,
        ws_scene_save,
        ws_scene_delete,
        ws_zone_save,
        ws_zone_delete,
        ws_target_options,
        ws_settings_save,
        ws_preview,
        ws_try,
        ws_try_stop,
        ws_reclaim,
        ws_reshuffle,
        ws_subscribe,
    ):
        websocket_api.async_register_command(hass, handler)


# ---- helpers


def _loaded(hass: HomeAssistant) -> MagicLightsData | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


def _data_or_error(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> MagicLightsData | None:
    if (data := _loaded(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Magic Lights is not set up")
    return data


def _where(path: Sequence[Any]) -> str:
    """Name an error location: ["layers", 0, "params", "speed"] -> "layer 1, speed"."""
    parts: list[str] = []
    items = list(path)
    while items:
        item = items.pop(0)
        if item == "layers" and items and isinstance(items[0], int):
            parts.append(f"layer {items.pop(0) + 1}")
        elif item != "params":
            parts.append(str(item))
    return ", ".join(parts)


def _send_invalid(
    connection: ActiveConnection,
    msg: dict[str, Any],
    err: vol.Invalid,
    skip: int = 0,
) -> None:
    """Reply with a readable message; `skip` drops leading path parts."""
    code = err.code if isinstance(err, ConfigError) else "invalid"
    message = err.msg
    # Name and reference errors say what is wrong; the others need a place.
    if code in _LOCATED and (where := _where(err.path[skip:])):
        message = f"{message} ({where})"
    connection.send_error(msg["id"], code, message)


def _draft(data: MagicLightsData, raw: Mapping[str, Any]) -> Scene:
    """Validate a scene that is previewed or tried, not stored.

    Its name is not checked against other scenes: that happens on save.
    """
    return validate_scene(raw, data.registry, Config())


def _with_new_scene(config: Config, scene: Scene) -> Mapping[TargetId, TargetOptions]:
    """Show a new scene in menus the user has ordered by hand."""
    if scene.id in config.scenes or scene.preset is not None:
        return config.targets
    return {
        target_id: (
            dataclasses.replace(
                options, visible_scenes=(*options.visible_scenes, scene.id)
            )
            if options.visible_scenes is not None
            and (not scene.scope or target_id in scene.scope)
            else options
        )
        for target_id, options in config.targets.items()
    }


def _without_target(
    scenes: Mapping[str, Scene], target_id: TargetId
) -> dict[str, Scene]:
    """Drop a deleted target from the scenes' scopes.

    A scene scoped to it alone goes too: an empty scope would mean everywhere.
    """
    out: dict[str, Scene] = {}
    for scene_id, scene in scenes.items():
        if target_id not in scene.scope:
            out[scene_id] = scene
        elif scope := tuple(t for t in scene.scope if t != target_id):
            out[scene_id] = dataclasses.replace(scene, scope=scope)
    return out


def _config_dto(hass: HomeAssistant, data: MagicLightsData) -> dict[str, Any]:
    coordinator = data.coordinator
    config = coordinator.config
    scenes = [*data.presets.values(), *config.scenes.values()]
    return {
        "zones": {zone_id: zone.to_dict() for zone_id, zone in config.zones.items()},
        "targets": [
            target_info(hass, coordinator, target)
            for target in coordinator.targets.values()
        ],
        "scenes": [scene_info(coordinator, data.registry, s) for s in scenes],
        "lights": [
            light_info(light) for _, light in sorted(data.lights.lights.items())
        ],
        "effects": [info.to_dict() for info in data.registry.catalogue()],
        "buses": bus_infos(data.lights.lights),
        "palettes": palettes_from_presets(data.presets),
        "settings": config.settings.to_dict(),
        "version": data.version,
    }


# ---- config


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@websocket_api.require_admin
@callback
def ws_config(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    if (data := _data_or_error(hass, connection, msg)) is not None:
        connection.send_result(msg["id"], _config_dto(hass, data))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/scene/save", vol.Required("scene"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scene_save(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    coordinator = data.coordinator
    config = coordinator.config
    try:
        scene = validate_scene(
            msg["scene"],
            data.registry,
            config,
            presets=data.presets,
            targets=coordinator.targets,
        )
    except ConfigError as err:
        _send_invalid(connection, msg, err, skip=2)
        return
    await data.async_commit(
        hass,
        dataclasses.replace(
            config,
            scenes={**config.scenes, scene.id: scene},
            targets=_with_new_scene(config, scene),
        ),
    )
    connection.send_result(
        msg["id"], {"scene": scene_info(coordinator, data.registry, scene)}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/scene/delete", vol.Required("scene_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scene_delete(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete a user scene; deleting a customised copy resets its preset."""
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    config = data.coordinator.config
    if (scene_id := msg["scene_id"]) not in config.scenes:
        connection.send_error(
            msg["id"], "not_found", f"There is no saved scene '{scene_id}'"
        )
        return
    scenes = {k: v for k, v in config.scenes.items() if k != scene_id}
    await data.async_commit(hass, dataclasses.replace(config, scenes=scenes))
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/zone/save", vol.Required("zone"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_zone_save(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create (id "") or update a zone."""
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    config = data.coordinator.config
    raw = dict(msg["zone"])
    if not raw.get("id"):
        raw["id"] = new_zone_id(str(raw.get("name") or ""), config.zones)
    try:
        zone = Zone.from_dict(ZONE_SCHEMA(raw))
    except vol.Invalid as err:
        _send_invalid(connection, msg, err)
        return
    await data.async_commit(
        hass,
        dataclasses.replace(config, zones={**config.zones, zone.id: zone}),
    )
    connection.send_result(msg["id"], {"zone": zone.to_dict()})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/zone/delete", vol.Required("zone_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_zone_delete(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    config = data.coordinator.config
    if (zone_id := msg["zone_id"]) not in config.zones:
        connection.send_error(msg["id"], "not_found", f"There is no zone '{zone_id}'")
        return
    target_id = f"zone:{zone_id}"
    await data.async_commit(
        hass,
        dataclasses.replace(
            config,
            zones={k: v for k, v in config.zones.items() if k != zone_id},
            targets={k: v for k, v in config.targets.items() if k != target_id},
            scenes=_without_target(config.scenes, target_id),
        ),
    )
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/target/options",
        vol.Required("target_id"): str,
        vol.Required("options"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_target_options(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Change some of a target's options."""
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    coordinator = data.coordinator
    config = coordinator.config
    if (target_id := msg["target_id"]) not in coordinator.targets:
        connection.send_error(
            msg["id"], "not_found", f"There is no target '{target_id}'"
        )
        return
    current = config.targets.get(target_id, TargetOptions())
    try:
        options = TargetOptions.from_dict(
            OPTIONS_SCHEMA({**current.to_dict(), **msg["options"]})
        )
    except vol.Invalid as err:
        _send_invalid(connection, msg, err)
        return
    await data.async_commit(
        hass,
        dataclasses.replace(config, targets={**config.targets, target_id: options}),
    )
    target = coordinator.targets[target_id]
    connection.send_result(
        msg["id"], {"target": target_info(hass, coordinator, target)}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/settings/save", vol.Required("settings"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_settings_save(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Change some settings; `buses` and `light_buses` are replaced whole."""
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    config = data.coordinator.config
    try:
        settings = Settings.from_dict(
            SETTINGS_SCHEMA({**config.settings.to_dict(), **msg["settings"]})
        )
    except vol.Invalid as err:
        _send_invalid(connection, msg, err)
        return
    await data.async_commit(hass, dataclasses.replace(config, settings=settings))
    connection.send_result(msg["id"], {})


# ---- runtime


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preview",
        vol.Required("target_id"): str,
        vol.Required("scene"): dict,
        vol.Optional("duration"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=86400)
        ),
        vol.Optional("samples"): vol.All(vol.Coerce(int), vol.Range(min=1, max=240)),
        vol.Optional("start"): cv.datetime,
        vol.Optional("adjust"): ADJUST,
    }
)
@websocket_api.require_admin
@callback
def ws_preview(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    try:
        scene = _draft(data, msg["scene"])
    except ConfigError as err:
        _send_invalid(connection, msg, err, skip=2)
        return
    start = msg.get("start")
    frames = data.coordinator.preview(
        msg["target_id"],
        scene,
        start=None if start is None else dt_util.as_utc(start),
        duration=msg.get("duration"),
        samples=msg.get("samples"),
        adjust=msg.get("adjust"),
    )
    connection.send_result(msg["id"], {"frames": frames})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/try",
        vol.Required("target_id"): str,
        vol.Required("scene"): dict,
        vol.Optional("adjust"): ADJUST,
    }
)
@websocket_api.require_admin
@callback
def ws_try(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Run an unsaved scene until try/stop, a save, or the connection closing."""
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    try:
        scene = _draft(data, msg["scene"])
    except ConfigError as err:
        _send_invalid(connection, msg, err, skip=2)
        return
    coordinator = data.coordinator
    target_id = msg["target_id"]
    context = connection.context(msg)
    coordinator.try_scene(target_id, scene, msg.get("adjust"), context=context)
    # A closed editor tab must not leave the draft running. Keyed by target,
    # so the editor's repeated tries replace each other.
    connection.subscriptions[_try_key(target_id)] = partial(
        _end_draft, hass, target_id, coordinator.by_target.get(target_id), context
    )
    connection.send_result(msg["id"], {})


def _try_key(target_id: TargetId) -> tuple[str, str, TargetId]:
    return (DOMAIN, "try", target_id)


@callback
def _end_draft(
    hass: HomeAssistant,
    target_id: TargetId,
    activation_id: str | None,
    context: Context,
) -> None:
    # Only the draft this try started; stop_try leaves it alone once saved.
    if (
        activation_id is not None
        and (data := _loaded(hass)) is not None
        and data.coordinator.by_target.get(target_id) == activation_id
    ):
        data.coordinator.stop_try(target_id, context=context)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/try/stop", vol.Required("target_id"): str}
)
@websocket_api.require_admin
@callback
def ws_try_stop(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    if (data := _data_or_error(hass, connection, msg)) is not None:
        connection.subscriptions.pop(_try_key(msg["target_id"]), None)
        data.coordinator.stop_try(msg["target_id"], context=connection.context(msg))
        connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/reclaim",
        vol.Required("target_id"): str,
        vol.Optional("entity_id"): cv.entity_id,
    }
)
@websocket_api.require_admin
@callback
def ws_reclaim(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    if (data := _data_or_error(hass, connection, msg)) is not None:
        data.coordinator.reclaim(
            msg["target_id"], msg.get("entity_id"), context=connection.context(msg)
        )
        connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/reshuffle", vol.Required("target_id"): str}
)
@websocket_api.require_admin
@callback
def ws_reshuffle(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    if (data := _data_or_error(hass, connection, msg)) is not None:
        data.coordinator.reshuffle(msg["target_id"], context=connection.context(msg))
        connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Announce config, target and effect changes; status lives in hass.states."""

    def forward(key: str) -> Callable[..., None]:
        @callback
        def send(*_: Any) -> None:
            connection.send_message(websocket_api.event_message(msg["id"], {key: True}))

        return send

    unsubs = [
        async_dispatcher_connect(hass, signal, forward(key))
        for signal, key in (
            (SIGNAL_CONFIG, "config_changed"),
            (SIGNAL_TARGETS, "targets_changed"),
            (SIGNAL_EFFECTS, "effects_changed"),
        )
    ]

    @callback
    def unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
