"""Store v3 to v4: looks become shared parts, and each target gets its own scenes.

Every target keeps what its scenes do there, by name and by id (through the
remap), its menu and its switch-on default. tests/unit/test_migrate_v4.py
proves it against a model of v3.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
import copy
import dataclasses
from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any

from .migrate_v3 import layer_key, target_lights
from .models import EffectLookup, Part, RuntimeV1, TargetId

_LOGGER = logging.getLogger(__name__)

type Raw = dict[str, Any]
# Every area of the registry -> its name and the light ids the registries
# place there, if any: an area of extra lights alone is still a room.
type Areas = Mapping[str, tuple[str, Collection[str]]]
# (v3 id as runtime state knows it, target) -> the target's v4 scene.
type Remap = dict[tuple[str, TargetId], str]

_NAME_MAX = 64
# A v3 scene with these runs as its only layer would: a part scene.
_PLAIN = {"others": "off", "brightness": 100, "transition": None}


def scene_id(old_id: str, target_id: TargetId) -> str:
    """Return the id of a v3 scene's copy in a target, unless that id collided."""
    return _digest("s_", f"{old_id}|{target_id}")


def migrate(
    data: Mapping[str, Any],
    effects: EffectLookup,
    builtins: Mapping[str, Part],
    areas: Areas,
) -> tuple[Raw, Remap]:
    """Return the v4 config and where each target's v3 scenes went."""
    run = _Migration(copy.deepcopy(dict(data)), effects, builtins, areas)
    return run.result(), run.remap


def remap_runtime(runtime: RuntimeV1, remap: Remap) -> RuntimeV1:
    """Point running activations and their salts at their target's new scenes."""

    def salt(key: str) -> str:
        old, _, target_id = key.partition("|")
        return f"{remap.get((old, target_id), old)}|{target_id}"

    return RuntimeV1(
        activations=tuple(
            dataclasses.replace(
                a, scene_id=remap.get((a.scene_id, a.target), a.scene_id)
            )
            for a in runtime.activations
        ),
        salts={salt(key): value for key, value in runtime.salts.items()},
    )


def _digest(prefix: str, text: str) -> str:
    return prefix + hashlib.sha256(text.encode()).hexdigest()[:8]


def _fresh(prefix: str, text: str, taken: set[str]) -> str:
    """Hash `text` into an id outside `taken`; a collision re-hashes with "|2" on."""
    candidate, n = _digest(prefix, text), 2
    while candidate in taken:
        candidate, n = _digest(prefix, f"{text}|{n}"), n + 1
    taken.add(candidate)
    return candidate


def _free_name(base: str, taken: set[str], suffix: str = "") -> str:
    """Return `base` with `suffix`, then a number, as the first name not taken."""
    n = 1
    while True:
        tail = suffix if n == 1 else f"{suffix} {n}"
        candidate = (base[: max(1, _NAME_MAX - len(tail))].rstrip() + tail)[:_NAME_MAX]
        if candidate.casefold() not in taken:
            taken.add(candidate.casefold())
            return candidate
        n += 1


def _target_order(target_id: TargetId) -> tuple[bool, str]:
    return target_id.startswith("zone:"), target_id


@dataclass(slots=True)
class _Look:
    """A user part in the making: one look of the scenes with one name."""

    id: str
    name: str
    effect: str
    params: Raw
    first: tuple[int, int]  # (v3 scene, layer) of its first use
    targets: set[TargetId] = field(default_factory=set)


