"""presets/parts.json against the real effects."""

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
    PARTS_FILE,
    load_parts,
    palettes_from_parts,
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


class BuiltinPartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = Registry(BUILTIN)
        cls.raw = json.loads(PARTS_FILE.read_text(encoding="utf-8"))
        cls.parts = load_parts(cls.registry)

    def test_all_load_with_stable_ids(self) -> None:
        with self.assertNoLogs("custom_components.magic_lights.presets", "ERROR"):
            parts = load_parts(self.registry)
        self.assertEqual(list(parts), IDS)

    def test_every_param_validates(self) -> None:
        for item in self.raw:
            with self.subTest(item["id"]):
                effect = self.registry.get(item["effect"])
                keys = {marker.schema for marker in effect.schema.schema}
                self.assertLessEqual(set(item["params"]), keys)
                params = effect.validate(item["params"])
                self.assertEqual(effect.validate(params), params)
                self.assertEqual(self.parts[item["id"]].params, params)

    def test_every_part_renders(self) -> None:
        members = tuple(light.entity_id for light in LIGHTS)
        for part in self.parts.values():
            with self.subTest(part.id):
                effect = self.registry.get(part.effect)
                ctx = RenderContext(
                    lights=LIGHTS,
                    members=members,
                    params=part.params,
                    now=START,
                    t=0.0,
                    sun=BERLIN,
                    rng=random.Random(f"{part.id}|room:test|0|0"),
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
                swatches = effect.swatches(part.params)
                self.assertTrue(1 <= len(swatches) <= 5)
                self.assertTrue(all(HEX.match(s) for s in swatches), swatches)

    def test_adaptive_parts_hold_their_day_brightness(self) -> None:
        for item in self.raw:
            if item["category"] != "adaptive":
                continue
            with self.subTest(item["id"]):
                params = self.parts[item["id"]].params
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
        for part in self.parts.values():
            with self.subTest(part.id):
                self.assertIn(part.effect, effects[part.category])

    def test_palettes_validate(self) -> None:
        palettes = palettes_from_parts(self.parts)
        self.assertEqual(len(palettes), 35)
        selector = PaletteSelector({"min": 1, "max": 9})
        for palette in palettes:
            with self.subTest(palette["id"]):
                self.assertEqual(selector(palette["colors"]), palette["colors"])


if __name__ == "__main__":
    unittest.main()
