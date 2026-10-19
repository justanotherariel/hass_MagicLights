"""Store v3 -> v4: every target keeps what its scenes do, by name and by id."""

from __future__ import annotations

from collections.abc import Mapping
import copy
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest import mock

from custom_components.magic_lights import migrate_v4, store
from custom_components.magic_lights.engine import build_coordinator
from custom_components.magic_lights.engine.activation import plan_runs
from custom_components.magic_lights.migrate_v4 import (
    Areas,
    Remap,
    migrate,
    remap_runtime,
    scene_id,
)
from custom_components.magic_lights.models import (
    ActivationSnapshot,
    Config,
    RuntimeV1,
    Scene,
    Target,
    TargetOptions,
    all_parts,
    is_user_part,
)
from custom_components.magic_lights.validation import validate
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    UnsupportedStorageVersionError,
)
from homeassistant.helpers.storage import Store

from . import test_migrate_v3
from .fakes import (
    FakeDriver,
    FakeHost,
    FakeLightIndex,
    FakeRuntimeStore,
    ShellHass,
    make_light,
    make_room,
    make_zone,
)
from .v3_model import (
    BUILTINS,
    REGISTRY,
    V3Scene,
    available as v3_available,
    default as v3_default,
    find as v3_find,
    fits,
    fitting as v3_fitting,
    menu_of,
    resolve as v3_resolve,
    scenes_for as v3_scenes_for,
)

LOGGER = "custom_components.magic_lights.migrate_v4"

# Set to the output of scripts/export-store.py to check a real home before
# deploying.
STORE_ENV = "MAGIC_LIGHTS_V3_STORE"

type Raw = dict[str, Any]
# A lookup: the scene found (also one that does not fit) and the error.
type Found = tuple[Scene | V3Scene | None, str | None]

SUN = {
    "min_brightness": 40,
    "max_brightness": 100,
    "min_kelvin": 2200,
    "max_kelvin": 5000,
    "brightness_mode": "tanh",
    "ramp_dark": 60,
    "ramp_light": 90,
    "min_sunrise": "06:00:00",
    "max_sunrise": "08:00:00",
    "min_sunset": "18:30:00",
    "max_sunset": "21:00:00",
}
SHELF = {**SUN, "min_brightness": 12, "max_brightness": 30}
WARM = {**SUN, "max_kelvin": 2200}
DIM = {"kelvin": 2725, "brightness": 30}
WARM_DIM = {"kelvin": 2200, "brightness": 30}
EMBER = {
    "palette": [{"xy": [0.561, 0.4042]}],
    "white": {"kelvin": 2000},
    "brightness": 1,
}


def lights(area: str, *names: str) -> list[str]:
    return [f"light.{area}_{name}" for name in names]


def layer(items: Any, effect: str, params: Mapping[str, Any]) -> Raw:
    return {"lights": items, "effect": effect, "params": dict(params)}


def scene(scene_id_: str, name: str, *layers: Raw, **kw: Any) -> Raw:
    return {
        "id": scene_id_,
        "name": name,
        "icon": None,
        "preset": None,
        "layers": list(layers),
        "others": "off",
        "brightness": 100,
        "transition": None,
        **kw,
    }


def stored(*scenes: Raw, **parts: Any) -> Raw:
    return {"zones": {}, "targets": {}, "settings": {}, **parts} | {
        "scenes": {s["id"]: s for s in scenes}
    }


def options(**kw: Any) -> Raw:
    return {
        "excluded_lights": [],
        "extra_lights": [],
        "visible_scenes": None,
        "default_scene": None,
        "reclaim_on_turn_on": True,
        "detect_changes": True,
        **kw,
    }


CEILINGS = lights("schlafzimmer", "deckenlicht_1", "deckenlicht_2", "deckenlicht_3")
HELL, GEDIMMT, NACHTLICHT, FILM = "s_12186051", "s_254d2fcd", "s_0a915dea", "s_946c66cd"

# The live home: 25 lights in 9 rooms, no zones. One light is in an area but
# excluded by the room's options.
HOME3_AREAS: dict[str, tuple[str, list[str]]] = {
    "abstellkammer": ("Abstellkammer", lights("abstellkammer", "lichtschalter_deckenlicht")),
    "ankleide": ("Ankleide", lights("ankleide", "led_streifen", "schranklicht")),
    "arbeitszimmer": (
        "Arbeitszimmer",
        [
            *lights("arbeitszimmer", "deckenlicht_1", "deckenlicht_2", "deckenlicht_3"),
            *lights("arbeitszimmer", "regallicht", "stehlampe", "bucherregallicht"),
            "light.ws_links_desk_lamp_light",
        ],
    ),
    "badezimmer": ("Badezimmer", lights("badezimmer", "lichtschalter_deckenlicht")),
    "flur": ("Flur", lights("flur", "deckenlicht_1", "deckenlicht_2")),
    "kuche": ("Küche", lights("kuche", "deckenleuchte", "deckenleuchte_hintergrund", "lichterkette")),
    "schlafzimmer": (
        "Schlafzimmer",
        [*CEILINGS, *lights("schlafzimmer", "nachttischlampe_links", "nachttischlampe_rechts")],
    ),
    "wc": ("WC", lights("wc", "deckenlicht")),
    "wohnzimmer": (
        "Wohnzimmer",
        lights("wohnzimmer", "deckenlicht_1", "deckenlicht_2", "regallicht", "lichterkette"),
    ),
}  # fmt: skip