class _Migration:
    def __init__(
        self,
        data: Raw,
        effects: EffectLookup,
        builtins: Mapping[str, Part],
        areas: Areas,
    ) -> None:
        self._data = data
        self._effects = effects
        self._builtins = builtins
        self._zones: Mapping[str, Raw] = data.get("zones") or {}
        self._options: dict[TargetId, Raw] = data.get("targets") or {}
        # As in LightIndex, a room is an existing area; options alone make none.
        held = target_lights(
            self._zones,
            {
                t: o
                for t, o in self._options.items()
                if not t.startswith("room:") or t.removeprefix("room:") in areas
            },
            {a: lights for a, (_, lights) in areas.items()},
        )
        # Targets without lights do not exist, so their v3 scenes ran nowhere.
        self._lights = {t: held[t] for t in sorted(held, key=_target_order) if held[t]}
        self._names = {f"room:{a}": name for a, (name, _) in areas.items()} | {
            f"zone:{z}": zone.get("name") or z for z, zone in self._zones.items()
        }
        stored: Mapping[str, Raw] = data.get("scenes") or {}
        # v3 keeps one edit per preset; the last one stored wins, as there.
        edits = {s["preset"]: s for s in stored.values() if s.get("preset") in builtins}
        self._edits, self._odd = self._builtin_edits(edits)
        self._available = self._v3_available(stored, edits)
        self._rank = {pid: i for i, pid in enumerate(self._available)}
        self._taken: set[str] = set()
        self._looks: dict[tuple[str, str, str], _Look] = {}
        self._scenes: dict[TargetId, list[Raw]] = {t: [] for t in self._lights}
        self._warned: set[str] = set()
        self.remap: Remap = {}
        self._warn_unplaced(stored)
        for target_id in self._lights:
            self._add_menu(target_id)
        self._warn_dropped()
        self._migrate_defaults()

    # ---- v3 as it ran

    def _warn_unplaced(self, stored: Mapping[str, Raw]) -> None:
        """Report the lights of scenes that no target holds any more."""
        placed = set().union(*self._lights.values())
        for scene in stored.values():
            named = {
                x
                for layer in scene["layers"]
                if layer["lights"] != "all"
                for x in layer["lights"]
                if x.startswith("light.")
            }
            if missing := sorted(named - placed):
                _LOGGER.warning(
                    "No room holds %s of '%s'", ", ".join(missing), scene["name"]
                )

    def _warn_dropped(self) -> None:
        """Report the user scenes that reach no target: no target gets them."""
        for pid, scene in self._available.items():
            if pid not in self._builtins and not any(
                self._layers_in(scene, t) for t in self._lights
            ):
                _LOGGER.warning("'%s' reaches no room and is dropped", scene["name"])

    def _builtin_edits(
        self, edits: Mapping[str, Raw]
    ) -> tuple[dict[str, Raw], set[str]]:
        """Return the edits that become built-in edits, and the presets edited otherwise.

        Only an edit shaped like a built-in part fits into one; any other keeps
        its look in the targets whose menu shows it.
        """
        parts: dict[str, Raw] = {}
        odd: set[str] = set()
        for pid, scene in edits.items():
            builtin = self._builtins[pid]
            (layer, *more) = scene["layers"]
            plain = all(scene.get(k, v) == v for k, v in _PLAIN.items())
            if more or layer["lights"] != "all" or not plain:
                odd.add(pid)
                _LOGGER.warning(
                    "The edit of '%s' becomes a scene of the rooms whose menu"
                    " shows it; elsewhere, and called by its id, the built-in runs",
                    builtin.name,
                )
                continue
            if scene.get("icon") != builtin.icon:
                # Parts have no icons: only the menus' scenes keep it.
                _LOGGER.warning(
                    "The edited icon of '%s' stays only on the scenes of the menus"
                    " that show it",
                    builtin.name,
                )
            if layer_key(layer, self._effects) != (builtin.effect, builtin.params):
                parts[pid] = {
                    "id": pid,
                    "name": builtin.name,
                    "effect": layer["effect"],
                    "params": copy.deepcopy(layer.get("params") or {}),
                }
        return parts, odd

    def _v3_available(
        self, stored: Mapping[str, Raw], edits: Mapping[str, Raw]
    ) -> dict[str, Raw]:
        """v3's scenes by public id: presets, their edit in place, then user scenes."""
        out: dict[str, Raw] = {
            pid: edits.get(pid)
            or {"id": pid, "name": part.name, "layers": [{"lights": "all"}]}
            for pid, part in self._builtins.items()
        }
        out.update(
            (s["id"], s)
            for s in stored.values()
            if s.get("preset") not in self._builtins
        )
        return out

    def _reach(self, items: Any, target_id: TargetId) -> set[str]:
        """Return the target's lights a layer reaches, as LightIndex.expand does."""
        members = self._lights[target_id]
        if items == "all":
            return set(members)
        out: set[str] = set()
        for item in items:
            out |= self._lights.get(item, set()) if item.startswith("room:") else {item}
        return out & members

    def _layers_in(self, scene: Raw, target_id: TargetId) -> list[int]:
        return [
            i
            for i, layer in enumerate(scene["layers"])
            if self._reach(layer["lights"], target_id)
        ]

    def _fitting(self, target_id: TargetId) -> dict[str, Raw]:
        """Return the scenes that fit by public id; of a name, the one find_scene ran."""
        fitting: dict[str, Raw] = {}
        names: dict[str, str] = {}
        for pid, scene in self._available.items():
            if not self._layers_in(scene, target_id):
                continue
            if (kept := names.setdefault(scene["name"].casefold(), pid)) == pid:
                fitting[pid] = scene
            else:
                _LOGGER.warning(
                    "'%s' (%s) is left out of %s: '%s' (%s) has its name there",
                    scene["name"],
                    pid,
                    target_id,
                    self._available[kept]["name"],
                    kept,
                )
        return fitting

    # ---- scenes

    def _add_menu(self, target_id: TargetId) -> None:
        """Give the target its v3 menu, then the user scenes it left out."""
        fitting = self._fitting(target_id)
        users = [pid for pid in fitting if pid not in self._builtins]
        order = (self._options.get(target_id) or {}).get("visible_scenes")
        menu = users if order is None else [pid for pid in order if pid in fitting]
        for pid in menu:
            self._add(target_id, pid)
        for pid in users:
            if pid not in menu:
                _LOGGER.warning(
                    "'%s' joins the menu of %s: it could run there by name",
                    fitting[pid]["name"],
                    target_id,
                )
                self._add(target_id, pid)

    def _add(self, target_id: TargetId, pid: str, *, plain: bool = False) -> str:
        """Add the target's scene of a v3 public id; `plain` takes the preset unedited."""
        if pid in self._builtins and (plain or pid not in self._odd):
            builtin = self._builtins[pid]
            # An edit's icon, as v3's menus showed it.
            icon = self._available[pid].get("icon", builtin.icon)
            scene = {
                "name": builtin.name,
                "icon": builtin.icon if plain else icon,
                "layers": [{"lights": "all", "part": pid}],
                "others": "off",
                "transition": None,
            }
        else:
            scene = self._copy(target_id, pid)
        new_id = _fresh("s_", f"{pid}|{target_id}", self._taken)
        self._scenes[target_id].append({"id": new_id, **scene})
        self.remap[(pid, target_id)] = new_id
        return new_id

    def _copy(self, target_id: TargetId, pid: str) -> Raw:
        """Return a v3 scene as the target's own: its layers there, same lights."""
        raw = self._available[pid]
        if raw.get("brightness", 100) != 100 and pid not in self._warned:
            self._warned.add(pid)
            _LOGGER.warning(
                "'%s' loses its overall brightness of %s %%: scenes have none",
                raw["name"],
                raw["brightness"],
            )
        layers = [
            {
                "lights": self._slice(raw["layers"][i]["lights"], target_id),
                "part": self._part(
                    raw["name"], raw["layers"][i], target_id, (self._rank[pid], i)
                ),
            }
            for i in self._layers_in(raw, target_id)
        ]
        return {
            "name": raw["name"],
            "icon": raw.get("icon"),
            "layers": layers,
            "others": raw.get("others", "off"),
            "transition": raw.get("transition"),
        }

    def _slice(self, items: Any, target_id: TargetId) -> Any:
        """Return the layer's lights in the target, reaching what they reached."""
        if items == "all" or target_id in items:
            return "all"
        reached = self._reach(items, target_id)
        if not target_id.startswith("zone:"):
            return sorted(reached)
        # A zone's scene follows its rooms; lights reached otherwise are named.
        zone = self._zones[target_id.removeprefix("zone:")]
        rooms = {f"room:{area_id}" for area_id in zone.get("rooms") or ()}
        refs = [x for x in items if x in rooms]
        covered = set().union(*(self._lights.get(ref, set()) for ref in refs))
        return [*refs, *sorted(reached - covered)]

    def _part(
        self, name: str, layer: Raw, target_id: TargetId, position: tuple[int, int]
    ) -> str:
        """Return the id of the part for a layer's look in scenes called `name`."""
        effect, params = layer_key(layer, self._effects)
        key = (name.casefold(), effect, json.dumps(params, sort_keys=True))
        if (look := self._looks.get(key)) is None:
            look = self._looks[key] = _Look(
                id=_fresh("p_", json.dumps(key), self._taken),
                name=name,
                effect=layer["effect"],
                params=copy.deepcopy(layer.get("params") or {}),
                first=position,
            )
        look.first = min(look.first, position)
        look.targets.add(target_id)
        return look.id

    # ---- defaults

    def _migrate_defaults(self) -> None:
        for target_id, options in self._options.items():
            default = options.get("default_scene")
            if default in (None, "none"):
                continue
            if target_id in self._lights:
                options["default_scene"] = self._scene_of(target_id, default)
            else:
                _LOGGER.warning(
                    "%s has no lights: its switch-on scene '%s' is dropped",
                    target_id,
                    default,
                )
                options["default_scene"] = None
        settings = self._data.setdefault("settings", {})
        default = settings.get("default_scene")
        if default in self._odd:
            # The edit runs where a menu shows it, as that room's own scene.
            for target_id in self._inheriting():
                if (new_id := self.remap.get((default, target_id))) is not None:
                    self._options.setdefault(target_id, {})["default_scene"] = new_id
            return
        if default is None or default in self._builtins:
            return
        settings["default_scene"] = None
        if default not in self._available:
            _LOGGER.warning("The switch-on scene '%s' no longer exists", default)
            return
        # Only a scene of its own can be a target's default; rooms inherited it.
        for target_id in self._inheriting():
            self._options.setdefault(target_id, {})["default_scene"] = self._scene_of(
                target_id, default
            )

    def _inheriting(self) -> list[TargetId]:
        """Return the rooms that start the global switch-on scene."""
        return [
            t
            for t in self._lights
            if t.startswith("room:")
            and (self._options.get(t) or {}).get("default_scene") is None
        ]

    def _scene_of(self, target_id: TargetId, pid: str) -> str:
        """Return the target's scene of a v3 default, or "none" where v3 ran nothing."""
        if (new_id := self.remap.get((pid, target_id))) is not None:
            return new_id
        fits = pid in self._available and self._layers_in(
            self._available[pid], target_id
        )
        if pid in self._builtins:
            return self._add(target_id, pid, plain=True) if fits else "none"
        if fits:
            # Fits, but another scene has its name there.
            _LOGGER.warning(
                "%s no longer switches on with '%s'",
                target_id,
                self._available[pid]["name"],
            )
        elif pid not in self._available:
            _LOGGER.warning(
                "The switch-on scene '%s' of %s no longer exists", pid, target_id
            )
        return "none"

    # ---- result

    def _parts(self) -> dict[str, Raw]:
        """Name the looks: a scene name's first look gets the name, others a suffix.

        A later look used in one target only is named after it: "Hell · Ankleide".
        """
        taken = {part.name.casefold() for part in self._builtins.values()}
        bases: dict[str, str] = {}
        out = dict(self._edits)
        for look in sorted(self._looks.values(), key=lambda look: look.first):
            suffix = ""
            if (key := look.name.casefold()) not in bases:
                bases[key] = look.name
            elif len(look.targets) == 1:
                (target_id,) = look.targets
                suffix = f" · {self._names.get(target_id, target_id)}"
            out[look.id] = {
                "id": look.id,
                "name": _free_name(bases[key], taken, suffix),
                "effect": look.effect,
                "params": look.params,
            }
        return out

    def result(self) -> Raw:
        targets = {}
        for target_id, options in self._options.items():
            options.pop("visible_scenes", None)
            if options:
                targets[target_id] = options
        return {
            "zones": self._zones,
            "targets": targets,
            "parts": self._parts(),
            "scenes": {t: scenes for t, scenes in self._scenes.items() if scenes},
            "settings": self._data.get("settings") or {},
        }
