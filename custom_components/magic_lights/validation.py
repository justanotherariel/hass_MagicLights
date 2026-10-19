"""Validation of the stored config, the built-in parts and drafts."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

import voluptuous as vol

from .const import DEFAULT_TRANSITION, OPTION_MANUAL, OPTION_OFF
from .models import (
    Config,
    EffectLookup,
    Part,
    Settings,
    StoredScene,
    TargetId,
    TargetOptions,
    Zone,
    all_parts,
    is_user_part,
)

RESERVED_NAMES = frozenset({OPTION_OFF.casefold(), OPTION_MANUAL.casefold()})

_ID = vol.All(str, vol.Match(r"^[a-z0-9_]{1,64}$"))
_SCENE_ID = vol.All(str, vol.Match(r"^s_[0-9a-f]{8}$"))
# A user part's "p_" + 8 hex or a built-in's slug, never shaped like a scene id.
_PART_ID = vol.All(str, vol.Match(r"^(?!s_[0-9a-f]{8}$)[a-z0-9_]{1,64}$"))
_BUILTIN_ID = vol.All(str, vol.Match(r"^(?![sp]_[0-9a-f]{8}$)[a-z0-9_]{1,64}$"))
_NAME = vol.All(str, vol.Strip, vol.Length(min=1, max=64))
_TARGET_ID = vol.All(str, vol.Match(r"^(room|zone):\S+$"))
_LIGHT_ID = vol.All(str, vol.Match(r"^light\.[a-z0-9_]+$"))
_ROOM_REF = vol.All(str, vol.Match(r"^room:\S+$"))
_TRANSITION = vol.All(vol.Coerce(float), vol.Range(min=0, max=300))


def _unique(values: list[Any]) -> list[Any]:
    if len(set(values)) != len(values):
        raise vol.Invalid("duplicate entries")
    return values


_LIGHTS = vol.Any(
    "all", vol.All([vol.Any(_LIGHT_ID, _ROOM_REF)], vol.Length(min=1), _unique)
)
_EFFECT = vol.All(str, vol.Length(min=1))

PART_SCHEMA = vol.Schema(
    {
        vol.Required("id"): _PART_ID,
        vol.Required("name"): _NAME,
        vol.Required("effect"): _EFFECT,
        vol.Optional("params", default=dict): dict,
    }
)

BUILTIN_PART_SCHEMA = PART_SCHEMA.extend(
    {
        vol.Required("id"): _BUILTIN_ID,
        vol.Required("icon"): vol.All(str, vol.Match(r"^mdi:[a-z0-9-]+$")),
        vol.Required("category"): vol.In(["white", "color", "dynamic", "adaptive"]),
    }
)

STORED_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): _SCENE_ID,
        vol.Required("name"): _NAME,
        vol.Optional("icon", default=None): vol.Maybe(str),
        vol.Required("layers"): vol.All(
            [{vol.Required("lights"): _LIGHTS, vol.Required("part"): _PART_ID}],
            vol.Length(min=1),
        ),
        vol.Optional("others", default="off"): vol.In(["off", "keep"]),
        vol.Optional("transition", default=None): vol.Maybe(_TRANSITION),
    }
)

# The part editor tries its part as a scene under the part's id.
DRAFT_SCENE_SCHEMA = STORED_SCENE_SCHEMA.extend(
    {vol.Required("id"): vol.Any(_SCENE_ID, _PART_ID)}
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
        # One of this target's scenes; a room without one inherits.
        vol.Optional("default_scene", default=None): vol.Maybe(
            vol.Any("none", _SCENE_ID)
        ),
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
        vol.Optional("default_scene", default=None): vol.Maybe(_PART_ID),
        vol.Optional("native_effects", default=True): bool,
        vol.Optional("buses", default=dict): {str: BUS_LIMITS_SCHEMA},
        vol.Optional("light_buses", default=dict): {_LIGHT_ID: str},
    }
)

# Entries are checked one by one, so that errors carry their ids in the path.
CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("zones", default=dict): {str: ZONE_SCHEMA},
        vol.Optional("targets", default=dict): {_TARGET_ID: OPTIONS_SCHEMA},
        vol.Optional("parts", default=dict): {str: dict},
        vol.Optional("scenes", default=dict): {_TARGET_ID: [dict]},
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


def _check_reserved(name: str, path: Sequence[Any]) -> None:
    if name.casefold() in RESERVED_NAMES:
        raise ConfigError("reserved_name", f"'{name}' is a reserved name", path)


def _params(
    raw: Mapping[str, Any], registry: EffectLookup, path: Sequence[Any]
) -> dict[str, Any]:
    """Return the params of a part checked by its effect, defaults filled."""
    if (effect := registry.get(raw["effect"])) is None:
        raise ConfigError(
            "unknown_effect", f"unknown effect '{raw['effect']}'", [*path, "effect"]
        )
    try:
        return effect.validate(raw["params"])
    except vol.Invalid as err:
        raise ConfigError(
            "invalid_params",
            f"{raw['effect']}: {err.msg}",
            [*path, "params", *err.path],
        ) from err


def _touched(only: Collection[str] | None, *entity_ids: str) -> bool:
    return only is None or any(entity_id in only for entity_id in entity_ids)


def _saved(only: Collection[str] | None, entity_id: str) -> bool:
    """Return whether a save names the entity, so drift around it is its error."""
    return only is not None and entity_id in only


def _build_part(
    raw: Mapping[str, Any], registry: EffectLookup, path: Sequence[Any]
) -> Part:
    return Part.from_dict({**raw, "params": _params(raw, registry, path)})


def validate_builtin_part(data: Mapping[str, Any], registry: EffectLookup) -> Part:
    """Validate one entry of presets/parts.json."""
    path = ["parts", data.get("id")]
    return _build_part(_apply(BUILTIN_PART_SCHEMA, data, path), registry, path)


def _part(
    key: str,
    data: Any,
    registry: EffectLookup,
    *,
    checked: bool = True,
) -> Part:
    """Validate a stored part; unchecked, its params stay as stored."""
    path = ["parts", key]
    raw = _apply(PART_SCHEMA, data, path)
    part = _build_part(raw, registry, path) if checked else Part.from_dict(raw)
    # A part plays under its own name when it is the switch-on default.
    _check_reserved(part.name, [*path, "name"])
    if part.id != key:
        raise ConfigError("invalid", "part id does not match its key", path)
    return part


def _stored_parts(
    raw: Mapping[str, Any], registry: EffectLookup, only: Collection[str] | None
) -> dict[str, Part]:
    # An effect a plugin no longer provides must not block saving anything else.
    return {
        key: _part(key, data, registry, checked=_touched(only, key))
        for key, data in raw.items()
    }


def _stored_scenes(
    raw: Mapping[TargetId, Sequence[Mapping[str, Any]]],
) -> dict[TargetId, tuple[StoredScene, ...]]:
    seen: set[str] = set()
    scenes = {}
    for target_id, items in raw.items():
        built = []
        for data in items:
            path = ["scenes", data.get("id")]
            scene = StoredScene.from_dict(_apply(STORED_SCENE_SCHEMA, data, path))
            _check_reserved(scene.name, [*path, "name"])
            # Runtime state and services find a scene by its id alone.
            if scene.id in seen:
                raise ConfigError("invalid", "a scene id is used twice", path)
            seen.add(scene.id)
            built.append(scene)
        scenes[target_id] = tuple(built)
    return scenes


def _name_clash(kind: str, offender: Part | StoredScene, where: str) -> ConfigError:
    path = ["parts" if kind == "part" else "scenes", offender.id, "name"]
    return ConfigError(
        "duplicate_name",
        f"a {kind} named '{offender.name}' already exists ({where})",
        path,
    )


def _check_unique_names(
    kind: str,
    items: Iterable[Part | StoredScene],
    where: str,
    only: Collection[str] | None,
) -> None:
    seen: dict[str, Part | StoredScene] = {}
    for item in items:
        other = seen.setdefault(item.name.casefold(), item)
        if other is not item and _touched(only, item.id, other.id):
            offender = item if _touched(only, item.id) else other
            raise _name_clash(kind, offender, where)


def _check_part_names(
    parts: Mapping[str, Part],
    builtins: Mapping[str, Part],
    only: Collection[str] | None,
) -> None:
    """Check that part names are unique over all parts, built-ins included.

    Automations and select states know a built-in by its name, so its edit
    keeps it.
    """
    for part in parts.values():
        builtin = builtins.get(part.id)
        if builtin is None and not is_user_part(part.id) and _saved(only, part.id):
            raise ConfigError(
                "unknown_part",
                f"there is no built-in part '{part.id}'",
                ["parts", part.id, "id"],
            )
        if (
            builtin is not None
            and part.name != builtin.name
            and _touched(only, part.id)
        ):
            raise ConfigError(
                "builtin_name",
                f"an edited built-in part keeps its name '{builtin.name}'",
                ["parts", part.id, "name"],
            )
    builtin_names = {b.name.casefold() for b in builtins.values()}
    users = [p for p in parts.values() if is_user_part(p.id)]
    for part in users:
        if part.name.casefold() in builtin_names and _touched(only, part.id):
            raise _name_clash("part", part, "a built-in part")
    _check_unique_names("part", users, "your parts", only)


def _check_layers(
    scene: StoredScene, parts: Collection[str], rooms: Collection[str] | None
) -> None:
    """Check a scene's parts, and its room refs unless `rooms` is None."""
    for i, layer in enumerate(scene.layers):
        path = ["scenes", scene.id, "layers", i]
        if layer.part not in parts:
            raise ConfigError(
                "unknown_part", f"unknown part '{layer.part}'", [*path, "part"]
            )
        lights = () if layer.lights == "all" or rooms is None else layer.lights
        if stray := [x for x in lights if x.startswith("room:") and x not in rooms]:
            raise ConfigError(
                "unknown_target",
                f"'{stray[0]}' is not part of this target",
                [*path, "lights"],
            )


