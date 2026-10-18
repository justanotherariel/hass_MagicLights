"""presets/scenes.json against the real effects."""

from __future__ import annotations

import json
import random
import re
import unittest

from custom_components.magic_lights.effects import BUILTIN, Registry
from custom_components.magic_lights.effects.base import RenderContext, as_frame
from custom_components.magic_lights.effects.schema import PaletteSelector
from custom_components.magic_lights.models import normalise
from custom_components.magic_lights.presets import (
    SCENES_FILE,
    load_presets,
    palettes_from_presets,
)

from .fakes import BERLIN, START, make_caps, make_light

IDS = [
    "energize", "concentrate", "read", "bright", "relax", "dimmed", "nightlight", "rest",
    "adaptive", "adaptive_bright", "adaptive_relax",
    "savanna_sunset", "tropical_twilight", "arctic_aurora", "spring_blossom", "honolulu",
    "fairfax", "tokyo", "galaxy", "miami", "ruby_glow", "rio", "chinatown", "disturbia",
    "motown", "nebula", "sundown", "blood_moon", "golden_star", "midsummer_sun",
    "autumn_gold", "frosty_dawn", "winter_beauty", "soho", "palm_beach", "blue_lagoon",
    "ocean_dawn", "golden_pond", "rosy_sparkle", "lake_placid", "amethyst_valley",
    "magneto", "moonlight", "sleepy", "starlight", "crocus",
    "cozy_candle", "fireplace",
]  # fmt: skip

LIGHTS = (
    make_light("light.colour"),
    make_light("light.gradient", caps=make_caps(gradient_points=5)),
    make_light("light.hue", caps=make_caps(effects=frozenset({"candle", "fireplace"}))),
    make_light("light.white", caps=make_caps(color=False)),
    make_light("light.dimmer", caps=make_caps(color=False, color_temp=False)),
    make_light(
        "light.plug",
        caps=make_caps(
            brightness=False, color=False, color_temp=False, transition=False
        ),
    ),
)

HEX = re.compile(r"^#[0-9a-f]{6}$")


class PresetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = Registry(BUILTIN)
        cls.raw = json.loads(SCENES_FILE.read_text(encoding="utf-8"))
        cls.presets = load_presets(cls.registry)

    def test_all_load_with_stable_ids(self) -> None:
        with self.assertNoLogs("custom_components.magic_lights.presets", "ERROR"):
            presets = load_presets(self.registry)
        self.assertEqual(list(presets), IDS)

    def test_every_param_validates(self) -> None:
        for item in self.raw:
            for i, layer in enumerate(item["layers"]):
                with self.subTest(item["id"], layer=i):
                    effect = self.registry.get(layer["effect"])
                    keys = {marker.schema for marker in effect.schema.schema}
                    self.assertLessEqual(set(layer["params"]), keys)
                    params = effect.validate(layer["params"])
                    self.assertEqual(effect.validate(params), params)
                    self.assertEqual(self.presets[item["id"]].layers[i].params, params)

    def test_every_preset_renders(self) -> None:
        members = tuple(light.entity_id for light in LIGHTS)
        for scene in self.presets.values():
            for i, layer in enumerate(scene.layers):
                with self.subTest(scene.id, layer=i):
                    effect = self.registry.get(layer.effect)
                    ctx = RenderContext(
                        lights=LIGHTS,
                        members=members,
                        params=layer.params,
                        now=START,
                        t=0.0,
                        sun=BERLIN,
                        rng=random.Random(f"{scene.id}|room:test|{i}|0"),
                        memory={},
                        prev={},
                        min_period=0.5,
                        adjust={},
                        first=True,
                    )
                    frame = as_frame(effect.render(ctx))
                    self.assertEqual(set(frame.states), set(members))
                    for light in LIGHTS:
                        state = frame.states[light.entity_id]
                        self.assertTrue(state.on)
                        if state.brightness is not None:
                            self.assertTrue(0 < state.brightness <= 1)
                        if state.effect is not None:
                            self.assertTrue(light.supports_effect(state.effect))
                            self.assertIsNotNone(state.color)
                        if state.gradient is not None:
                            self.assertTrue(light.caps.gradient_points)
                            self.assertIsNotNone(state.color)
                        normalise(state, light.caps)
                    swatches = effect.swatches(layer.params)
                    self.assertTrue(1 <= len(swatches) <= 5)
                    self.assertTrue(all(HEX.match(s) for s in swatches), swatches)

    def test_adaptive_presets_hold_their_day_brightness(self) -> None:
        for item in self.raw:
            if item["category"] != "adaptive":
                continue
            with self.subTest(item["id"]):
                params = self.presets[item["id"]].layers[0].params
                self.assertEqual(params["brightness_mode"], "static")
                self.assertEqual(params["brightness"], params["max_brightness"])
                self.assertLess(params["min_brightness"], params["max_brightness"])

    def test_categories_match_effects(self) -> None:
        effects = {
            "white": {"white", "palette"},
            "color": {"palette"},
            "dynamic": {"light_effect"},
            "adaptive": {"adaptive"},
        }
        for scene in self.presets.values():
            with self.subTest(scene.id):
                self.assertIn(scene.layers[0].effect, effects[scene.category])

    def test_palettes_validate(self) -> None:
        palettes = palettes_from_presets(self.presets)
        self.assertEqual(len(palettes), 35)
        selector = PaletteSelector({"min": 1, "max": 9})
        for palette in palettes:
            with self.subTest(palette["id"]):
                self.assertEqual(selector(palette["colors"]), palette["colors"])


if __name__ == "__main__":
    unittest.main()
