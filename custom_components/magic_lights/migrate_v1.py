"""Pure conversion of a Magic Lights v1 configuration into the v2 shape."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import hashlib
import json
import math
from typing import Any

import voluptuous as vol

from . import color as color_util
from .models import Color, Settings, new_zone_id
from .validation import RESERVED_NAMES

V1_EFFECT_SCHEMA = {
    vol.Required("name"): str,
    vol.Optional("conf"): vol.Any(dict, list),
}
V1_MODIFIER_SCHEMA = {
    vol.Required("name"): str,
    vol.Optional("conf"): vol.Any(dict, list),
    vol.Optional("min_update_interval"): int,
}
V1_PIPE_SCHEMA = {
    vol.Required("entities"): [str],
    vol.Required("effect"): V1_EFFECT_SCHEMA,
    vol.Optional("modifiers"): [V1_MODIFIER_SCHEMA],
}
V1_ZONE_SCHEMA = vol.Schema(
    {
        vol.Optional("entities"): [str],
        vol.Optional("areas"): [str],
        vol.Optional("groups"): {vol.Optional(str): [str]},
        vol.Required("scenes"): {vol.Required(str): [V1_PIPE_SCHEMA]},
    }
)


def _none_to_empty(value: Any) -> Any:
    return {} if value is None else value


# The v1 YAML (`magic_lights:` in configuration.yaml), entities and areas optional.
V1_CONFIG_SCHEMA = vol.Schema(
    vol.All(_none_to_empty, {vol.Optional(str): V1_ZONE_SCHEMA})
)

# v1 transitions presets: (colour space, colours).
_TRANSITIONS: dict[str, tuple[str, list[Any]]] = {
    "pink-glow": (
        "HEX-sRGB255",
        ["#ffe4ed", "#f0839a", "#ce505c", "#01555f", "#0d2c2f"],
    ),
    "saturated-sunset": (
        "HEX-sRGB255",
        ["#240002", "#6f0100", "#a53005", "#d97d0c", "#fec135"],
    ),
    "tokyo": ("xyY1", [[0.1562, 0.1648, 1], [0.49, 0.316, 1]]),
}
_SPACES = frozenset(
    {"HEX-sRGB255", "sRGB255", "sRGB1", "xyY1", "xyY100", "XYZ1", "XYZ100"}
)
_MAX_PALETTE = 9
_MAX_NAME = 64


def migrate_v1(
    data: Mapping[str, Any],
    area_lights: Mapping[str, frozenset[str]],
    *,
    presets: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Convert v1 YAML or a v1 store payload into a ConfigV2 dict plus notes.

    `area_lights` maps every existing area id to its light entity ids.
    `presets` are the built-in scenes as stored. A v1 static preset becomes
    the first layer of the built-in scene with its name. Scenes are renamed
    when a built-in scene has their name, the name is reserved, or it repeats
    within a target.
    """
    notes: list[str] = []
    zones: dict[str, dict[str, Any]] = {}
    scenes: dict[str, dict[str, Any]] = {}
    builtins = {preset["name"].casefold(): preset["layers"][0] for preset in presets}
    taken = RESERVED_NAMES | set(builtins)
    names: dict[str, set[str]] = {}
    for zone_name, raw in _unwrap(data or {}).items():
        name = str(zone_name)
        try:
            zone = V1_ZONE_SCHEMA(raw)
        except vol.Invalid as err:
            notes.append(f"Zone '{name}' could not be read and was skipped: {err}")
            continue
        target_id = _migrate_zone(name, zone, area_lights, zones, notes)
        groups = zone.get("groups") or {}
        for scene_name, pipes in zone["scenes"].items():
            if scene_name.casefold() in ("off", "custom"):
                continue
            label = f"{name} / {scene_name}"
            layers = [
                layer
                for pipe in pipes
                for layer in _migrate_pipe(pipe, groups, builtins, label, notes)
            ]
            if not layers:
                notes.append(f"Scene '{label}' had nothing to convert and was dropped.")
                continue
            scene_id = _scene_id(name, scene_name, scenes)
            base = scene_name.strip()[:_MAX_NAME] or "Scene"
            new_name = _free_name(base, names.setdefault(target_id, set(taken)))
            if new_name != base:
                notes.append(
                    f"Scene '{label}' was renamed to '{new_name}': a built-in or"
                    " another scene already has its name."
                )
            scenes[scene_id] = {
                "id": scene_id,
                "name": new_name,
                "icon": None,
                "scope": [target_id],
                "preset": None,
                "layers": layers,
                "others": "off",
                "brightness": 100,
                "transition": None,
            }
    settings = {**Settings().to_dict(), "migration_notes": list(notes)}
    return {
        "zones": zones,
        "targets": {},
        "scenes": scenes,
        "settings": settings,
    }, notes