def _check_target_scenes(
    config: Config,
    target_id: TargetId,
    parts: Collection[str],
    live: Collection[TargetId] | None,
    only: Collection[str] | None,
) -> None:
    """Check that saved scenes have a live target, known parts and its rooms.

    Unsaved scenes may have drifted from all three: dormant, a layer dropped
    at resolve, a room ref that reaches nothing.
    """
    scenes = config.scenes[target_id]
    zone = (
        config.zones.get(target_id.removeprefix("zone:"))
        if target_id.startswith("zone:")
        else None
    )
    if live is not None:
        exists = target_id in live
    else:
        exists = zone is not None or target_id.startswith("room:")
    rooms = {f"room:{room}" for room in zone.rooms} if zone else {target_id}
    for scene in scenes:
        if not _saved(only, scene.id):
            continue
        if not exists:
            raise ConfigError(
                "unknown_target", f"unknown target '{target_id}'", ["scenes", scene.id]
            )
        _check_layers(scene, parts, rooms)
    _check_unique_names("scene", scenes, target_id, only)


def _check_defaults(config: Config, only: Collection[str] | None) -> None:
    """Check that a target's default is one of its scenes, the global one a part.

    A global default naming a built-in that left the catalogue means none.
    """
    for target_id, options in config.targets.items():
        default = options.default_scene
        if default in (None, "none") or not _touched(only, target_id):
            continue
        if default not in {s.id for s in config.scenes.get(target_id, ())}:
            raise ConfigError(
                "unknown_scene",
                f"'{default}' is not a scene of {target_id}",
                ["targets", target_id, "default_scene"],
            )
    default = config.settings.default_scene
    if (
        default is not None
        and is_user_part(default)
        and default not in config.parts
        and _touched(only, "settings")
    ):
        raise ConfigError(
            "unknown_part",
            f"unknown part '{default}'",
            ["settings", "default_scene"],
        )


