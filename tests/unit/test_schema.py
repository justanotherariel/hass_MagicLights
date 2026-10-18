"""Param selectors, ha-form serialisation, the effect base and the preset catalogue."""

from __future__ import annotations

import json
import unittest

import voluptuous as vol

from custom_components.magic_lights.effects.base import Effect, as_frame
from custom_components.magic_lights.effects.schema import (
    KELVIN,
    PCT,
    ColorSelector,
    PaletteSelector,
    defaults_of,
    serialize_schema,
)
from custom_components.magic_lights.models import Frame, LightState
from custom_components.magic_lights.presets import (
    SCENES_FILE,
    load_presets,
    palettes_from_presets,
)
from custom_components.magic_lights.validation import RESERVED_NAMES
from homeassistant.helpers import selector

from .fakes import PARAM_TABLE, FakeRegistry

SCHEMA = vol.Schema(
    {
        vol.Required(
            "color", default={"kelvin": 2700}, description={"label": "Colour"}
        ): ColorSelector(),
        vol.Required("palette"): PaletteSelector({"min": 2, "max": 5}),
        vol.Optional(
            "speed",
            default=50,
            description={
                "helper": "Seconds",
                "visible": {"field": "motion", "value": "dynamic"},
            },
        ): PCT,
        vol.Optional("kelvin"): KELVIN(2000, 6000),
    }
)


class SelectorTest(unittest.TestCase):
    def test_color_selector(self) -> None:
        sel = ColorSelector()
        self.assertEqual(sel({"kelvin": 3000}), {"kelvin": 3000})
        self.assertEqual(sel("#ff0000"), {"xy": [0.701, 0.299]})
        self.assertEqual(sel({"rgb": [255, 0, 0]}), {"xy": [0.701, 0.299]})
        for bad in ({"kelvin": 500}, {"kelvin": 12000}, {"hs": [1, 2]}, "blue", None):
            with self.subTest(bad=bad), self.assertRaises(vol.Invalid):
                sel(bad)
        white_only = ColorSelector({"allow_color": False})
        self.assertEqual(white_only({"kelvin": 2200}), {"kelvin": 2200})
        with self.assertRaises(vol.Invalid):
            white_only({"xy": [0.5, 0.4]})
        with self.assertRaises(vol.Invalid):
            ColorSelector({"allow_white": False})({"kelvin": 2700})
        with self.assertRaises(vol.Invalid):
            ColorSelector({"bogus": 1})

    def test_palette_selector(self) -> None:
        sel = PaletteSelector({"min": 2, "max": 3})
        self.assertEqual(
            sel(["#ff0000", {"kelvin": 2700}]),
            [{"xy": [0.701, 0.299]}, {"kelvin": 2700}],
        )
        for bad in (
            [{"kelvin": 2700}],
            [{"kelvin": 2700}] * 4,
            "#ff0000",
            [{"rgb": [0, 0, 0]}] * 2,
        ):
            with self.subTest(bad=bad), self.assertRaises(vol.Invalid):
                sel(bad)

    def test_in_schema(self) -> None:
        out = SCHEMA({"palette": ["#ff0000", "#00ff00"]})
        self.assertEqual(out["color"], {"kelvin": 2700})
        self.assertEqual(out["speed"], 50)
        with self.assertRaises(vol.MultipleInvalid) as ctx:
            SCHEMA({"palette": ["#ff0000"], "kelvin": 7000})
        self.assertIn(ctx.exception.path[0], ("palette", "kelvin"))

    def test_shared_selectors(self) -> None:
        self.assertEqual(PCT(55), 55.0)
        with self.assertRaises(vol.Invalid):
            PCT(0)
        self.assertEqual(KELVIN().config, {"unit": "kelvin", "min": 1500, "max": 6500})
        with self.assertRaises(vol.Invalid):
            KELVIN(2000, 6000)(1900)


