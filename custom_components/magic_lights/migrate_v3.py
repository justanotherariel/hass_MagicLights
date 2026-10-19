"""Store v2 to v3: scenes lose their scope, and same-named room scenes become one."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
import copy
import dataclasses
import logging
from typing import Any

from .models import EffectLookup, Part, RuntimeV1

_LOGGER = logging.getLogger(__name__)

# The v2 default menu. The v2 panel wrote it into a menu on its first edit,
# so a menu holding all of it was not picked by hand.
OLD_DEFAULT_MENU = frozenset(
    {
        "energize",
        "concentrate",
        "read",
        "bright",
        "relax",
        "dimmed",
        "nightlight",
        "adaptive",
        "savanna_sunset",
        "tropical_twilight",
        "arctic_aurora",
        "spring_blossom",
        "tokyo",
        "blue_lagoon",
        "cozy_candle",
        "fireplace",
    }
)

type Raw = dict[str, Any]


def migrate(
    data: Mapping[str, Any],
    effects: EffectLookup,
    presets: Mapping[str, Part],
    areas: Mapping[str, Collection[str]],
) -> tuple[Raw, dict[str, str]]:
    """Return the v3 config and the ids of merged scenes mapped to the kept one.

    `presets` are the built-in parts, which v3 knew as presets. `areas` holds
    the light ids of each area, as the registries place them.
    """
    data = copy.deepcopy(dict(data))
    zones: Mapping[str, Raw] = data.get("zones") or {}
    targets: Mapping[str, Raw] = data.get("targets") or {}
    lights = target_lights(zones, targets, areas)
    scenes: dict[str, Raw] = {}
    gone: set[str] = set()
    for scene_id, scene in (data.get("scenes") or {}).items():
        if scene.get("preset") not in presets:
            # Ran as a user scene already: its preset left the catalogue.
            scene["preset"] = None
            if _within_scope(scene, zones, lights):
                scenes[scene_id] = scene
            else:
                gone.add(scene_id)
        elif (kept := _override(scene, presets[scene["preset"]])) is not None:
            scenes[scene_id] = kept
    scenes, remap = _merge_by_name(scenes, targets, effects)
    for scene in scenes.values():
        scene.pop("scope", None)
    data["scenes"] = scenes
    data["targets"] = {
        target_id: _remapped_options(options, remap, gone)
        for target_id, options in targets.items()
    }
    settings = data.get("settings") or {}
    if settings.get("default_scene") in remap:
        settings["default_scene"] = remap[settings["default_scene"]]
    elif settings.get("default_scene") in gone:
        settings["default_scene"] = None
    return data, remap


def remap_runtime(runtime: RuntimeV1, remap: Mapping[str, str]) -> RuntimeV1:
    """Point running activations and their salts at the merged scenes."""

    def salt(key: str) -> str:
        scene_id, _, target_id = key.partition("|")
        return f"{remap.get(scene_id, scene_id)}|{target_id}"

    return RuntimeV1(
        activations=tuple(
            dataclasses.replace(a, scene_id=remap.get(a.scene_id, a.scene_id))
            for a in runtime.activations
        ),
        salts={salt(key): value for key, value in runtime.salts.items()},
    )


def _scope_refs(scope: Sequence[str], zones: Mapping[str, Raw]) -> list[str]:
    """Layer lights that cover the scope's targets and nothing else."""
    refs: list[str] = []
    for target_id in scope:
        kind, _, key = target_id.partition(":")
        if kind == "room":
            refs.append(target_id)
        elif (zone := zones.get(key)) is not None:
            refs += [f"room:{area_id}" for area_id in zone.get("rooms") or ()]
            refs += zone.get("lights") or ()
    return list(dict.fromkeys(refs))


