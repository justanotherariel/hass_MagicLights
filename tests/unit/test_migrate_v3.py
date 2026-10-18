"""Store v2 -> v3: every room keeps what each of its scenes does and shows."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import dataclasses
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest

from custom_components.magic_lights import store
from custom_components.magic_lights.effects import BUILTIN, Registry
from custom_components.magic_lights.engine import Coordinator, build_coordinator
from custom_components.magic_lights.engine.activation import plan_runs
from custom_components.magic_lights.migrate_v3 import (
    OLD_DEFAULT_MENU,
    migrate,
    remap_runtime,
)
from custom_components.magic_lights.models import (
    ActivationSnapshot,
    Layer,
    RuntimeV1,
    Scene,
    Target,
    TargetOptions,
)
from custom_components.magic_lights.presets import load_presets
from custom_components.magic_lights.validation import validate
from homeassistant.exceptions import HomeAssistantError, UnsupportedStorageVersionError
from homeassistant.helpers.storage import Store

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

REGISTRY = Registry(BUILTIN)
PRESETS = load_presets(REGISTRY)

# Set to a JSON file {"store": <.storage/magic_lights>, "areas": {area: [lights]}}
# to check a real home before deploying.
STORE_ENV = "MAGIC_LIGHTS_V2_STORE"

SUN = {"brightness_mode": "tanh", "min_brightness": 40, "max_brightness": 100}
SHELF = {**SUN, "min_brightness": 12, "max_brightness": 30}
WARM = {**SUN, "min_kelvin": 2200, "max_kelvin": 2200}
EMBER = {"palette": [{"xy": [0.561, 0.4042]}], "brightness": 1}
DIM = {"kelvin": 2725, "brightness": 30}

AREAS = {
    "living": ["light.living_1", "light.living_2", "light.string", "light.shelf"],
    "bedroom": ["light.bed_1", "light.bed_2", "light.side_l", "light.side_r"],
    "office": ["light.office_1", "light.office_shelf", "light.lamp", "light.desk"],
    "kitchen": ["light.kitchen_1", "light.kitchen_strip"],
    "hall": ["light.hall_1", "light.hall_2"],
    "closet": ["light.closet"],
    "storage": ["light.relay"],
}


def layer(lights: Any, effect: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {"lights": lights, "effect": effect, "params": dict(params)}


def scene(
    scene_id: str, name: str, scope: list[str], *layers: dict[str, Any], **kw: Any
) -> dict[str, Any]:
    return {
        "id": scene_id,
        "name": name,
        "icon": None,
        "scope": scope,
        "preset": None,
        "layers": list(layers),
        "others": "off",
        "brightness": 100,
        "transition": None,
        **kw,
    }


def stored(*scenes: dict[str, Any], **parts: Any) -> dict[str, Any]:
    return {"zones": {}, "targets": {}, "settings": {}, **parts} | {
        "scenes": {s["id"]: s for s in scenes}
    }


# Same-named scenes per room, as the v1 migration made them.
HOME = stored(
    scene(
        "s_000000b1",
        "Sunny",
        ["room:living"],
        layer(["light.living_1", "light.living_2", "light.string"], "adaptive", SUN),
        layer(["light.shelf"], "adaptive", SHELF),
    ),
    scene(
        "s_000000d1",
        "Low",
        ["room:bedroom"],
        layer(["light.bed_1", "light.bed_2"], "palette", EMBER),
        layer(["light.side_l", "light.side_r"], "white", DIM),
    ),  # fmt: skip
    scene(
        "s_000000b2",
        "Sunny",
        ["room:bedroom"],
        layer(["light.bed_1", "light.bed_2"], "adaptive", SUN),
    ),  # fmt: skip
    scene(
        "s_000000d2",
        "Low",
        ["room:living"],
        layer(["light.string", "light.shelf"], "white", DIM),
    ),  # fmt: skip
    scene(
        "s_000000f1", "Film", ["room:living"], layer(["light.shelf"], "white", DIM)
    ),  # fmt: skip
    scene(
        "s_000000d3",
        "Low",
        ["room:office"],
        layer(["light.office_shelf", "light.lamp"], "white", DIM),
    ),  # fmt: skip
    scene("s_000000b3", "Sunny", ["room:hall"], layer("all", "adaptive", SUN)),
    scene("s_000000d4", "low", ["room:kitchen"], layer("all", "white", DIM)),
    scene("s_000000b0", "Sunny", ["room:closet"], layer("all", "adaptive", WARM)),
    # Kept apart: the brightness differs.
    scene(
        "s_000000e1",
        "Night",
        ["room:hall"],
        layer("all", "palette", EMBER),
        brightness=50,
    ),  # fmt: skip
    scene(
        "s_000000e2", "Night", ["room:office"], layer(["light.lamp"], "palette", EMBER)
    ),  # fmt: skip
    scene("s_000000a1", "Mine", ["room:office"], layer("all", "adaptive", SUN)),
    scene(
        "s_000000c1",
        "Relax",
        ["room:storage"],
        layer("all", "white", DIM),
        preset="relax",
    ),  # fmt: skip
    scene("s_000000c2", "Read", [], layer("all", "white", DIM), preset="read"),
    targets={
        "room:office": {
            "excluded_lights": ["light.desk"],
            "visible_scenes": [
                *sorted(OLD_DEFAULT_MENU),
                "s_000000d3",
                "s_000000e2",
                "adaptive_bright",
                "s_000000a1",
            ],
        },
        "room:closet": {
            "visible_scenes": ["s_000000b0", "tokyo"],
            "default_scene": "s_000000b0",
        },
        "room:living": {
            # A stale entry: v2 left deleted and foreign scenes in menus.
            "visible_scenes": ["s_000000d2", "s_000000b1", "relax", "s_000000b3"],
            "default_scene": "s_000000b1",
        },
    },
    settings={"transition": 1.0, "default_scene": "s_000000d4"},
)


def index_of(data: Mapping[str, Any], areas: Mapping[str, list[str]]) -> FakeLightIndex:
    options = {
        k: TargetOptions.from_dict(v) for k, v in (data.get("targets") or {}).items()
    }
    rooms = {}
    for area_id, lights in areas.items():
        target = options.get(f"room:{area_id}", TargetOptions())
        members = (set(lights) | set(target.extra_lights)) - set(target.excluded_lights)
        rooms[area_id] = make_room(area_id, members, options=target)
    zones = [
        make_zone(
            zone_id,
            [rooms[a] for a in zone.get("rooms") or () if a in rooms],
            zone.get("lights") or (),
            options=options.get(f"zone:{zone_id}"),
        )
        for zone_id, zone in (data.get("zones") or {}).items()
    ]
    every = {e for lights in areas.values() for e in lights}
    every |= {e for o in options.values() for e in o.extra_lights}
    return FakeLightIndex(
        [make_light(e) for e in sorted(every)], [*rooms.values(), *zones]
    )


def fits(index: FakeLightIndex, scene: Scene, target: Target) -> bool:
    return any(index.expand(layer.lights, target) for layer in scene.layers)


def v2_scene(raw: Mapping[str, Any]) -> Scene:
    """Build a v2 scene with its params validated, as the engine ran it."""
    plain = Scene.from_dict(raw)
    layers = tuple(
        Layer(lay.lights, lay.effect, REGISTRY.get(lay.effect).validate(lay.params))
        for lay in plain.layers
    )
    return dataclasses.replace(plain, layers=layers)


def v2_available(
    target_id: str, data: Mapping[str, Any]
) -> tuple[dict[str, Scene], set[str]]:
    """v2's scenes of a target by public id, and the ids of room-scoped edits."""
    copies: dict[str, tuple[Scene, list[str]]] = {}
    users: dict[str, Scene] = {}
    for raw in data["scenes"].values():
        found, scope = v2_scene(raw), raw.get("scope") or []
        if found.preset not in PRESETS:
            if not scope or target_id in scope:
                users[found.id] = found
        elif target_id in scope:
            if (current := copies.get(found.preset)) is None or not current[1]:
                copies[found.preset] = (found, scope)
        elif not scope:
            copies.setdefault(found.preset, (found, scope))
    out = {pid: copies.get(pid, (preset, []))[0] for pid, preset in PRESETS.items()}
    scoped = {c.id for c, scope in copies.values() if scope}
    return out | users, scoped