def _unwrap(data: Mapping[str, Any]) -> Mapping[str, Any]:
    # The v1 store saved {"config": {...}}; a zone named "config" has "scenes".
    inner = data.get("config")
    if len(data) == 1 and isinstance(inner, Mapping) and "scenes" not in inner:
        return inner
    return data


def _expand(ids: Sequence[str], groups: Mapping[str, Sequence[str]]) -> list[str]:
    return [member for i in ids for member in groups.get(i, [i])]


def _migrate_zone(
    name: str,
    zone: Mapping[str, Any],
    area_lights: Mapping[str, frozenset[str]],
    zones: dict[str, dict[str, Any]],
    notes: list[str],
) -> str:
    """Return the target id the zone's scenes are scoped to."""
    entities = set(_expand(zone.get("entities") or [], zone.get("groups") or {}))
    lights = {e for e in entities if e.startswith("light.") and e != "light.all"}
    if others := sorted(entities - lights - {"light.all"}):
        notes.append(
            f"Zone '{name}': {', '.join(others)} are not lights and were dropped."
        )
    areas = list(dict.fromkeys(zone.get("areas") or []))
    if missing := [a for a in areas if a not in area_lights]:
        notes.append(f"Zone '{name}': area {', '.join(missing)} no longer exists.")
    rooms = [a for a in areas if a in area_lights]
    in_rooms = frozenset().union(*(area_lights[a] for a in rooms))
    light_set = lights | in_rooms
    matches = [
        a for a, members in area_lights.items() if members and members == light_set
    ]
    # A second (even empty) room keeps the zone: it gains that room's lights later.
    if len(matches) == 1 and set(rooms) <= {matches[0]}:
        return f"room:{matches[0]}"
    zone_id = new_zone_id(name, zones)
    zones[zone_id] = {
        "id": zone_id,
        "name": name.strip()[:_MAX_NAME] or zone_id,
        "icon": None,
        "rooms": rooms,
        "lights": sorted(lights - in_rooms),
    }
    return f"zone:{zone_id}"


def _free_name(name: str, taken: set[str]) -> str:
    """Return `name`, or the first free "<name> (v1 n)"; claim it in `taken`."""
    candidate = name
    n = 1
    while candidate.casefold() in taken:
        suffix = " (v1)" if n == 1 else f" (v1 {n})"
        candidate = name[: _MAX_NAME - len(suffix)].rstrip() + suffix
        n += 1
    taken.add(candidate.casefold())
    return candidate


def _scene_id(zone: str, scene: str, taken: Mapping[str, Any]) -> str:
    # Deterministic, so importing the same YAML twice yields the same ids.
    n = 0
    while True:
        digest = hashlib.sha256(f"{zone}|{scene}|{n}".encode()).hexdigest()
        if (scene_id := f"s_{digest[:8]}") not in taken:
            return scene_id
        n += 1


def _layer(
    lights: str | list[str], effect: str, params: dict[str, Any]
) -> dict[str, Any]:
    return {"lights": lights, "effect": effect, "params": params}


def _migrate_pipe(
    pipe: Mapping[str, Any],
    groups: Mapping[str, Sequence[str]],
    builtins: Mapping[str, Mapping[str, Any]],
    label: str,
    notes: list[str],
) -> list[dict[str, Any]]:
    if pipe.get("modifiers"):
        notes.append(f"Scene '{label}': modifiers are not supported and were dropped.")
    entities = _expand(pipe["entities"], groups)
    lights: str | list[str] = (
        "all"
        if "light.all" in entities
        else sorted({e for e in entities if e.startswith("light.")})
    )
    effect = pipe["effect"]["name"].casefold()
    conf = pipe["effect"].get("conf")
    if not lights:
        notes.append(
            f"Scene '{label}': a {effect} layer had no lights and was dropped."
        )
        return []
    try:
        if effect == "off":
            return [_layer(lights, "off", {})]
        if effect == "static":
            return _static(lights, conf, groups, builtins, label, notes)
        if effect == "transitions":
            return [_transitions(lights, conf, label, notes)]
    except (ValueError, TypeError, KeyError, IndexError) as err:
        notes.append(
            f"Scene '{label}': a {effect} layer could not be converted ({err}) and was dropped."
        )
        return []
    notes.append(
        f"Scene '{label}': effect '{effect}' has no equivalent; its layer was dropped."
    )
    return []


