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
    RuntimeV1,
    Scene,
    Settings,
    TargetOptions,
    TargetStatus,
    Zone,
    available_scenes,
    ha_to_state,
    normalise,
)
from custom_components.magic_lights.validation import (
    ConfigError,
    validate,
    validate_preset,
    validate_scene,
)
from homeassistant.core import Context, State

from .fakes import FakeRegistry, make_caps

FULL = make_caps(min_kelvin=2202, max_kelvin=6535, effects=frozenset({"candle"}))
CT_ONLY = make_caps(color=False, min_kelvin=2700, max_kelvin=6500)
COLOR_ONLY = make_caps(color_temp=False)
DIM_ONLY = make_caps(color=False, color_temp=False)
ON_OFF = make_caps(brightness=False, color=False, color_temp=False, transition=False)


def scene_dict(scene_id: str, name: str, **kw):
    return {
        "id": scene_id,
        "name": name,
        "layers": [{"lights": "all", "effect": "white", "params": {}}],
        **kw,
    }


def preset(scene_id: str, name: str) -> Scene:
    return Scene(
        id=scene_id,
        name=name,
        icon=None,
        layers=(Layer("all", "white", {"kelvin": 2700, "brightness": 100}),),
        category="white",
        builtin=True,
    )


PRESETS = {"relax": preset("relax", "Relax"), "read": preset("read", "Read")}


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
        layers = (
            Layer(
                ("light.a", "room:kitchen"), "static", {"color": {"xy": [0.6, 0.35]}}
            ),
            Layer("all", "white", {"kelvin": 2700, "brightness": 60}),
        )
        scene = Scene(
            "s_0123abcd",
            "Movie",
            "mdi:movie",
            layers,
            scope=("room:living",),
            preset="relax",
            others="keep",
            brightness=70,
            transition=2.5,
        )
        return Config(
            zones={
                "down": Zone(
                    "down", "Downstairs", None, ("living", "kitchen"), ("light.x",)
                )
            },
            targets={
                "room:living": TargetOptions(
                    excluded_lights=("light.b",),
                    visible_scenes=("relax", "s_0123abcd"),
                    default_scene="none",
                    detect_changes=False,
                ),
                "room:kitchen": TargetOptions(),
            },
            scenes={scene.id: scene},
            settings=Settings(
                transition=0.5,
                default_scene="adaptive",
                native_effects=False,
                buses={"zigbee:abc": {"rate": 5.0}},
                light_buses={"light.a": "zigbee:abc"},
            ),
        )

    def test_config(self) -> None:
        config = self.config()
        data = json.loads(json.dumps(config.to_dict()))
        self.assertNotIn(
            "room:kitchen", data["targets"]
        )  # default options are not stored
        expected = dataclasses.replace(
            config, targets={"room:living": config.targets["room:living"]}
        )
        self.assertEqual(Config.from_dict(data), expected)
        self.assertEqual(Config.from_dict({}), Config())

    def test_scene_stored_keys(self) -> None:
        scene = self.config().scenes["s_0123abcd"]
        self.assertEqual(
            set(scene.to_dict()),
            {
                "id",
                "name",
                "icon",
                "scope",
                "preset",
                "layers",
                "others",
                "brightness",
                "transition",
            },
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
        ):
            with self.subTest(type(obj).__name__):
                self.assertFalse(hasattr(obj, "__dict__"))
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    obj.__setattr__(dataclasses.fields(obj)[0].name, None)


