"""Websocket commands of the panel (magic_lights/*)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
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
from .dto import bus_infos, light_info, part_info, scene_info, target_info
from .models import (
    Config,
    Part,
    Scene,
    Settings,
    TargetId,
    TargetOptions,
    Zone,
    is_user_part,
    new_zone_id,
)
from .presets import palettes_from_parts
from .validation import (
    OPTIONS_SCHEMA,
    SETTINGS_SCHEMA,
    ZONE_SCHEMA,
    ConfigError,
    validate,
    validate_draft,
)

_LOCATED = frozenset({"invalid", "invalid_params", "unknown_effect", "unknown_part"})

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
        ws_library_save,
        ws_scene_delete,
        ws_scenes_order,
        ws_part_delete,
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


def _checked(data: MagicLightsData, config: Config, only: Collection[str]) -> Config:
    """Validate a changed config; only what `only` names can make it fail."""
    return validate(
        config.to_dict(),
        data.registry,
        data.builtins,
        only=only,
        live=data.coordinator.targets,
    )


def _owner(config: Config, scene_id: str) -> TargetId | None:
    """Return the target a stored scene belongs to."""
    return next(
        (
            t
            for t, scenes in config.scenes.items()
            if any(s.id == scene_id for s in scenes)
        ),
        None,
    )


def _with_library(
    config: Config,
    parts: Sequence[Mapping[str, Any]],
    scenes: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    """Return the stored form with parts and scenes put in, and their ids."""
    raw = config.to_dict()
    saved: set[str] = set()
    for part in parts:
        part_id = str(part.get("id"))
        # A part may come back in the API's shape; icon and category are the
        # catalogue's.
        raw["parts"][part_id] = {
            k: v for k, v in part.items() if k not in ("icon", "category")
        }
        saved.add(part_id)
    for item in scenes:
        target_id, scene = item["target"], item["scene"]
        scene_id = scene.get("id")
        if _owner(config, scene_id) not in (None, target_id):
            raise ConfigError(
                "invalid", "a scene stays in its room", ["scenes", scene_id, "target"]
            )
        listed = raw["scenes"].setdefault(target_id, [])
        at = next((i for i, s in enumerate(listed) if s["id"] == scene_id), None)
        if at is None:
            listed.append(scene)
        else:
            listed[at] = scene
        saved.add(scene_id)
    return raw, saved


def _without_pristine(config: Config, builtins: Mapping[str, Part]) -> Config:
    """Drop the edits of built-ins that equal them: the built-in is unedited."""
    parts = {
        part_id: part
        for part_id, part in config.parts.items()
        if (builtin := builtins.get(part_id)) is None
        or (part.effect, part.params) != (builtin.effect, builtin.params)
    }
    return dataclasses.replace(config, parts=parts)


def _without_scenes(config: Config, gone: Collection[str]) -> Config:
    """Drop scenes, and the target defaults that name them."""
    scenes = {
        target_id: kept
        for target_id, listed in config.scenes.items()
        if (kept := tuple(s for s in listed if s.id not in gone))
    }
    targets = {
        target_id: (
            dataclasses.replace(options, default_scene=None)
            if options.default_scene in gone
            else options
        )
        for target_id, options in config.targets.items()
    }
    return dataclasses.replace(config, scenes=scenes, targets=targets)


def _draft(data: MagicLightsData, msg: Mapping[str, Any]) -> Scene:
    """Resolve a scene that is previewed or tried over its unsaved parts."""
    coordinator = data.coordinator
    scene, parts = validate_draft(
        msg["scene"], msg["parts"], data.registry, coordinator.parts
    )
    return coordinator.resolve_draft(scene, parts)


def _config_dto(hass: HomeAssistant, data: MagicLightsData) -> dict[str, Any]:
    coordinator = data.coordinator
    config = coordinator.config
    registry = data.registry
    return {
        "zones": {zone_id: zone.to_dict() for zone_id, zone in config.zones.items()},
        "targets": [
            target_info(hass, coordinator, target)
            for target in coordinator.targets.values()
        ],
        "parts": [
            part_info(coordinator, registry, p) for p in coordinator.parts.values()
        ],
        "scenes": [
            scene_info(coordinator, registry, target_id, scene)
            for target_id, scenes in config.scenes.items()
            for scene in scenes
        ],
        "lights": [
            light_info(light) for _, light in sorted(data.lights.lights.items())
        ],
        "effects": [info.to_dict() for info in registry.catalogue()],
        "buses": bus_infos(data.lights.lights),
        "palettes": palettes_from_parts(data.builtins),
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
    {
        vol.Required("type"): f"{DOMAIN}/library/save",
        vol.Optional("parts", default=list): [dict],
        vol.Optional("scenes", default=list): [
            {vol.Required("target"): str, vol.Required("scene"): dict}
        ],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_library_save(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Save parts and scenes together, or nothing.

    An edit of a built-in that equals it removes the edit.
    """
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    coordinator = data.coordinator
    try:
        raw, saved = _with_library(coordinator.config, msg["parts"], msg["scenes"])
        config = validate(
            raw, data.registry, data.builtins, only=saved, live=coordinator.targets
        )
    except ConfigError as err:
        _send_invalid(connection, msg, err, skip=2)
        return
    config = _without_pristine(config, data.builtins)
    await data.async_commit(hass, config, saved)
    stored = {
        (target_id, s.id): s
        for target_id, scenes in config.scenes.items()
        for s in scenes
    }
    part_ids = dict.fromkeys(str(p["id"]) for p in msg["parts"])
    connection.send_result(
        msg["id"],
        {
            "parts": [
                part_info(coordinator, data.registry, coordinator.parts[part_id])
                for part_id in part_ids
            ],
            "scenes": [
                scene_info(
                    coordinator,
                    data.registry,
                    item["target"],
                    stored[item["target"], item["scene"]["id"]],
                )
                for item in msg["scenes"]
            ],
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/scene/delete", vol.Required("scene_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scene_delete(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete a scene; its target no longer switches on with it."""
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    config = data.coordinator.config
    if _owner(config, scene_id := msg["scene_id"]) is None:
        connection.send_error(
            msg["id"], "not_found", f"There is no saved scene '{scene_id}'"
        )
        return
    await data.async_commit(hass, _without_scenes(config, {scene_id}))
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/scenes/order",
        vol.Required("target_id"): str,
        vol.Required("scene_ids"): [str],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scenes_order(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put a target's scenes in a new order: its menu's."""
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    coordinator = data.coordinator
    config = coordinator.config
    if (target_id := msg["target_id"]) not in coordinator.targets:
        connection.send_error(
            msg["id"], "not_found", f"There is no target '{target_id}'"
        )
        return
    by_id = {s.id: s for s in config.scenes.get(target_id, ())}
    order = msg["scene_ids"]
    if len(order) != len(by_id) or set(order) != by_id.keys():
        connection.send_error(
            msg["id"], "invalid", f"Not an order of the scenes of {target_id}"
        )
        return
    if order:
        scenes = {**config.scenes, target_id: tuple(by_id[s] for s in order)}
        await data.async_commit(hass, dataclasses.replace(config, scenes=scenes))
    target = coordinator.targets[target_id]
    connection.send_result(
        msg["id"], {"target": target_info(hass, coordinator, target)}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/part/delete", vol.Required("part_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_part_delete(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete one of your parts, or reset an edited built-in.

    A part that a scene of an existing target or the switch-on default uses
    stays; scenes of targets that are gone go with it.
    """
    if (data := _data_or_error(hass, connection, msg)) is None:
        return
    coordinator = data.coordinator
    config = coordinator.config
    if (part_id := msg["part_id"]) not in config.parts:
        connection.send_error(
            msg["id"], "not_found", f"There is no saved part '{part_id}'"
        )
        return
    parts = {k: v for k, v in config.parts.items() if k != part_id}
    config = dataclasses.replace(config, parts=parts)
    if is_user_part(part_id):
        users = [
            (target_id, scene)
            for target_id, scenes in config.scenes.items()
            for scene in scenes
            if any(layer.part == part_id for layer in scene.layers)
        ]
        uses = [
            f"{coordinator.targets[target_id].name} · {scene.name}"
            for target_id, scene in users
            if target_id in coordinator.targets
        ]
        if config.settings.default_scene == part_id:
            uses.append("the switch-on default")
        if uses:
            connection.send_error(
                msg["id"], "part_in_use", f"Used by {', '.join(uses)}"
            )
            return
        config = _without_scenes(config, {scene.id for _, scene in users})
    await data.async_commit(hass, config)
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
    """Delete a zone with its options and scenes."""
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
            scenes={k: v for k, v in config.scenes.items() if k != target_id},
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
        config = _checked(
            data,
            dataclasses.replace(config, targets={**config.targets, target_id: options}),
            {target_id},
        )
    except vol.Invalid as err:
        _send_invalid(connection, msg, err)
        return
    await data.async_commit(hass, config)
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
        config = _checked(
            data, dataclasses.replace(config, settings=settings), {"settings"}
        )
    except vol.Invalid as err:
        _send_invalid(connection, msg, err)
        return
    await data.async_commit(hass, config)
    connection.send_result(msg["id"], {})


# ---- runtime

# A draft: the scene previewed or tried, and the parts it changes or adds.
_DRAFT = {
    vol.Required("scene"): dict,
    vol.Optional("parts", default=list): [dict],
}


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preview",
        vol.Required("target_id"): str,
        **_DRAFT,
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
        scene = _draft(data, msg)
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
        **_DRAFT,
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
        scene = _draft(data, msg)
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
