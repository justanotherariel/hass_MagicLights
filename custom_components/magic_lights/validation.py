"""Validation of stored config, scenes and presets."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
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


# The targets a scene has lights in.
type Fits = Callable[[Scene], Collection[TargetId]]


def _check_preset(
    scene: Scene, presets: Mapping[str, Scene] | None, path: Sequence[Any]
) -> None:
    if presets is None or scene.preset is None:
        return
    if (preset := presets.get(scene.preset)) is None:
        raise ConfigError(
            "unknown_preset", f"unknown preset '{scene.preset}'", [*path, "preset"]
        )
    # Automations and select states know an edited preset by the preset's name.
    if scene.name != preset.name:
        raise ConfigError(
            "preset_name",
            f"an edited preset keeps its name '{preset.name}'",
            [*path, "name"],
        )


def _check_copies(scene: Scene, scenes: Mapping[str, Scene]) -> None:
    """Reject a second edit of a preset: one applies to every target."""
    if scene.preset is None:
        return
    for other in scenes.values():
        if other.id != scene.id and other.preset == scene.preset:
            raise ConfigError(
                "duplicate_copy",
                f"'{other.name}' is already edited ({other.id})",
                ["scenes", scene.id, "preset"],
            )


def _check_names(
    scenes: Mapping[str, Scene],
    presets: Mapping[str, Scene],
    only: str | None,
    fits: Fits | None,
) -> None:
    """Names are unique among the scenes of each target; presets count everywhere.

    Without `fits`, user scenes are only checked against the presets.
    """

    def clash(offender: Scene, where: str) -> ConfigError:
        return ConfigError(
            "duplicate_name",
            f"a scene named '{offender.name}' already exists ({where})",
            ["scenes", offender.id, "name"],
        )

    preset_names = {p.name.casefold() for p in presets.values()}
    users = [s for s in scenes.values() if s.preset not in presets]
    for scene in users:
        if only in (None, scene.id) and scene.name.casefold() in preset_names:
            raise clash(scene, "a preset")
    if fits is None:
        return
    seen: dict[tuple[TargetId, str], Scene] = {}
    for scene in users:
        for target_id in sorted(fits(scene)):
            other = seen.setdefault((target_id, scene.name.casefold()), scene)
            if other is scene or only not in (None, scene.id, other.id):
                continue
            raise clash(scene if only in (None, scene.id) else other, target_id)


def validate_scene(
    data: Mapping[str, Any],
    registry: EffectLookup,
    config: Config,
    *,
    presets: Mapping[str, Scene] | None = None,
    fits: Fits | None = None,
) -> Scene:
    """Validate one stored scene for saving into `config`."""
    path = ["scenes", data.get("id")]
    raw = _apply(SCENE_SCHEMA, data, path)
    scene = _build_scene(raw, registry, path)
    _check_preset(scene, presets, path)
    _check_copies(scene, config.scenes)
    _check_names({**config.scenes, scene.id: scene}, presets or {}, scene.id, fits)
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
    fits: Fits | None = None,
) -> Config:
    """Validate a whole stored config; params come back with defaults filled."""
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
        _check_preset(scenes[key], presets, path)
        _check_copies(scenes[key], scenes)
    _check_names(scenes, presets or {}, None, fits)
    return Config(
        zones={k: Zone.from_dict(v) for k, v in raw["zones"].items()},
        targets={k: TargetOptions.from_dict(v) for k, v in raw["targets"].items()},
        scenes=scenes,
        settings=Settings.from_dict(raw["settings"]),
    )