def target_lights(
    zones: Mapping[str, Raw],
    targets: Mapping[str, Raw],
    areas: Mapping[str, Collection[str]],
) -> dict[str, set[str]]:
    """Every light each target may hold: a room's area lights and extras, a zone's.

    Unlike LightIndex, a light HA no longer knows still counts, and options
    alone make a room.
    """
    room_ids = {
        *areas,
        *(t.removeprefix("room:") for t in targets if t.startswith("room:")),
    }
    out: dict[str, set[str]] = {}
    for area_id in room_ids:
        options = targets.get(f"room:{area_id}") or {}
        out[f"room:{area_id}"] = (
            set(areas.get(area_id, ())) | set(options.get("extra_lights") or ())
        ) - set(options.get("excluded_lights") or ())
    for zone_id, zone in zones.items():
        out[f"zone:{zone_id}"] = set(zone.get("lights") or ()).union(
            *(out.get(f"room:{area_id}", ()) for area_id in zone.get("rooms") or ())
        )
    return out


def _within_scope(
    scene: Raw, zones: Mapping[str, Raw], lights: Mapping[str, set[str]]
) -> bool:
    """Give "all" layers the scope's lights and drop the lights out of its reach.

    Unscoped, those would light up, and fit, the rooms they belong to.
    False when no layer is left.
    """
    if not (scope := scene.get("scope")):
        return True
    refs = _scope_refs(scope, zones)
    held = set().union(*(lights.get(target_id, ()) for target_id in scope))
    reached = held | set(refs)
    dropped: list[str] = []
    layers = []
    for layer in scene["layers"]:
        items = layer["lights"]
        if items == "all":
            items = refs or []
            dropped += [] if refs else ["all lights"]
        kept: list[str] = []
        for item in items:
            if item in reached:
                kept.append(item)
            # v2 ran a room ref on the lights of that room the scope held
            # (extra lights, a zone's own lights).
            elif borrowed := sorted(lights.get(item, set()) & held):
                kept += borrowed
            else:
                dropped.append(item)
        if items := list(dict.fromkeys(kept)):
            layers.append(layer | {"lights": items})
    if dropped:
        _LOGGER.warning(
            "Dropping %s of '%s': %s does not hold them",
            ", ".join(dropped),
            scene["name"],
            ", ".join(scope),
        )
    named = {
        x for layer in layers for x in layer["lights"] if not x.startswith("room:")
    }
    if spread := sorted(
        target_id
        for target_id, held_there in lights.items()
        if target_id.startswith("room:")
        and target_id not in scope
        and named & held_there
    ):
        # A light lent through extra lights or a zone belongs to its own room too.
        _LOGGER.warning(
            "'%s' also fits %s now: some of its lights belong there",
            scene["name"],
            ", ".join(spread),
        )
    scene["layers"] = layers
    return bool(layers)


def _override(scene: Raw, preset: Part) -> Raw | None:
    """Keep the everywhere edit of a preset; drop one scoped to some targets."""
    if scene.get("scope"):
        _LOGGER.warning(
            "Dropping the edit of preset '%s' for %s: an edited preset applies"
            " to every room now (it stays in magic_lights.v2.bak)",
            preset.name,
            ", ".join(scene["scope"]),
        )
        return None
    if scene["name"] != preset.name:
        _LOGGER.warning(
            "Renaming the edit of preset '%s' from '%s': it keeps the preset's name",
            preset.name,
            scene["name"],
        )
        scene["name"] = preset.name
    return scene


def _merge_by_name(
    scenes: Mapping[str, Raw], targets: Mapping[str, Raw], effects: EffectLookup
) -> tuple[dict[str, Raw], dict[str, str]]:
    groups: dict[str, list[Raw]] = {}
    for scene in scenes.values():
        if scene["preset"] is None:
            groups.setdefault(scene["name"].casefold(), []).append(scene)
    # Every member's id -> the merged scene, which takes the first one's place.
    merged: dict[str, Raw] = {}
    for members in groups.values():
        if len(members) < 2 or not _mergeable(members, targets):
            continue
        kept = min(members, key=lambda s: s["id"])
        for member in members:
            if member["name"] != kept["name"]:
                _LOGGER.warning(
                    "'%s' of %s becomes '%s': its select shows that name",
                    member["name"],
                    member["scope"][0],
                    kept["name"],
                )
        scene = {**kept, "layers": _merge_layers(members, effects)}
        merged.update((m["id"], scene) for m in members)
    out: dict[str, Raw] = {}
    for scene_id, scene in scenes.items():
        scene = merged.get(scene_id, scene)
        out.setdefault(scene["id"], scene)
    remap = {old: scene["id"] for old, scene in merged.items() if old != scene["id"]}
    return out, remap