def v2_menu(target: Target, available: Mapping[str, Scene], index: Any) -> list[str]:
    fitting = [pid for pid, s in available.items() if fits(index, s, target)]
    if (order := target.options.visible_scenes) is None:
        return [pid for pid in fitting if pid not in PRESETS]
    return [pid for pid in order if pid in fitting]


def runs(scene: Scene, target: Target, index: FakeLightIndex) -> dict[Any, Any]:
    """Map the lights each layer runs together to its effect and params.

    Layers keyed by their lights, not their place: a palette deals its colours
    among exactly the lights of one layer.
    """
    plans, uncovered = plan_runs(scene, target, target.lights, index)
    out: dict[Any, Any] = {p.members: (p.effect, dict(p.params)) for p in plans}
    if scene.others == "off":
        out |= dict.fromkeys(uncovered, "off")
    return out | {"scene": (scene.others, scene.brightness, scene.transition)}


class MigrationCase(unittest.TestCase):
    def migrated(
        self, data: Mapping[str, Any], areas: Mapping[str, list[str]]
    ) -> tuple[Coordinator, dict[str, str], dict[str, Any]]:
        """Migrate and prove that no room runs or shows anything else."""
        before = copy.deepcopy(dict(data))
        result, remap = migrate(data, REGISTRY, PRESETS, areas)
        self.assertEqual(data, before, "the input is left alone")
        self.assertNotIn("scope", json.dumps(result["scenes"]))
        index = index_of(result, areas)
        old_targets = index_of(data, areas).targets
        config = validate(
            result,
            REGISTRY,
            presets=PRESETS,
            fits=lambda s: {t.id for t in index.targets.values() if fits(index, s, t)},
        )
        coordinator = build_coordinator(
            FakeHost(),
            index,
            REGISTRY,
            {"ha": FakeDriver()},
            config,
            PRESETS,
            FakeRuntimeStore(),
        )
        for target in index.targets.values():
            with self.subTest(target=target.id):
                old = old_targets[target.id]
                self.assertEqual(old.lights, target.lights)
                self.assert_room_kept(coordinator, index, old, data, remap)
        return coordinator, remap, result

    def assert_room_kept(
        self,
        coordinator: Coordinator,
        index: FakeLightIndex,
        target: Target,
        data: Mapping[str, Any],
        remap: Mapping[str, str],
    ) -> None:
        """Check one target; `target` is as v2 had it, with its v2 menu."""
        available, room_edits = v2_available(target.id, data)
        old = {pid: s for pid, s in available.items() if fits(index, s, target)}
        for public_id, scene in old.items():
            now = coordinator.resolve_scene(remap.get(public_id, public_id))
            self.assertIsNotNone(now, public_id)
            self.assertEqual(now.name.casefold(), scene.name.casefold(), public_id)
            self.assertTrue(coordinator.fits(now, target), public_id)
            if scene.id in room_edits:
                # An edit of a preset for some rooms goes; the preset stays.
                self.assertNotEqual(now.id, scene.id)
                continue
            self.assertEqual(
                runs(now, target, index), runs(scene, target, index), public_id
            )
        every = coordinator.scenes_for(target.id, include_hidden=True)
        names = [s.name.casefold() for s in every]
        self.assertEqual(len(names), len(set(names)), "one option per name")
        menu = [available[pid].name for pid in v2_menu(target, available, index)]
        if set(target.options.visible_scenes or ()) >= OLD_DEFAULT_MENU:
            dropped = {PRESETS[pid].name for pid in OLD_DEFAULT_MENU}
            menu = [name for name in menu if name not in dropped]
        shown = {s.name.casefold() for s in coordinator.scenes_for(target.id)}
        fit_before = {s.name.casefold() for s in old.values()}
        menu_before = {name.casefold() for name in menu}
        # Without scopes, a zone shows what is set up in its rooms, and a
        # zone's scene is set up in the zone's rooms.
        if target.kind == "zone":
            self.assertLessEqual(fit_before, set(names))
            self.assertLessEqual(menu_before, shown)
            return
        zoned = {
            raw["name"].casefold()
            for raw in data["scenes"].values()
            if any(t.startswith("zone:") for t in raw.get("scope") or ())
        }
        self.assertEqual(set(names) - zoned, fit_before - zoned)
        self.assertEqual(shown - zoned, menu_before - zoned)