class SerializeTest(unittest.TestCase):
    def test_fields(self) -> None:
        fields = {f["name"]: f for f in serialize_schema(SCHEMA)}
        color = fields["color"]
        self.assertEqual(
            color["selector"],
            {
                "magic_lights_color": {
                    "allow_white": True,
                    "allow_color": True,
                    "min_kelvin": 1000,
                    "max_kelvin": 10000,
                }
            },
        )
        self.assertEqual(
            (color["required"], color["default"], color["label"]),
            (True, {"kelvin": 2700}, "Colour"),
        )
        self.assertNotIn("description", color)
        self.assertEqual(
            fields["palette"]["selector"],
            {"magic_lights_palette": {"min": 2, "max": 5}},
        )
        speed = fields["speed"]
        self.assertEqual(speed["visible"], {"field": "motion", "value": "dynamic"})
        self.assertEqual(speed["helper"], "Seconds")
        self.assertFalse(speed["required"])
        self.assertEqual(
            fields["kelvin"]["selector"],
            {"color_temp": {"unit": "kelvin", "min": 2000, "max": 6000}},
        )
        json.dumps(list(fields.values()))

    def test_param_table_serialises(self) -> None:
        for key, schema in PARAM_TABLE.items():
            with self.subTest(key):
                self.assertIsInstance(serialize_schema(schema), list)

    def test_layout_hints_are_lifted(self) -> None:
        steps = [{"value": 30, "label": "Short 30 min"}]
        low, high = serialize_schema(
            vol.Schema(
                {
                    vol.Required(
                        "low",
                        description={
                            "advanced": True,
                            "pair": {"max": "high", "label": "Range"},
                            "row": "r",
                            "steps": steps,
                            "ends": ["Dim", "Bright"],
                            "hints": {"1": "Dim"},
                            "other": "dropped",
                        },
                    ): PCT,
                    vol.Required("high"): PCT,
                }
            )
        )
        self.assertEqual(
            {k: low[k] for k in ("advanced", "pair", "row", "steps", "ends", "hints")},
            {
                "advanced": True,
                "pair": {"max": "high", "label": "Range"},
                "row": "r",
                "steps": steps,
                "ends": ["Dim", "Bright"],
                "hints": {"1": "Dim"},
            },
        )
        self.assertNotIn("other", low)
        self.assertNotIn("advanced", high)

    def test_plain_description_is_dropped(self) -> None:
        (field,) = serialize_schema(
            vol.Schema({vol.Required("a", description="A"): PCT})
        )
        self.assertNotIn("description", field)
        self.assertNotIn("label", field)

    def test_empty(self) -> None:
        self.assertEqual(serialize_schema(vol.Schema({})), [])

    def test_failure_returns_none(self) -> None:
        nested = vol.Schema({vol.Required("outer"): {vol.Required("inner"): PCT}})
        with self.assertLogs(
            "custom_components.magic_lights.effects.schema", "WARNING"
        ):
            self.assertIsNone(serialize_schema(nested))

    def test_defaults_of(self) -> None:
        schema = vol.Schema(
            {
                vol.Required("a", default=1): PCT,
                vol.Required("b"): PCT,
                vol.Optional("c", default=list): selector.ObjectSelector(),
                vol.Optional("d"): PCT,
                vol.Remove("e"): PCT,
            }
        )
        self.assertEqual(defaults_of(schema), {"a": 1, "c": []})
        self.assertEqual(defaults_of(vol.Schema({})), {})


class EffectBaseTest(unittest.TestCase):
    def test_validate_and_frame(self) -> None:
        class Glow(Effect):
            key = "glow"
            name = "Glow"
            schema = vol.Schema({vol.Required("brightness", default=50): PCT})

            def render(self, ctx):
                return {}

        self.assertEqual(Glow.validate({}), {"brightness": 50})
        self.assertEqual(Glow().validate({"brightness": 20}), {"brightness": 20})
        with self.assertRaises(vol.Invalid):
            Glow.validate({"other": 1})
        self.assertEqual(
            (Glow.swatches({}), Glow.icon, Glow.category),
            ([], "mdi:auto-fix", "other"),
        )
        frame = Frame({"light.a": LightState()}, next_update=2.0)
        self.assertIs(as_frame(frame), frame)
        self.assertEqual(
            as_frame({"light.a": LightState()}), Frame({"light.a": LightState()})
        )

    def test_render_is_abstract(self) -> None:
        class Broken(Effect):
            key = "broken"
            name = "Broken"

        with self.assertRaises(TypeError):
            Broken()


