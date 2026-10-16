"""v1 → v2 migration (migrate_v1 is pure)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from custom_components.magic_lights import store
from custom_components.magic_lights.const import OPTION_MANUAL
from custom_components.magic_lights.migrate_v1 import V1_CONFIG_SCHEMA, migrate_v1
from custom_components.magic_lights.models import (
    ActivationSnapshot,
    Config,
    RuntimeV1,
    Settings,
)
from custom_components.magic_lights.presets import load_presets, preset_rows
from custom_components.magic_lights.validation import validate
from homeassistant.core import CoreState

from .fakes import FakeRegistry

PRESETS = preset_rows()
AREAS = {
    "living": frozenset({"light.l1", "light.l2"}),
    "kitchen": frozenset({"light.k1"}),
    "empty": frozenset(),
}


def pipe(entities, name, conf=None, **kw):
    effect = {"name": name} if conf is None else {"name": name, "conf": conf}
    return {"entities": entities, "effect": effect, **kw}


def only_scene(config: dict) -> dict:
    (scene,) = config["scenes"].values()
    return scene


class ShapesTest(unittest.TestCase):
    def test_head_yaml_single_area_zone_becomes_room(self) -> None:
        yaml = {
            "Living": {
                "entities": ["light.l2", "light.l1"],
                "scenes": {
                    "Relax": [pipe(["light.all"], "static", {"preset": "Relax"})],
                    "off": [pipe(["light.all"], "off")],
                    "Custom": [pipe(["light.all"], "custom")],
                },
            }
        }
        config, notes = migrate_v1(V1_CONFIG_SCHEMA(yaml), AREAS, presets=PRESETS)
        self.assertEqual(config["zones"], {})
        scene = only_scene(config)
        self.assertEqual(scene["scope"], ["room:living"])
        self.assertEqual(scene["name"], "Relax (v1)")  # the built-in keeps "Relax"
        self.assertEqual(
            scene["layers"],
            [
                {
                    "lights": "all",
                    "effect": "white",
                    "params": {"kelvin": 2237, "brightness": 56},
                }
            ],
        )
        self.assertEqual(
            (
                scene["preset"],
                scene["others"],
                scene["brightness"],
                scene["transition"],
            ),
            (None, "off", 100, None),
        )
        self.assertEqual(len(notes), 1)
        self.assertIn("renamed to 'Relax (v1)'", notes[0])

    def test_wip_areas(self) -> None:
        wip = {
            "Living": {
                "areas": ["living"],
                "scenes": {"A": [pipe(["light.all"], "off")]},
            },
            "Downstairs": {
                "areas": ["living", "kitchen"],
                "entities": ["light.x", "light.l1"],
                "scenes": {"B": [pipe(["light.all"], "off")]},
            },
        }
        config, _ = migrate_v1(V1_CONFIG_SCHEMA(wip), AREAS)
        scopes = {s["name"]: s["scope"] for s in config["scenes"].values()}
        self.assertEqual(scopes, {"A": ["room:living"], "B": ["zone:downstairs"]})
        self.assertEqual(
            config["zones"],
            {
                "downstairs": {
                    "id": "downstairs",
                    "name": "Downstairs",
                    "icon": None,
                    "rooms": ["living", "kitchen"],
                    "lights": ["light.x"],
                }
            },
        )

    def test_store_shape(self) -> None:
        zones = {
            "Kitchen": {
                "areas": ["kitchen"],
                "scenes": {"A": [pipe(["light.all"], "off")]},
            }
        }
        self.assertEqual(migrate_v1({"config": zones}, AREAS), migrate_v1(zones, AREAS))
        named_config = {
            "config": zones["Kitchen"]
        }  # a zone that happens to be called "config"
        config, _ = migrate_v1(named_config, AREAS)
        self.assertEqual(only_scene(config)["scope"], ["room:kitchen"])
        self.assertEqual(migrate_v1({}, AREAS)[0]["scenes"], {})

    def test_yaml_schema_is_relaxed(self) -> None:
        self.assertEqual(V1_CONFIG_SCHEMA(None), {})
        V1_CONFIG_SCHEMA({"Z": {"scenes": {"A": [pipe(["light.a"], "off")]}}})


class ZonesTest(unittest.TestCase):
    def scope_of(self, zone: dict) -> tuple[list[str], dict, list[str]]:
        config, notes = migrate_v1(
            {"Z": {**zone, "scenes": {"A": [pipe(["light.all"], "off")]}}}, AREAS
        )
        return only_scene(config)["scope"], config["zones"], notes

    def test_exact_area_lights_only(self) -> None:
        self.assertEqual(self.scope_of({"entities": ["light.k1"]})[0], ["room:kitchen"])
        scope, zones, _ = self.scope_of({"entities": ["light.l1"]})  # subset of an area
        self.assertEqual(scope, ["zone:z"])
        self.assertEqual(zones["z"]["lights"], ["light.l1"])
        self.assertEqual(self.scope_of({"areas": ["empty"]})[0], ["zone:z"])

    def test_second_empty_area_keeps_the_zone(self) -> None:
        scope, zones, notes = self.scope_of({"areas": ["living", "empty"]})
        self.assertEqual(scope, ["zone:z"])
        self.assertEqual(zones["z"]["rooms"], ["living", "empty"])
        self.assertEqual(notes, [])

    def test_missing_area(self) -> None:
        scope, zones, notes = self.scope_of(
            {"areas": ["kitchen", "gone"], "entities": ["light.l1"]}
        )
        self.assertEqual(scope, ["zone:z"])
        self.assertEqual(zones["z"]["rooms"], ["kitchen"])
        self.assertTrue(any("gone" in n for n in notes))

    def test_groups_and_non_lights(self) -> None:
        zone = {
            "entities": ["ceiling", "switch.fan", "light.l3"],
            "groups": {"ceiling": ["light.c1", "light.c2"]},
            "scenes": {
                "A": [pipe(["ceiling", "light.l3"], "static", {"preset": "Read"})]
            },
        }
        config, notes = migrate_v1({"Hall": zone}, AREAS)
        self.assertEqual(
            config["zones"]["hall"]["lights"], ["light.c1", "light.c2", "light.l3"]
        )
        self.assertEqual(
            only_scene(config)["layers"][0]["lights"],
            ["light.c1", "light.c2", "light.l3"],
        )
        self.assertTrue(any("switch.fan" in n for n in notes))

    def test_unique_zone_ids(self) -> None:
        v1 = {
            name: {"entities": [e], "scenes": {"A": [pipe(["light.all"], "off")]}}
            for name, e in (("Hall", "light.h1"), ("hall", "light.h2"))
        }
        config, _ = migrate_v1(v1, AREAS)
        self.assertEqual(sorted(config["zones"]), ["hall", "hall_2"])


class EffectsTest(unittest.TestCase):
    def layers(self, *pipes) -> tuple[list[dict], list[str]]:
        config, notes = migrate_v1(
            {"Living": {"areas": ["living"], "scenes": {"S": list(pipes)}}},
            AREAS,
            presets=PRESETS,
        )
        return (only_scene(config)["layers"] if config["scenes"] else []), notes

    def test_static_presets(self) -> None:
        scenes = {s["id"]: s for s in PRESETS}
        for v1, v2, effect in (
            ("Energize", "energize", "white"),
            ("Concentrate", "concentrate", "white"),
            ("Read", "read", "white"),
            ("Relax", "relax", "white"),
            ("Bright", "bright", "white"),
            ("Dimmed", "dimmed", "white"),
            ("Nightlight", "nightlight", "palette"),
            ("Savanna Sunset", "savanna_sunset", "palette"),
            ("Tropical Twilight", "tropical_twilight", "palette"),
            ("Arctic Aurora", "arctic_aurora", "palette"),
            ("Spring Blossom", "spring_blossom", "palette"),
        ):
            with self.subTest(v1):
                (layer,), _ = self.layers(pipe(["light.all"], "static", {"preset": v1}))
                self.assertEqual(layer, scenes[v2]["layers"][0])
                self.assertEqual(layer["effect"], effect)
                self.assertIsNot(layer["params"], scenes[v2]["layers"][0]["params"])
        (layer,), notes = self.layers(
            pipe(["light.all"], "static", {"preset": "Disco"})
        )
        self.assertEqual(
            layer,
            {
                "lights": "all",
                "effect": "white",
                "params": {"kelvin": 2700, "brightness": 100},
            },
        )
        self.assertTrue(any("Disco" in n for n in notes))

    def test_static_per_entity(self) -> None:
        conf = {
            "light.l1": {"brightness": 128, "color_temp_kelvin": 3000},
            "light.l2": "off",
            "light.k1": {"color_temp_kelvin": 3000, "brightness": 128},
            "light.p": {"brightness_pct": 40, "hs_color": [30, 80]},
            "light.r": {"rgb_color": [255, 0, 0]},
            "light.x": {"xy_color": [0.3, 0.3], "transition": 3},
            "light.m": {"color_temp": 250},
            "light.b": {"brightness": 0},
        }
        layers, notes = self.layers(pipe(["light.all"], "static", conf))
        by_lights = {tuple(layer["lights"]): layer for layer in layers}
        self.assertEqual(
            by_lights[("light.k1", "light.l1")]["params"],
            {"brightness": 50, "color": {"kelvin": 3000}},
        )
        self.assertEqual(
            by_lights[("light.l2",)],
            {"lights": ["light.l2"], "effect": "off", "params": {}},
        )
        self.assertEqual(by_lights[("light.p",)]["params"]["brightness"], 40)
        self.assertIn("xy", by_lights[("light.p",)]["params"]["color"])
        self.assertEqual(
            by_lights[("light.r",)]["params"], {"color": {"xy": [0.701, 0.299]}}
        )
        self.assertEqual(
            by_lights[("light.x",)]["params"], {"color": {"xy": [0.3, 0.3]}}
        )
        self.assertEqual(by_lights[("light.m",)]["params"], {"color": {"kelvin": 4000}})
        self.assertEqual(by_lights[("light.b",)]["params"]["brightness"], 1)
        self.assertEqual(len(layers), 7)
        self.assertTrue(any("2700 K" in n for n in notes))  # light.b has no colour

    def test_transitions(self) -> None:
        (layer,), notes = self.layers(
            pipe(["light.all"], "transitions", {"preset": "pink-glow"})
        )
        self.assertEqual(layer["effect"], "palette")
        self.assertEqual(len(layer["params"]["palette"]), 5)
        self.assertEqual(layer["params"]["motion"], "dynamic")
        self.assertEqual(layer["params"]["speed"], round(math.log(2) / math.log(60), 2))
        self.assertEqual(notes, [])

        (layer,), notes = self.layers(
            pipe(["light.all"], "transitions", {"preset": "tokyo"})
        )
        self.assertEqual(
            layer["params"]["palette"],
            [{"xy": [0.1562, 0.1648]}, {"xy": [0.49, 0.316]}],
        )
        self.assertTrue(any("Tokyo" in n for n in notes))

        custom = {
            "colors": [[255, 0, 0], [0, 0, 0], [0, 0, 255]],
            "color_space": "sRGB255",
            "transition_time": 10,
        }
        (layer,), _ = self.layers(pipe(["light.all"], "transitions", custom))
        self.assertEqual(len(layer["params"]["palette"]), 2)  # black is dropped
        self.assertEqual(
            layer["params"]["speed"], round(math.log(12) / math.log(60), 2)
        )

        slow = {
            "colors": [[0.4, 0.4, 0.2]],
            "color_space": "XYZ1",
            "transition_time": 600,
        }
        (layer,), _ = self.layers(pipe(["light.all"], "transitions", slow))
        self.assertEqual(layer["params"]["speed"], 0.05)
        self.assertEqual(layer["params"]["palette"], [{"xy": [0.4, 0.4]}])

        fast = {
            "colors": ["#ff0000"] * 12,
            "color_space": "HEX-sRGB255",
            "transition_time": 1,
        }
        (layer,), notes = self.layers(pipe(["light.all"], "transitions", fast))
        self.assertEqual(
            (len(layer["params"]["palette"]), layer["params"]["speed"]), (9, 1.0)
        )
        self.assertTrue(any("first 9" in n for n in notes))

        lab = {"colors": [[50, 20, 20]], "color_space": "CIELab"}
        layers, notes = self.layers(pipe(["light.all"], "transitions", lab))
        self.assertEqual(layers, [])
        self.assertTrue(any("CIELab" in n for n in notes))

    def test_dropped_layers_and_notes(self) -> None:
        layers, notes = self.layers(
            pipe(["light.l1"], "off", modifiers=[{"name": "brightness"}]),
            pipe(["light.l2"], "custom"),
            pipe(["light.l2"], "sparkles", {"x": 1}),
            pipe(["switch.fan"], "off"),
            pipe(["light.l2"], "static"),
        )
        self.assertEqual(
            layers, [{"lights": ["light.l1"], "effect": "off", "params": {}}]
        )
        self.assertEqual(len(notes), 5)
        self.assertTrue(any("modifiers" in n for n in notes))
        self.assertTrue(any("'custom'" in n for n in notes))
        self.assertTrue(any("'sparkles'" in n for n in notes))

    def test_scene_without_layers_is_dropped(self) -> None:
        config, notes = migrate_v1(
            {
                "Living": {
                    "areas": ["living"],
                    "scenes": {"S": [pipe(["light.l1"], "custom")]},
                }
            },
            AREAS,
        )
        self.assertEqual(config["scenes"], {})
        self.assertTrue(any("dropped" in n for n in notes))


class OutputTest(unittest.TestCase):
    V1 = {
        "Living": {
            "areas": ["living"],
            "scenes": {
                "Relax": [pipe(["light.all"], "static", {"preset": "Relax"})],
                "Party": [
                    pipe(["light.all"], "transitions", {"preset": "saturated-sunset"})
                ],
            },
        },
        "Downstairs": {
            "areas": ["living", "kitchen", "gone"],
            "scenes": {
                "Evening": [
                    pipe(["light.l1"], "static", {"light.l1": {"brightness": 50}}),
                    pipe(["light.k1"], "off"),
                ]
            },
        },
    }

    def test_ids_notes_and_validity(self) -> None:
        config, notes = migrate_v1(self.V1, AREAS)
        self.assertEqual(migrate_v1(self.V1, AREAS)[0], config)  # deterministic
        for scene_id, scene in config["scenes"].items():
            self.assertRegex(scene_id, r"^s_[0-9a-f]{8}$")
            self.assertEqual(scene["id"], scene_id)
        self.assertEqual(config["settings"]["migration_notes"], notes)
        self.assertTrue(notes)
        self.assertEqual(config["targets"], {})
        json.dumps(config)
        validated = validate(config, FakeRegistry())
        self.assertIsInstance(validated, Config)
        party = next(s for s in validated.scenes.values() if s.name == "Party")
        # The palette's default brightness, filled in.
        self.assertEqual(party.layers[0].params["brightness"], 60)
        self.assertTrue(all(re.match(r"^[a-z0-9_]+$", z) for z in config["zones"]))


class NamesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FakeRegistry()
        self.presets = load_presets(self.registry)

    def migrate(self, v1: dict) -> tuple[dict, list[str]]:
        return migrate_v1(v1, AREAS, presets=PRESETS)

    def test_collisions_are_renamed_and_validate(self) -> None:
        relax = [pipe(["light.all"], "static", {"preset": "Relax"})]
        long_name = "x" * 64
        v1 = {
            "Living": {
                "areas": ["living"],
                "scenes": {
                    "relax": relax,
                    "Relax (v1)": relax,
                    OPTION_MANUAL: relax,
                    long_name: relax,
                    long_name.upper(): relax,
                    "Movie": relax,
                },
            },
            # Collapses into the same room, so its names share the set.
            "Living again": {
                "entities": ["light.l1", "light.l2"],
                "scenes": {"MOVIE": relax},
            },
            "Kitchen": {
                "areas": ["kitchen"],
                "scenes": {"Movie": relax, "Read": relax},
            },
        }
        config, notes = self.migrate(v1)
        names = {(s["scope"][0], s["name"]) for s in config["scenes"].values()}
        self.assertEqual(
            names,
            {
                ("room:living", "relax (v1)"),
                ("room:living", "Relax (v1) (v1)"),
                ("room:living", "Manual (v1)"),
                ("room:living", long_name),
                ("room:living", "X" * 59 + " (v1)"),
                ("room:living", "Movie"),
                ("room:living", "MOVIE (v1)"),
                ("room:kitchen", "Movie"),
                ("room:kitchen", "Read (v1)"),
            },
        )
        self.assertEqual(sum("renamed" in n for n in notes), 6)
        validate(
            config,
            self.registry,
            presets=self.presets,
            targets={"room:living", "room:kitchen"},
        )

    def test_numbered_suffixes(self) -> None:
        relax = [pipe(["light.all"], "static", {"preset": "Relax"})]
        config, _ = self.migrate(
            {
                "Living": {
                    "areas": ["living"],
                    "scenes": {"Relax (v1)": relax, "RELAX": relax},
                }
            }
        )
        self.assertEqual(
            sorted(s["name"] for s in config["scenes"].values()),
            ["RELAX (v1 2)", "Relax (v1)"],
        )

    def test_without_collisions_names_are_kept(self) -> None:
        config, notes = self.migrate(OutputTest.V1)
        self.assertEqual(
            sorted(s["name"] for s in config["scenes"].values()),
            ["Evening", "Party", "Relax (v1)"],
        )
        self.assertFalse(any("Party" in n and "renamed" in n for n in notes))
        validate(config, self.registry, presets=self.presets)


class _ShellConfig:
    def __init__(self, config_dir: str) -> None:
        self.config_dir = config_dir

    def path(self, *parts: str) -> str:
        return str(Path(self.config_dir, *parts))


class _ShellBus:
    def async_listen_once(self, event_type: str, listener: Any) -> Any:
        return lambda: None


class _ShellHass:
    """Just enough of HomeAssistant for helpers.storage.Store."""

    def __init__(self, config_dir: str) -> None:
        self.data: dict[str, Any] = {}
        self.config = _ShellConfig(config_dir)
        self.loop = asyncio.get_running_loop()
        self.state = CoreState.running
        self.bus = _ShellBus()

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


class StoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = Path(tmp.name, ".storage")
        self.storage.mkdir()
        self.hass = _ShellHass(tmp.name)

    def write(self, key: str, version: int, data: Any) -> Path:
        path = self.storage / key
        path.write_text(
            json.dumps(
                {"version": version, "minor_version": 1, "key": key, "data": data}
            )
        )
        return path

    async def test_v1_store_migrates_with_backup(self) -> None:
        v1 = {
            "config": {
                "Living": {
                    "areas": ["living"],
                    "scenes": {
                        "Relax": [pipe(["light.all"], "static", {"preset": "Relax"})]
                    },
                }
            }
        }
        path = self.write("magic_lights", 1, v1)
        original = path.read_text()
        with (
            patch.object(store, "area_lights", return_value=AREAS),
            self.assertLogs("custom_components.magic_lights.store", "WARNING") as logs,
        ):
            config = await store.ConfigStore(self.hass).async_load_config()
        self.assertIn("Relax (v1)", logs.output[0])
        self.assertEqual(
            [(s.scope, s.name) for s in config.scenes.values()],
            [(("room:living",), "Relax (v1)")],
        )
        self.assertEqual(Path(f"{path}.v1.bak").read_text(), original)
        saved = json.loads(path.read_text())
        self.assertEqual((saved["version"], saved["minor_version"]), (2, 1))
        self.assertEqual(Config.from_dict(saved["data"]), config)

    async def test_config_round_trip(self) -> None:
        self.assertIsNone(await store.ConfigStore(self.hass).async_load_config())
        config = Config(settings=Settings(transition=2.0, migration_notes=("hello",)))
        await store.ConfigStore(self.hass).async_save_config(config)
        self.assertEqual(await store.ConfigStore(self.hass).async_load_config(), config)

    async def test_import_v1(self) -> None:
        yaml = V1_CONFIG_SCHEMA(
            {
                "Kitchen": {
                    "entities": ["light.k1"],
                    "scenes": {
                        "Cook": [pipe(["light.all"], "static", {"preset": "Bright"})]
                    },
                }
            }
        )
        with patch.object(store, "area_lights", return_value=AREAS):
            config = await store.ConfigStore(self.hass).async_import_v1(yaml)
        self.assertEqual(await store.ConfigStore(self.hass).async_load_config(), config)
        self.assertEqual([s.name for s in config.scenes.values()], ["Cook"])

    def test_area_lights_lists_empty_areas(self) -> None:
        def entry(entity_id: str, **kw: Any) -> SimpleNamespace:
            return SimpleNamespace(
                entity_id=entity_id,
                domain=entity_id.split(".")[0],
                disabled_by=kw.get("disabled_by"),
                entity_category=None,
                platform=kw.get("platform", "hue"),
                area_id=kw.get("area_id"),
                device_id=kw.get("device_id"),
            )

        entities = [
            entry("light.a", area_id="living"),
            entry("light.b", device_id="dev1"),
            entry("light.group", area_id="living", platform="group"),
            entry("light.off", area_id="living", disabled_by="user"),
            entry("switch.s", area_id="living"),
        ]
        devices = {"dev1": SimpleNamespace(area_id="kitchen")}
        with (
            patch.object(
                store.ar,
                "async_get",
                return_value=SimpleNamespace(
                    areas={"living": 1, "kitchen": 2, "hall": 3}
                ),
            ),
            patch.object(
                store.er,
                "async_get",
                return_value=SimpleNamespace(
                    entities={e.entity_id: e for e in entities}
                ),
            ),
            patch.object(
                store.dr,
                "async_get",
                return_value=SimpleNamespace(async_get=devices.get),
            ),
        ):
            result = store.area_lights(self.hass)
        self.assertEqual(
            result,
            {
                "living": frozenset({"light.a"}),
                "kitchen": frozenset({"light.b"}),
                "hall": frozenset(),
            },
        )

    async def test_runtime_store(self) -> None:
        runtime = store.RuntimeStoreImpl(self.hass)
        self.assertIsNone(await runtime.load())
        snapshot = RuntimeV1(
            (
                ActivationSnapshot(
                    "a1",
                    "room:living",
                    "relax",
                    datetime(2026, 5, 1, tzinfo=UTC),
                    ("light.l1",),
                    (),
                    {},
                    False,
                ),
            ),
            {"relax|room:living": 2},
        )
        runtime.schedule_save(snapshot)
        await runtime.flush()
        self.assertEqual(await store.RuntimeStoreImpl(self.hass).load(), snapshot)
        self.write("magic_lights.runtime", 1, {"activations": [{"id": "broken"}]})
        with self.assertLogs("custom_components.magic_lights.store", "WARNING"):
            self.assertIsNone(await store.RuntimeStoreImpl(self.hass).load())


if __name__ == "__main__":
    unittest.main()