def _static(
    lights: str | list[str],
    conf: Any,
    groups: Mapping[str, Sequence[str]],
    builtins: Mapping[str, Mapping[str, Any]],
    label: str,
    notes: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(conf, Mapping):
        raise TypeError("no configuration")
    if "preset" in conf:
        name = str(conf["preset"])
        if (layer := builtins.get(name.casefold())) is not None:
            return [_layer(lights, layer["effect"], copy.deepcopy(layer["params"]))]
        notes.append(f"Scene '{label}': unknown preset '{name}' became white 2700 K.")
        return [_layer(lights, "white", {"kelvin": 2700, "brightness": 100})]
    # One layer per distinct per-entity value.
    by_value: dict[str, tuple[Any, set[str]]] = {}
    for key, value in conf.items():
        _, members = by_value.setdefault(
            json.dumps(value, sort_keys=True), (value, set())
        )
        members.update(e for e in _expand([key], groups) if e.startswith("light."))
    layers = []
    for value, members in by_value.values():
        if not members:
            continue
        if value == "off":
            layers.append(_layer(sorted(members), "off", {}))
        elif isinstance(value, Mapping):
            layers.append(
                _layer(sorted(members), "static", _static_params(value, label, notes))
            )
        else:
            raise ValueError(f"unsupported value {value!r}")
    return layers


def _pct(value: float) -> int:
    return min(100, max(1, round(value)))


def _static_params(
    data: Mapping[str, Any], label: str, notes: list[str]
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if "brightness_pct" in data:
        params["brightness"] = _pct(data["brightness_pct"])
    elif "brightness" in data:
        params["brightness"] = _pct(data["brightness"] / 255 * 100)
    color: Any = None
    if "color_temp_kelvin" in data:
        color = {"kelvin": data["color_temp_kelvin"]}
    elif "color_temp" in data:
        color = {"kelvin": 1_000_000 / data["color_temp"]}
    elif "xy_color" in data:
        color = {"xy": data["xy_color"]}
    elif "hs_color" in data:
        color = {"xy": list(color_util.xy_from_hs(data["hs_color"]))}
    elif "rgb_color" in data:
        color = {"rgb": data["rgb_color"]}
    if color is None:
        notes.append(f"Scene '{label}': lights without a colour now get 2700 K.")
    else:
        params["color"] = Color.parse(color).to_dict()
    return params


def _transitions(
    lights: str | list[str], conf: Any, label: str, notes: list[str]
) -> dict[str, Any]:
    if not isinstance(conf, Mapping):
        raise TypeError("no configuration")
    if "preset" in conf:
        preset = str(conf["preset"])
        if preset not in _TRANSITIONS:
            raise ValueError(f"unknown preset '{preset}'")
        space, colors = _TRANSITIONS[preset]
        if preset == "tokyo":
            notes.append(
                f"Scene '{label}': the built-in scene Tokyo is a closer match."
            )
    else:
        space, colors = conf["color_space"], conf["colors"]
    if space not in _SPACES:
        raise ValueError(f"colour space '{space}' is not supported")
    palette = []
    for value in colors:
        try:
            palette.append(_v1_color(value, space).to_dict())
        except ValueError, TypeError, IndexError, ArithmeticError:
            continue
    if not palette:
        raise ValueError("no usable colours")
    if len(palette) > _MAX_PALETTE:
        notes.append(
            f"Scene '{label}': only the first {_MAX_PALETTE} colours were kept."
        )
        palette = palette[:_MAX_PALETTE]
    period = max(float(conf.get("transition_time", 60)), 1e-3)
    speed = min(1.0, max(0.05, math.log(120 / period) / math.log(60)))
    return _layer(
        lights,
        "palette",
        {"palette": palette, "motion": "dynamic", "speed": round(speed, 2)},
    )


def _v1_color(value: Any, space: str) -> Color:
    if space == "HEX-sRGB255":
        return Color.parse(value)
    if space == "sRGB255":
        return Color.parse({"rgb": list(value[:3])})
    if space == "sRGB1":
        return Color.parse({"rgb": [v * 255 for v in value[:3]]})
    if space in ("xyY1", "xyY100"):
        return Color.parse({"xy": list(value[:2])})
    x, y, z = value[:3]  # XYZ1 / XYZ100
    return Color.parse({"xy": [x / (x + y + z), y / (x + y + z)]})