class HomeTest(MigrationCase):
    def test_same_named_room_scenes_become_one(self) -> None:
        coordinator, remap, result = self.migrated(HOME, AREAS)
        self.assertEqual(
            remap,
            {
                "s_000000b1": "s_000000b0",
                "s_000000b2": "s_000000b0",
                "s_000000b3": "s_000000b0",
                "s_000000d2": "s_000000d1",
                "s_000000d3": "s_000000d1",
                "s_000000d4": "s_000000d1",
            },
        )
        scenes = result["scenes"]
        # Merged scenes take their first member's place.
        self.assertEqual(
            list(scenes),
            [
                "s_000000b0",
                "s_000000d1",
                "s_000000f1",
                "s_000000e1",
                "s_000000e2",
                "s_000000a1",
                "s_000000c2",
            ],
        )
        # The most common first layer leads; a room's own layer follows it.
        self.assertEqual(
            scenes["s_000000b0"]["layers"],
            [
                layer(
                    [
                        "light.living_1",
                        "light.living_2",
                        "light.string",
                        "light.bed_1",
                        "light.bed_2",
                        "room:hall",
                    ],
                    "adaptive",
                    SUN,
                ),
                layer(["light.shelf"], "adaptive", SHELF),
                layer(["room:closet"], "adaptive", WARM),
            ],
        )
        self.assertEqual(
            [lay["lights"] for lay in scenes["s_000000d1"]["layers"]],
            [
                [
                    "light.string",
                    "light.shelf",
                    "light.office_shelf",
                    "light.lamp",
                    "room:kitchen",
                    "light.side_l",
                    "light.side_r",
                ],
                ["light.bed_1", "light.bed_2"],
            ],
        )
        self.assertEqual(scenes["s_000000d1"]["name"], "Low")
        self.assertEqual(
            (scenes["s_000000e1"]["layers"][0]["lights"], scenes["s_000000e1"]["name"]),
            (["room:hall"], "Night"),
        )
        self.assertEqual(scenes["s_000000a1"]["layers"][0]["lights"], ["room:office"])
        self.assertEqual(scenes["s_000000c2"]["layers"][0]["lights"], "all")

        targets = result["targets"]
        self.assertEqual(
            targets["room:office"]["visible_scenes"],
            ["s_000000d1", "s_000000e2", "adaptive_bright", "s_000000a1"],
        )
        self.assertEqual(
            targets["room:living"],
            {
                "visible_scenes": ["s_000000d1", "s_000000b0", "relax"],
                "default_scene": "s_000000b0",
            },
        )
        self.assertEqual(targets["room:closet"]["default_scene"], "s_000000b0")
        # Only menus with every old default lose them.
        self.assertEqual(
            targets["room:closet"]["visible_scenes"], ["s_000000b0", "tokyo"]
        )
        self.assertEqual(
            result["settings"], {"transition": 1.0, "default_scene": "s_000000d1"}
        )

        self.assertIs(coordinator.resolve_scene("relax"), PRESETS["relax"])
        self.assertEqual(coordinator.resolve_scene("read").id, "s_000000c2")
        # Names stay apart where the scenes do.
        self.assertEqual(coordinator.find_scene("room:hall", "night").id, "s_000000e1")
        self.assertEqual(
            coordinator.find_scene("room:office", "Night").id, "s_000000e2"
        )

    def test_a_room_edit_of_a_preset_is_dropped_with_a_warning(self) -> None:
        with self.assertLogs(
            "custom_components.magic_lights.migrate_v3", "WARNING"
        ) as logs:
            migrate(HOME, REGISTRY, PRESETS, AREAS)
        self.assertIn("Relax", logs.output[0])
        self.assertIn("room:storage", logs.output[0])

    def test_running_scenes_follow_the_merge(self) -> None:
        started = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)

        def running(target_id: str, scene_id: str) -> ActivationSnapshot:
            lights = tuple(sorted(AREAS[target_id.removeprefix("room:")]))
            return ActivationSnapshot(
                f"a_{target_id}", target_id, scene_id, started, lights, (), {}, False
            )

        runtime = RuntimeV1(
            (
                running("room:bedroom", "s_000000b2"),
                running("room:hall", "s_000000b3"),
                running("room:living", "s_000000f1"),
                running("room:storage", "relax"),
            ),
            {"s_000000b2|room:bedroom": 3, "tokyo|room:living": 1},
        )
        coordinator, remap, _ = self.migrated(HOME, AREAS)
        moved = remap_runtime(runtime, remap)
        self.assertEqual(
            [a.scene_id for a in moved.activations],
            ["s_000000b0", "s_000000b0", "s_000000f1", "relax"],
        )
        self.assertEqual(
            moved.salts, {"s_000000b0|room:bedroom": 3, "tokyo|room:living": 1}
        )
        coordinator.restore_ownership(moved)
        self.assertEqual(
            {a.target_id: a.scene_id for a in coordinator.activations.values()},
            {
                "room:bedroom": "s_000000b0",
                "room:hall": "s_000000b0",
                "room:living": "s_000000f1",
                "room:storage": "relax",
            },
        )
        self.assertEqual(coordinator.salts["s_000000b0|room:bedroom"], 3)