# The live store as v3 left it: one scene per name; Ankleide's own looks are
# its layers.
HOME3 = stored(
    scene(
        HELL,
        "Hell",
        layer(
            [
                *CEILINGS,
                *lights("wohnzimmer", "deckenlicht_1", "deckenlicht_2", "lichterkette"),
                *lights("arbeitszimmer", "deckenlicht_1", "deckenlicht_2", "deckenlicht_3"),
                *lights("arbeitszimmer", "regallicht", "stehlampe"),
                *lights("kuche", "deckenleuchte", "deckenleuchte_hintergrund"),
                "room:flur",
                "room:wc",
                "room:badezimmer",
            ],
            "adaptive",
            SUN,
        ),
        layer(lights("wohnzimmer", "regallicht"), "adaptive", SHELF),
        layer(["room:ankleide"], "adaptive", WARM),
    ),
    scene(
        GEDIMMT,
        "Gedimmt",
        layer(
            [
                *lights("wohnzimmer", "lichterkette", "regallicht"),
                *lights("arbeitszimmer", "regallicht", "stehlampe"),
                *lights("kuche", "deckenleuchte_hintergrund", "lichterkette"),
                "room:flur",
                *lights("schlafzimmer", "nachttischlampe_links", "nachttischlampe_rechts"),
            ],
            "white",
            DIM,
        ),
        layer(CEILINGS, "palette", EMBER),
        layer(["room:ankleide"], "white", WARM_DIM),
    ),
    scene(
        NACHTLICHT,
        "Nachtlicht",
        layer(
            [
                *CEILINGS,
                *lights("arbeitszimmer", "regallicht", "stehlampe"),
                "room:flur",
                "room:wc",
            ],
            "palette",
            EMBER,
        ),
    ),
    scene(FILM, "Film", layer(lights("wohnzimmer", "regallicht"), "white", DIM)),
    targets={
        "room:arbeitszimmer": options(
            excluded_lights=["light.ws_links_desk_lamp_light"],
            visible_scenes=[HELL, GEDIMMT, NACHTLICHT, "adaptive_bright"],
        ),
        "room:ankleide": options(visible_scenes=[HELL, GEDIMMT], default_scene=HELL),
    },
    settings={
        "transition": 1.0,
        "default_scene": "adaptive",
        "native_effects": True,
        "buses": {},
        "light_buses": {},
    },
)  # fmt: skip

STARTED = datetime(2026, 10, 17, 13, 5, 22, tzinfo=UTC)


def running(
    target_id: str, scene_id_: str, areas: Areas, **kw: Any
) -> ActivationSnapshot:
    area_lights = areas[target_id.removeprefix("room:")][1]
    return ActivationSnapshot(
        f"a_{target_id}", target_id, scene_id_, STARTED, tuple(sorted(area_lights)),
        (), kw, False,
    )  # fmt: skip


HOME3_RUNTIME = RuntimeV1(
    tuple(
        running(f"room:{area}", HELL, HOME3_AREAS, **kw)
        for area, kw in (
            ("schlafzimmer", {}),
            ("kuche", {}),
            ("wohnzimmer", {}),
            ("flur", {"scale": 1.0}),
        )
    )
)

# Every rule at once: a zone, edits of presets, menus that leave scenes out,
# a name two scenes share, a borrowed light and one no room holds.
EDGE_AREAS: dict[str, tuple[str, list[str]]] = {
    "living": ("Living", ["light.living_1", "light.living_2", "light.shelf"]),
    "kitchen": ("Kitchen", ["light.kitchen_1", "light.kitchen_2"]),
    "hall": ("Hall", ["light.hall_1", "light.hall_2"]),
    "office": ("Office", ["light.office_1", "light.lamp"]),
}
TOKYO = dict(BUILTINS["tokyo"].params)
EDGE = stored(
    scene(
        "s_000000b3",
        "Tokyo",
        layer("all", "white", DIM),
        layer(["light.lamp"], "palette", TOKYO),
        preset="tokyo",
    ),
    scene(
        "s_000000a1",
        "Glow",
        layer(["room:living", "light.kitchen_1"], "white", DIM),
        layer(["light.shelf"], "palette", EMBER),
    ),
    scene(
        "s_000000a2",
        "Party",
        layer(
            ["room:living", "room:kitchen", "light.hall_1"], "white", {"kelvin": 4000}
        ),
        layer(["light.kitchen_1"], "white", DIM),
        transition=2.0,
    ),
    scene("s_000000a3", "Low", layer(["light.hall_1"], "white", DIM), brightness=50),
    scene("s_000000a4", "low", layer(["room:hall"], "palette", EMBER), others="keep"),
    scene(
        "s_000000b1",
        "Relax",
        layer("all", "white", {"kelvin": 2400, "brightness": 50}),
        preset="relax",
    ),
    scene(
        "s_000000b2",
        "Read",
        layer("all", BUILTINS["read"].effect, BUILTINS["read"].params),
        preset="read",
    ),
    scene("s_000000c1", "Mine", layer(["light.porch"], "white", DIM)),
    scene("s_000000c2", "Away", layer(["light.gone"], "white", DIM)),
    zones={
        "down": {
            "id": "down",
            "name": "Down",
            "icon": None,
            "rooms": ["living", "kitchen"],
            "lights": ["light.hall_1"],
        }
    },
    targets={
        "room:kitchen": options(
            extra_lights=["light.porch"],
            visible_scenes=["s_000000c1", "relax", "read"],
            default_scene="adaptive_bright",
        ),
        "room:office": options(
            visible_scenes=["tokyo", "s_000000a1"], default_scene="s_000000a2"
        ),
        "room:living": options(default_scene="s_000000c9"),
        "zone:down": options(
            visible_scenes=["s_000000a2", "nightlight"], default_scene="nightlight"
        ),
        "room:attic": options(default_scene="s_000000a1"),
    },
    settings={"transition": 1.0, "default_scene": "relax"},
)
EDGE_RUNTIME = RuntimeV1(
    (
        running("room:kitchen", "relax", EDGE_AREAS),
        running("room:hall", "s_000000a3", EDGE_AREAS),
        running("room:office", "tokyo", EDGE_AREAS),
        running("room:living", "bright", EDGE_AREAS),
        ActivationSnapshot(
            "a_down",
            "zone:down",
            "s_000000a2",
            STARTED,
            ("light.hall_1",),
            (),
            {},
            False,
        ),
    ),
    {"s_000000a2|zone:down": 2, "bright|room:living": 1},
)


def inheriting(data: Raw, default: str) -> Raw:
    """Return `data` with its rooms inheriting a global switch-on scene."""
    data = copy.deepcopy(data)
    for target_id, target in data["targets"].items():
        if target_id.startswith("room:"):
            target["default_scene"] = None
    data["settings"]["default_scene"] = default
    return data


