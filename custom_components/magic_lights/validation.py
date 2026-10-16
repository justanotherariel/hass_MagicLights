"""Validation of stored config, scenes and presets."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
import dataclasses
from typing import Any

import voluptuous as vol

from .const import DEFAULT_TRANSITION, OPTION_MANUAL, OPTION_OFF
from .models import (
    Config,
    EffectLookup,
    Layer,
    Scene,
    Settings,
    TargetId,
    TargetOptions,
    Zone,
    available_scenes,
)

RESERVED_NAMES = frozenset({OPTION_OFF.casefold(), OPTION_MANUAL.casefold()})

_ID = vol.All(str, vol.Match(r"^[a-z0-9_]{1,64}$"))
_SCENE_ID = vol.All(str, vol.Match(r"^s_[0-9a-f]{8}$"))
_NAME = vol.All(str, vol.Strip, vol.Length(min=1, max=64))
_TARGET_ID = vol.All(str, vol.Match(r"^(room|zone):\S+$"))
_LIGHT_ID = vol.All(str, vol.Match(r"^light\.[a-z0-9_]+$"))
_ROOM_REF = vol.All(str, vol.Match(r"^room:\S+$"))
_TRANSITION = vol.All(vol.Coerce(float), vol.Range(min=0, max=300))


def _unique(values: list[Any]) -> list[Any]:
    if len(set(values)) != len(values):
        raise vol.Invalid("duplicate entries")
    return values


LAYER_SCHEMA = vol.Schema(
    {
        vol.Required("lights"): vol.Any(
            "all",
            vol.All([vol.Any(_LIGHT_ID, _ROOM_REF)], vol.Length(min=1), _unique),
        ),
        vol.Required("effect"): vol.All(str, vol.Length(min=1)),
        vol.Optional("params", default=dict): dict,
    }
)

SCENE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): _SCENE_ID,
        vol.Required("name"): _NAME,
        vol.Optional("icon", default=None): vol.Maybe(str),
        vol.Optional("scope", default=list): vol.All([_TARGET_ID], _unique),
        vol.Optional("preset", default=None): vol.Maybe(_ID),
        vol.Required("layers"): vol.All([LAYER_SCHEMA], vol.Length(min=1)),
        vol.Optional("others", default="off"): vol.In(["off", "keep"]),
        vol.Optional("brightness", default=100): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=100), round
        ),
        vol.Optional("transition", default=None): vol.Maybe(_TRANSITION),
    }
)

PRESET_SCHEMA = SCENE_SCHEMA.extend(
    {
        vol.Required("id"): _ID,
        vol.Required("category"): vol.In(["white", "color", "dynamic", "adaptive"]),
        vol.Required("default_visible"): bool,
    }
)

ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): _ID,
        vol.Required("name"): _NAME,
        vol.Optional("icon", default=None): vol.Maybe(str),
        vol.Optional("rooms", default=list): vol.All([str], _unique),
        vol.Optional("lights", default=list): vol.All([_LIGHT_ID], _unique),
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional("excluded_lights", default=list): vol.All([_LIGHT_ID], _unique),
        vol.Optional("extra_lights", default=list): vol.All([_LIGHT_ID], _unique),
        vol.Optional("visible_scenes", default=None): vol.Maybe(
            vol.All([str], _unique)
        ),
        vol.Optional("default_scene", default=None): vol.Maybe(str),
        vol.Optional("reclaim_on_turn_on", default=True): bool,
        vol.Optional("detect_changes", default=True): bool,
    }
)

BUS_LIMITS_SCHEMA = vol.Schema(
    {
        vol.Optional("rate"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1000)),
        vol.Optional("burst"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
        vol.Optional("min_interval"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=60)
        ),
    }
)

SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional("transition", default=DEFAULT_TRANSITION): _TRANSITION,
        vol.Optional("default_scene", default=None): vol.Maybe(str),
        vol.Optional("native_effects", default=True): bool,
        vol.Optional("buses", default=dict): {str: BUS_LIMITS_SCHEMA},
        vol.Optional("light_buses", default=dict): {_LIGHT_ID: str},
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("zones", default=dict): {str: ZONE_SCHEMA},
        vol.Optional("targets", default=dict): {_TARGET_ID: OPTIONS_SCHEMA},
        vol.Optional("scenes", default=dict): {str: SCENE_SCHEMA},
        vol.Optional("settings", default=dict): SETTINGS_SCHEMA,
    }
)


class ConfigError(vol.Invalid):
    """Config validation failed; `code` is a stable error key."""

    def __init__(self, code: str, message: str, path: Sequence[Any] = ()) -> None:
        super().__init__(message, list(path))
        self._config_code = code

    # A property, because probatio's Invalid (HA 2026.10+) has a read-only `code`.
    @property
    def code(self) -> str:
        return self._config_code


def _apply(schema: vol.Schema, data: Any, path: Sequence[Any]) -> dict[str, Any]:
    try:
        return schema(data)
    except vol.Invalid as err:
        raise ConfigError("invalid", err.msg, [*path, *err.path]) from err


def _build_scene(
    raw: Mapping[str, Any], registry: EffectLookup, path: Sequence[Any]
) -> Scene:
    if raw["name"].casefold() in RESERVED_NAMES:
        raise ConfigError(
            "reserved_name", f"'{raw['name']}' is a reserved name", [*path, "name"]
        )
    layers = []
    for i, layer in enumerate(raw["layers"]):
        effect = registry.get(layer["effect"])
        if effect is None:
            raise ConfigError(
                "unknown_effect",
                f"unknown effect '{layer['effect']}'",
                [*path, "layers", i, "effect"],
            )
        try:
            params = effect.validate(layer["params"])
        except vol.Invalid as err:
            raise ConfigError(
                "invalid_params",
                f"{layer['effect']}: {err.msg}",
                [*path, "layers", i, "params", *err.path],
            ) from err
        layers.append(Layer.from_dict({**layer, "params": params}))
    return dataclasses.replace(Scene.from_dict(raw), layers=tuple(layers))


def _check_refs(
    scene: Scene,
    presets: Mapping[str, Scene] | None,
    targets: Collection[TargetId] | None,
    path: Sequence[Any],
    stored: Scene | None = None,
) -> None:
    if presets is not None and scene.preset is not None and scene.preset not in presets:
        raise ConfigError(
            "unknown_preset", f"unknown preset '{scene.preset}'", [*path, "preset"]
        )
    if targets is not None:
        # An edit may keep the entry of a target that went away (a deleted area).
        kept = () if stored is None else stored.scope
        for i, target_id in enumerate(scene.scope):
            if target_id not in targets and target_id not in kept:
                raise ConfigError(
                    "unknown_target",
                    f"unknown target '{target_id}'",
                    [*path, "scope", i],
                )


def _check_copies(scene: Scene, scenes: Mapping[str, Scene]) -> None:
    """Reject a second copy of a preset where another copy already applies."""
    if scene.preset is None:
        return
    for other in scenes.values():
        if other.id == scene.id or other.preset != scene.preset:
            continue
        if not scene.scope and not other.scope:
            where = "everywhere"
        elif shared := [t for t in scene.scope if t in other.scope]:
            where = f"in {', '.join(shared)}"
        else:
            continue
        raise ConfigError(
            "duplicate_copy",
            f"another customised copy ('{other.name}') already applies {where}",
            ["scenes", scene.id, "scope"],
        )


def _check_names(
    scenes: Mapping[str, Scene],
    presets: Mapping[str, Scene],
    only: str | None,
    targets: Collection[TargetId] | None,
) -> None:
    # Availability only depends on whether a target is named in a scope, so
    # every unnamed target behaves like the "*" stand-in. A target that went
    # away shows no scenes, so its scope entries cannot clash.
    named = {t for s in scenes.values() for t in s.scope}
    if targets is not None:
        named &= set(targets)
    for target_id in [*sorted(named), "*"]:
        seen: dict[str, Scene] = {}
        for scene in available_scenes(target_id, scenes, presets).values():
            other = seen.setdefault(scene.name.casefold(), scene)
            if other is scene or (
                only is not None and only not in (scene.id, other.id)
            ):
                continue
            offender = scene if only in (None, scene.id) else other
            where = "everywhere" if target_id == "*" else target_id
            raise ConfigError(
                "duplicate_name",
                f"a scene named '{scene.name}' already exists ({where})",
                ["scenes", offender.id, "name"],
            )


def validate_scene(
    data: Mapping[str, Any],
    registry: EffectLookup,
    config: Config,
    *,
    presets: Mapping[str, Scene] | None = None,
    targets: Collection[TargetId] | None = None,
) -> Scene:
    """Validate one stored scene for saving into `config`."""
    path = ["scenes", data.get("id")]
    raw = _apply(SCENE_SCHEMA, data, path)
    scene = _build_scene(raw, registry, path)
    _check_refs(scene, presets, targets, path, config.scenes.get(scene.id))
    _check_copies(scene, config.scenes)
    _check_names({**config.scenes, scene.id: scene}, presets or {}, scene.id, targets)
    return scene


def validate_preset(data: Mapping[str, Any], registry: EffectLookup) -> Scene:
    """Validate one entry of presets/scenes.json."""
    path = ["presets", data.get("id")]
    raw = _apply(PRESET_SCHEMA, data, path)
    return dataclasses.replace(_build_scene(raw, registry, path), builtin=True)


def validate(
    data: Mapping[str, Any],
    registry: EffectLookup,
    *,
    presets: Mapping[str, Scene] | None = None,
    targets: Collection[TargetId] | None = None,
) -> Config:
    """Validate a whole ConfigV2 dict; params come back with defaults filled."""
    raw = _apply(CONFIG_SCHEMA, data, [])
    for key, zone in raw["zones"].items():
        if zone["id"] != key:
            raise ConfigError(
                "invalid", "zone id does not match its key", ["zones", key]
            )
    scenes = {}
    for key, scene_data in raw["scenes"].items():
        path = ["scenes", key]
        if scene_data["id"] != key:
            raise ConfigError("invalid", "scene id does not match its key", path)
        scenes[key] = _build_scene(scene_data, registry, path)
        _check_refs(scenes[key], presets, targets, path)
    _check_names(scenes, presets or {}, None, targets)
    return Config(
        zones={k: Zone.from_dict(v) for k, v in raw["zones"].items()},
        targets={k: TargetOptions.from_dict(v) for k, v in raw["targets"].items()},
        scenes=scenes,
        settings=Settings.from_dict(raw["settings"]),
    )
