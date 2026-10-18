"""The built-in effects, rendered without the engine."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, time, timedelta
import random
import statistics
from typing import Any
import unittest
from zoneinfo import ZoneInfo

from astral import Observer
import voluptuous as vol

from custom_components.magic_lights.color import hue_angle
from custom_components.magic_lights.effects.adaptive import Adaptive, _curve_params
from custom_components.magic_lights.effects.adaptive_curve import (
    AdaptiveParams,
    adaptive_values,
)
from custom_components.magic_lights.effects.base import Effect, RenderContext, as_frame
from custom_components.magic_lights.effects.light_effect import (
    FLAMES,
    LightEffect,
    timed_duration,
)
from custom_components.magic_lights.effects.off import Off
from custom_components.magic_lights.effects.palette import (
    Palette,
    _next,
    step_seconds,
)
from custom_components.magic_lights.effects.schema import serialize_schema
from custom_components.magic_lights.effects.static import Static
from custom_components.magic_lights.effects.white import White
from custom_components.magic_lights.models import (
    OFF,
    Color,
    Frame,
    Light,
    LightState,
    SunLocation,
)

from .fakes import BERLIN, START, make_caps, make_light
from .ref.al_cab import SunLightSettings

COLOR = make_caps()
GRADIENT = make_caps(gradient_points=3)
CT_ONLY = make_caps(color=False)
CANDLE = make_caps(effects=frozenset({"candle", "fireplace", "off"}))
SUNRISE = make_caps(effects=frozenset({"sunrise"}))
# HA's hue integration lists the bridge's (aiohue) names; WLED capitalises.
HUE_BRIDGE = make_caps(
    effects=frozenset({"off", "candle", "fire", "prism", "sunrise", "sunset"})
)
WLED = make_caps(effects=frozenset({"Solid", "Candle", "Fire 2012"}))

FIVE = [
    {"xy": [0.6364, 0.2963]},
    {"xy": [0.5753, 0.3275]},
    {"xy": [0.1562, 0.1626]},
    {"xy": [0.4759, 0.4283]},
    {"xy": [0.2072, 0.4525]},
]


def lights(n: int, caps=COLOR, prefix: str = "l") -> list[Light]:
    return [make_light(f"light.{prefix}{i}", caps=caps) for i in range(n)]


class Run:
    """One layer of one activation, driven the way the engine drives it."""

    def __init__(
        self,
        effect: type[Effect],
        members: list[Light],
        params: dict[str, Any] | None = None,
        *,
        seed: str = "s_00000001|room:test|0|0",
        min_period: float = 0.5,
        adjust: dict[str, Any] | None = None,
        sun: SunLocation = BERLIN,
    ) -> None:
        self.effect = effect()
        self.params = effect.validate(params or {})
        self.lights = {light.entity_id: light for light in members}
        self.members = tuple(sorted(self.lights))
        self.rng = random.Random(seed)
        self.memory: dict[str, Any] = {}
        self.prev: dict[str, LightState] = {}
        self.min_period = min_period
        self.adjust = adjust or {}
        self.sun = sun

    def render(
        self,
        t: float = 0.0,
        *,
        only: set[str] | None = None,
        first: bool | None = None,
        now: datetime | None = None,
    ) -> Frame:
        ctx = RenderContext(
            lights=tuple(
                self.lights[e] for e in self.members if only is None or e in only
            ),
            members=self.members,
            params=self.params,
            now=now or START + timedelta(seconds=t),
            t=t,
            sun=self.sun,
            rng=self.rng,
            memory=self.memory,
            prev=self.prev,
            min_period=self.min_period,
            adjust=self.adjust,
            first=t == 0 if first is None else first,
        )
        frame = as_frame(self.effect.render(ctx))
        self.prev = {**self.prev, **frame.states}
        return frame

    def play(self, until: float) -> list[tuple[float, Frame]]:
        """Render at t=0, then whenever the effect asks, up to `until`."""
        out = [(0.0, self.render(0.0))]
        t = 0.0
        while (nxt := out[-1][1].next_update) is not None and t + nxt <= until:
            t += max(nxt, self.min_period, 0.1)
            out.append((t, self.render(t)))
        return out


class SimpleEffectsTest(unittest.TestCase):
    def test_off(self) -> None:
        frame = Run(Off, lights(2)).render()
        self.assertEqual(frame.states, {"light.l0": OFF, "light.l1": OFF})
        self.assertIsNone(frame.next_update)

    def test_white(self) -> None:
        run = Run(White, lights(2), {"kelvin": 3000, "brightness": 40})
        frame = run.render()
        state = LightState(brightness=0.4, color=Color(kelvin=3000))
        self.assertEqual(frame.states, {"light.l0": state, "light.l1": state})
        self.assertIsInstance(frame.states["light.l0"].color.kelvin, int)
        self.assertIsNone(frame.next_update)
        self.assertEqual(White.swatches(run.params), [Color(kelvin=3000).as_hex()])

    def test_white_defaults(self) -> None:
        self.assertEqual(White.validate({}), {"kelvin": 2700, "brightness": 100})

    def test_static(self) -> None:
        run = Run(Static, lights(1), {"color": "#ff0000", "brightness": 50})
        state = run.render().states["light.l0"]
        self.assertEqual(
            state, LightState(brightness=0.5, color=Color(xy=(0.701, 0.299)))
        )
        self.assertEqual(Static.swatches(run.params), ["#ff0000"])


class DeterminismTest(unittest.TestCase):
    CASES = (
        (Palette, {"palette": FIVE, "motion": "dynamic", "speed": 0.8}),
        (Palette, {"palette": FIVE}),
        (LightEffect, {"effect": "fireplace"}),
        (Adaptive, {}),
        (White, {}),
    )

    def test_same_seed_same_frames(self) -> None:
        members = [*lights(4), *lights(2, GRADIENT, "g"), *lights(1, CT_ONLY, "w")]
        for effect, params in self.CASES:
            with self.subTest(effect.key):
                a, b = Run(effect, members, params), Run(effect, members, params)
                before = copy.deepcopy(a.params)
                self.assertEqual(a.play(60), b.play(60))
                self.assertEqual(a.params, before)  # params are never mutated

    def test_salt_changes_the_deal(self) -> None:
        def colours(seed: str) -> list[Color]:
            frame = Run(Palette, lights(8), {"palette": FIVE}, seed=seed).render()
            return [frame.states[e].color for e in sorted(frame.states)]

        first = colours("s_00000001|room:test|0|0")
        self.assertEqual(first, colours("s_00000001|room:test|0|0"))
        self.assertTrue(
            any(first != colours(f"s_00000001|room:test|0|{salt}") for salt in (1, 2))
        )


class PaletteTest(unittest.TestCase):
    def colours(self, frame: Frame) -> dict[str, tuple[float, float]]:
        return {e: s.color.xy for e, s in frame.states.items()}

    def test_deck_without_replacement(self) -> None:
        palette = [Color.parse(c).xy for c in FIVE]
        for n in (5, 10, 7):
            with self.subTest(n=n):
                used = list(
                    self.colours(
                        Run(Palette, lights(n), {"palette": FIVE}).render()
                    ).values()
                )
                counts = [used.count(xy) for xy in palette]
                self.assertEqual(sum(counts), n)
                self.assertLessEqual(max(counts) - min(counts), 1)

    def test_white_only_lights_take_no_card(self) -> None:
        # White-only lights sort first here; five colour lights still get all
        # five colours once.
        members = [*lights(3, CT_ONLY, "a"), *lights(5)]
        palette = [Color.parse(c) for c in FIVE]
        for salt in range(10):
            with self.subTest(salt=salt):
                frame = Run(
                    Palette, members, {"palette": FIVE}, seed=f"s|room:test|0|{salt}"
                ).render()
                used = [frame.states[f"light.l{i}"].color for i in range(5)]
                self.assertCountEqual(used, palette)

    def test_deal_ignores_which_members_render(self) -> None:
        members = lights(6)
        full = self.colours(Run(Palette, members, {"palette": FIVE}).render())
        partial = self.colours(
            Run(Palette, members, {"palette": FIVE}).render(
                only={"light.l0", "light.l2", "light.l5"}
            )
        )
        self.assertEqual(set(partial), {"light.l0", "light.l2", "light.l5"})
        self.assertEqual(partial, {e: full[e] for e in partial})

    def test_static_frame(self) -> None:
        run = Run(Palette, lights(3), {"palette": FIVE, "brightness": 30})
        frame = run.render()
        self.assertIsNone(frame.next_update)
        for state in frame.states.values():
            self.assertEqual(
                (state.brightness, state.transition, state.gradient), (0.3, None, None)
            )
        self.assertEqual(len(Palette.swatches(run.params)), 5)

    def test_gradients(self) -> None:
        members = [*lights(1), *lights(2, GRADIENT, "g")]
        run = Run(Palette, members, {"palette": FIVE})
        frame = run.render()
        palette = [Color.parse(c) for c in FIVE]
        self.assertIsNone(frame.states["light.l0"].gradient)
        deal = run.memory["deal"]
        for e in ("light.g0", "light.g1"):
            state = frame.states[e]
            expected = tuple(palette[(deal[e] + k) % 5] for k in range(3))
            self.assertEqual(state.gradient, expected)
            self.assertEqual(state.color, palette[deal[e]])
        off = Run(Palette, members, {"palette": FIVE, "gradients": False}).render()
        self.assertTrue(all(s.gradient is None for s in off.states.values()))
        single = Run(Palette, members, {"palette": FIVE[:1]}).render()
        self.assertTrue(all(s.gradient is None for s in single.states.values()))
        two = Run(Palette, members, {"palette": FIVE[:2]}).render()
        self.assertEqual(len(two.states["light.g0"].gradient), 2)

    def test_white_only_lights_get_white(self) -> None:
        for motion in ("static", "dynamic"):
            with self.subTest(motion):
                run = Run(
                    Palette,
                    lights(2, CT_ONLY, "w"),
                    {"palette": FIVE, "white": {"kelvin": 2200}, "motion": motion},
                )
                frames = run.play(120)
                self.assertEqual(len(frames), 1)  # nothing to animate
                self.assertTrue(
                    all(
                        s == LightState(brightness=0.6, color=Color(kelvin=2200))
                        for s in frames[0][1].states.values()
                    )
                )

    def test_first_dynamic_frame(self) -> None:
        run = Run(
            Palette, lights(8), {"palette": FIVE, "motion": "dynamic", "speed": 0.6}
        )
        period = step_seconds(0.6)
        frame = run.render()
        palette = [Color.parse(c) for c in FIVE]
        deal = run.memory["deal"]
        for e, state in frame.states.items():
            self.assertEqual(state, LightState(brightness=0.6, color=palette[deal[e]]))
        until = [s["until"] for s in run.memory["steps"].values()]
        self.assertTrue(all(0.2 * period <= u <= period for u in until))
        self.assertGreater(statistics.pstdev(until), 0.15 * period)
        self.assertAlmostEqual(frame.next_update, max(min(until), 0.5))

    def test_dynamic_steps(self) -> None:
        run = Run(
            Palette, lights(6), {"palette": FIVE, "motion": "dynamic", "speed": 0.6}
        )
        period = step_seconds(0.6)
        prev = run.render().states
        stepped: set[str] = set()
        for _ in range(40):
            due = {e: s["until"] for e, s in run.memory["steps"].items()}
            t = min(due.values())
            frame = run.render(t)
            for e, state in frame.states.items():
                if due[e] <= t:
                    stepped.add(e)
                    self.assertNotEqual(state.color, prev[e].color)
                    self.assertAlmostEqual(state.transition, 0.8 * period)
                    self.assertTrue(0.6 * 0.9 <= state.brightness <= 0.6 * 1.1)
                    until = run.memory["steps"][e]["until"]
                    self.assertTrue(0.8 * period <= until - t <= 1.2 * period + 1e-9)
                else:
                    self.assertIs(state, prev[e])  # unchanged: the diff drops it
            self.assertGreaterEqual(frame.next_update, run.min_period)
            prev = frame.states
        self.assertEqual(stepped, set(run.members))

    def test_rejoin_resends_without_a_long_fade(self) -> None:
        run = Run(
            Palette, lights(3), {"palette": FIVE, "motion": "dynamic", "speed": 0.6}
        )
        period = step_seconds(0.6)
        run.render()
        colour = run.memory["steps"]["light.l1"]["color"]
        run.render(100, only={"light.l0", "light.l2"})  # l1 is manual meanwhile
        frame = run.render(101, only={"light.l1"}, first=True)
        palette = [Color.parse(c) for c in FIVE]
        self.assertEqual(
            frame.states,
            {"light.l1": LightState(brightness=0.6, color=palette[colour])},
        )
        until = run.memory["steps"]["light.l1"]["until"]
        self.assertTrue(101 + 0.2 * period <= until <= 101 + period)

    def test_min_period_paces_the_steps(self) -> None:
        run = Run(
            Palette,
            lights(4),
            {"palette": FIVE, "motion": "dynamic", "speed": 1.0},
            min_period=5.0,
        )
        frames = run.play(120)
        self.assertGreater(len(frames), 4)
        for _, frame in frames:
            self.assertGreaterEqual(frame.next_update, 5.0)
            for state in frame.states.values():
                if state.transition is not None:
                    self.assertAlmostEqual(state.transition, 4.0)

    def test_adjust_dynamic_overrides_motion(self) -> None:
        params = {"palette": FIVE, "motion": "static"}
        self.assertIsNotNone(
            Run(Palette, lights(2), params, adjust={"dynamic": True})
            .render()
            .next_update
        )
        params = {"palette": FIVE, "motion": "dynamic"}
        frame = Run(Palette, lights(2), params, adjust={"dynamic": False}).render()
        self.assertIsNone(frame.next_update)

    def test_memory_survives_param_edit(self) -> None:
        run = Run(
            Palette, lights(6), {"palette": FIVE, "motion": "dynamic", "speed": 0.9}
        )
        run.play(60)
        two = FIVE[3:]
        run.params = Palette.validate({**run.params, "palette": two, "brightness": 20})
        allowed = {Color.parse(c) for c in two}
        frame = run.render(61, first=True)  # config change: everything re-sent
        self.assertEqual(set(frame.states), set(run.members))
        for state in frame.states.values():
            self.assertIn(state.color, allowed)
            self.assertEqual((state.brightness, state.transition), (0.2, None))
        t = 61.0
        for _ in range(30):
            t += run.render(t).next_update
            self.assertTrue(all(s.color in allowed for s in run.prev.values()))

    def test_never_fades_through_white(self) -> None:
        # Red, a near-red, cyan opposite red, and a green 120 degrees away.
        colours = [
            Color(xy=xy) for xy in ((0.68, 0.31), (0.6, 0.38), (0.17, 0.33), (0.3, 0.6))
        ]
        rng = random.Random(1)
        for cur in range(len(colours)):
            a = hue_angle(colours[cur].as_xy())
            for _ in range(50):
                nxt = _next(cur, colours, rng)
                self.assertNotEqual(nxt, cur)
                diff = abs((hue_angle(colours[nxt].as_xy()) - a + 180) % 360 - 180)
                self.assertTrue(2 <= diff <= 150, (cur, nxt, diff))
        # No admissible colour: any other one, never the current.
        pair = [Color(xy=(0.68, 0.31)), Color(xy=(0.17, 0.33))]
        self.assertEqual(_next(0, pair, rng), 1)
        self.assertEqual(_next(0, pair[:1], rng), 0)

    def test_step_seconds(self) -> None:
        self.assertEqual(step_seconds(1.0), 2.0)
        self.assertAlmostEqual(step_seconds(0.6), 120 * (1 / 60) ** 0.6)
        self.assertLess(step_seconds(0.05), 120)


class LightEffectTest(unittest.TestCase):
    def members(self) -> list[Light]:
        return [
            make_light("light.native", caps=CANDLE),
            make_light("light.colour", caps=COLOR),
            make_light("light.white", caps=CT_ONLY),
        ]

    def test_native_and_emulated(self) -> None:
        run = Run(
            LightEffect,
            self.members(),
            {"effect": "candle", "brightness": 40, "speed": 0.5},
            min_period=0.7,
        )
        frame = run.render()
        colour = Color(xy=(0.561, 0.4042))
        self.assertEqual(
            frame.states["light.native"],
            LightState(brightness=0.4, color=colour, effect="candle", effect_speed=0.5),
        )
        flame = frame.states["light.colour"]
        self.assertIn(flame.color.xy, FLAMES["candle"])
        self.assertIsNone(flame.effect)
        self.assertIsNone(flame.transition)  # the scene's fade applies
        jitter = 0.15 + 0.35 * 0.5
        self.assertTrue(0.4 * (1 - jitter) <= flame.brightness <= 0.4 * (1 + jitter))
        self.assertEqual(frame.states["light.white"].color, Color(kelvin=2000))
        later = run.play(60)
        self.assertGreater(len(later), 10)
        self.assertTrue(
            all(
                f.states["light.native"] == frame.states["light.native"]
                for _, f in later
            )
        )
        self.assertGreater(len({f.states["light.colour"] for _, f in later}), 5)

    def test_emulated_steps_are_slow(self) -> None:
        # Every step is a state write per light: keep them a few seconds apart.
        run = Run(LightEffect, self.members(), {"effect": "candle", "speed": 0.5})
        frames = run.play(300)
        self.assertLess(len(frames), 300 / 1.5 + 1)
        self.assertIsNone(frames[0][1].states["light.colour"].transition)
        for _, frame in frames:
            self.assertTrue(1.5 <= frame.next_update <= 3.0, frame.next_update)
        for _, frame in frames[1:]:
            self.assertEqual(frame.states["light.colour"].transition, frame.next_update)

    def test_emulation_respects_min_period(self) -> None:
        run = Run(LightEffect, self.members(), {"speed": 1.0}, min_period=3.0)
        frames = run.play(60)
        for _, frame in frames:
            self.assertGreaterEqual(frame.next_update, 3.0)
        for _, frame in frames[1:]:
            self.assertEqual(frame.states["light.colour"].transition, 3.0)

    def test_static_fallback(self) -> None:
        members = [*self.members(), make_light("light.sun", caps=SUNRISE)]
        run = Run(
            LightEffect, members, {"effect": "sunrise", "color": {"kelvin": 2500}}
        )
        frame = run.render()
        self.assertIsNone(frame.next_update)
        self.assertEqual(frame.states["light.sun"].effect, "sunrise")
        for e in ("light.native", "light.colour", "light.white"):
            self.assertEqual(
                frame.states[e], LightState(brightness=0.35, color=Color(kelvin=2500))
            )
        self.assertEqual(
            LightEffect.swatches(run.params), [Color(kelvin=2500).as_hex()]
        )

    def test_native_names_per_light(self) -> None:
        members = [
            make_light("light.z2m", caps=CANDLE),
            make_light("light.bridge", caps=HUE_BRIDGE),
            make_light("light.wled", caps=WLED),
        ]
        for name, want in (
            ("fireplace", {"light.z2m": "fireplace", "light.bridge": "fire"}),
            ("candle", {"light.z2m": "candle", "light.bridge": "candle"}),
            ("colorloop", {"light.bridge": "prism"}),
        ):
            with self.subTest(name):
                frame = Run(LightEffect, members, {"effect": name}).render()
                got = {e: s.effect for e, s in frame.states.items() if s.effect}
                self.assertEqual(
                    got, want | ({"light.wled": "Candle"} if name == "candle" else {})
                )

    def test_timed_effects_get_their_duration(self) -> None:
        bridge = [make_light("light.bridge", caps=HUE_BRIDGE)]
        for speed, seconds in ((0.2, 7200), (0.5, None), (1.0, 60)):
            with self.subTest(speed=speed):
                params = {"effect": "sunrise", "speed": speed}
                state = Run(LightEffect, bridge, params).render().states["light.bridge"]
                self.assertEqual(state.effect, "sunrise")
                self.assertAlmostEqual(
                    state.transition, seconds or timed_duration(speed)
                )
        self.assertTrue(600 > timed_duration(0.5) > 300)  # Hue: about 10 min
        candle = Run(LightEffect, bridge, {"effect": "candle"}).render()
        self.assertIsNone(candle.states["light.bridge"].transition)

    def test_stop_value_is_not_an_effect(self) -> None:
        run = Run(LightEffect, self.members()[:1], {"effect": "off"})
        self.assertIsNone(run.render().states["light.native"].effect)

    def test_swatches(self) -> None:
        params = LightEffect.validate({"effect": "fireplace", "brightness": 45})
        self.assertEqual(
            LightEffect.swatches(params),
            [Color(xy=xy).as_hex() for xy in FLAMES["fireplace"]],
        )
        with self.assertRaises(vol.Invalid):
            LightEffect.validate({"effect": 3})


ZURICH = SunLocation(Observer(47.37, 8.54, 400), ZoneInfo("Europe/Zurich"))


def al_reference(mode: str) -> SunLightSettings:
    return SunLightSettings(
        name="test",
        astral_observer=ZURICH.observer,
        adapt_until_sleep=False,
        max_brightness=100,
        max_color_temp=5500,
        min_brightness=1,
        min_color_temp=2000,
        sleep_brightness=1,
        sleep_rgb_or_color_temp="color_temp",
        sleep_color_temp=1000,
        sleep_rgb_color=(255, 56, 0),
        sunrise_time=None,
        min_sunrise_time=None,
        max_sunrise_time=None,
        sunset_time=None,
        min_sunset_time=None,
        max_sunset_time=None,
        brightness_mode_time_dark=timedelta(minutes=15),
        brightness_mode_time_light=timedelta(minutes=60),
        brightness_mode=mode,
        timezone=ZURICH.tz,
    )


class AdaptiveTest(unittest.TestCase):
    def test_matches_adaptive_lighting(self) -> None:
        # Winter, summer and both DST change days.
        days = (
            date(2026, 1, 15),
            date(2026, 6, 21),
            date(2026, 3, 29),
            date(2026, 10, 25),
        )
        for mode in ("default", "linear", "tanh"):
            ours = AdaptiveParams(
                min_brightness=1,
                max_brightness=100,
                min_kelvin=2000,
                max_kelvin=5500,
                brightness_mode=mode,
                ramp_dark=timedelta(minutes=15),
                ramp_light=timedelta(minutes=60),
            )
            ref = al_reference(mode)
            for day in days:
                with self.subTest(mode=mode, day=day):
                    start = datetime.combine(day, time(), UTC) - timedelta(hours=2)
                    for minute in range(0, 24 * 60, 11):
                        now = start + timedelta(minutes=minute)
                        bri, kelvin = adaptive_values(now, ZURICH, ours)
                        want = ref.brightness_and_color(now, False)
                        self.assertLessEqual(abs(bri - want["brightness_pct"]), 1, now)
                        self.assertLessEqual(
                            abs(kelvin - want["color_temp_kelvin"]), 10, now
                        )

    def test_dst_night_is_continuous(self) -> None:
        params = Adaptive.validate({"brightness_mode": "tanh"})
        for day in (date(2026, 3, 29), date(2026, 10, 25)):
            with self.subTest(day=day):
                start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
                run = Run(Adaptive, lights(1), params, sun=ZURICH)
                values = [
                    run.render(first=False, now=start + timedelta(minutes=m)).states[
                        "light.l0"
                    ]
                    for m in range(0, 180, 5)
                ]
                self.assertTrue(all(v.brightness == 0.4 for v in values))
                self.assertTrue(all(v.color == Color(kelvin=2200) for v in values))

    def test_static_brightness_with_kelvin_following_the_sun(self) -> None:
        params = {"brightness_mode": "static", "brightness": 70, "min_brightness": 10}
        run = Run(Adaptive, lights(1), params, sun=BERLIN)
        day = datetime(2026, 6, 21, tzinfo=UTC)
        states = [
            run.render(first=False, now=day + timedelta(hours=h)).states["light.l0"]
            for h in range(24)
        ]
        self.assertEqual({s.brightness for s in states}, {0.7})
        kelvins = [s.color.kelvin for s in states]
        self.assertEqual((min(kelvins), max(kelvins)), (2200, 5000))
        self.assertEqual(Adaptive.validate({})["brightness_mode"], "static")

    def test_brightness_fields_follow_the_mode(self) -> None:
        fields = {f["name"]: f for f in serialize_schema(Adaptive.schema)}
        mode = "brightness_mode"
        self.assertEqual(
            [o["value"] for o in fields[mode]["selector"]["select"]["options"]],
            ["static", "default", "linear", "tanh"],
        )
        self.assertEqual(
            fields["brightness"]["visible"], {"field": mode, "value": "static"}
        )
        for name in ("min_brightness", "max_brightness"):
            self.assertEqual(
                fields[name]["visible"],
                {"field": mode, "operator": "not_eq", "value": "static"},
            )
        for name in ("ramp_dark", "ramp_light"):
            self.assertEqual(
                fields[name]["visible"],
                {"field": mode, "operator": "in", "value": ["linear", "tanh"]},
            )
        # Kelvin uses the rest in every mode.
        always = set(fields) - {
            mode,
            "brightness",
            "min_brightness",
            "max_brightness",
            "ramp_dark",
            "ramp_light",
        }
        self.assertEqual(
            {n for n in always if "visible" in fields[n]}, set(), sorted(always)
        )

    def test_layout_hints(self) -> None:
        fields = {f["name"]: f for f in serialize_schema(Adaptive.schema)}
        advanced = {n for n, f in fields.items() if f.get("advanced")}
        self.assertEqual(
            advanced,
            {
                "sunrise_offset",
                "sunset_offset",
                "min_sunrise",
                "max_sunrise",
                "min_sunset",
                "max_sunset",
                "interval",
                "transition",
            },
        )
        self.assertEqual(
            fields["min_kelvin"]["pair"],
            {"max": "max_kelvin", "label": "Warmest ↔ coolest white"},
        )
        self.assertEqual(
            fields["min_brightness"]["row"], fields["max_brightness"]["row"]
        )
        for name in ("ramp_dark", "ramp_light"):
            steps = fields[name]["steps"]
            # The default is a step, so a fresh scene shows no custom value.
            self.assertIn(Adaptive.validate({})[name], [s["value"] for s in steps])
            self.assertEqual(
                [s["label"].split()[0] for s in steps], ["Short", "Medium", "Long"]
            )

    def test_curve_defaults_match_the_schema(self) -> None:
        self.assertEqual(_curve_params(Adaptive.validate({})), AdaptiveParams())

    def test_frames(self) -> None:
        run = Run(Adaptive, lights(2), {}, sun=BERLIN)
        curve = AdaptiveParams()  # the schema's defaults
        first = run.render(0)
        # Aimed at now: Magic Lights fades it in with the scene's transition.
        bri, kelvin = adaptive_values(START, BERLIN, curve)
        self.assertEqual(
            first.states["light.l0"],
            LightState(brightness=bri / 100, color=Color(kelvin=kelvin)),
        )
        self.assertEqual(first.next_update, 90.0)
        evening = START.replace(hour=18, minute=30)
        later = run.render(90, now=evening)
        bri, kelvin = adaptive_values(evening + timedelta(seconds=45), BERLIN, curve)
        state = later.states["light.l1"]
        self.assertEqual(
            (state.brightness, state.color.kelvin, state.transition),
            (bri / 100, kelvin, 45.0),
        )
        self.assertEqual(kelvin % 10, 0)

    def test_clamps(self) -> None:
        # Berlin in June: sunset ~21:30 local, clamped to 21:00 by the preset times.
        params = {
            "min_sunset": "18:30:00",
            "max_sunset": "21:00",
            "brightness_mode": "linear",
        }
        run = Run(Adaptive, lights(1), params, sun=BERLIN)
        at = datetime(2026, 6, 21, 19, 30, tzinfo=UTC)  # 21:30 local
        clamped = run.render(first=False, now=at).states["light.l0"]
        free = Run(Adaptive, lights(1), {"brightness_mode": "linear"}, sun=BERLIN)
        unclamped = free.render(first=False, now=at).states["light.l0"]
        self.assertLess(clamped.brightness, unclamped.brightness)

    def test_reordered_events_fall_back_to_the_plain_sun(self) -> None:
        params = {"min_sunrise": "20:00", "max_sunset": "07:00"}
        at = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)
        with self.assertRaises(ValueError):
            adaptive_values(
                at,
                BERLIN,
                AdaptiveParams(min_sunrise=time(20), max_sunset=time(7)),
            )
        odd = Run(Adaptive, lights(1), params).render(first=False, now=at)
        plain = Run(Adaptive, lights(1), {}).render(first=False, now=at)
        self.assertEqual(odd.states, plain.states)

    def test_swatches(self) -> None:
        params = Adaptive.validate({})
        self.assertEqual(
            Adaptive.swatches(params),
            [Color(kelvin=2200).as_hex(), Color(kelvin=5000).as_hex()],
        )


if __name__ == "__main__":
    unittest.main()