def index_of(data: Mapping[str, Any], areas: Areas) -> FakeLightIndex:
    """Build the targets LightIndex makes of the areas and the stored options."""
    opts = {
        k: TargetOptions.from_dict(v) for k, v in (data.get("targets") or {}).items()
    }
    rooms = {}
    for area_id, (name, area_lights) in areas.items():
        target = opts.get(f"room:{area_id}", TargetOptions())
        members = (set(area_lights) | set(target.extra_lights)) - set(
            target.excluded_lights
        )
        rooms[area_id] = make_room(area_id, members, name=name, options=target)
    zones = [
        make_zone(
            zone_id,
            [rooms[a] for a in zone.get("rooms") or () if a in rooms],
            zone.get("lights") or (),
            name=zone["name"],
            options=opts.get(f"zone:{zone_id}"),
        )
        for zone_id, zone in (data.get("zones") or {}).items()
    ]
    every = {e for _, area_lights in areas.values() for e in area_lights}
    every |= {e for o in opts.values() for e in o.extra_lights}
    every |= {
        e
        for zone in (data.get("zones") or {}).values()
        for e in zone.get("lights") or ()
    }
    return FakeLightIndex(
        [make_light(e) for e in sorted(every)], [*rooms.values(), *zones]
    )


def live(index: FakeLightIndex) -> list[Target]:
    return [t for t in index.targets.values() if t.lights]


def runs(
    found: Scene | V3Scene, target: Target, index: FakeLightIndex
) -> dict[Any, Any]:
    """Map the lights each layer runs together to its look; uncovered ones off.

    Layers keyed by their lights, not their place: a palette deals its colours
    among exactly the lights of one layer.
    """
    plans, uncovered = plan_runs(found, target, target.lights, index)
    out: dict[Any, Any] = {p.members: (p.effect, dict(p.params)) for p in plans}
    if found.others == "off":
        out |= dict.fromkeys(uncovered, "off")
    return out | {"scene": (found.others, found.transition)}


# ---- v3 as it ran (v3_model), and the questions put to both