class MergeRulesTest(MigrationCase):
    def test_each_room_keeps_its_layer_order(self) -> None:
        a, b = {"kelvin": 2700}, {"kelvin": 5000}
        data = stored(
            scene("s_00000001", "K", ["room:one"],
                  layer("all", "white", a), layer(["light.one_b"], "white", b)),
            scene("s_00000002", "K", ["room:two"],
                  layer("all", "white", b), layer(["light.two_a"], "white", a)),
        )  # fmt: skip
        areas = {
            "one": ["light.one_a", "light.one_b"],
            "two": ["light.two_a", "light.two_b"],
        }
        _, remap, result = self.migrated(data, areas)
        self.assertEqual(remap, {"s_00000002": "s_00000001"})
        self.assertEqual(
            result["scenes"]["s_00000001"]["layers"],
            [
                layer(["room:one"], "white", a),
                layer(["light.one_b", "room:two"], "white", b),
                layer(["light.two_a"], "white", a),
            ],
        )

    def test_a_member_keeps_its_equal_layers_apart(self) -> None:
        duo = {"palette": [{"xy": [0.561, 0.4042]}, {"xy": [0.2, 0.3]}]}
        data = stored(
            scene("s_00000001", "Duo", ["room:living"],
                  layer(["light.living_1"], "palette", duo),
                  layer(["light.living_2"], "white", DIM),
                  layer(["light.string"], "palette", duo)),
            scene("s_00000002", "Duo", ["room:kitchen"],
                  layer(["light.kitchen_1"], "palette", duo)),
        )  # fmt: skip
        _, _, result = self.migrated(data, AREAS)
        self.assertEqual(
            [lay["lights"] for lay in result["scenes"]["s_00000001"]["layers"]],
            [
                ["light.living_1", "light.kitchen_1"],
                ["light.living_2"],
                ["light.string"],
            ],
        )

    def test_a_merge_that_changes_a_name_warns(self) -> None:
        data = stored(
            scene("s_00000001", "Glow", ["room:living"], layer("all", "white", DIM)),
            scene("s_00000002", "glow", ["room:kitchen"], layer("all", "white", DIM)),
        )
        with self.assertLogs(
            "custom_components.magic_lights.migrate_v3", "WARNING"
        ) as logs:
            _, remap, _ = self.migrated(data, AREAS)
        self.assertEqual(remap, {"s_00000002": "s_00000001"})
        self.assertIn("'glow' of room:kitchen becomes 'Glow'", logs.output[0])

    def test_a_zone_without_lights_loses_its_scenes(self) -> None:
        data = stored(
            scene("s_00000001", "Empty", ["zone:void"], layer("all", "white", DIM)),
            scene(
                "s_00000002",
                "Half",
                ["zone:void"],
                layer("all", "white", DIM),
                layer(["light.lamp"], "palette", EMBER),
            ),
            scene("s_00000003", "Kept", ["room:office"], layer("all", "white", DIM)),
            zones={"void": {"id": "void", "name": "Void"}},
            targets={
                "zone:void": {
                    "visible_scenes": ["s_00000001", "s_00000002", "s_00000003"],
                    "default_scene": "s_00000001",
                }
            },
            settings={"default_scene": "s_00000002"},
        )
        with self.assertLogs(
            "custom_components.magic_lights.migrate_v3", "WARNING"
        ) as logs:
            _, _, result = self.migrated(data, AREAS)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("Dropping all lights, light.lamp of 'Half'", logs.output[1])
        self.assertEqual(list(result["scenes"]), ["s_00000003"])
        self.assertEqual(
            result["targets"]["zone:void"],
            {"visible_scenes": ["s_00000003"], "default_scene": None},
        )
        self.assertIsNone(result["settings"]["default_scene"])

    def test_a_zone_scene_covers_its_rooms_and_lights(self) -> None:
        data = stored(
            scene("s_00000001", "Party", ["zone:down"], layer("all", "white", DIM)),
            zones={
                "down": {
                    "id": "down",
                    "name": "Down",
                    "rooms": ["living", "kitchen"],
                    "lights": ["light.hall_1"],
                }
            },
        )
        _, _, result = self.migrated(data, AREAS)
        self.assertEqual(
            result["scenes"]["s_00000001"]["layers"][0]["lights"],
            ["room:living", "room:kitchen", "light.hall_1"],
        )

    def test_groups_that_stay_apart(self) -> None:
        def pair(**kw: Any) -> dict[str, Any]:
            first = scene(
                "s_00000001", "Glow", ["room:living"], layer("all", "white", DIM)
            )
            second = scene(
                "s_00000002", "Glow", ["room:kitchen"], layer("all", "white", DIM)
            )
            for key, (one, two) in kw.items():
                first[key], second[key] = one, two
            return stored(first, second)

        cases = {
            "fade": pair(transition=(None, 2.0)),
            "others": pair(others=("off", "keep")),
            "two rooms": pair(scope=(["room:living", "room:hall"], ["room:kitchen"])),
            "zone": pair(scope=(["zone:down"], ["room:kitchen"])),
            "borrowed light": pair(),
        }
        cases["borrowed light"]["targets"] = {
            "room:kitchen": {"extra_lights": ["light.porch"]}
        }
        for name, data in cases.items():
            data["zones"] = {"down": {"id": "down", "name": "Down", "rooms": ["hall"]}}
            with self.subTest(name):
                _, remap, result = self.migrated(data, AREAS)
                self.assertEqual(remap, {})
                self.assertEqual(
                    [s["name"] for s in result["scenes"].values()], ["Glow", "Glow"]
                )

    def test_lights_out_of_scope_are_dropped(self) -> None:
        def kitchen(name: str, **kw: Any) -> dict[str, Any]:
            return scene(
                "s_00000002",
                name,
                ["room:kitchen"],
                layer(["light.kitchen_1", "light.living_1"], "palette", EMBER),
                layer(["light.living_2", "room:living"], "white", DIM),
                **kw,
            )

        living = scene(
            "s_00000001", "Low", ["room:living"], layer(["light.string"], "white", DIM)
        )
        kept = layer(["light.kitchen_1"], "palette", EMBER)
        with self.subTest("merged"):
            with self.assertLogs(
                "custom_components.magic_lights.migrate_v3", "WARNING"
            ) as logs:
                _, remap, result = self.migrated(stored(living, kitchen("Low")), AREAS)
            self.assertIn(
                "Dropping light.living_1, light.living_2, room:living of 'Low'",
                logs.output[0],
            )
            self.assertEqual(remap, {"s_00000002": "s_00000001"})
            self.assertEqual(
                result["scenes"]["s_00000001"]["layers"],
                [layer(["light.string"], "white", DIM), kept],
            )
        with self.subTest("apart"):
            data = stored(living, kitchen("Low", brightness=50))
            _, remap, result = self.migrated(data, AREAS)
            self.assertEqual(remap, {})
            self.assertEqual(result["scenes"]["s_00000002"]["layers"], [kept])

    def test_a_room_ref_keeps_the_lights_the_scope_held(self) -> None:
        cases = {
            "extra light": (
                ["room:kitchen"],
                layer(["room:living"], "palette", EMBER),
                {"targets": {"room:kitchen": {"extra_lights": ["light.living_1"]}}},
                ["light.living_1"],
            ),
            "zone light": (
                ["zone:down"],
                layer(["room:hall"], "white", DIM),
                {"zones": {"down": {"id": "down", "name": "Down", "rooms": [], "lights": ["light.hall_1"]}}},
                ["light.hall_1"],
            ),
        }  # fmt: skip
        for name, (scope, glow, extra, lights) in cases.items():
            with self.subTest(name):
                data = stored(scene("s_00000001", "Glow", scope, glow), **extra)
                with self.assertLogs(
                    "custom_components.magic_lights.migrate_v3", "WARNING"
                ) as logs:
                    result, _ = migrate(data, REGISTRY, PRESETS, AREAS)
                self.assertEqual(
                    result["scenes"]["s_00000001"]["layers"][0]["lights"], lights
                )
                self.assertIn("'Glow' also fits room:", logs.output[0])

    def test_a_scene_with_only_foreign_lights_goes(self) -> None:
        data = stored(
            scene(
                "s_00000001",
                "Away",
                ["room:kitchen"],
                layer(["light.living_1"], "white", DIM),
            ),
            scene(
                "s_00000002",
                "Borrowed",
                ["room:kitchen", "zone:down"],
                layer(["light.porch", "light.hall_1", "light.lamp"], "white", DIM),
            ),
            zones={"down": {"id": "down", "name": "Down", "lights": ["light.hall_1"]}},
            targets={
                "room:kitchen": {
                    "extra_lights": ["light.porch"],
                    "visible_scenes": ["s_00000001", "s_00000002"],
                    "default_scene": "s_00000001",
                }
            },
        )
        with self.assertLogs("custom_components.magic_lights.migrate_v3", "WARNING"):
            _, _, result = self.migrated(data, AREAS)
        self.assertEqual(list(result["scenes"]), ["s_00000002"])
        self.assertEqual(
            result["scenes"]["s_00000002"]["layers"][0]["lights"],
            ["light.porch", "light.hall_1"],
        )
        self.assertEqual(
            result["targets"]["room:kitchen"],
            {
                "extra_lights": ["light.porch"],
                "visible_scenes": ["s_00000002"],
                "default_scene": None,
            },
        )