def validate(
    data: Mapping[str, Any],
    registry: EffectLookup,
    builtins: Mapping[str, Part],
    *,
    only: Collection[str] | None = None,
    live: Collection[TargetId] | None = None,
) -> Config:
    """Validate a whole stored config; params come back with defaults filled.

    Shapes are checked everywhere, params of the parts a save names (None =
    all). The rules between entries count only where an entry in `only` takes
    part: the ids of saved parts, scenes and targets, or "settings". So a save
    never fails on state that drifted without one. A scene's target, parts and
    room refs drift, so they count only for a scene `only` names: the scenes
    of a target that is gone stay dormant (`live`: the target ids that exist;
    None takes every room as existing) and a layer whose part is missing is
    dropped at resolve.
    """
    raw = _apply(CONFIG_SCHEMA, data, [])
    for key, zone in raw["zones"].items():
        if zone["id"] != key:
            raise ConfigError(
                "invalid", "zone id does not match its key", ["zones", key]
            )
    config = Config(
        zones={k: Zone.from_dict(v) for k, v in raw["zones"].items()},
        targets={k: TargetOptions.from_dict(v) for k, v in raw["targets"].items()},
        parts=_stored_parts(raw["parts"], registry, only),
        scenes=_stored_scenes(raw["scenes"]),
        settings=Settings.from_dict(raw["settings"]),
    )
    _check_part_names(config.parts, builtins, only)
    known = all_parts(config.parts, builtins).keys()
    for target_id in config.scenes:
        _check_target_scenes(config, target_id, known, live, only)
    _check_defaults(config, only)
    return config


def validate_draft(
    scene: Mapping[str, Any],
    parts: Iterable[Mapping[str, Any]],
    registry: EffectLookup,
    known: Collection[str],
) -> tuple[StoredScene, dict[str, Part]]:
    """Validate a scene and its changed parts that are previewed or tried.

    Names are not checked against others: that happens on save. `known` are
    the part ids the draft may use besides its own.
    """
    drafts = {}
    for data in parts:
        part = _part(str(data.get("id")), data, registry)
        drafts[part.id] = part
    path = ["scenes", scene.get("id")]
    stored = StoredScene.from_dict(_apply(DRAFT_SCENE_SCHEMA, scene, path))
    _check_reserved(stored.name, [*path, "name"])
    _check_layers(stored, {*known, *drafts}, None)
    return stored, drafts