def _explicit_ids(scene: Raw) -> set[str]:
    return {
        light
        for layer in scene["layers"]
        if layer["lights"] != "all"
        for light in layer["lights"]
        if light.startswith("light.")
    }


def _mergeable(members: Sequence[Raw], targets: Mapping[str, Raw]) -> bool:
    """One room each, the same scene settings, disjoint lights, no borrowed lights."""

    def settings(scene: Raw) -> tuple[Any, ...]:
        return (
            scene.get("others", "off"),
            scene.get("brightness", 100),
            scene.get("transition"),
        )

    if any(
        len(scope := m.get("scope") or ()) != 1 or not scope[0].startswith("room:")
        for m in members
    ):
        return False
    if len({settings(m) for m in members}) != 1:
        return False
    ids = [_explicit_ids(m) for m in members]
    if sum(map(len, ids)) != len(set().union(*ids)):
        return False
    return not any(
        (targets.get(m["scope"][0]) or {}).get("extra_lights") for m in members
    )


def layer_key(layer: Raw, effects: EffectLookup) -> tuple[str, Any]:
    """Return a layer's look: its effect and params, defaults filled if known."""
    params = layer.get("params") or {}
    if (effect := effects.get(layer["effect"])) is not None:
        params = effect.validate(params)
    return layer["effect"], params


def _disjoint(a: Any, b: Any) -> bool:
    """Only two explicit light id lists without a common id are known apart."""

    def ids(lights: Any) -> set[str] | None:
        if lights == "all" or not all(x.startswith("light.") for x in lights):
            return None
        return set(lights)

    ids_a, ids_b = ids(a), ids(b)
    return ids_a is not None and ids_b is not None and not ids_a & ids_b


def _merge_layers(members: Sequence[Raw], effects: EffectLookup) -> list[Raw]:
    """Union equal layers while every member keeps the order of its own layers.

    A member's layer joins the first equal merged layer that lies after each
    of the member's earlier layers it overlaps and that holds none of its
    layers yet; otherwise it is appended.
    """
    # The most shared first layer comes first; sorted() keeps stored order on ties.
    firsts = [layer_key(m["layers"][0], effects) for m in members]
    ordered = [
        members[i]
        for i in sorted(range(len(members)), key=lambda i: -firsts.count(firsts[i]))
    ]
    merged: list[tuple[tuple[str, Any], Raw]] = []
    for member in ordered:
        placed: list[tuple[int, Raw]] = []
        for layer in member["layers"]:
            key = layer_key(layer, effects)
            floor = max(
                (
                    pos
                    for pos, earlier in placed
                    if not _disjoint(earlier["lights"], layer["lights"])
                ),
                default=-1,
            )
            used = {pos for pos, _ in placed}
            pos = next(
                (
                    i
                    for i, (k, _) in enumerate(merged)
                    if i > floor and i not in used and k == key
                ),
                None,
            )
            if pos is None:
                merged.append((key, copy.deepcopy(layer)))
                pos = len(merged) - 1
            else:
                target = merged[pos][1]
                target["lights"] = list(
                    dict.fromkeys([*target["lights"], *layer["lights"]])
                )
            placed.append((pos, layer))
    return [layer for _, layer in merged]


def _remapped_options(
    options: Raw, remap: Mapping[str, str], gone: Collection[str]
) -> Raw:
    if (visible := options.get("visible_scenes")) is not None:
        ids = list(dict.fromkeys(remap.get(s, s) for s in visible if s not in gone))
        if set(ids) >= OLD_DEFAULT_MENU:
            ids = [s for s in ids if s not in OLD_DEFAULT_MENU]
        options["visible_scenes"] = ids
    if options.get("default_scene") in remap:
        options["default_scene"] = remap[options["default_scene"]]
    elif options.get("default_scene") in gone:
        options["default_scene"] = None
    return options