@unittest.skipUnless(os.environ.get(STORE_ENV), f"set {STORE_ENV} to check a home")
class StoredHomeTest(MigrationCase):
    def test_a_stored_home(self) -> None:
        home = json.loads(Path(os.environ[STORE_ENV]).read_text(encoding="utf-8"))
        self.migrated(home["store"]["data"], home["areas"])


class StoreMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = Path(tmp.name, ".storage")
        self.storage.mkdir()
        self.hass = ShellHass(tmp.name)

    def write(self, key: str, version: int, data: Any) -> str:
        text = json.dumps(
            {"version": version, "minor_version": 1, "key": key, "data": data}
        )
        (self.storage / key).write_text(text)
        return text

    def config_store(self) -> store.ConfigStore:
        return store.ConfigStore(self.hass, REGISTRY, PRESETS, lambda: AREAS)

    async def test_upgrade_keeps_a_backup_and_older_code_refuses_it(self) -> None:
        old = self.write(store.STORAGE_KEY, 2, HOME)
        runtime = self.write(store.RUNTIME_KEY, 1, {"activations": [], "salts": {}})
        config_store = self.config_store()
        config = await config_store.async_load_config()
        self.assertEqual(config_store.remap["s_000000b1"], "s_000000b0")
        self.assertEqual(len(config.scenes), 7)
        self.assertEqual((self.storage / "magic_lights.v2.bak").read_text(), old)
        self.assertEqual(
            (self.storage / "magic_lights.runtime.v1.bak").read_text(), runtime
        )
        saved = json.loads((self.storage / store.STORAGE_KEY).read_text())
        self.assertEqual((saved["version"], saved["minor_version"]), (3, 1))
        self.assertEqual(await self.config_store().async_load_config(), config)

        older = Store(self.hass, 2, store.STORAGE_KEY, minor_version=1)
        with self.assertRaises(UnsupportedStorageVersionError):
            await older.async_load()

    async def test_a_plugin_effect_registered_later_migrates(self) -> None:
        home = copy.deepcopy(HOME)
        home["scenes"]["s_000000f1"]["layers"][0] |= {
            "effect": "disco",
            "params": {"speed": 3},
        }
        self.write(store.STORAGE_KEY, 2, home)
        config = await self.config_store().async_load_config()
        self.assertEqual(
            config.scenes["s_000000f1"].layers[0],
            Layer(("light.shelf",), "disco", {"speed": 3}),
        )

    async def test_a_result_that_fails_validation_saves_nothing(self) -> None:
        broken = copy.deepcopy(HOME)
        broken["scenes"]["s_000000f1"]["brightness"] = 500
        old = self.write(store.STORAGE_KEY, 2, broken)
        config_store = self.config_store()
        with self.assertRaises(HomeAssistantError) as err:
            await config_store.async_load_config()
        self.assertIn("s_000000f1", str(err.exception))
        self.assertEqual((self.storage / store.STORAGE_KEY).read_text(), old)
        self.assertEqual((self.storage / "magic_lights.v2.bak").read_text(), old)
        self.assertEqual(config_store.remap, {})


if __name__ == "__main__":
    unittest.main()