class PresetCatalogueTest(unittest.TestCase):
    """scenes.json against the parameter table (fakes.PARAM_TABLE)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = json.loads(SCENES_FILE.read_text(encoding="utf-8"))
        cls.presets = load_presets(FakeRegistry())

    def test_all_load(self) -> None:
        self.assertEqual(len(self.raw), 48)
        self.assertEqual(list(self.presets), [s["id"] for s in self.raw])
        self.assertTrue(
            all(s.builtin and s.preset is None for s in self.presets.values())
        )

    def test_categories(self) -> None:
        by_category: dict[str, list[str]] = {}
        for scene in self.presets.values():
            by_category.setdefault(scene.category, []).append(scene.id)
        self.assertEqual(
            {k: len(v) for k, v in by_category.items()},
            {"white": 8, "adaptive": 3, "color": 35, "dynamic": 2},
        )

    def test_names_and_icons(self) -> None:
        names = [s.name.casefold() for s in self.presets.values()]
        self.assertEqual(len(set(names)), 48)
        self.assertFalse(set(names) & RESERVED_NAMES)
        self.assertTrue(
            all(s.icon and s.icon.startswith("mdi:") for s in self.presets.values())
        )

    def test_exact_param_keys(self) -> None:
        for item in self.raw:
            for layer in item["layers"]:
                with self.subTest(item["id"]):
                    keys = {m.schema for m in PARAM_TABLE[layer["effect"]].schema}
                    self.assertLessEqual(set(layer["params"]), keys)
            self.assertEqual(
                (item["others"], item["brightness"], item["transition"]),
                ("off", 100, None),
            )
            self.assertNotIn("scope", item)

    def test_values(self) -> None:
        relax = self.presets["relax"].layers[0]
        self.assertEqual(
            (relax.effect, relax.params["kelvin"], relax.params["brightness"]),
            ("white", 2237, 56),
        )
        night = self.presets["nightlight"].layers[0].params
        self.assertEqual(
            (night["palette"], night["white"], night["brightness"]),
            ([{"xy": [0.561, 0.4042]}], {"kelvin": 2000}, 1),
        )
        savanna = self.presets["savanna_sunset"].layers[0].params
        self.assertEqual(len(savanna["palette"]), 5)
        self.assertEqual(
            (savanna["motion"], savanna["gradients"], savanna["brightness"]),
            ("static", True, 58),
        )
        candle = self.presets["cozy_candle"].layers[0]
        self.assertEqual(
            (candle.effect, candle.params["effect"], candle.params["brightness"]),
            ("light_effect", "candle", 35),
        )
        adaptive = self.presets["adaptive"].layers[0].params
        self.assertEqual(
            (adaptive["min_sunset"], adaptive["max_sunset"], adaptive["interval"]),
            ("18:30:00", "21:00:00", 90),
        )

    def test_palettes(self) -> None:
        palettes = palettes_from_presets(self.presets)
        self.assertEqual(len(palettes), 35)
        self.assertEqual(set(palettes[0]), {"id", "name", "colors"})
        json.dumps(palettes)

    def test_unknown_effect_is_skipped(self) -> None:
        registry = FakeRegistry()
        del registry.effects["adaptive"]
        with self.assertLogs("custom_components.magic_lights.presets", "ERROR"):
            presets = load_presets(registry)
        self.assertEqual(len(presets), 45)


if __name__ == "__main__":
    unittest.main()
