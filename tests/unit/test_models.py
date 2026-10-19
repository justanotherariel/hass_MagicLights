"""Domain types: parsing, normalisation, storage round trips and validation."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
import json
import unittest

import voluptuous as vol

from custom_components.magic_lights import color as color_util
from custom_components.magic_lights.context import create_context, is_ours
from custom_components.magic_lights.models import (
    OFF,
    ActivationSnapshot,
    Caps,
    Color,
    Config,
    HaLight,
    Layer,
    LightState,
    Part,
    PartLayer,
    RuntimeV1,
    Scene,
    Settings,
    StoredScene,
    TargetOptions,
    TargetStatus,
    Zone,
    all_parts,
    ha_to_state,
    is_user_part,
    is_whole_room,
    normalise,
    part_scene,
    resolve,
)
from custom_components.magic_lights.validation import (
    ConfigError,
    validate,
    validate_builtin_part,
    validate_draft,
)
from homeassistant.core import Context, State

from .fakes import FakeRegistry, make_caps

FULL = make_caps(min_kelvin=2202, max_kelvin=6535, effects=frozenset({"candle"}))
CT_ONLY = make_caps(color=False, min_kelvin=2700, max_kelvin=6500)
COLOR_ONLY = make_caps(color_temp=False)
DIM_ONLY = make_caps(color=False, color_temp=False)
ON_OFF = make_caps(brightness=False, color=False, color_temp=False, transition=False)


WARM = {"kelvin": 2700, "brightness": 100}
BUILTINS = {
    "relax": Part("relax", "Relax", "white", WARM, "mdi:sofa", "white"),
    "read": Part("read", "Read", "white", WARM, "mdi:book", "white"),
}


def part_dict(part_id: str, name: str, **kw):
    return {"id": part_id, "name": name, "effect": "white", "params": {}, **kw}


def stored_scene(scene_id: str, name: str, *parts: str, **kw):
    layers = [{"lights": "all", "part": part} for part in parts or ("relax",)]
    return {"id": scene_id, "name": name, "layers": layers, **kw}


class ColorTest(unittest.TestCase):
    def test_parse_forms(self) -> None:
        self.assertEqual(Color.parse({"kelvin": 2700.4}), Color(kelvin=2700))
        self.assertEqual(Color.parse({"xy": [0.123456, 0.4]}), Color(xy=(0.1235, 0.4)))
        self.assertEqual(Color.parse({"rgb": [255, 0, 0]}), Color(xy=(0.701, 0.299)))
        self.assertEqual(Color.parse("#FF0000"), Color(xy=(0.701, 0.299)))
        same = Color(kelvin=3000)
        self.assertIs(Color.parse(same), same)

    def test_parse_rejects(self) -> None:
        for bad in (
            {"kelvin": 2700, "xy": [0.3, 0.3]},
            {"hs": [10, 10]},
            {"kelvin": True},
            {"kelvin": -5},
            {"xy": [0.3]},
            {"xy": [1.2, 0.3]},
            {"xy": [0.3, 0]},
            {"rgb": [0, 0, 0]},
            {"rgb": [256, 0, 0]},
            "#12345",
            "red",
            [0.3, 0.3],
            None,
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Color.parse(bad)
        with self.assertRaises(ValueError):
            Color()
        with self.assertRaises(ValueError):
            Color(kelvin=2700, xy=(0.3, 0.3))

    def test_conversions(self) -> None:
        warm = Color(kelvin=2700)
        self.assertEqual(warm.to_dict(), {"kelvin": 2700})
        self.assertEqual(warm.as_xy(), color_util.xy_from_kelvin(2700))
        self.assertEqual(warm.as_kelvin(), 2700)
        self.assertEqual(warm.as_hex(), "#ffa757")
        candle = Color(xy=(0.561, 0.4042))
        self.assertEqual(candle.to_dict(), {"xy": [0.561, 0.4042]})
        self.assertEqual(candle.as_xy(), (0.561, 0.4042))
        self.assertEqual(candle.as_kelvin(), color_util.kelvin_from_xy((0.561, 0.4042)))
        self.assertRegex(candle.as_hex(), r"^#[0-9a-f]{6}$")


class NormaliseTest(unittest.TestCase):
    def test_kelvin(self) -> None:
        st = LightState(brightness=0.5, color=Color(kelvin=2000))
        self.assertEqual(normalise(st, FULL).color, Color(kelvin=2202))
        self.assertEqual(normalise(st, CT_ONLY).color, Color(kelvin=2700))
        hot = LightState(color=Color(kelvin=9000))
        self.assertEqual(normalise(hot, CT_ONLY).color, Color(kelvin=6500))
        x, y = Color(kelvin=2000).as_xy()
        self.assertEqual(normalise(st, COLOR_ONLY).color, Color(xy=(x, y)))

    def test_xy(self) -> None:
        st = LightState(color=Color(xy=(0.6, 0.35)))
        self.assertEqual(normalise(st, FULL).color, Color(xy=(0.6, 0.35)))
        self.assertEqual(normalise(st, COLOR_ONLY).color, Color(xy=(0.6, 0.35)))
        warm = normalise(LightState(color=Color(xy=(0.561, 0.4042))), CT_ONLY)
        self.assertEqual(warm.color, Color(kelvin=2700))  # ~1800 K, clamped
        neutral = normalise(LightState(color=Color(xy=(0.38, 0.38))), CT_ONLY)
        mccamy = Color(xy=(0.38, 0.38)).as_kelvin()
        self.assertLessEqual(
            abs(color_util.mired(neutral.color.kelvin) - color_util.mired(mccamy)), 1
        )
        cold = normalise(LightState(color=Color(xy=(0.3, 0.3))), CT_ONLY)
        self.assertEqual(cold.color, Color(kelvin=6500))

    def test_saturated_xy_on_white_only_lights(self) -> None:
        # The nearest white, not McCamy's formula far off the white curve.
        for hex_color, kelvin in (
            ("#ff0000", 2700),
            ("#ff8000", 2700),
            ("#ff00ff", 2700),
            ("#0000ff", 6500),
            ("#00ffff", 6500),
        ):
            with self.subTest(hex_color):
                state = LightState(color=Color.parse(hex_color))
                self.assertEqual(normalise(state, CT_ONLY).color, Color(kelvin=kelvin))

    def test_capability_stripping(self) -> None:
        st = LightState(brightness=0.4, color=Color(kelvin=3000), transition=2)
        self.assertEqual(
            normalise(st, DIM_ONLY), LightState(brightness=102 / 255, transition=2)
        )
        self.assertEqual(normalise(st, ON_OFF), LightState())

    def test_brightness_rounding(self) -> None:
        caps = FULL
        self.assertEqual(
            normalise(LightState(brightness=0.5), caps).brightness, 128 / 255
        )
        self.assertEqual(
            normalise(LightState(brightness=0.0001), caps).brightness, 1 / 255
        )
        self.assertEqual(
            normalise(LightState(brightness=0.0), caps).brightness, 1 / 255
        )
        self.assertEqual(normalise(LightState(brightness=1.3), caps).brightness, 1.0)
        self.assertIsNone(normalise(LightState(), caps).brightness)

    def test_effects(self) -> None:
        st = LightState(color=Color(xy=(0.5, 0.4)), effect="candle", effect_speed=0.3)
        self.assertEqual(normalise(st, FULL).effect, "candle")
        self.assertEqual(normalise(st, FULL).effect_speed, 0.3)
        dropped = normalise(st.replace(effect="sparkle"), FULL)
        self.assertIsNone(dropped.effect)
        self.assertIsNone(dropped.effect_speed)
        self.assertEqual(dropped.color, Color(xy=(0.5, 0.4)))

    def test_gradient(self) -> None:
        colors = tuple(Color(xy=(0.1 + i / 20, 0.3)) for i in range(5))
        strip = make_caps(gradient_points=3)
        out = normalise(LightState(gradient=colors), strip)
        self.assertEqual(out.gradient, colors[:3])
        self.assertEqual(out.color, colors[2])  # midpoint of the requested gradient
        plain = normalise(LightState(gradient=colors), FULL)
        self.assertIsNone(plain.gradient)
        self.assertEqual(plain.color, colors[2])
        explicit = normalise(
            LightState(color=Color(kelvin=3000), gradient=colors), FULL
        )
        self.assertEqual(explicit.color, Color(kelvin=3000))

    def test_off_and_transition(self) -> None:
        st = LightState(
            on=False, brightness=0.5, color=Color(kelvin=3000), transition=3
        )
        self.assertEqual(normalise(st, FULL), LightState(on=False, transition=3))
        self.assertEqual(normalise(st, ON_OFF), OFF)
        self.assertIsNone(normalise(LightState(transition=4), ON_OFF).transition)


class HaLightTest(unittest.TestCase):
    def test_from_state(self) -> None:
        on = State(
            "light.a",
            "on",
            {
                "brightness": 128,
                "color_temp_kelvin": 2700,
                "xy_color": (0.46, 0.41),
                "effect": "off",
                "color_mode": "color_temp",
            },
        )
        self.assertEqual(
            HaLight.from_state(on),
            HaLight(True, True, 128, 2700, (0.46, 0.41), "off", "color_temp"),
        )
        self.assertEqual(
            HaLight.from_state(State("light.a", "unavailable")),
            HaLight(False, False, None, None, None, None, None),
        )
        self.assertFalse(HaLight.from_state(State("light.a", "unknown")).available)
        off = HaLight.from_state(State("light.a", "off"))
        self.assertTrue(off.available)
        self.assertFalse(off.on)

    def test_ha_to_state(self) -> None:
        ct = HaLight(True, True, 255, 2700, (0.46, 0.41), None, "color_temp")
        self.assertEqual(
            ha_to_state(ct), LightState(brightness=1.0, color=Color(kelvin=2700))
        )
        xy = HaLight(True, True, 51, 2700, (0.6, 0.35), "candle", "xy")
        self.assertEqual(
            ha_to_state(xy),
            LightState(brightness=0.2, color=Color(xy=(0.6, 0.35)), effect="candle"),
        )
        for none in (None, "off", "none", "None"):
            light = HaLight(True, True, None, None, None, none, "onoff")
            self.assertEqual(ha_to_state(light), LightState())
        self.assertEqual(
            ha_to_state(HaLight(True, False, 0, None, None, None, None)), OFF
        )


class RoundTripTest(unittest.TestCase):
    def config(self) -> Config:
        scene = StoredScene(
            "s_0123abcd",
            "Movie",
            "mdi:movie",
            (
                PartLayer(("light.a", "room:kitchen"), "p_0123abcd"),
                PartLayer("all", "relax"),
            ),
            others="keep",
            transition=2.5,
        )
        return Config(
            zones={
                "down": Zone(
                    "down", "Downstairs", None, ("living", "kitchen"), ("light.x",)
                )
            },
            targets={
                "zone:down": TargetOptions(
                    excluded_lights=("light.b",),
                    default_scene="s_0123abcd",
                    detect_changes=False,
                ),
                "room:kitchen": TargetOptions(),
            },
            parts={
                "p_0123abcd": Part("p_0123abcd", "Hell", "white", {"kelvin": 3000}),
                "relax": Part("relax", "Relax", "white", WARM),
            },
            scenes={"zone:down": (scene,), "room:kitchen": ()},
            settings=Settings(
                transition=0.5,
                default_scene="p_0123abcd",
                native_effects=False,
                buses={"zigbee:abc": {"rate": 5.0}},
                light_buses={"light.a": "zigbee:abc"},
            ),
        )

    def test_config(self) -> None:
        config = self.config()
        data = json.loads(json.dumps(config.to_dict()))
        self.assertEqual(set(data), {"zones", "targets", "parts", "scenes", "settings"})
        # Default options are not stored.
        self.assertEqual(list(data["targets"]), ["zone:down"])
        self.assertEqual(
            set(data["targets"]["zone:down"]),
            {
                "excluded_lights",
                "extra_lights",
                "default_scene",
                "reclaim_on_turn_on",
                "detect_changes",
            },
        )
        self.assertEqual(
            data["scenes"]["zone:down"][0],
            {
                "id": "s_0123abcd",
                "name": "Movie",
                "icon": "mdi:movie",
                "layers": [
                    {"lights": ["light.a", "room:kitchen"], "part": "p_0123abcd"},
                    {"lights": "all", "part": "relax"},
                ],
                "others": "keep",
                "transition": 2.5,
            },
        )
        expected = dataclasses.replace(
            config, targets={"zone:down": config.targets["zone:down"]}
        )
        self.assertEqual(Config.from_dict(data), expected)
        self.assertEqual(Config.from_dict({}), Config())
        validate(data, FakeRegistry(), BUILTINS)

    def test_part_stored_keys(self) -> None:
        # Icons and categories live in the catalogue, not the store.
        self.assertEqual(
            BUILTINS["relax"].to_dict(),
            {"id": "relax", "name": "Relax", "effect": "white", "params": WARM},
        )
        self.assertEqual(
            Part.from_dict({**part_dict("read", "Read"), "icon": "mdi:book"}).icon,
            "mdi:book",
        )

    def test_runtime(self) -> None:
        snap = ActivationSnapshot(
            id="01J0",
            target="room:living",
            scene_id="relax",
            started=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            lights=("light.a", "light.b"),
            manual=("light.b",),
            adjust={"brightness": 40, "dynamic": True},
            only_on=True,
        )
        runtime = RuntimeV1((snap,), {"relax|room:living": 3})
        data = json.loads(json.dumps(runtime.to_dict()))
        self.assertEqual(RuntimeV1.from_dict(data), runtime)
        self.assertEqual(RuntimeV1.from_dict({}), RuntimeV1())

    def test_target_status(self) -> None:
        status = TargetStatus(
            "zone:down",
            "mixed",
            lights={"light.a": "elsewhere", "light.b": "manual"},
            elsewhere={"light.a": ("room:living", "relax")},
            parts=(("room:living", "relax", "Relax", ("light.a",)),),
            manual=("light.b",),
            since=datetime(2026, 1, 1, tzinfo=UTC),
        )
        data = json.loads(json.dumps(status.to_dict()))
        self.assertEqual(data["status"], "mixed")
        self.assertEqual(
            data["elsewhere"],
            {"light.a": {"target_id": "room:living", "scene_id": "relax"}},
        )
        self.assertEqual(
            data["parts"],
            [
                {
                    "target_id": "room:living",
                    "scene_id": "relax",
                    "scene_name": "Relax",
                    "lights": ["light.a"],
                }
            ],
        )
        self.assertEqual(data["since"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(
            set(data),
            {"status", "scene_id", "scene_name", "via", "partial", "dynamic", "motion",
             "brightness", "manual", "lights", "elsewhere", "parts", "since", "errors"},
        )  # fmt: skip

    def test_caps_dict(self) -> None:
        caps = make_caps(effects=frozenset({"fireplace", "candle"}), gradient_points=9)
        self.assertEqual(caps.to_dict()["effects"], ["candle", "fireplace"])
        json.dumps(caps.to_dict())

    def test_frozen_slots(self) -> None:
        for obj in (
            Color(kelvin=2700),
            LightState(),
            Caps(True, True, True),
            Settings(),
            Config(),
            Part("p_00000001", "Hell", "white", {}),
            PartLayer("all", "p_00000001"),
            StoredScene("s_00000001", "Hell", None, ()),
            Scene("s_00000001", "Hell", None, ()),
        ):
            with self.subTest(type(obj).__name__):
                self.assertFalse(hasattr(obj, "__dict__"))
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    obj.__setattr__(dataclasses.fields(obj)[0].name, None)


class ConfigErrorTest(unittest.TestCase):
    def test_code_on_a_read_only_base(self) -> None:
        # probatio's Invalid (HA 2026.10+) has a read-only `code` property.
        class ReadOnlyCode(vol.Invalid):
            @property
            def code(self) -> None:
                return None

        class ProbatioConfigError(ConfigError, ReadOnlyCode):
            pass

        err = ProbatioConfigError("duplicate_name", "taken", ["scenes", "s_1"])
        self.assertEqual(
            (err.code, err.msg, err.path),
            ("duplicate_name", "taken", ["scenes", "s_1"]),
        )


class AllPartsTest(unittest.TestCase):
    def test_edits_apply_in_catalogue_order_then_user_parts(self) -> None:
        dim = {"kelvin": 2200, "brightness": 30}
        edit = Part("read", "Read", "white", dim)
        hell = Part("p_0000000b", "Hell", "white", WARM)
        orphan = Part("gone", "Gone", "white", WARM)
        film = Part("p_0000000a", "Film", "white", dim)
        stored = {p.id: p for p in (hell, edit, orphan, film)}
        parts = all_parts(stored, BUILTINS)
        self.assertEqual(list(parts), ["relax", "read", "p_0000000b", "p_0000000a"])
        self.assertIs(parts["relax"], BUILTINS["relax"])
        self.assertEqual(
            parts["read"],
            Part("read", "Read", "white", dim, "mdi:book", "white"),
        )
        self.assertIs(parts["p_0000000b"], hell)
        self.assertEqual(all_parts({}, BUILTINS), BUILTINS)

    def test_user_part_ids(self) -> None:
        self.assertTrue(is_user_part("p_0123abcd"))
        for part_id in ("relax", "p_0123abc", "p_0123ABCD", "s_0123abcd"):
            with self.subTest(part_id):
                self.assertFalse(is_user_part(part_id))


class ResolveTest(unittest.TestCase):
    def test_layers_take_their_part_look(self) -> None:
        hell = Part("p_0000000b", "Hell", "static", {"color": {"kelvin": 3000}})
        parts = {**BUILTINS, hell.id: hell}
        scene = StoredScene(
            "s_00000001",
            "Evening",
            "mdi:weather-night",
            (
                PartLayer("all", "relax"),
                PartLayer(("light.a",), "p_0000000b"),
                PartLayer(("room:wc",), "p_0000000c"),  # missing: dropped
            ),
            others="keep",
            transition=3.0,
        )
        self.assertEqual(
            resolve(scene, parts),
            Scene(
                "s_00000001",
                "Evening",
                "mdi:weather-night",
                (
                    Layer("all", "white", WARM),
                    Layer(("light.a",), "static", {"color": {"kelvin": 3000}}),
                ),
                others="keep",
                transition=3.0,
            ),
        )
        self.assertEqual(resolve(scene, parts).brightness, 100)
        self.assertEqual(resolve(scene, {}).layers, ())

    def test_a_part_on_its_own(self) -> None:
        self.assertEqual(
            part_scene(BUILTINS["read"]),
            Scene("read", "Read", "mdi:book", (Layer("all", "white", WARM),)),
        )
        plain = StoredScene("s_00000001", "Read", None, (PartLayer("all", "read"),))
        self.assertTrue(is_whole_room(plain, "read"))
        self.assertFalse(is_whole_room(plain, "relax"))
        for other in (
            dataclasses.replace(plain, others="keep"),
            dataclasses.replace(plain, transition=1.0),
            dataclasses.replace(plain, layers=(PartLayer(("light.a",), "read"),)),
            dataclasses.replace(plain, layers=(*plain.layers, *plain.layers)),
        ):
            with self.subTest(other=other):
                self.assertFalse(is_whole_room(other, "read"))


class ValidateTest(unittest.TestCase):
    registry = FakeRegistry()

    def run_validate(self, data, **kw) -> Config:
        return validate(data, self.registry, BUILTINS, **kw)

    def assertCode(self, code: str, data, **kw) -> ConfigError:
        with self.assertRaises(ConfigError) as ctx:
            self.run_validate(data, **kw)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_valid_config_fills_defaults(self) -> None:
        config = self.run_validate(
            {
                "zones": {"down": {"id": "down", "name": "Down", "rooms": ["wc"]}},
                "targets": {
                    "zone:down": {"default_scene": "s_00000001"},
                    "room:wc": {"detect_changes": False},
                },
                "parts": {
                    "p_0000000a": part_dict("p_0000000a", " Hell "),
                    "read": part_dict("read", "Read", params={"brightness": 50}),
                },
                "scenes": {
                    "zone:down": [
                        {
                            "id": "s_00000001",
                            "name": " Evening ",
                            "layers": [
                                {"lights": ["room:wc"], "part": "p_0000000a"},
                                {"lights": "all", "part": "read"},
                            ],
                        }
                    ]
                },
                "settings": {
                    "default_scene": "p_0000000a",
                    "buses": {"zigbee:1": {"rate": 4}},
                },
            }
        )
        self.assertEqual(config.parts["p_0000000a"].name, "Hell")
        self.assertEqual(config.parts["p_0000000a"].params, WARM)
        self.assertEqual(config.parts["read"].params["kelvin"], 2700)
        (scene,) = config.scenes["zone:down"]
        self.assertEqual(
            scene,
            StoredScene(
                "s_00000001",
                "Evening",
                None,
                (PartLayer(("room:wc",), "p_0000000a"), PartLayer("all", "read")),
            ),
        )
        self.assertFalse(config.targets["room:wc"].detect_changes)
        self.assertEqual(config.zones["down"].lights, ())
        self.assertEqual(config.settings.default_scene, "p_0000000a")
        self.assertEqual(config.settings.buses, {"zigbee:1": {"rate": 4.0}})
        self.assertEqual(config.settings.transition, 0.4)
        self.assertEqual(self.run_validate({}), Config())

    def test_a_stored_transition_is_kept(self) -> None:
        stored = {"settings": {"transition": 1.0}}
        self.assertEqual(self.run_validate(stored).settings.transition, 1.0)
        self.assertEqual(Config.from_dict(stored).settings.transition, 1.0)

    def test_structure(self) -> None:
        scene = stored_scene("s_00000001", "A")
        for data in (
            {"parts": {"p_0000000a": part_dict("p_0000000b", "A")}},
            {"parts": {"s_0000000a": part_dict("s_0000000a", "A")}},
            {"parts": {"Hell": part_dict("Hell", "A")}},
            {"parts": {"p_0000000a": part_dict("p_0000000a", "")}},
            # Icons and categories belong to the catalogue.
            {"parts": {"p_0000000a": part_dict("p_0000000a", "A", icon="mdi:x")}},
            {"parts": {"p_0000000a": {"id": "p_0000000a", "name": "A"}}},
            {"scenes": {"room:a": scene}},
            {"scenes": {"area:a": [scene]}},
            {"scenes": {"room:a": [stored_scene("p_00000001", "A")]}},
            {"scenes": {"room:a": [{**scene, "layers": []}]}},
            {"scenes": {"room:a": [{**scene, "brightness": 50}]}},
            {"scenes": {"room:a": [{**scene, "others": "on"}]}},
            {"scenes": {"room:a": [stored_scene("s_00000001", "A", "s_00000002")]}},
            {"scenes": {"room:a": [scene], "room:b": [scene]}},
            # A panel that still sends scopes or menus fails loudly.
            {"scenes": {"room:a": [{**scene, "scope": ["room:a"]}]}},
            {"targets": {"room:a": {"visible_scenes": None}}},
            {"targets": {"room:a": {"default_scene": "relax"}}},
            {"targets": {"area:x": {}}},
            {"targets": {"room:x": {"extra_lights": ["switch.x"]}}},
            {"settings": {"default_scene": "s_00000001"}},
            {"settings": {"buses": {"z": {"rate": 0}}}},
            {"zones": {"down": {"id": "up", "name": "Down"}}},
            {"zones": {"Down": {"id": "Down", "name": "Down"}}},
        ):
            with self.subTest(data=data):
                self.assertCode("invalid", data)

    def test_error_paths(self) -> None:
        bad = part_dict("p_0000000a", "A", effect="strobe")
        err = self.assertCode("unknown_effect", {"parts": {"p_0000000a": bad}})
        self.assertEqual(err.path, ["parts", "p_0000000a", "effect"])
        bad = part_dict("p_0000000a", "A", params={"kelvin": 9000})
        err = self.assertCode("invalid_params", {"parts": {"p_0000000a": bad}})
        self.assertEqual(err.path, ["parts", "p_0000000a", "params", "kelvin"])
        bad = {**stored_scene("s_00000001", "A"), "others": "on"}
        err = self.assertCode("invalid", {"scenes": {"room:a": [bad]}})
        self.assertEqual(err.path, ["scenes", "s_00000001", "others"])
        for name in ("Off", " manual "):
            with self.subTest(name=name):
                scene = stored_scene("s_00000001", name)
                err = self.assertCode("reserved_name", {"scenes": {"room:a": [scene]}})
                self.assertEqual(err.path, ["scenes", "s_00000001", "name"])
                part = {"p_0000000a": part_dict("p_0000000a", name)}
                err = self.assertCode("reserved_name", {"parts": part})
                self.assertEqual(err.path, ["parts", "p_0000000a", "name"])

    def test_part_names(self) -> None:
        def parts(*items):
            return {"parts": {p["id"]: p for p in items}}

        hell = part_dict("p_0000000a", "Hell")
        other = part_dict("p_0000000b", "HELL")
        err = self.assertCode("duplicate_name", parts(hell, other))
        self.assertEqual(err.path, ["parts", "p_0000000b", "name"])
        err = self.assertCode("duplicate_name", parts(hell, other), only={"p_0000000a"})
        self.assertEqual(err.path, ["parts", "p_0000000a", "name"])
        self.run_validate(parts(hell, other), only={"s_00000001"})
        # Built-in names are taken, and an edit keeps its built-in's name.
        mine = part_dict("p_0000000c", "relax")
        err = self.assertCode("duplicate_name", parts(mine))
        self.assertEqual(err.path, ["parts", "p_0000000c", "name"])
        self.run_validate(parts(mine), only={"read"})
        renamed = part_dict("read", "My read")
        err = self.assertCode("builtin_name", parts(renamed), only={"read"})
        self.assertEqual(err.path, ["parts", "read", "name"])
        self.assertIn("'Read'", err.msg)
        self.run_validate(parts(renamed), only={"p_0000000a"})
        self.run_validate(parts(part_dict("read", "Read"), hell))
        # An edit whose built-in left the catalogue is ignored, but not saved.
        gone = part_dict("gone", "Hell")
        self.run_validate(parts(gone, hell))
        err = self.assertCode("unknown_part", parts(gone), only={"gone"})
        self.assertEqual(err.path, ["parts", "gone", "id"])

    def test_a_save_never_fails_on_a_part_it_does_not_name(self) -> None:
        # Its effect may come from a plugin that is not loaded (any more).
        strobe = part_dict("p_0000000a", "Strobe", effect="strobe", params={"x": 1})
        data = {"parts": {"p_0000000a": strobe}}
        config = self.run_validate(data, only={"s_00000001"})
        self.assertEqual(config.parts["p_0000000a"].params, {"x": 1})
        self.assertCode("unknown_effect", data, only={"p_0000000a"})
        self.assertCode("unknown_effect", data)

    def test_scene_names_are_per_target(self) -> None:
        living = stored_scene("s_0000000a", "Movie")
        again = stored_scene("s_0000000b", "movie")
        kitchen = stored_scene("s_0000000c", "MOVIE")
        apart = {"scenes": {"room:living": [living], "room:kitchen": [kitchen]}}
        config = self.run_validate(apart)
        self.assertEqual(list(config.scenes), ["room:living", "room:kitchen"])
        clash = {"scenes": {"room:living": [living, again]}}
        err = self.assertCode("duplicate_name", clash)
        self.assertEqual(err.path, ["scenes", "s_0000000b", "name"])
        self.assertIn("room:living", err.msg)
        err = self.assertCode("duplicate_name", clash, only={"s_0000000a"})
        self.assertEqual(err.path, ["scenes", "s_0000000a", "name"])
        self.run_validate(clash, only={"s_0000000c"})
        # A scene may share a part's name.
        self.run_validate(
            {"scenes": {"room:living": [stored_scene("s_0000000d", "Relax")]}}
        )

    def test_unknown_part(self) -> None:
        data = {
            "scenes": {"room:a": [stored_scene("s_00000001", "A", "relax", "gone")]}
        }
        err = self.assertCode("unknown_part", data, only={"s_00000001"})
        self.assertEqual(err.path, ["scenes", "s_00000001", "layers", 1, "part"])
        # Unsaved, it is dropped at resolve.
        self.run_validate(data, only={"s_00000002"})
        self.run_validate(data)
        # A user part is known once stored; an edit of a gone built-in is not.
        mine = {"p_0000000a": part_dict("p_0000000a", "Hell")}
        uses = {"room:a": [stored_scene("s_00000001", "A", "p_0000000a")]}
        self.run_validate({"parts": mine, "scenes": uses})
        gone = {"gone": part_dict("gone", "Gone")}
        uses = {"room:a": [stored_scene("s_00000001", "A", "gone")]}
        self.assertCode(
            "unknown_part", {"parts": gone, "scenes": uses}, only={"s_00000001"}
        )

    def test_scenes_of_a_target_that_is_gone_are_dormant(self) -> None:
        data = {
            "scenes": {
                "room:gone": [stored_scene("s_00000001", "A")],
                "zone:gone": [stored_scene("s_00000002", "A")],
                "room:living": [stored_scene("s_00000003", "A")],
            }
        }
        live = {"room:living"}
        self.run_validate(data, only={"s_00000003"}, live=live)
        err = self.assertCode("unknown_target", data, only={"s_00000001"}, live=live)
        self.assertEqual(err.path, ["scenes", "s_00000001"])
        self.run_validate(data, live=live)
        # Without live targets, rooms count as existing and zones must be stored.
        self.run_validate(data, only={"s_00000001", "s_00000003"})
        err = self.assertCode("unknown_target", data, only={"s_00000002"})
        self.assertEqual(err.path, ["scenes", "s_00000002"])

    def test_room_refs_stay_in_their_zone(self) -> None:
        def refs(*lights: str):
            scene = stored_scene("s_00000001", "A")
            scene["layers"] = [{"lights": list(lights), "part": "relax"}]
            return {
                "zones": {"down": {"id": "down", "name": "Down", "rooms": ["wc"]}},
                "scenes": {
                    "zone:down": [scene],
                    "room:wc": [{**scene, "id": "s_00000002"}],
                },
            }

        self.run_validate(refs("room:wc", "light.a"), only={"s_00000001"})
        bad = refs("room:wc", "room:bath")
        err = self.assertCode("unknown_target", bad, only={"s_00000001"})
        self.assertEqual(err.path, ["scenes", "s_00000001", "layers", 0, "lights"])
        self.assertIn("'room:bath'", err.msg)
        # A room reaches only itself; a stale ref in an untouched scene reaches nothing.
        self.run_validate(refs("room:wc"), only={"s_00000002"})
        self.assertCode("unknown_target", refs("room:bath"), only={"s_00000002"})
        self.run_validate(refs("room:bath"), only={"s_00000003"})
        self.run_validate(refs("room:bath"))

    def test_defaults(self) -> None:
        scenes = {"room:a": [stored_scene("s_00000001", "A")]}
        for default in (None, "none", "s_00000001"):
            with self.subTest(default=default):
                targets = {"room:a": {"default_scene": default}}
                self.run_validate({"targets": targets, "scenes": scenes})
        dangling = {
            "targets": {"room:b": {"default_scene": "s_00000001"}},
            "scenes": scenes,
        }
        err = self.assertCode("unknown_scene", dangling)
        self.assertEqual(err.path, ["targets", "room:b", "default_scene"])
        self.assertCode("unknown_scene", dangling, only={"room:b"})
        self.run_validate(dangling, only={"room:a", "s_00000001"})
        # The global default is a part; a built-in gone from the catalogue means none.
        for default in ("relax", "gone", None):
            with self.subTest(default=default):
                self.run_validate({"settings": {"default_scene": default}})
        missing = {"settings": {"default_scene": "p_0000000a"}}
        err = self.assertCode("unknown_part", missing)
        self.assertEqual(err.path, ["settings", "default_scene"])
        self.assertCode("unknown_part", missing, only={"settings"})
        self.run_validate(missing, only={"room:a"})
        mine = {"p_0000000a": part_dict("p_0000000a", "Hell")}
        self.run_validate({**missing, "parts": mine})

    def test_validate_draft(self) -> None:
        hell = part_dict("p_0000000a", "Hell", params={"kelvin": 3000})
        draft = stored_scene("s_00000001", "Draft", "p_0000000a", "relax")
        scene, parts = validate_draft(draft, [hell], self.registry, BUILTINS)
        self.assertEqual(
            scene.layers, (PartLayer("all", "p_0000000a"), PartLayer("all", "relax"))
        )
        self.assertEqual(parts["p_0000000a"].params["kelvin"], 3000)
        # The part editor tries a part under its own id.
        validate_draft(stored_scene("relax", "Relax"), [], self.registry, BUILTINS)
        for scene_data, part_data, code in (
            (stored_scene("s_00000001", "Draft", "p_0000000b"), [], "unknown_part"),
            (stored_scene("s_00000001", "Off"), [], "reserved_name"),
            (draft, [{**hell, "effect": "strobe"}], "unknown_effect"),
            ({**draft, "layers": []}, [hell], "invalid"),
        ):
            with self.subTest(code=code), self.assertRaises(ConfigError) as ctx:
                validate_draft(scene_data, part_data, self.registry, BUILTINS)
            self.assertEqual(ctx.exception.code, code)

    def test_validate_builtin_part(self) -> None:
        data = part_dict("cozy", "Cozy", icon="mdi:sofa", category="white")
        part = validate_builtin_part(data, self.registry)
        self.assertEqual(part, Part("cozy", "Cozy", "white", WARM, "mdi:sofa", "white"))
        for bad in (
            part_dict("cozy", "Cozy", icon="mdi:sofa"),
            part_dict("cozy", "Cozy", category="white"),
            {**data, "category": "custom"},
            {**data, "id": "p_0123abcd"},
            {**data, "id": "s_0123abcd"},
            {**data, "params": {"kelvin": 9000}},
        ):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                validate_builtin_part(bad, self.registry)


class ContextTest(unittest.TestCase):
    def test_tagged_context(self) -> None:
        ctx = create_context()
        self.assertEqual(len(ctx.id), 26)
        self.assertTrue(is_ours(ctx))
        self.assertFalse(is_ours(Context()))
        self.assertFalse(is_ours(None))


if __name__ == "__main__":
    unittest.main()