class ValidateTest(unittest.TestCase):
    registry = FakeRegistry()

    def test_valid_config_fills_defaults(self) -> None:
        config = validate(
            {
                "zones": {
                    "down": {"id": "down", "name": "Downstairs", "rooms": ["living"]}
                },
                "targets": {"room:living": {"detect_changes": False}},
                "scenes": {
                    "s_00000001": scene_dict(
                        "s_00000001", " Movie ", scope=["room:living"]
                    )
                },
                "settings": {"buses": {"zigbee:1": {"rate": 4}}},
            },
            self.registry,
            presets=PRESETS,
            targets={"room:living", "zone:down"},
        )
        scene = config.scenes["s_00000001"]
        self.assertEqual(scene.name, "Movie")
        self.assertEqual(scene.layers[0].params, {"kelvin": 2700, "brightness": 100})
        self.assertEqual(scene.brightness, 100)
        self.assertFalse(config.targets["room:living"].detect_changes)
        self.assertEqual(config.settings.buses, {"zigbee:1": {"rate": 4.0}})
        self.assertEqual(config.zones["down"].lights, ())

    def assertCode(self, code: str, data, **kw) -> ConfigError:
        with self.assertRaises(ConfigError) as ctx:
            validate(data, self.registry, **kw)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_unknown_effect(self) -> None:
        scene = scene_dict("s_00000001", "A")
        scene["layers"].append({"lights": ["light.a"], "effect": "strobe"})
        err = self.assertCode("unknown_effect", {"scenes": {"s_00000001": scene}})
        self.assertEqual(err.path, ["scenes", "s_00000001", "layers", 1, "effect"])

    def test_invalid_params(self) -> None:
        scene = scene_dict("s_00000001", "A")
        scene["layers"][0]["params"] = {"kelvin": 9000}
        err = self.assertCode("invalid_params", {"scenes": {"s_00000001": scene}})
        self.assertEqual(
            err.path, ["scenes", "s_00000001", "layers", 0, "params", "kelvin"]
        )
        scene["layers"][0]["params"] = {"colour": 1}
        self.assertCode("invalid_params", {"scenes": {"s_00000001": scene}})

    def test_structure(self) -> None:
        for data in (
            {"scenes": {"relax": scene_dict("relax", "A")}},  # stored ids are s_<8 hex>
            {"scenes": {"s_00000001": scene_dict("s_00000002", "A")}},
            {"scenes": {"s_00000001": scene_dict("s_00000001", "A", layers=[])}},
            {"scenes": {"s_00000001": scene_dict("s_00000001", "")}},
            {"scenes": {"s_00000001": scene_dict("s_00000001", "x" * 65)}},
            {"scenes": {"s_00000001": scene_dict("s_00000001", "A", brightness=0)}},
            {"scenes": {"s_00000001": scene_dict("s_00000001", "A", others="on")}},
            {"scenes": {"s_00000001": {**scene_dict("s_00000001", "A"), "extra": 1}}},
            {"zones": {"Down": {"id": "Down", "name": "Down"}}},
            {"zones": {"down": {"id": "up", "name": "Down"}}},
            {"targets": {"area:x": {}}},
            {"targets": {"room:x": {"extra_lights": ["switch.x"]}}},
            {"settings": {"buses": {"z": {"rate": 0}}}},
        ):
            with self.subTest(data=data):
                self.assertCode("invalid", data)

    def test_reserved_names(self) -> None:
        for name in ("Off", " manual ", "OFF"):
            with self.subTest(name=name):
                self.assertCode(
                    "reserved_name",
                    {"scenes": {"s_00000001": scene_dict("s_00000001", name)}},
                )

    def test_references(self) -> None:
        scoped = {
            "scenes": {"s_00000001": scene_dict("s_00000001", "A", scope=["room:gone"])}
        }
        self.assertCode("unknown_target", scoped, targets={"room:living"})
        validate(scoped, self.registry)  # targets unknown: not checked
        copy = {"scenes": {"s_00000001": scene_dict("s_00000001", "A", preset="nope")}}
        self.assertCode("unknown_preset", copy, presets=PRESETS)

    def test_duplicate_names(self) -> None:
        def scenes(*items):
            return {"scenes": {s["id"]: s for s in items}}

        a = scene_dict("s_0000000a", "Movie")
        b_living = scene_dict("s_0000000b", "movie", scope=["room:living"])
        c_kitchen = scene_dict("s_0000000c", "MOVIE", scope=["room:kitchen"])
        self.assertCode("duplicate_name", scenes(a, b_living))
        validate(scenes(b_living, c_kitchen), self.registry)
        err = self.assertCode(
            "duplicate_name", scenes(scene_dict("s_0000000d", "relax")), presets=PRESETS
        )
        self.assertEqual(err.path, ["scenes", "s_0000000d", "name"])
        # A copy replaces its preset inside its scope only.
        copy = scene_dict("s_0000000e", "Relax", preset="relax", scope=["room:living"])
        validate(scenes(copy), self.registry, presets=PRESETS)
        renamed = scene_dict(
            "s_0000000f", "My relax", preset="relax", scope=["room:living"]
        )
        user = scene_dict("s_00000010", "Relax", scope=["room:living"])
        validate(scenes(renamed, user), self.registry, presets=PRESETS)
        self.assertCode(
            "duplicate_name", scenes(renamed, {**user, "scope": []}), presets=PRESETS
        )
        clash = scene_dict("s_00000011", "Read", preset="relax", scope=["room:living"])
        self.assertCode("duplicate_name", scenes(clash), presets=PRESETS)

    def test_validate_scene(self) -> None:
        existing = Config.from_dict(
            {
                "scenes": {
                    s["id"]: s
                    for s in (
                        scene_dict("s_0000000a", "Movie"),
                        scene_dict("s_0000000b", "Movie"),
                    )
                }
            }
        )
        new = validate_scene(
            scene_dict("s_0000000c", "Dinner"), self.registry, existing, presets=PRESETS
        )
        self.assertEqual(new.layers[0].params, {"kelvin": 2700, "brightness": 100})
        single = Config.from_dict(
            {"scenes": {"s_0000000a": scene_dict("s_0000000a", "Movie")}}
        )
        edit = validate_scene(
            scene_dict("s_0000000a", "Movie", brightness=40), self.registry, single
        )
        self.assertEqual(edit.brightness, 40)
        with self.assertRaises(ConfigError) as ctx:
            validate_scene(scene_dict("s_0000000c", "movie"), self.registry, existing)
        self.assertEqual(
            (ctx.exception.code, ctx.exception.path),
            ("duplicate_name", ["scenes", "s_0000000c", "name"]),
        )
        with self.assertRaises(ConfigError) as ctx:
            validate_scene(
                scene_dict("s_0000000c", "x", scope=["zone:x"]),
                self.registry,
                existing,
                targets=set(),
            )
        self.assertEqual(ctx.exception.code, "unknown_target")

    def test_scopes_of_a_deleted_area(self) -> None:
        orphan = scene_dict("s_0000000a", "Evening", scope=["room:gone"])
        both = scene_dict("s_0000000b", "Movie", scope=["room:gone", "room:hall"])
        stored = Config.from_dict({"scenes": {s["id"]: s for s in (orphan, both)}})
        live = {"room:hall", "room:bed"}
        # A scene nobody can reach any more does not take its name.
        validate_scene(
            scene_dict("s_0000000c", "Evening"), self.registry, stored, targets=live
        )
        # An edit keeps the dead entry; a new one may not add it.
        edit = validate_scene(
            {**both, "brightness": 40}, self.registry, stored, targets=live
        )
        self.assertEqual(edit.scope, ("room:gone", "room:hall"))
        with self.assertRaises(ConfigError) as ctx:
            validate_scene(
                {**both, "scope": ["room:gone", "room:bed", "room:old"]},
                self.registry,
                stored,
                targets=live,
            )
        self.assertEqual(
            (ctx.exception.code, ctx.exception.path),
            ("unknown_target", ["scenes", "s_0000000b", "scope", 2]),
        )
        # Where the scene can still be reached, its name still counts.
        with self.assertRaises(ConfigError) as ctx:
            validate_scene(
                scene_dict("s_0000000c", "Movie", scope=["room:hall"]),
                self.registry,
                stored,
                targets=live,
            )
        self.assertEqual(ctx.exception.code, "duplicate_name")

    def test_one_copy_per_preset_and_target(self) -> None:
        def config(*items) -> Config:
            return Config.from_dict({"scenes": {s["id"]: s for s in items}})

        everywhere = scene_dict("s_0000000a", "Relax", preset="relax")
        living = scene_dict(
            "s_0000000b", "Relax", preset="relax", scope=["room:living", "room:hall"]
        )
        stored = config(everywhere, living)
        cases = {
            "second everywhere copy": (
                scene_dict("s_0000000c", "Relax", preset="relax"),
                "everywhere",
            ),
            "room copy widened to everywhere": (
                {**living, "scope": []},
                "everywhere",
            ),
            "room already has a copy": (
                scene_dict("s_0000000c", "Relax", preset="relax", scope=["room:hall"]),
                "in room:hall",
            ),
        }
        for name, (data, where) in cases.items():
            with self.subTest(name), self.assertRaises(ConfigError) as ctx:
                validate_scene(data, self.registry, stored, presets=PRESETS)
            self.assertEqual(ctx.exception.code, "duplicate_copy")
            self.assertEqual(ctx.exception.path, ["scenes", data["id"], "scope"])
            self.assertIn(where, ctx.exception.msg)
        # Edits in place, other rooms and other presets are fine.
        for data in (
            {**everywhere, "brightness": 50},
            {**living, "scope": ["room:living"]},
            scene_dict("s_0000000c", "Relax", preset="relax", scope=["room:bed"]),
            scene_dict("s_0000000c", "Read", preset="read"),
            scene_dict("s_0000000c", "Mine"),
        ):
            with self.subTest(data=data):
                validate_scene(data, self.registry, stored, presets=PRESETS)

    def test_validate_preset(self) -> None:
        data = {
            **scene_dict("cozy", "Cozy"),
            "category": "white",
            "default_visible": False,
        }
        scene = validate_preset(data, self.registry)
        self.assertTrue(scene.builtin)
        self.assertEqual((scene.category, scene.default_visible), ("white", False))
        with self.assertRaises(ConfigError):
            validate_preset(scene_dict("cozy", "Cozy"), self.registry)


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