def queries(data: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every id and name v3 knows, as ("id" | "name", text), and unknown ones."""
    available = v3_available(data)
    edit_ids = [s["id"] for s in data["scenes"].values() if s.get("preset") in BUILTINS]
    names = sorted({s.name for s in available.values()})
    return [
        *(("id", x) for x in [*available, *edit_ids, "s_00000000"]),
        *(("name", n) for n in [*names, *(n.upper() for n in names), "Nope"]),
    ]


def public(data: Mapping[str, Any], scene_id_: str) -> str:
    """Return the id runtime state knows a scene by: an edit's is its preset's."""
    raw = data["scenes"].get(scene_id_) or {}
    return raw.get("preset") if raw.get("preset") in BUILTINS else scene_id_


def odd_edits(data: Mapping[str, Any]) -> set[str]:
    """Presets whose edit is not shaped like a built-in part."""
    return {
        s["preset"]
        for s in data["scenes"].values()
        if s.get("preset") in BUILTINS
        and (
            len(s["layers"]) != 1
            or s["layers"][0]["lights"] != "all"
            or (s.get("others", "off"), s.get("brightness", 100), s.get("transition"))
            != ("off", 100, None)
        )
    }


class V4:
    """The coordinator over a migrated config, answering as the proof asks."""

    def __init__(self, config: Config, index: FakeLightIndex) -> None:
        self.config = config
        self.coordinator = build_coordinator(
            FakeHost(),
            index,
            REGISTRY,
            {"ha": FakeDriver()},
            config,
            BUILTINS,
            FakeRuntimeStore(),
        )

    def scenes_for(self, target: Target) -> list[Scene]:
        return self.coordinator.scenes_for(target.id)

    def resolve_scene(self, target: Target, scene_id_: str) -> Scene | None:
        return self.coordinator.resolve_scene(target.id, scene_id_)

    def find(self, target: Target, query: str) -> Found:
        try:
            return self.coordinator.find_scene(target.id, query), None
        except ServiceValidationError as err:
            return None, err.translation_key

    def default(self, target: Target) -> Scene | None:
        """Return the scene a light switched on in the idle target starts."""
        coordinator = self.coordinator
        target = coordinator.targets[target.id]
        if (chosen := coordinator._default_scene(target)) is None:  # noqa: SLF001
            return None
        found = coordinator.resolve_scene(target.id, chosen)
        return found if found is not None and coordinator.fits(found, target) else None


class ProofCase(unittest.TestCase):
    maxDiff = None

    def migrate_logged(
        self, data: Mapping[str, Any], areas: Areas
    ) -> tuple[Raw, Remap, list[str]]:
        """Migrate; return the result, the remap and the warnings logged."""
        logger = logging.getLogger(LOGGER)
        with self.assertLogs(logger, "WARNING") as logs:
            result, remap = migrate(data, REGISTRY, BUILTINS, areas)
            logger.warning("end")
        return result, remap, logs.output[:-1]

    def proven(
        self, data: Mapping[str, Any], areas: Areas, runtime: RuntimeV1 = RuntimeV1()
    ) -> tuple[Raw, Remap, list[str]]:
        """Migrate and prove that no target runs, shows or starts anything else."""
        before = copy.deepcopy(dict(data))
        result, remap, logs = self.migrate_logged(data, areas)
        self.assertEqual(data, before, "the input is left alone")
        self.assertEqual(self.migrate_logged(data, areas), (result, remap, logs))
        self.assertEqual(json.loads(json.dumps(result)), result)
        config = validate(result, REGISTRY, BUILTINS)
        index = index_of(data, areas)
        v4 = V4(config, index_of(result, areas))
        self.assert_well_formed(config, index)
        for target in live(index):
            with self.subTest(target=target.id):
                self.assert_calls_kept(data, target, index, v4, remap)
                self.assert_menu_kept(data, target, index, v4)
                self.assert_default_kept(data, target, index, v4)
        self.assert_runtime_kept(data, runtime, remap, index, v4)
        return result, remap, logs

    def prove_home(self, home: Mapping[str, Any]) -> None:
        """Prove the migration on an export, every light of its scenes placed."""
        self.assertEqual(home["store"]["version"], 3, "an export of store v3")
        areas = {a: (v["name"], v["lights"]) for a, v in home["areas"].items()}
        runtime = RuntimeV1.from_dict((home.get("runtime") or {}).get("data") or {})
        _, _, logs = self.proven(home["store"]["data"], areas, runtime)
        self.assertEqual([line for line in logs if "No room holds" in line], [])

    def assert_well_formed(self, config: Config, index: FakeLightIndex) -> None:
        """Check that nothing validate tolerates as drift is written."""
        parts = all_parts(config.parts, BUILTINS)
        used: set[str] = set()
        ids: list[str] = []
        for target_id, scenes in config.scenes.items():
            self.assertTrue(index.targets[target_id].lights, target_id)
            names = [s.name.casefold() for s in scenes]
            self.assertEqual(len(names), len(set(names)), target_id)
            for found in scenes:
                ids.append(found.id)
                self.assertTrue(found.layers)
                for lay in found.layers:
                    self.assertIn(lay.part, parts)
                    used.add(lay.part)
        self.assertEqual(len(ids), len(set(ids)))
        for part_id in config.parts:
            self.assertTrue(is_user_part(part_id) or part_id in BUILTINS, part_id)
            if is_user_part(part_id):
                self.assertIn(part_id, used, "a look no scene uses")
        for target_id, opts in config.targets.items():
            own = {s.id for s in config.scenes.get(target_id, ())}
            self.assertIn(opts.default_scene, {None, "none", *own}, target_id)
        self.assertIn(config.settings.default_scene, {None, *parts})

    def assert_calls_kept(
        self,
        data: Mapping[str, Any],
        target: Target,
        index: FakeLightIndex,
        v4: V4,
        remap: Remap,
    ) -> None:
        """Check that a call by name or built-in id runs the same, or fails as it did.

        A v3 scene id is no call any more; the remap carries it, so the
        target's new id stands in for it.
        """
        menu = [s.public_id for s in v3_scenes_for(data, target, index)]
        fitting = v3_fitting(data, target, index)
        odd = odd_edits(data)

        def accepted(found: Scene | None) -> bool:
            # An odd edit stays only where its menu shows it; a scene hidden
            # by another of its name runs nowhere.
            if found is None:
                return False
            if found.preset in odd:
                return found.preset not in menu
            return (
                found.preset not in BUILTINS
                and fits(index, found, target)
                and found.public_id not in fitting
            )

        for kind, query in queries(data):
            old, error = v3_find(data, target, query, index)
            if kind == "id" and query not in BUILTINS:
                self.assertEqual(v4.find(target, query)[1], "unknown_scene", query)
                query = remap.get((public(data, query), target.id), public(data, query))
            elif kind == "id" and query in odd:
                continue  # by its id, an odd edit's preset runs as the built-in
            if accepted(old):
                continue
            new, new_error = v4.find(target, query)
            if error is not None:
                self.assertIsNotNone(new_error, query)
                if error == "unknown_scene":
                    self.assertEqual(new_error, error, query)
                continue
            self.assertIsNone(new_error, query)
            self.assertEqual(new.name, old.name, query)
            self.assertEqual(runs(new, target, index), runs(old, target, index), query)

    def assert_menu_kept(
        self, data: Mapping[str, Any], target: Target, index: FakeLightIndex, v4: V4
    ) -> None:
        """Check the menu: v3's, the scenes it left out, then a preset default.

        A preset default joins only where it ran, as a scene of its own.
        """
        names = [s.name for s in v3_scenes_for(data, target, index)]
        if menu_of(data, target.id) is not None:
            names += [
                s.name
                for pid, s in v3_fitting(data, target, index).items()
                if pid not in BUILTINS and s.name not in names
            ]
        default = target.options.default_scene
        if (
            default in BUILTINS
            and BUILTINS[default].name not in names
            and fits(index, v3_resolve(data, default), target)
        ):
            names.append(BUILTINS[default].name)
        shown = v4.scenes_for(target)
        self.assertEqual([s.name for s in shown], names)
        self.assertEqual(len(shown), len(v4.config.scenes.get(target.id, ())))

    def assert_default_kept(
        self, data: Mapping[str, Any], target: Target, index: FakeLightIndex, v4: V4
    ) -> None:
        old = v3_default(data, target, index)
        new = v4.default(target)
        default = target.options.default_scene
        if default is None and target.kind == "room":
            default = data["settings"].get("default_scene")
        menu = [s.public_id for s in v3_scenes_for(data, target, index)]
        # As for calls: an odd edit runs as the built-in where its menu does
        # not show it; a scene hidden by another of its name has none here.
        if (default in odd_edits(data) and default not in menu) or (
            old is not None
            and old.preset not in BUILTINS
            and old.public_id not in v3_fitting(data, target, index)
        ):
            return
        self.assertEqual(old is None, new is None)
        if old is not None:
            self.assertEqual(runs(new, target, index), runs(old, target, index))

    def assert_runtime_kept(
        self,
        data: Mapping[str, Any],
        runtime: RuntimeV1,
        remap: Remap,
        index: FakeLightIndex,
        v4: V4,
    ) -> None:
        """Every running scene goes on as it ran, also found without the remap."""
        moved = remap_runtime(runtime, remap)
        for old, new in zip(runtime.activations, moved.activations, strict=True):
            target = index.targets.get(old.target)
            ran = v3_resolve(data, old.scene_id)
            if target is None or ran is None or not fits(index, ran, target):
                continue
            with self.subTest(runtime=old.target):
                if (old.scene_id, old.target) in remap:
                    self.assertEqual(new.scene_id, scene_id(old.scene_id, old.target))
                menu = [s.public_id for s in v3_scenes_for(data, target, index)]
                # Where its menu does not show an odd edit the built-in runs; a
                # scene hidden by another of its name has none anywhere.
                if (ran.preset in odd_edits(data) and ran.preset not in menu) or (
                    new.scene_id == old.scene_id
                    and ran.public_id not in v3_fitting(data, target, index)
                ):
                    continue
                now = v4.resolve_scene(target, new.scene_id)
                self.assertEqual(runs(now, target, index), runs(ran, target, index))
        self.assertEqual(sorted(moved.salts.values()), sorted(runtime.salts.values()))


def described(result: Raw, target_id: str) -> list[tuple[str, list[tuple[Any, str]]]]:
    """Return a target's scenes as (name, [(lights, part name)]), lights short."""
    parts = {
        **{k: v.name for k, v in BUILTINS.items()},
        **{k: v["name"] for k, v in result["parts"].items()},
    }
    prefix = f"light.{target_id.removeprefix('room:')}_"

    def short(items: Any) -> Any:
        return items if items == "all" else tuple(x.removeprefix(prefix) for x in items)

    return [
        (s["name"], [(short(lay["lights"]), parts[lay["part"]]) for lay in s["layers"]])
        for s in result["scenes"].get(target_id, [])
    ]


class Home3Test(ProofCase):
    def test_the_live_home(self) -> None:
        result, remap, logs = self.proven(HOME3, HOME3_AREAS, HOME3_RUNTIME)
        self.assertEqual(logs, [])
        self.assertEqual(
            [p["name"] for p in result["parts"].values()],
            [
                "Hell",
                "Hell · Wohnzimmer",
                "Hell · Ankleide",
                "Gedimmt",
                "Gedimmt · Schlafzimmer",
                "Gedimmt · Ankleide",
                "Nachtlicht",
                "Film",
            ],
        )
        parts = {p["name"]: p for p in result["parts"].values()}
        self.assertEqual(parts["Hell · Ankleide"]["params"], WARM)
        self.assertEqual(parts["Gedimmt · Schlafzimmer"]["params"], EMBER)
        self.assertEqual(parts["Film"]["params"], DIM)
        self.assertNotIn("icon", json.dumps(result["parts"]))
        ceilings = ("deckenlicht_1", "deckenlicht_2", "deckenlicht_3")
        shelf, steh = "regallicht", "stehlampe"
        self.assertEqual(
            {t: described(result, t) for t in result["scenes"]},
            {
                "room:ankleide": [
                    ("Hell", [("all", "Hell · Ankleide")]),
                    ("Gedimmt", [("all", "Gedimmt · Ankleide")]),
                ],
                "room:arbeitszimmer": [
                    ("Hell", [((*ceilings, shelf, steh), "Hell")]),
                    ("Gedimmt", [((shelf, steh), "Gedimmt")]),
                    ("Nachtlicht", [((shelf, steh), "Nachtlicht")]),
                    ("Adaptive bright", [("all", "Adaptive bright")]),
                ],
                "room:badezimmer": [("Hell", [("all", "Hell")])],
                "room:flur": [
                    ("Hell", [("all", "Hell")]),
                    ("Gedimmt", [("all", "Gedimmt")]),
                    ("Nachtlicht", [("all", "Nachtlicht")]),
                ],
                "room:kuche": [
                    ("Hell", [(("deckenleuchte", "deckenleuchte_hintergrund"), "Hell")]),
                    ("Gedimmt", [(("deckenleuchte_hintergrund", "lichterkette"), "Gedimmt")]),
                ],
                "room:schlafzimmer": [
                    ("Hell", [(ceilings, "Hell")]),
                    (
                        "Gedimmt",
                        [
                            (("nachttischlampe_links", "nachttischlampe_rechts"), "Gedimmt"),
                            (ceilings, "Gedimmt · Schlafzimmer"),
                        ],
                    ),
                    ("Nachtlicht", [(ceilings, "Nachtlicht")]),
                ],
                "room:wc": [("Hell", [("all", "Hell")]), ("Nachtlicht", [("all", "Nachtlicht")])],
                "room:wohnzimmer": [
                    (
                        "Hell",
                        [
                            (("deckenlicht_1", "deckenlicht_2", "lichterkette"), "Hell"),
                            ((shelf,), "Hell · Wohnzimmer"),
                        ],
                    ),
                    ("Gedimmt", [(("lichterkette", shelf), "Gedimmt")]),
                    ("Film", [((shelf,), "Film")]),
                ],
            },
        )  # fmt: skip
        self.assertEqual(sum(map(len, result["scenes"].values())), 20)
        ankleide_hell = remap[(HELL, "room:ankleide")]
        self.assertEqual(
            result["targets"],
            {
                "room:arbeitszimmer": {
                    "excluded_lights": ["light.ws_links_desk_lamp_light"],
                    "extra_lights": [],
                    "default_scene": None,
                    "reclaim_on_turn_on": True,
                    "detect_changes": True,
                },
                "room:ankleide": {
                    "excluded_lights": [],
                    "extra_lights": [],
                    "default_scene": ankleide_hell,
                    "reclaim_on_turn_on": True,
                    "detect_changes": True,
                },
            },
        )
        self.assertEqual(result["settings"], HOME3["settings"])
        self.assertEqual(
            [a.scene_id for a in remap_runtime(HOME3_RUNTIME, remap).activations],
            [
                remap[(HELL, f"room:{area}")]
                for area in ("schlafzimmer", "kuche", "wohnzimmer", "flur")
            ],
        )

    def test_a_look_runs_as_the_room_scene_of_it(self) -> None:
        result, remap, _ = self.migrate_logged(HOME3, HOME3_AREAS)
        index = index_of(HOME3, HOME3_AREAS)
        v4 = V4(validate(result, REGISTRY, BUILTINS), index_of(result, HOME3_AREAS))
        office = index.targets["room:arbeitszimmer"]
        found, _ = v4.find(office, "adaptive_bright")
        self.assertEqual(found.id, remap[("adaptive_bright", office.id)])
        found, _ = v4.find(office, "Tokyo")
        self.assertEqual(found.id, "tokyo")
        kitchen = index.targets["room:kuche"]
        self.assertEqual(v4.find(kitchen, "Nightlight")[0].id, "nightlight")
        self.assertEqual(v4.find(kitchen, "Nachtlicht")[1], "unknown_scene")
        self.assertEqual(v4.default(kitchen).id, "adaptive")

    def test_a_v3_scene_id_is_no_call(self) -> None:
        result, remap, _ = self.migrate_logged(HOME3, HOME3_AREAS)
        index = index_of(HOME3, HOME3_AREAS)
        v4 = V4(validate(result, REGISTRY, BUILTINS), index_of(result, HOME3_AREAS))
        office = index.targets["room:arbeitszimmer"]
        self.assertEqual(v4.find(office, HELL)[1], "unknown_scene")
        self.assertEqual(v4.find(office, remap[(HELL, office.id)])[0].name, "Hell")


class RulesTest(ProofCase):
    def test_every_rule_keeps_what_ran(self) -> None:
        self.proven(EDGE, EDGE_AREAS, EDGE_RUNTIME)

    def test_edits_of_presets(self) -> None:
        result, _, logs = self.migrate_logged(EDGE, EDGE_AREAS)
        # Shaped like a part: a built-in edit; equal to the built-in: none.
        self.assertEqual(
            result["parts"]["relax"],
            {
                "id": "relax",
                "name": "Relax",
                "effect": "white",
                "params": {"kelvin": 2400, "brightness": 50},
            },
        )
        self.assertNotIn("read", result["parts"])
        self.assertNotIn("tokyo", result["parts"])
        # Any other edit: a scene where its menu shows it, its looks named apart.
        self.assertEqual(
            described(result, "room:office"),
            [("Tokyo", [("all", "Tokyo 2"), (("light.lamp",), "Tokyo · Office")])],
        )
        self.assertIn("The edit of 'Tokyo' becomes a scene", "\n".join(logs))

    def test_parts_are_named_by_first_use(self) -> None:
        result, _, _ = self.migrate_logged(EDGE, EDGE_AREAS)
        self.assertEqual(
            [p["name"] for p in result["parts"].values()],
            [
                "Relax",
                "Tokyo 2",
                "Tokyo · Office",
                "Glow",
                "Glow 2",
                "Party",
                "Party 2",
                "Low",
                "Mine",
            ],
        )

    def test_menus_keep_their_order_and_gain_the_scenes_left_out(self) -> None:
        result, _, logs = self.migrate_logged(EDGE, EDGE_AREAS)
        names = {t: [s["name"] for s in v] for t, v in result["scenes"].items()}
        self.assertEqual(
            names,
            {
                "room:hall": ["Party", "Low"],
                "room:kitchen": ["Mine", "Relax", "Read", "Glow", "Party", "Adaptive bright"],
                "room:living": ["Glow", "Party"],
                "room:office": ["Tokyo"],
                "zone:down": ["Party", "Nightlight", "Glow", "Low", "Mine"],
            },
        )  # fmt: skip
        self.assertIn("'Glow' joins the menu of room:kitchen", "\n".join(logs))
        # The name's first scene wins; the other is left out where both fit.
        left_out = [line for line in logs if "is left out" in line]
        self.assertEqual(len(left_out), 2)
        self.assertIn("'low' (s_000000a4) is left out of room:hall", left_out[0])

    def test_lights_keep_their_reach(self) -> None:
        result, _, _ = self.migrate_logged(EDGE, EDGE_AREAS)
        scenes = {
            (t, s["name"]): [lay["lights"] for lay in s["layers"]]
            for t, v in result["scenes"].items()
            for s in v
        }
        # A zone keeps its rooms' refs; a room gets its lights or "all".
        self.assertEqual(
            scenes["zone:down", "Party"],
            [["room:living", "room:kitchen", "light.hall_1"], ["light.kitchen_1"]],
        )
        self.assertEqual(
            scenes["zone:down", "Glow"],
            [["room:living", "light.kitchen_1"], ["light.shelf"]],
        )
        self.assertEqual(scenes["room:living", "Party"], ["all"])
        self.assertEqual(scenes["room:hall", "Party"], [["light.hall_1"]])
        self.assertEqual(scenes["room:kitchen", "Mine"], [["light.porch"]])
        party = result["scenes"]["room:hall"][0]
        self.assertEqual((party["others"], party["transition"]), ("off", 2.0))

    def test_scene_brightness_is_dropped_with_a_warning(self) -> None:
        result, _, logs = self.migrate_logged(EDGE, EDGE_AREAS)
        warned = [line for line in logs if "overall brightness" in line]
        self.assertEqual(len(warned), 1)
        self.assertIn("'Low' loses its overall brightness of 50 %", warned[0])
        self.assertNotIn("brightness", json.dumps(result["scenes"]))

    def test_a_scene_that_reaches_no_room_is_reported(self) -> None:
        data = copy.deepcopy(EDGE)
        data["scenes"]["s_000000c3"] = scene(
            "s_000000c3", "Attic", layer(["room:attic"], "white", DIM)
        )
        result, _, logs = self.proven(data, EDGE_AREAS)
        text = "\n".join(logs)
        self.assertIn("No room holds light.gone of 'Away'", text)
        self.assertIn("'Away' reaches no room and is dropped", text)
        self.assertIn("'Attic' reaches no room and is dropped", text)
        self.assertNotIn("of 'Attic'", text)
        self.assertNotIn("'Mine' reaches", text)
        self.assertNotIn("Attic", json.dumps(result["scenes"]))

    def test_options_alone_make_no_room(self) -> None:
        data = copy.deepcopy(EDGE)
        data["targets"]["room:attic"]["extra_lights"] = ["light.porch"]
        result, _, _ = self.proven(data, EDGE_AREAS)
        self.assertNotIn("room:attic", result["scenes"])
        self.assertIsNone(result["targets"]["room:attic"]["default_scene"])

    def test_an_area_of_extra_lights_alone_is_a_room(self) -> None:
        data = copy.deepcopy(EDGE)
        data["targets"]["room:attic"]["extra_lights"] = ["light.porch"]
        result, _, _ = self.proven(data, {**EDGE_AREAS, "attic": ("Attic", [])})
        attic = [s["name"] for s in result["scenes"]["room:attic"]]
        self.assertEqual(attic, ["Party", "Mine"])
        # Its v3 default does not fit the porch light, so nothing ran.
        self.assertEqual(result["targets"]["room:attic"]["default_scene"], "none")

    def test_switch_on_scenes(self) -> None:
        result, remap, logs = self.migrate_logged(EDGE, EDGE_AREAS)
        targets = result["targets"]
        self.assertEqual(
            {t: o.get("default_scene") for t, o in targets.items()},
            {
                "room:kitchen": remap[("adaptive_bright", "room:kitchen")],
                "room:office": "none",  # its scene did not fit: nothing ran
                "room:living": "none",  # gone: nothing ran
                "zone:down": remap[("nightlight", "zone:down")],
                "room:attic": None,
            },
        )
        self.assertEqual(result["settings"]["default_scene"], "relax")
        self.assertNotIn("visible_scenes", json.dumps(targets))
        text = "\n".join(logs)
        self.assertIn("'s_000000c9' of room:living no longer exists", text)
        self.assertIn("room:attic has no lights", text)

    def test_an_edit_that_did_not_fit_switches_nothing_on(self) -> None:
        data = copy.deepcopy(EDGE)
        data["scenes"]["s_000000b4"] = scene(
            "s_000000b4", "Dimmed", layer(["light.lamp"], "white", DIM), preset="dimmed"
        )
        data["targets"]["room:kitchen"]["default_scene"] = "dimmed"
        result, _, _ = self.proven(data, EDGE_AREAS)
        self.assertEqual(result["targets"]["room:kitchen"]["default_scene"], "none")

    def test_a_global_user_scene_becomes_each_rooms_own(self) -> None:
        data = inheriting(EDGE, "s_000000a1")
        result, remap, _ = self.proven(data, EDGE_AREAS, EDGE_RUNTIME)
        self.assertEqual(
            {t: o.get("default_scene") for t, o in result["targets"].items()},
            {
                "room:kitchen": remap[("s_000000a1", "room:kitchen")],
                "room:office": "none",
                "room:living": remap[("s_000000a1", "room:living")],
                "zone:down": remap[("nightlight", "zone:down")],
                "room:attic": None,
                "room:hall": "none",
            },
        )
        self.assertIsNone(result["settings"]["default_scene"])

    def test_a_global_odd_edit_runs_where_menus_show_it(self) -> None:
        result, remap, _ = self.proven(
            inheriting(EDGE, "tokyo"), EDGE_AREAS, EDGE_RUNTIME
        )
        self.assertEqual(
            {t: o.get("default_scene") for t, o in result["targets"].items()},
            {
                "room:kitchen": None,
                "room:office": remap[("tokyo", "room:office")],
                "room:living": None,
                "zone:down": remap[("nightlight", "zone:down")],
                "room:attic": None,
            },
        )
        self.assertEqual(result["settings"]["default_scene"], "tokyo")

    def test_an_odd_edit_outside_menus_runs_as_the_built_in(self) -> None:
        data = copy.deepcopy(EDGE)
        data["targets"]["room:living"]["default_scene"] = "tokyo"
        runtime = RuntimeV1((running("room:living", "tokyo", EDGE_AREAS),))
        result, remap, _ = self.proven(data, EDGE_AREAS, runtime)
        plain = remap[("tokyo", "room:living")]
        self.assertEqual(result["targets"]["room:living"]["default_scene"], plain)
        self.assertEqual(
            described(result, "room:living")[-1], ("Tokyo", [("all", "Tokyo")])
        )
        self.assertEqual(remap_runtime(runtime, remap).activations[0].scene_id, plain)

    def test_an_edited_icon_stays_on_the_menus_scenes(self) -> None:
        data = copy.deepcopy(EDGE)
        data["scenes"]["s_000000b1"]["icon"] = BUILTINS["relax"].icon
        data["scenes"]["s_000000b2"]["icon"] = "mdi:book-open"
        result, _, logs = self.proven(data, EDGE_AREAS)
        kitchen = {s["name"]: s["icon"] for s in result["scenes"]["room:kitchen"]}
        self.assertEqual(kitchen["Read"], "mdi:book-open")
        self.assertEqual(kitchen["Relax"], BUILTINS["relax"].icon)
        self.assertEqual(kitchen["Adaptive bright"], BUILTINS["adaptive_bright"].icon)
        warned = [line for line in logs if "edited icon" in line]
        self.assertEqual(len(warned), 1)
        self.assertIn("The edited icon of 'Read' stays only", warned[0])

    def test_a_global_scene_that_is_gone_means_none(self) -> None:
        result, _, logs = self.proven(inheriting(EDGE, "s_0000dead"), EDGE_AREAS)
        self.assertIsNone(result["settings"]["default_scene"])
        self.assertIn("The switch-on scene 's_0000dead' no longer exists", logs[-1])

    def test_running_scenes_and_salts_move(self) -> None:
        _, remap, _ = self.migrate_logged(EDGE, EDGE_AREAS)
        moved = remap_runtime(EDGE_RUNTIME, remap)
        down = remap[("s_000000a2", "zone:down")]
        self.assertEqual(
            [a.scene_id for a in moved.activations],
            [
                remap[("relax", "room:kitchen")],
                remap[("s_000000a3", "room:hall")],
                remap[("tokyo", "room:office")],
                "bright",  # no scene of it in the living room: a part scene
                down,
            ],
        )
        self.assertEqual(moved.salts, {f"{down}|zone:down": 2, "bright|room:living": 1})

    def test_ids_are_hashes_and_collisions_rehash(self) -> None:
        result, remap, _ = self.migrate_logged(EDGE, EDGE_AREAS)
        for (old, target_id), new in remap.items():
            self.assertEqual(new, scene_id(old, target_id))
        real = migrate_v4._digest  # noqa: SLF001

        def colliding(prefix: str, text: str) -> str:
            rehashed = re.search(r"\|\d+$", text) is not None
            return real(prefix, text) if rehashed else f"{prefix}00000000"

        with mock.patch.object(migrate_v4, "_digest", colliding):
            clashed, clashed_remap, _ = self.migrate_logged(EDGE, EDGE_AREAS)
        ids = list(clashed_remap.values())
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("s_00000000", ids)
        self.assertEqual(len(clashed["parts"]), len(result["parts"]))
        validate(clashed, REGISTRY, BUILTINS)

    def test_a_long_name_stays_within_bounds(self) -> None:
        name = "X" * 64
        data = stored(
            scene(
                "s_00000001",
                name,
                layer("all", "white", DIM),
                layer(["light.lamp"], "white", WARM_DIM),
            )
        )
        result, _, _ = self.migrate_logged(data, EDGE_AREAS)
        names = [p["name"] for p in result["parts"].values()]
        self.assertEqual(names, [name, f"{'X' * 55} · Office"])
        validate(result, REGISTRY, BUILTINS)


class _ThroughV4(ProofCase):
    """Carry test_migrate_v3's v2 homes on to v4 and prove that step too."""

    def migrated(
        self, data: Mapping[str, Any], areas: Mapping[str, list[str]]
    ) -> tuple[Any, dict[str, str], Raw]:
        coordinator, remap, result = super().migrated(data, areas)  # type: ignore[misc]
        self.proven(result, {a: (a.title(), lights) for a, lights in areas.items()})
        return coordinator, remap, result


class ChainedHomeTest(_ThroughV4, test_migrate_v3.HomeTest):
    pass


class ChainedMergeRulesTest(_ThroughV4, test_migrate_v3.MergeRulesTest):
    pass


class ChainedStoredHomeTest(_ThroughV4, test_migrate_v3.StoredHomeTest):
    pass


def storage_file(key: str, data: Any, version: int = 1) -> str:
    return json.dumps(
        {"version": version, "minor_version": 1, "key": key, "data": data}
    )


def registries(areas: Areas) -> dict[str, Any]:
    """Return the registry files of a home with `areas` and lights to leave out.

    Every other light is placed through its device rather than itself.
    """
    devices = [
        {
            "id": "d_group",
            "area_id": "wohnzimmer",
            "manufacturer": "Zigbee2MQTT",
            "model": "Group",
        }
    ]
    entities = [
        {"entity_id": "light.wohnzimmer_deckenlicht", "platform": "mqtt", "device_id": "d_group", "area_id": None},
        {"entity_id": "light.voice_led_ring", "platform": "esphome", "area_id": "arbeitszimmer", "entity_category": "config"},
        {"entity_id": "light.old", "platform": "hue", "area_id": "flur", "disabled_by": "user"},
        {"entity_id": "light.loose", "platform": "hue", "area_id": None},
        {"entity_id": "switch.kettle", "platform": "mqtt", "area_id": "kuche"},
    ]  # fmt: skip
    for area_id, (_, area_lights) in areas.items():
        for n, entity_id in enumerate(area_lights):
            if n % 2:
                devices.append({"id": f"d_{entity_id}", "area_id": area_id})
                entry = {"device_id": f"d_{entity_id}", "area_id": None}
            else:
                entry = {"device_id": None, "area_id": area_id}
            entities.append({"entity_id": entity_id, "platform": "mqtt", **entry})
    named = [{"id": a, "name": name} for a, (name, _) in areas.items()]
    return {
        "core.area_registry": {
            "areas": [*named, {"id": "draussen", "name": "Draußen"}]
        },
        "core.device_registry": {"devices": devices, "deleted_devices": []},
        "core.entity_registry": {"entities": entities, "deleted_entities": []},
    }


class ExportTest(ProofCase):
    def test_the_export_feeds_the_live_proof(self) -> None:
        script = Path(__file__).parents[2] / "scripts" / "export-store.py"
        with tempfile.TemporaryDirectory() as config:
            storage = Path(config, ".storage")
            storage.mkdir()
            files = {
                "magic_lights": storage_file("magic_lights", HOME3, 3),
                "magic_lights.runtime": storage_file(
                    "magic_lights.runtime", HOME3_RUNTIME.to_dict()
                ),
                **{k: storage_file(k, v) for k, v in registries(HOME3_AREAS).items()},
            }
            for key, text in files.items():
                (storage / key).write_text(text, encoding="utf-8")
            out = subprocess.run(
                [sys.executable, str(script), config],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                {p.name: p.read_text(encoding="utf-8") for p in storage.iterdir()},
                files,
                "read only",
            )
        self.assertIn("store v3.1, 26 lights in 10 areas", out.stderr)
        home = json.loads(out.stdout)
        self.assertEqual(
            home["areas"],
            {a: {"name": n, "lights": sorted(ls)} for a, (n, ls) in HOME3_AREAS.items()}
            | {"draussen": {"name": "Draußen", "lights": []}},
        )
        self.assertEqual(home["store"]["data"], HOME3)
        self.prove_home(home)


class StoreUpgradeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = Path(tmp.name, ".storage")
        self.storage.mkdir()
        self.hass = ShellHass(tmp.name)

    def write(self, key: str, version: int, data: Any) -> str:
        text = storage_file(key, data, version)
        (self.storage / key).write_text(text)
        return text

    def config_store(self) -> store.ConfigStore:
        return store.ConfigStore(self.hass, REGISTRY, BUILTINS, lambda: HOME3_AREAS)

    async def test_the_live_home_upgrades_with_backups(self) -> None:
        old = self.write(store.STORAGE_KEY, 3, HOME3)
        runtime = self.write(store.RUNTIME_KEY, 1, HOME3_RUNTIME.to_dict())
        config_store = self.config_store()
        config = await config_store.async_load_config()
        self.assertEqual(config_store.remap_v3, {})
        expected, remap = migrate(HOME3, REGISTRY, BUILTINS, HOME3_AREAS)
        self.assertEqual(config_store.remap, remap)
        self.assertEqual(config, Config.from_dict(expected))
        self.assertEqual((self.storage / "magic_lights.v3.bak").read_text(), old)
        self.assertEqual(
            (self.storage / "magic_lights.runtime.v3.bak").read_text(), runtime
        )
        saved = json.loads((self.storage / store.STORAGE_KEY).read_text())
        self.assertEqual((saved["version"], saved["minor_version"]), (4, 1))
        moved = config_store.remap_runtime(HOME3_RUNTIME)
        self.assertEqual(moved, remap_runtime(HOME3_RUNTIME, remap))
        # Loaded again, nothing moves.
        again = self.config_store()
        self.assertEqual(await again.async_load_config(), config)
        self.assertEqual(again.remap_runtime(moved), moved)

    async def test_the_version_before_refuses_the_upgraded_file(self) -> None:
        self.write(store.STORAGE_KEY, 3, HOME3)
        await self.config_store().async_load_config()
        older = Store(self.hass, 3, store.STORAGE_KEY, minor_version=1)
        with self.assertRaises(UnsupportedStorageVersionError):
            await older.async_load()

    async def test_an_unknown_version_is_refused(self) -> None:
        self.write(store.STORAGE_KEY, 1, HOME3)
        with self.assertRaises(NotImplementedError):
            await self.config_store().async_load_config()

    async def test_a_failed_upgrade_saves_nothing(self) -> None:
        broken = copy.deepcopy(HOME3)
        broken["scenes"][FILM]["layers"] = None
        old = self.write(store.STORAGE_KEY, 3, broken)
        config_store = self.config_store()
        with self.assertRaises(HomeAssistantError) as err:
            await config_store.async_load_config()
        self.assertIn("copied to magic_lights.v3.bak", str(err.exception))
        self.assertEqual((self.storage / store.STORAGE_KEY).read_text(), old)
        self.assertEqual(config_store.remap, {})


@unittest.skipUnless(os.environ.get(STORE_ENV), f"set {STORE_ENV} to check a home")
class StoredHomeTest(ProofCase):
    def test_a_stored_home(self) -> None:
        self.prove_home(
            json.loads(Path(os.environ[STORE_ENV]).read_text(encoding="utf-8"))
        )


if __name__ == "__main__":
    unittest.main()