class AvailableScenesTest(unittest.TestCase):
    def test_precedence_and_order(self) -> None:
        everywhere = Scene.from_dict(
            scene_dict("s_0000000a", "Relax all", preset="relax")
        )
        living = Scene.from_dict(
            scene_dict(
                "s_0000000b", "Relax here", preset="relax", scope=["room:living"]
            )
        )
        user = Scene.from_dict(scene_dict("s_0000000c", "Mine", scope=["room:kitchen"]))
        orphan = Scene.from_dict(scene_dict("s_0000000d", "Old copy", preset="gone"))
        scenes = {s.id: s for s in (everywhere, living, user, orphan)}
        here = available_scenes("room:living", scenes, PRESETS)
        self.assertEqual(list(here), ["relax", "read", "s_0000000d"])
        self.assertIs(here["relax"], living)
        kitchen = available_scenes("room:kitchen", scenes, PRESETS)
        self.assertEqual(list(kitchen), ["relax", "read", "s_0000000c", "s_0000000d"])
        self.assertIs(kitchen["relax"], everywhere)
        self.assertIs(available_scenes("room:x", {}, PRESETS)["read"], PRESETS["read"])


class ContextTest(unittest.TestCase):
    def test_tagged_context(self) -> None:
        ctx = create_context()
        self.assertEqual(len(ctx.id), 26)
        self.assertTrue(is_ours(ctx))
        self.assertFalse(is_ours(Context()))
        self.assertFalse(is_ours(None))


if __name__ == "__main__":
    unittest.main()
