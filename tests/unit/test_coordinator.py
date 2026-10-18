"""Coordinator: activation lifecycle, ownership, restore, config changes, try/stop."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Any
import unittest

import voluptuous as vol

from custom_components.magic_lights.context import is_ours
from custom_components.magic_lights.effects import adaptive, light_effect
from custom_components.magic_lights.effects.base import Effect, RenderContext
from custom_components.magic_lights.engine import build_coordinator
from custom_components.magic_lights.engine.activation import seeded_rng
from custom_components.magic_lights.engine.coordinator import ADOPTION_GRACE
from custom_components.magic_lights.engine.sender import SETTLE_TIME
from custom_components.magic_lights.models import (
    OFF,
    ActivationSnapshot,
    Bus,
    BusLimits,
    Color,
    Config,
    Frame,
    HaLight,
    Layer,
    LightState,
    RuntimeV1,
    Scene,
    Settings,
    Target,
    TargetOptions,
)
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError

from .fakes import (
    PARAM_TABLE,
    START,
    FakeDriver,
    FakeHost,
    FakeLightIndex,
    FakeRegistry,
    FakeRuntimeStore,
    make_caps,
    make_light,
    make_room,
    make_zone,
)

FAST = Bus("ip:fast", "ip", "fast", BusLimits(100, 100, 0))

# ---- test-only effects


class Flicker(Effect):
    """An emulated candle: every light steps every 0.5 s."""

    key = "flicker"
    name = "Flicker"
    dynamic = True
    PERIOD = 0.5

    def render(self, ctx: RenderContext) -> Frame:
        states = {
            light.entity_id: LightState(
                brightness=round(ctx.rng.uniform(0.2, 0.6), 2),
                color=Color(kelvin=2000),
                transition=ctx.min_period,
            )
            for light in ctx.lights
        }
        return Frame(states, self.PERIOD)


class Adaptive(Effect):
    """Kelvin follows the minute of the hour; renders every `interval`."""

    key = "adaptive"
    name = "Adaptive"
    category = "adaptive"
    schema = PARAM_TABLE["adaptive"]

    def render(self, ctx: RenderContext) -> Frame:
        transition = None if ctx.first else float(ctx.params["transition"])
        at = ctx.now + timedelta(seconds=transition or 0)
        kelvin = 2200 + 200 * at.minute
        state = LightState(
            brightness=0.8, color=Color(kelvin=kelvin), transition=transition
        )
        return Frame(
            {light.entity_id: state for light in ctx.lights},
            float(ctx.params["interval"]),
        )


class Dusk(Effect):
    """Dims itself by a tenth every minute, as an adaptive scene does at night."""

    key = "dusk"
    name = "Dusk"

    def render(self, ctx: RenderContext) -> Frame:
        state = LightState(
            brightness=0.8 - 0.1 * (ctx.t // 60), color=Color(kelvin=2700)
        )
        return Frame({light.entity_id: state for light in ctx.lights}, 60.0)


class Motion(Effect):
    """Static unless the activation asks for motion, then a step every second."""

    key = "motion"
    name = "Motion"
    dynamic = True

    def render(self, ctx: RenderContext) -> Frame:
        state = LightState(brightness=0.5, color=Color(kelvin=2700))
        moving = ctx.adjust.get("dynamic", False)
        return Frame(
            {light.entity_id: state for light in ctx.lights}, 1.0 if moving else None
        )


class Sunrise(Effect):
    """A timed firmware effect: on a Hue bridge its transition is its duration."""

    key = "sunrise"
    name = "Sunrise"

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        state = LightState(brightness=0.8, effect="sunrise", transition=600.0)
        return {light.entity_id: state for light in ctx.lights}


class Native(Effect):
    """A firmware candle where supported, a plain warm white elsewhere."""

    key = "native"
    name = "Native"

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        out = {}
        for light in ctx.lights:
            state = LightState(brightness=0.4, color=Color(xy=(0.56, 0.4)))
            if light.supports_effect("candle"):
                state = state.replace(effect="candle")
            if light.caps.gradient_points:
                state = state.replace(
                    gradient=(Color(xy=(0.6, 0.35)), Color(xy=(0.2, 0.2)))
                )
            out[light.entity_id] = state
        return out


class Deal(Effect):
    """Picks a brightness from the rng once and keeps it in memory."""

    key = "deal"
    name = "Deal"
    schema = vol.Schema({vol.Optional("offset", default=0): int})

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        pick = ctx.memory.setdefault("pick", ctx.rng.randrange(1000))
        state = LightState(brightness=(pick % 50 + ctx.params["offset"] + 1) / 100)
        return {light.entity_id: state for light in ctx.lights}


class Broken(Effect):
    key = "broken"
    name = "Broken"

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        raise RuntimeError("plugin bug")


def deal_brightness(scene_id: str, target_id: str, salt: int) -> float:
    pick = seeded_rng(scene_id, target_id, 0, salt).randrange(1000)
    return (pick % 50 + 1) / 100


# ---- scenes


def preset(
    scene_id: str,
    effect: str,
    params: Mapping[str, Any] | None = None,
    *,
    lights: Any = "all",
    **kw: Any,
) -> Scene:
    return Scene(
        scene_id,
        scene_id.replace("_", " ").title(),
        None,
        (Layer(lights, effect, params or {}),),
        builtin=True,
        **kw,
    )


PRESETS = {
    s.id: s
    for s in (
        preset("relax", "white", {"kelvin": 2237, "brightness": 56}),
        preset("read", "white", {"kelvin": 2890}),
        preset("tokyo", "static", {"color": {"xy": [0.2, 0.3]}}),
        preset("candle", "flicker"),
        preset("adaptive", "adaptive"),
        preset("glow", "native"),
        preset("dark", "off"),
        preset(
            "corner",
            "white",
            {"kelvin": 2700},
            lights=("light.l1",),
        ),
        preset(
            "corner_keep",
            "white",
            {"kelvin": 2700},
            lights=("light.l1",),
            others="keep",
        ),
        preset("dealt", "deal"),
    )
}


def user_scene(
    scene_id: str,
    name: str,
    effect: str = "white",
    params: Mapping[str, Any] | None = None,
    **kw: Any,
) -> Scene:
    return Scene(scene_id, name, None, (Layer("all", effect, params or {}),), **kw)


# ---- the rig


def ha_from(state: LightState) -> HaLight:
    """Return what a light reports after applying a normalised command."""
    if not state.on:
        return HaLight(True, False, None, None, None, None, None)
    color = state.color
    return HaLight(
        True,
        True,
        None if state.brightness is None else round(state.brightness * 255),
        color.kelvin if color is not None else None,
        color.xy if color is not None else None,
        state.effect,
        None if color is None else "color_temp" if color.kelvin else "xy",
    )


def ha_on(
    brightness: int = 255,
    *,
    kelvin: int | None = None,
    xy: tuple[float, float] | None = None,
    effect: str | None = None,
) -> HaLight:
    mode = "color_temp" if kelvin else "xy" if xy else None
    return HaLight(True, True, brightness, kelvin, xy, effect, mode)


HA_OFF = HaLight(True, False, None, None, None, None, None)
HA_GONE = HaLight(False, False, None, None, None, None, None)

LIVING_LIGHTS = ("light.l1", "light.l2", "light.l3")
KITCHEN_LIGHTS = ("light.k1", "light.k2")


def world(
    *,
    living: TargetOptions | None = None,
    kitchen: TargetOptions | None = None,
    zone: TargetOptions | None = None,
    caps: Mapping[str, Any] | None = None,
) -> tuple[list, list[Target]]:
    lights = [
        make_light(e, bus=FAST, caps=(caps or {}).get(e))
        for e in (*LIVING_LIGHTS, *KITCHEN_LIGHTS)
    ]
    living_room = make_room("living", LIVING_LIGHTS, options=living)
    kitchen_room = make_room("kitchen", KITCHEN_LIGHTS, options=kitchen)
    downstairs = make_zone("downstairs", [living_room, kitchen_room], options=zone)
    return lights, [living_room, kitchen_room, downstairs]


class Rig:
    def __init__(
        self,
        *,
        scenes: Iterable[Scene] = (),
        settings: Settings | None = None,
        runtime: RuntimeV1 | None = None,
        lights: Iterable | None = None,
        targets: Iterable[Target] | None = None,
        start: bool = True,
        **world_kw: Any,
    ) -> None:
        default_lights, default_targets = world(**world_kw)
        self.host = FakeHost()
        self.index = FakeLightIndex(
            default_lights if lights is None else lights,
            default_targets if targets is None else targets,
        )
        self.registry = FakeRegistry()
        effects = (Flicker(), Adaptive(), Dusk(), Motion(), Sunrise(), Native())
        for effect in (*effects, Deal(), Broken()):
            self.registry.add(effect)
        self.driver = FakeDriver()
        self.store = FakeRuntimeStore(runtime)
        self.config = Config(
            scenes={s.id: s for s in scenes}, settings=settings or Settings()
        )
        for entity_id in self.index.lights:
            self.host.set_state(entity_id, HA_OFF)
        self.c = build_coordinator(
            self.host,
            self.index,
            self.registry,
            {"ha": self.driver},
            self.config,
            PRESETS,
            self.store,
        )
        if runtime is not None:
            self.c.restore_ownership(runtime)
        if start:
            self.c.start()

    # actions
    def activate(self, target: str, scene_id: str, **kw: Any) -> None:
        scene = self.c.resolve_scene(target, scene_id) or self.config.scenes[scene_id]
        self.c.activate(target, scene, **kw)

    def report(self, entity_id: str, new: HaLight | None) -> None:
        old = self.host.state(entity_id)
        self.host.set_state(entity_id, new)
        self.c.on_light_state(entity_id, old, new)

    def echo(self, *entity_ids: str) -> None:
        """Lights report the last command they were sent."""
        for entity_id in entity_ids or self.index.lights:
            if sent := self.driver.sent_to(entity_id):
                self.report(entity_id, ha_from(sent[-1]))

    def call(self, service: str, *entity_ids: str, bare: bool = True) -> None:
        self.c.on_foreign_call(set(entity_ids), service, bare)

    # inspection
    def sent(self, entity_id: str) -> list[LightState]:
        return self.driver.sent_to(entity_id)

    def last(self, entity_id: str) -> LightState:
        return self.sent(entity_id)[-1]

    def clear(self) -> None:
        self.driver.sent.clear()
        self.host.events.clear()

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self.host.events if kind is None or e["type"] == kind]

    def own(self, target_id: str):
        activation_id = self.c.by_target.get(target_id)
        return None if activation_id is None else self.c.activations[activation_id]


ROOM = "room:living"
KITCHEN = "room:kitchen"
ZONE = "zone:downstairs"
MENU = ("relax", "read", "tokyo", "candle", "adaptive", "glow")


# ---- tests


class ActivateTest(unittest.TestCase):
    def test_sends_the_scene_to_every_light(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        for e in LIVING_LIGHTS:
            st = rig.last(e)
            self.assertEqual(st.color.kelvin, 2237)
            self.assertEqual(st.brightness, 143 / 255)
            self.assertEqual(st.transition, 1.0)
        self.assertEqual(rig.sent("light.k1"), [])
        a = rig.own(ROOM)
        self.assertEqual(a.lights, set(LIVING_LIGHTS))
        self.assertEqual(
            rig.events(),
            [{"type": "activated", "target_id": ROOM, "scene_id": "relax"}],
        )
        status = rig.c.status(ROOM)
        self.assertEqual((status.state, status.scene_name), ("scene", "Relax"))
        snap = rig.store.last.activations
        self.assertEqual([(s.target, s.scene_id) for s in snap], [(ROOM, "relax")])
        self.assertIn(ROOM, rig.host.status_calls[-1])

    def test_brightness_and_transition(self) -> None:
        rig = Rig(settings=Settings(transition=2.0))
        rig.activate(ROOM, "read", brightness=50.4, transition=3)
        self.assertEqual(rig.c.status(ROOM).brightness, 50)
        self.assertEqual(rig.last("light.l1").brightness, 128 / 255)
        self.assertEqual(rig.last("light.l1").transition, 3)
        rig.activate(ROOM, "read")
        self.assertEqual(rig.last("light.l1").transition, 2.0)
        rig.c.activate(ROOM, replace(PRESETS["read"], transition=4.0))
        self.assertEqual(rig.last("light.l1").transition, 4.0)
        self.assertEqual(rig.c.status(ROOM).brightness, 100)

    def test_stepping_effects_switch_with_the_scene_fade(self) -> None:
        sun = user_scene("s_0000000f", "Sun", "adaptive")
        slow_sun = user_scene("s_00000010", "Slow sun", "adaptive", transition=2.5)
        fire = user_scene("s_00000011", "Fire", "light_effect", {"effect": "fireplace"})
        rig = Rig(scenes=[sun, slow_sun, fire], settings=Settings(transition=0.3))
        rig.registry.add(adaptive.Adaptive())
        rig.registry.add(light_effect.LightEffect())
        rig.activate(ROOM, "relax")
        for scene, fade in ((sun, 0.3), (slow_sun, 2.5), (fire, 0.3)):
            with self.subTest(scene.name):
                rig.activate(ROOM, scene.id)
                self.assertEqual(rig.last("light.l1").transition, fade)
        rig.activate(ROOM, sun.id)
        rig.echo()
        rig.host.advance(90)
        step = rig.own(ROOM).runs[0].prev["light.l1"]
        self.assertEqual(step.transition, 45)  # later steps glide on their own

    def test_steals_exactly_the_claimed_lights(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "tokyo")
        zone = rig.own(ZONE)
        rig.activate(ROOM, "relax")
        self.assertEqual(zone.lights, set(KITCHEN_LIGHTS))
        self.assertEqual(rig.c.owner["light.l1"], rig.own(ROOM).id)
        self.assertEqual(rig.events("ended"), [])
        status = rig.c.status(ZONE)
        self.assertEqual(status.elsewhere["light.l1"], (ROOM, "relax"))
        rig.activate(KITCHEN, "read")
        self.assertIsNone(rig.own(ZONE))
        self.assertEqual(rig.events("ended")[-1]["reason"], "superseded")

    def test_others_keep_takes_only_covered_lights(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "tokyo")
        rig.clear()
        rig.activate(ROOM, "corner_keep")
        self.assertEqual(rig.own(ROOM).lights, {"light.l1"})
        self.assertEqual(
            rig.own(ZONE).lights, {"light.l2", "light.l3", *KITCHEN_LIGHTS}
        )
        self.assertEqual(rig.sent("light.l2"), [])
        self.assertEqual(rig.c.status(ROOM).partial, False)

    def test_others_off_turns_uncovered_lights_off(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "corner")
        self.assertTrue(rig.last("light.l1").on)
        self.assertEqual(rig.last("light.l2"), OFF.replace(transition=1.0))
        self.assertEqual(rig.own(ROOM).lights, set(LIVING_LIGHTS))

    def test_running_scene_again_reclaims_and_resends(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.call("turn_on", "light.l1", bare=False)
        self.assertEqual(rig.own(ROOM).manual, {"light.l1"})
        rig.clear()
        rig.activate(ROOM, "relax")
        self.assertEqual(rig.own(ROOM).manual, set())
        self.assertEqual(len(rig.sent("light.l1")), 1)
        self.assertEqual(len(rig.sent("light.l2")), 1)  # forced although unchanged
        self.assertEqual(rig.events("ended"), [])  # the old one ends silently
        self.assertEqual(len(rig.c.activations), 1)

    def test_unknown_and_broken_effects_are_reported(self) -> None:
        bad = Scene(
            "s_000000aa",
            "Bad",
            None,
            (
                Layer(("light.l1",), "strobe", {}),
                Layer(("light.l2",), "broken", {}),
                Layer(("light.l3",), "white", {}),
            ),
        )
        rig = Rig(scenes=[bad])
        with self.assertLogs("custom_components.magic_lights.engine.activation"):
            rig.activate(ROOM, "s_000000aa")
        self.assertEqual(rig.c.status(ROOM).errors, ("layer 1: unknown effect strobe",))
        self.assertEqual(rig.sent("light.l1"), [])
        self.assertEqual(rig.sent("light.l2"), [])
        self.assertEqual(len(rig.sent("light.l3")), 1)

    def test_a_scene_covering_none_of_the_lights_changes_nothing(self) -> None:
        rig = Rig()
        rig.activate(KITCHEN, "read")
        before = rig.own(KITCHEN)
        rig.clear()
        rig.activate(KITCHEN, "corner_keep")  # keeps all but light.l1
        self.assertIs(rig.own(KITCHEN), before)
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.events(), [])

    def test_commands_are_attributed_to_the_action(self) -> None:
        user = Context(user_id="u1")
        rig = Rig()
        rig.activate(ROOM, "candle", context=user)
        first = rig.driver.sent[-1].context
        self.assertTrue(is_ours(first))
        self.assertEqual((first.parent_id, first.user_id), (user.id, "u1"))
        rig.clear()
        rig.host.advance(Flicker.PERIOD)  # later frames keep the origin
        self.assertEqual({s.context.parent_id for s in rig.driver.sent}, {user.id})
        other = Context(user_id="u2")
        rig.c.adjust_brightness(ROOM, value=50, context=other)
        self.assertEqual(rig.driver.sent[-1].context.parent_id, other.id)
        rig.c.turn_off(ROOM, context=other)
        self.assertEqual(rig.driver.sent[-1].context.user_id, "u2")

    def test_unknown_target_raises(self) -> None:
        rig = Rig()
        with self.assertRaises(ServiceValidationError):
            rig.c.activate("room:attic", PRESETS["relax"])
        self.assertEqual(rig.c.status("room:attic").state, "off")


class TurnOffReleaseReclaimTest(unittest.TestCase):
    def test_turn_off_sends_off_and_ends(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.host.set_state("light.l3", None)  # unknown: sent anyway
        rig.host.set_state("light.k1", HA_OFF)
        rig.clear()
        rig.c.turn_off(ROOM, transition=4)
        for e in LIVING_LIGHTS:
            self.assertEqual(rig.sent(e), [OFF.replace(transition=4)])
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual([e["type"] for e in rig.events()], ["ended", "off"])
        self.assertEqual(rig.events("ended")[0]["reason"], "turned_off")
        rig.echo()
        self.assertEqual(rig.c.status(ROOM).state, "off")

    def test_turn_off_skips_lights_that_are_off(self) -> None:
        rig = Rig()
        rig.report("light.l1", ha_on())
        rig.c.turn_off(ROOM)
        self.assertEqual(rig.sent("light.l1"), [OFF.replace(transition=1.0)])
        self.assertEqual(rig.sent("light.l2"), [])

    def test_turn_off_a_room_inside_a_running_zone(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "tokyo")
        rig.echo()
        rig.c.turn_off(KITCHEN)
        zone = rig.own(ZONE)
        self.assertEqual(zone.lights, set(LIVING_LIGHTS))
        rig.echo()
        status = rig.c.status(ZONE)
        self.assertEqual((status.state, status.partial), ("scene", True))
        self.assertEqual(rig.c.status(KITCHEN).state, "off")
        living = rig.c.status(ROOM)
        self.assertEqual((living.scene_id, living.via), ("tokyo", ZONE))

    def test_release_ends_without_sending(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.clear()
        rig.c.release(ROOM)
        self.assertEqual(rig.driver.sent, [])
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual([e["type"] for e in rig.events()], ["ended", "released"])
        self.assertEqual(rig.c.status(ROOM).state, "manual")

    def test_release_makes_lights_of_other_activations_manual(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "tokyo")
        rig.echo()
        rig.clear()
        rig.c.release(ROOM)
        zone = rig.own(ZONE)
        self.assertEqual(zone.manual, set(LIVING_LIGHTS))
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.c.status(ZONE).manual, LIVING_LIGHTS)
        self.assertEqual(rig.store.last.activations[0].manual, LIVING_LIGHTS)

    def test_reclaim_one_then_all(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.call("turn_on", "light.l1", "light.l2", bare=False)
        rig.clear()
        rig.c.reclaim(ROOM, "light.l1")
        self.assertEqual(rig.own(ROOM).manual, {"light.l2"})
        self.assertEqual(rig.last("light.l1").transition, 0.5)
        self.assertEqual(rig.sent("light.l3"), [])
        rig.c.reclaim(ROOM)
        self.assertEqual(rig.own(ROOM).manual, set())
        self.assertEqual(
            [e["entity_id"] for e in rig.events("reclaimed")], ["light.l1", "light.l2"]
        )


class AdjustNextReshuffleTest(unittest.TestCase):
    def test_adjust_brightness(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "read")
        rig.c.adjust_brightness(ROOM, value=50)
        self.assertEqual(rig.last("light.l1").brightness, 128 / 255)
        rig.c.adjust_brightness(ROOM, step=-10)
        self.assertEqual(rig.c.status(ROOM).brightness, 40)
        rig.c.adjust_brightness(ROOM, step=-100)
        self.assertEqual(rig.c.status(ROOM).brightness, 1)
        self.assertEqual(rig.last("light.l1").brightness, 3 / 255)
        self.assertEqual(rig.store.last.activations[0].adjust, {"scale": 0.01})
        rig.c.adjust_brightness(KITCHEN, step=10)  # nothing running there

    def test_brightness_is_the_level_of_the_brightest_light(self) -> None:
        two = Scene(
            "s_00000002",
            "Two",
            None,
            (
                Layer(("light.l1",), "white", {"kelvin": 2700, "brightness": 80}),
                Layer(("light.l2", "light.l3"), "white", {"brightness": 40}),
            ),
        )
        rig = Rig(scenes=[two])
        rig.activate(ROOM, "relax")  # white at 56 %
        self.assertEqual(rig.c.status(ROOM).brightness, 56)
        rig.c.adjust_brightness(ROOM, step=30)  # a dim scene can be brightened
        self.assertEqual(rig.c.status(ROOM).brightness, 86)
        self.assertEqual(rig.last("light.l1").brightness, 219 / 255)
        rig.activate(ROOM, "s_00000002", brightness=100)
        self.assertEqual(rig.c.status(ROOM).brightness, 100)
        self.assertEqual(rig.last("light.l1").brightness, 1.0)
        self.assertEqual(rig.last("light.l2").brightness, 128 / 255)  # in ratio

    def test_adjust_rescales_without_rendering_the_effect(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "adaptive")
        rig.echo()
        rig.host.advance(90)
        step = rig.last("light.l1")
        self.assertEqual(step.transition, 45)
        due = rig.own(ROOM).runs[0].due
        rig.echo()
        rig.host.advance(60)  # a render now would pick the next minute's colour
        rig.c.adjust_brightness(ROOM, value=40)
        st = rig.last("light.l1")
        # Answers at once, with the frame's colour; the next step stays due.
        self.assertEqual((st.transition, st.brightness), (1.0, 102 / 255))
        self.assertEqual(st.color, step.color)
        self.assertEqual(rig.own(ROOM).runs[0].due, due)

    def test_adjust_keeps_the_duration_of_a_firmware_effect(self) -> None:
        wake = user_scene("s_0000000e", "Wake", "sunrise")
        caps = make_caps(effects=frozenset({"sunrise"}))
        rig = Rig(scenes=[wake], caps=dict.fromkeys(LIVING_LIGHTS, caps))
        rig.activate(ROOM, wake.id)
        rig.c.adjust_brightness(ROOM, step=-10)
        st = rig.last("light.l1")
        self.assertEqual((st.effect, st.transition), ("sunrise", 600.0))
        self.assertAlmostEqual(st.brightness, 0.7, delta=1 / 255)

    def test_moving_colours_keep_the_level_shown(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "candle")
        shown = rig.c.status(ROOM).brightness
        rig.host.advance(10 * Flicker.PERIOD)
        self.assertEqual(rig.c.status(ROOM).brightness, shown)
        rig.c.adjust_brightness(ROOM, step=-10)
        self.assertEqual(rig.c.status(ROOM).brightness, shown - 10)

    def test_status_follows_a_scene_that_dims_itself(self) -> None:
        dusk = user_scene("s_0000000f", "Dusk", "dusk")
        candle = user_scene("s_00000010", "Candle", "flicker")
        rig = Rig(scenes=[dusk, candle])
        rig.activate(ROOM, dusk.id)
        rig.activate(KITCHEN, candle.id)
        rig.echo()
        rig.host.status_calls.clear()
        rig.host.advance(60)
        self.assertEqual(rig.c.status(ROOM).brightness, 70)
        # The flicker's steps leave the kitchen's status alone.
        self.assertEqual(rig.host.status_calls, [frozenset({ROOM, ZONE})])

    def test_adjust_acts_on_the_zone_scene_a_room_shows(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "read")
        rig.c.adjust_brightness(ROOM, value=50)
        self.assertEqual(rig.c.status(ROOM).brightness, 50)
        self.assertEqual(rig.last("light.l1").brightness, 128 / 255)
        self.assertEqual(rig.own(ZONE).lights, {*LIVING_LIGHTS, *KITCHEN_LIGHTS})

    def test_set_dynamic_switches_motion_in_place(self) -> None:
        moving = user_scene("s_0000000d", "Moving", "motion")
        rig = Rig(scenes=[moving])
        rig.activate(ROOM, moving.id)
        rig.echo()
        rig.call("turn_on", "light.l3", bare=False)
        a = rig.own(ROOM)
        self.assertFalse(rig.c.status(ROOM).dynamic)
        rig.clear()
        rig.c.set_dynamic(ROOM, True)
        self.assertIs(rig.own(ROOM), a)
        self.assertEqual(a.manual, {"light.l3"})
        self.assertTrue(rig.c.status(ROOM).dynamic)
        self.assertEqual(rig.driver.sent, [])  # same colours, now moving
        self.assertEqual(rig.store.last.activations[0].adjust, {"dynamic": True})
        rig.c.set_dynamic(ROOM, False)
        self.assertFalse(rig.c.status(ROOM).dynamic)

    def test_set_dynamic_through_a_zone_and_without_a_scene(self) -> None:
        moving = user_scene("s_0000000d", "Moving", "motion")
        rig = Rig(scenes=[moving])
        rig.activate(ZONE, moving.id)
        rig.c.set_dynamic(ROOM, True)
        self.assertTrue(rig.c.status(KITCHEN).dynamic)
        self.assertEqual(rig.own(ZONE).lights, {*LIVING_LIGHTS, *KITCHEN_LIGHTS})
        rig.c.turn_off(ZONE)
        with self.assertRaises(ServiceValidationError) as err:
            rig.c.set_dynamic(ROOM, True)
        self.assertEqual(err.exception.translation_key, "no_scene")

    def test_adjust_leaves_uncovered_lights_alone(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "corner")
        rig.clear()
        rig.c.adjust_brightness(ROOM, value=30)
        self.assertEqual(len(rig.sent("light.l1")), 1)
        self.assertEqual(rig.sent("light.l2"), [])

    def test_next_and_previous(self) -> None:
        rig = Rig(living=TargetOptions(visible_scenes=MENU))
        self.assertEqual([s.id for s in rig.c.scenes_for(ROOM)], list(MENU))
        rig.c.next_scene(ROOM)
        self.assertEqual(rig.own(ROOM).scene_id, "relax")
        rig.c.next_scene(ROOM)
        self.assertEqual(rig.own(ROOM).scene_id, "read")
        rig.c.next_scene(ROOM, -1)
        self.assertEqual(rig.own(ROOM).scene_id, "relax")
        rig.activate(ROOM, "glow")
        rig.c.next_scene(ROOM)
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.events()[-1]["type"], "off")
        rig.c.next_scene(ROOM, -1)
        self.assertEqual(rig.own(ROOM).scene_id, "glow")
        rig.activate(ROOM, "relax")
        rig.c.next_scene(ROOM, -1, cycle=False)  # nothing before the first
        self.assertEqual(rig.own(ROOM).scene_id, "relax")

    def test_next_from_a_zone_scene_steps_from_it(self) -> None:
        rig = Rig(living=TargetOptions(visible_scenes=MENU))
        rig.activate(ZONE, "read")
        rig.c.next_scene(ROOM)
        self.assertEqual(rig.own(ROOM).scene_id, "tokyo")
        self.assertEqual(rig.own(ZONE).lights, set(KITCHEN_LIGHTS))

    def test_reshuffle_reseeds_and_persists_the_salt(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "dealt")
        first = deal_brightness("dealt", ROOM, 0)
        self.assertAlmostEqual(rig.last("light.l1").brightness, first, delta=1 / 255)
        rig.c.reshuffle(ROOM)
        second = deal_brightness("dealt", ROOM, 1)
        self.assertAlmostEqual(rig.last("light.l1").brightness, second, delta=1 / 255)
        self.assertEqual(rig.store.last.salts, {f"dealt|{ROOM}": 1})
        rig.activate(ROOM, "dealt")  # the salt sticks to scene and target
        self.assertEqual(rig.own(ROOM).salt, 1)


class ScenesTest(unittest.TestCase):
    def test_default_menu_holds_the_user_scenes_in_scope(self) -> None:
        copy = user_scene("s_00000001", "Relax", preset="relax", scope=(ROOM,))
        mine = user_scene("s_00000002", "Mine", scope=(ROOM,))
        anywhere = user_scene("s_00000003", "Anywhere")
        rig = Rig(scenes=[copy, mine, anywhere])
        self.assertEqual(
            [s.id for s in rig.c.scenes_for(ROOM)], ["s_00000002", "s_00000003"]
        )
        self.assertEqual([s.id for s in rig.c.scenes_for(KITCHEN)], ["s_00000003"])
        # Hidden presets and copies still run.
        every = [s.id for s in rig.c.scenes_for(ROOM, include_hidden=True)]
        self.assertEqual(len(every), len(PRESETS) + 2)
        self.assertIn("s_00000001", every)
        self.assertIs(rig.c.find_scene(ROOM, "relax"), copy)
        self.assertEqual(rig.c.find_scene(KITCHEN, "Tokyo").id, "tokyo")

    def test_visibility_order_and_copies(self) -> None:
        copy = user_scene("s_00000001", "Relax", preset="relax", scope=(ROOM,))
        mine = user_scene("s_00000002", "Mine", scope=(ROOM,))
        rig = Rig(
            scenes=[copy, mine],
            living=TargetOptions(visible_scenes=("s_00000002", "relax", "gone")),
        )
        self.assertEqual(
            [s.id for s in rig.c.scenes_for(ROOM)], ["s_00000002", "s_00000001"]
        )
        hidden = rig.c.scenes_for(ROOM, include_hidden=True)
        self.assertEqual(len(hidden), len(PRESETS) + 1)
        self.assertIs(rig.c.resolve_scene(ROOM, "relax"), copy)
        self.assertIs(rig.c.resolve_scene(KITCHEN, "relax"), PRESETS["relax"])
        self.assertIsNone(rig.c.resolve_scene(KITCHEN, "s_00000002"))
        kitchen = [s.id for s in rig.c.scenes_for(KITCHEN)]
        self.assertNotIn("dark", kitchen)
        self.assertNotIn("s_00000002", kitchen)

    def test_find_scene(self) -> None:
        zone_only = user_scene("s_00000003", "Party", scope=(ZONE,))
        rig = Rig(scenes=[zone_only])
        self.assertEqual(rig.c.find_scene(ROOM, "relax").id, "relax")
        self.assertEqual(rig.c.find_scene(ROOM, "rELAx").id, "relax")
        self.assertEqual(rig.c.find_scene(ROOM, "dark").id, "dark")  # hidden
        with self.assertRaises(ServiceValidationError) as err:
            rig.c.find_scene(ROOM, "Party")
        self.assertEqual(err.exception.translation_key, "scene_not_available")
        with self.assertRaises(ServiceValidationError) as err:
            rig.c.find_scene(ROOM, "Nope")
        self.assertEqual(err.exception.translation_key, "unknown_scene")
        rig.activate(ZONE, "s_00000003")
        self.assertIs(rig.c.find_scene(ROOM, "party"), zone_only)  # via


class OnlyOnTest(unittest.TestCase):
    def test_default_scene_adopts_one_light_then_claims_more(self) -> None:
        rig = Rig(settings=Settings(default_scene="relax"))
        rig.report("light.l1", ha_on())
        a = rig.own(ROOM)
        self.assertEqual(
            (a.scene_id, a.only_on, a.lights), ("relax", True, {"light.l1"})
        )
        self.assertEqual(rig.last("light.l1").color.kelvin, 2237)
        self.assertEqual(rig.sent("light.l2"), [])
        self.assertTrue(rig.c.status(ROOM).partial)
        rig.echo("light.l1")
        rig.report("light.l2", ha_on())
        self.assertIs(rig.own(ROOM), a)
        self.assertEqual(a.lights, {"light.l1", "light.l2"})
        self.assertEqual(rig.last("light.l2").transition, 0.5)
        self.assertEqual(len(rig.sent("light.l1")), 1)
        rig.call("turn_on", "light.l3")
        self.assertEqual(a.lights, set(LIVING_LIGHTS))
        self.assertTrue(rig.store.last.activations[0].only_on)

    def test_room_option_wins_and_none_disables(self) -> None:
        rig = Rig(
            settings=Settings(default_scene="relax"),
            living=TargetOptions(default_scene="read"),
            kitchen=TargetOptions(default_scene="none"),
        )
        rig.report("light.l1", ha_on())
        self.assertEqual(rig.own(ROOM).scene_id, "read")
        rig.report("light.k1", ha_on())
        self.assertIsNone(rig.own(KITCHEN))
        self.assertEqual(rig.c.status(KITCHEN).state, "manual")

    def test_zone_default_when_the_room_has_none(self) -> None:
        rig = Rig(zone=TargetOptions(default_scene="tokyo"))
        rig.report("light.k1", ha_on())
        a = rig.own(ZONE)
        self.assertEqual((a.scene_id, a.lights), ("tokyo", {"light.k1"}))
        self.assertIsNone(rig.own(KITCHEN))

    def test_no_adoption_into_a_normal_activation(self) -> None:
        rig = Rig(settings=Settings(default_scene="relax"))
        rig.activate(ROOM, "corner_keep")
        rig.report("light.l2", ha_on())
        self.assertEqual(rig.own(ROOM).lights, {"light.l1"})
        self.assertNotIn("light.l2", rig.c.owner)

    def test_default_scene_that_keeps_the_light_does_nothing(self) -> None:
        rig = Rig(settings=Settings(default_scene="corner_keep"))
        rig.report("light.l2", ha_on())
        self.assertEqual(rig.c.activations, {})
        self.assertEqual(rig.driver.sent, [])

    def test_lamp_powered_on_at_the_wall_in_an_off_room(self) -> None:
        rig = Rig(settings=Settings(default_scene="relax"))
        rig.host.advance(ADOPTION_GRACE)
        rig.activate(ROOM, "read")
        rig.echo()
        rig.c.turn_off(ROOM)
        rig.echo()
        rig.report("light.l1", HA_GONE)
        rig.host.advance(600)
        rig.report("light.l1", ha_on())
        a = rig.own(ROOM)
        self.assertEqual((a.scene_id, a.lights), ("relax", {"light.l1"}))
        # One that was on before (a bridge restart) is left alone.
        rig.c.release(ROOM)
        rig.report("light.l1", HA_GONE)
        rig.clear()
        rig.report("light.l1", ha_on())
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.driver.sent, [])

    def test_only_on_never_claims_by_lights_change(self) -> None:
        rig = Rig(settings=Settings(default_scene="relax"))
        rig.report("light.l1", ha_on())
        rig.index.lights["light.l4"] = make_light("light.l4", bus=FAST)
        rig.index.targets[ROOM] = make_room("living", (*LIVING_LIGHTS, "light.l4"))
        rig.index.change(changed=[ROOM], lights=["light.l4"])
        self.assertEqual(rig.own(ROOM).lights, {"light.l1"})


class AllOffTest(unittest.TestCase):
    def test_never_during_a_scene_switch(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.activate(ROOM, "corner")
        rig.echo("light.l2", "light.l3")
        self.assertEqual(rig.own(ROOM).scene_id, "corner")
        self.assertEqual(rig.events("ended"), [])

    def test_switching_from_off_to_a_scene(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "dark")
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.host.advance(10)
        self.assertEqual(rig.own(ROOM).scene_id, "relax")

    def test_all_off_scene_ends_at_settle(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "dark")
        self.assertIsNotNone(rig.own(ROOM))
        rig.host.advance(1 + SETTLE_TIME)
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.events("ended")[0]["reason"], "all_off")

    def test_unavailable_lights_do_not_keep_it_alive(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.host.advance(10)
        rig.report("light.l1", HA_GONE)
        rig.report("light.l2", HA_OFF)
        self.assertIsNotNone(rig.own(ROOM))
        rig.report("light.l3", HA_OFF)
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.events("ended")[0]["reason"], "all_off")
        rig.clear()
        rig.report("light.l1", HA_OFF)  # back, in a room that is off
        self.assertEqual(rig.driver.sent, [])


def snapshot(
    target: str,
    scene_id: str,
    lights: Iterable[str],
    *,
    manual: Iterable[str] = (),
    adjust: Mapping[str, Any] | None = None,
    activation_id: str | None = None,
    only_on: bool = False,
) -> ActivationSnapshot:
    return ActivationSnapshot(
        id=activation_id or f"a_{target}_{scene_id}",
        target=target,
        scene_id=scene_id,
        started=START - timedelta(hours=1),
        lights=tuple(lights),
        manual=tuple(manual),
        adjust=dict(adjust or {}),
        only_on=only_on,
    )


RELAX_STATE = ha_on(143, kelvin=2237)


class RestoreTest(unittest.TestCase):
    def boot(self, runtime: RuntimeV1, states: Mapping[str, HaLight], **kw) -> Rig:
        rig = Rig(runtime=runtime, start=False, **kw)
        for entity_id, state in states.items():
            rig.host.set_state(entity_id, state)
        return rig

    def test_restore_sends_nothing_and_fires_no_events(self) -> None:
        rt = RuntimeV1(
            (snapshot(ROOM, "relax", LIVING_LIGHTS, manual=["light.l3"]),),
            {f"tokyo|{ROOM}": 3},
        )
        rig = self.boot(rt, dict.fromkeys(LIVING_LIGHTS, RELAX_STATE))
        a = rig.own(ROOM)
        self.assertEqual((a.id, a.manual), (f"a_{ROOM}_relax", {"light.l3"}))
        self.assertEqual(rig.c.salts, {f"tokyo|{ROOM}": 3})
        self.assertEqual(rig.c.status(ROOM).state, "scene")
        rig.c.start()
        rig.host.advance(1000)
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.host.events, [])
        self.assertEqual(rig.own(ROOM).manual, {"light.l3"})

    def test_matrix(self) -> None:
        diverging = {
            "relax": ha_on(143, kelvin=4000),
            "candle": ha_on(30, kelvin=4000),
            "adaptive": ha_on(204, kelvin=6500),
        }
        for scene_id in ("relax", "candle", "adaptive"):
            matching = RELAX_STATE
            if scene_id != "relax":
                # Render the scene once to learn what matches.
                probe = Rig()
                probe.activate(ROOM, scene_id)
                matching = ha_from(probe.last("light.l1"))
            for case in ("matching", "diverging", "off", "late_on", "late_off"):
                with self.subTest(scene=scene_id, case=case):
                    self._case(scene_id, case, matching, diverging[scene_id])

    def _case(
        self, scene_id: str, case: str, matching: HaLight, diverging: HaLight
    ) -> None:
        dynamic = scene_id == "candle"
        rt = RuntimeV1((snapshot(ROOM, scene_id, ["light.l1", "light.l2"]),), {})
        first = {
            "matching": matching,
            "diverging": diverging,
            "off": HA_OFF,
            "late_on": HA_GONE,
            "late_off": HA_GONE,
        }[case]
        rig = self.boot(rt, {"light.l1": first, "light.l2": matching})
        rig.c.start()
        a = rig.own(ROOM)
        if case == "late_on":
            rig.report("light.l1", diverging)
        elif case == "late_off":
            rig.report("light.l1", HA_OFF)
        sent = rig.sent("light.l1")
        if case in ("off", "late_off"):
            self.assertEqual(sent, [])
            self.assertEqual(a.manual, {"light.l1"})
            self.assertIsNotNone(rig.own(ROOM))  # light.l2 is still on
            self.assertEqual(rig.store.last.activations[0].manual, ("light.l1",))
            return
        self.assertEqual(a.manual, set())
        if case == "matching" or dynamic:
            self.assertEqual(sent, [])
        else:
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0].transition, 45 if scene_id == "adaptive" else 1.0)
        self.assertEqual(rig.sent("light.l2"), [])
        if dynamic:
            rig.host.advance(Flicker.PERIOD)
            self.assertEqual(len(rig.sent("light.l1")), 1)  # resumes stepping

    def test_everything_off_ends_it_at_start(self) -> None:
        rt = RuntimeV1((snapshot(ROOM, "relax", LIVING_LIGHTS),), {})
        rig = self.boot(rt, dict.fromkeys(LIVING_LIGHTS, HA_OFF))
        rig.c.start()
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.driver.sent, [])

    def test_wanted_off_found_on_goes_manual(self) -> None:
        rt = RuntimeV1((snapshot(ROOM, "corner", LIVING_LIGHTS),), {})
        rig = self.boot(
            rt,
            {
                "light.l1": ha_on(255, kelvin=2700),
                "light.l2": ha_on(),
                "light.l3": HA_OFF,
            },
        )
        rig.c.start()
        self.assertEqual(rig.own(ROOM).manual, {"light.l2"})
        self.assertEqual(rig.driver.sent, [])

    def test_unavailable_at_start_stays_active(self) -> None:
        rt = RuntimeV1((snapshot(ROOM, "relax", LIVING_LIGHTS),), {})
        rig = self.boot(rt, dict.fromkeys(LIVING_LIGHTS, HA_GONE))
        rig.c.start()
        self.assertIsNotNone(rig.own(ROOM))
        rig.report("light.l1", RELAX_STATE)
        rig.report("light.l2", HA_OFF)
        rig.report("light.l3", HA_OFF)
        self.assertEqual(rig.own(ROOM).manual, {"light.l2", "light.l3"})
        rig.report("light.l1", HA_OFF)  # the last on-ish light
        self.assertIsNone(rig.own(ROOM))

    def test_a_light_unseen_past_the_grace_period_keeps_nothing_alive(self) -> None:
        rt = RuntimeV1((snapshot(ROOM, "relax", LIVING_LIGHTS),), {})
        states = {"light.l1": HA_OFF, "light.l2": HA_OFF, "light.l3": HA_GONE}
        rig = self.boot(rt, states)
        rig.c.start()
        self.assertIsNotNone(rig.own(ROOM))
        rig.host.advance(ADOPTION_GRACE)
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.events("ended")[0]["reason"], "all_off")
        rig.report("light.l3", RELAX_STATE)  # powered again, hours later
        self.assertEqual(rig.driver.sent, [])
        # With no light seen at all there is nothing to judge.
        rig = self.boot(rt, dict.fromkeys(LIVING_LIGHTS, HA_GONE))
        rig.c.start()
        rig.host.advance(ADOPTION_GRACE)
        self.assertIsNotNone(rig.own(ROOM))

    def test_after_the_grace_period_returning_lights_are_reasserted(self) -> None:
        rt = RuntimeV1((snapshot(ROOM, "relax", LIVING_LIGHTS),), {})
        rig = self.boot(
            rt, {"light.l1": HA_GONE, "light.l2": RELAX_STATE, "light.l3": RELAX_STATE}
        )
        rig.c.start()
        rig.host.advance(ADOPTION_GRACE)
        rig.report("light.l1", HA_OFF)
        self.assertTrue(rig.last("light.l1").on)
        self.assertEqual(rig.own(ROOM).manual, set())

    def test_overlapping_snapshots_earlier_wins(self) -> None:
        zone = snapshot(ZONE, "tokyo", (*LIVING_LIGHTS, *KITCHEN_LIGHTS))
        room = snapshot(ROOM, "relax", LIVING_LIGHTS)
        rig = self.boot(RuntimeV1((zone, room), {}), {})
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(len(rig.own(ZONE).lights), 5)
        rig = self.boot(RuntimeV1((room, zone), {}), {})
        self.assertEqual(rig.own(ROOM).lights, set(LIVING_LIGHTS))
        self.assertEqual(rig.own(ZONE).lights, set(KITCHEN_LIGHTS))

    def test_never_widened_and_dropped_when_gone(self) -> None:
        rt = RuntimeV1(
            (
                snapshot(ROOM, "relax", ["light.l1", "light.gone"]),
                snapshot(KITCHEN, "s_deadbeef", KITCHEN_LIGHTS),
                snapshot("room:attic", "relax", ["light.a1"]),
            ),
            {},
        )
        rig = self.boot(rt, {})
        self.assertEqual(rig.own(ROOM).lights, {"light.l1"})
        self.assertIsNone(rig.own(KITCHEN))
        self.assertEqual(len(rig.c.activations), 1)

    def test_adjust_only_on_and_salt_restored(self) -> None:
        rt = RuntimeV1(
            (
                snapshot(
                    ROOM,
                    "dealt",
                    LIVING_LIGHTS,
                    adjust={"brightness": 50},
                    only_on=True,
                ),
            ),
            {f"dealt|{ROOM}": 2},
        )
        rig = self.boot(rt, {})
        a = rig.own(ROOM)
        self.assertEqual((a.adjust, a.only_on, a.salt), ({"brightness": 50}, True, 2))

    def test_effect_registered_before_start_sends_nothing(self) -> None:
        mine = user_scene("s_00000001", "Mine", "strobe")
        rt = RuntimeV1((snapshot(ROOM, mine.id, LIVING_LIGHTS),), {})
        on = ha_on(102, xy=(0.56, 0.4))
        states = {"light.l1": HA_OFF, "light.l2": on, "light.l3": on}
        rig = self.boot(rt, states, scenes=[mine])
        self.assertTrue(rig.c.status(ROOM).errors)
        rig.registry.add(type("Strobe", (Native,), {"key": "strobe"})())
        rig.c.apply_config(rig.config)
        self.assertEqual(rig.c.status(ROOM).errors, ())
        rig.c.start()
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.own(ROOM).manual, {"light.l1"})

    def test_lights_change_before_a_late_light_reports(self) -> None:
        rt = RuntimeV1((snapshot(ROOM, "relax", LIVING_LIGHTS),), {})
        rig = self.boot(
            rt, {"light.l1": HA_GONE, "light.l2": RELAX_STATE, "light.l3": RELAX_STATE}
        )
        rig.c.start()
        rig.host.set_state("light.l1", HA_OFF)  # the index hears of it first
        rig.index.change(lights=["light.l1"])
        self.assertEqual(rig.sent("light.l1"), [])
        rig.c.on_light_state("light.l1", HA_GONE, HA_OFF)
        self.assertEqual(rig.sent("light.l1"), [])
        self.assertEqual(rig.own(ROOM).manual, {"light.l1"})

    def test_activation_made_before_start_is_left_alone(self) -> None:
        rt = RuntimeV1((snapshot(ROOM, "relax", LIVING_LIGHTS),), {})
        rig = self.boot(rt, dict.fromkeys(LIVING_LIGHTS, HA_OFF))
        rig.activate(KITCHEN, "read")
        rig.c.start()
        self.assertIsNotNone(rig.own(KITCHEN))
        self.assertIsNone(rig.own(ROOM))


class ApplyConfigTest(unittest.TestCase):
    def test_edited_scene_rebuilds_in_place(self) -> None:
        mine = user_scene("s_00000001", "Mine", "deal")
        rig = Rig(scenes=[mine])
        rig.activate(ROOM, "s_00000001")
        rig.echo()
        rig.call("turn_on", "light.l3", bare=False)
        a = rig.own(ROOM)
        before = rig.last("light.l1").brightness
        rig.clear()
        edited = replace(mine, layers=(Layer("all", "deal", {"offset": 10}),))
        rig.c.apply_config(replace(rig.config, scenes={mine.id: edited}))
        self.assertIs(rig.own(ROOM), a)
        self.assertEqual(a.manual, {"light.l3"})
        # Same effect and members: the deal in memory survives the edit.
        self.assertAlmostEqual(
            rig.last("light.l1").brightness, before + 0.1, delta=1 / 255
        )
        self.assertEqual(rig.sent("light.l3"), [])
        self.assertEqual(rig.events(), [])

    def test_member_change_resets_memory(self) -> None:
        mine = user_scene("s_00000001", "Mine", "deal")
        rig = Rig(scenes=[mine])
        rig.activate(ROOM, "s_00000001")
        run = rig.own(ROOM).runs[0]
        edited = replace(mine, layers=(Layer(("light.l1",), "deal", {}),))
        rig.c.apply_config(replace(rig.config, scenes={mine.id: edited}))
        self.assertIsNot(rig.own(ROOM).runs[0], run)
        self.assertEqual(rig.last("light.l2"), OFF.replace(transition=1.0))

    def test_deleted_scene_ends(self) -> None:
        mine = user_scene("s_00000001", "Mine")
        rig = Rig(scenes=[mine])
        rig.activate(ROOM, "s_00000001")
        rig.c.apply_config(replace(rig.config, scenes={}))
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.events("ended")[0]["reason"], "config")

    def test_copy_replaces_and_reset_restores_the_preset(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        a = rig.own(ROOM)
        copy = user_scene(
            "s_00000009",
            "Relax",
            "white",
            {"kelvin": 2237, "brightness": 30},
            preset="relax",
        )
        rig.c.apply_config(replace(rig.config, scenes={copy.id: copy}))
        self.assertIs(rig.own(ROOM), a)
        self.assertIs(a.scene, copy)
        self.assertEqual(a.scene_id, "relax")
        self.assertEqual(rig.last("light.l1").brightness, 76 / 255)
        rig.c.apply_config(replace(rig.config, scenes={}))
        self.assertIs(a.scene, PRESETS["relax"])
        self.assertEqual(rig.last("light.l1").brightness, 143 / 255)

    def test_unchanged_scene_is_left_alone(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.clear()
        rig.c.apply_config(replace(rig.config, settings=Settings(transition=5)))
        self.assertEqual(rig.driver.sent, [])

    def test_running_activations_follow_transition_edits(self) -> None:
        mine = user_scene("s_00000001", "Mine", "white", {"kelvin": 2700})
        rig = Rig(scenes=[mine])
        rig.activate(ROOM, mine.id)
        rig.activate(KITCHEN, mine.id, transition=5)
        edited = replace(
            mine, transition=8.0, layers=(Layer("all", "white", {"kelvin": 4000}),)
        )
        rig.c.apply_config(replace(rig.config, scenes={mine.id: edited}))
        self.assertEqual(rig.last("light.l1").transition, 8.0)
        self.assertEqual(rig.last("light.k1").transition, 5)  # given explicitly
        unset = replace(edited, transition=None)
        rig.c.apply_config(
            Config(scenes={mine.id: unset}, settings=Settings(transition=3.0))
        )
        rig.c.adjust_brightness(ROOM, value=50)
        self.assertEqual(rig.last("light.l1").transition, 3.0)

    def test_late_registered_effect_heals_the_layer(self) -> None:
        mine = user_scene("s_00000001", "Mine", "strobe")
        rig = Rig(scenes=[mine])
        rig.activate(ROOM, "s_00000001")
        self.assertTrue(rig.c.status(ROOM).errors)
        strobe = type("Strobe", (Native,), {"key": "strobe"})
        rig.registry.add(strobe())
        rig.c.apply_config(rig.config)
        self.assertEqual(rig.c.status(ROOM).errors, ())
        self.assertEqual(len(rig.sent("light.l1")), 1)

    def test_zone_scene_running_on_a_room_survives(self) -> None:
        party = user_scene("s_00000003", "Party", scope=(ZONE,))
        rig = Rig(scenes=[party])
        rig.activate(ZONE, "s_00000003")
        rig.c.activate(ROOM, rig.c.find_scene(ROOM, "Party"))
        rig.c.apply_config(replace(rig.config, settings=Settings(transition=2)))
        self.assertEqual(rig.own(ROOM).scene_id, "s_00000003")

    def test_gone_target_ends(self) -> None:
        rig = Rig()
        rig.activate(KITCHEN, "relax")
        del rig.index.targets[KITCHEN]
        rig.c.apply_config(rig.config)
        self.assertIsNone(rig.own(KITCHEN))


DRAFT = user_scene("s_0000abcd", "Draft scene", "white", {"kelvin": 6000})


class TryTest(unittest.TestCase):
    def test_stop_brings_back_what_ran(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax", brightness=80)
        rig.echo()
        rig.call("turn_on", "light.l3", bare=False)
        before = rig.own(ROOM)
        rig.clear()
        rig.c.try_scene(ROOM, DRAFT, {"brightness": 50})
        rig.echo()
        draft = rig.own(ROOM)
        self.assertTrue(draft.draft)
        self.assertEqual(rig.last("light.l1").color.kelvin, 6000)
        self.assertEqual(rig.sent("light.l3"), [])  # the user's
        status = rig.c.status(ROOM)
        self.assertEqual((status.scene_id, status.scene_name), (None, "Draft"))
        # A restart while trying restores the replaced activation.
        self.assertEqual(rig.store.last.activations[0].id, before.id)
        rig.clear()
        rig.c.stop_try(ROOM)
        after = rig.own(ROOM)
        self.assertEqual(
            (after.id, after.scene_id, after.brightness, after.manual),
            (before.id, "relax", 80, {"light.l3"}),
        )
        self.assertEqual(rig.last("light.l1").color.kelvin, 2237)
        self.assertEqual(rig.sent("light.l3"), [])

    def test_a_light_switched_off_stays_off(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "relax")
        rig.echo()
        rig.report("light.l3", HA_OFF)
        rig.host.advance(SETTLE_TIME + 1)
        rig.clear()
        rig.c.try_scene(ROOM, DRAFT, {})
        self.assertEqual(rig.own(ROOM).manual, {"light.l3"})
        saved = replace(DRAFT, name="Bright")
        rig.c.apply_config(replace(rig.config, scenes={saved.id: saved}))
        rig.c.stop_try(ROOM)
        self.assertEqual(rig.own(ROOM).manual, {"light.l3"})  # still reclaimable
        self.assertEqual(rig.sent("light.l3"), [])

    def test_stop_gives_back_lights_taken_from_a_zone(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "tokyo")
        rig.echo()
        rig.call("turn_on", "light.l3", bare=False)
        zone = rig.own(ZONE)
        rig.c.try_scene(ROOM, DRAFT, {})
        rig.echo()
        self.assertEqual(zone.lights, set(KITCHEN_LIGHTS))
        # A restart while trying gives the zone its lights back too.
        (saved,) = rig.store.last.activations
        self.assertEqual(saved.id, zone.id)
        self.assertEqual(set(saved.lights), {*LIVING_LIGHTS, *KITCHEN_LIGHTS})
        self.assertEqual(saved.manual, ("light.l3",))
        rig.c.try_scene(ROOM, replace(DRAFT, brightness=20), {})
        rig.clear()
        rig.c.stop_try(ROOM)
        self.assertIsNone(rig.own(ROOM))
        self.assertIs(rig.own(ZONE), zone)
        self.assertEqual(zone.lights, {*LIVING_LIGHTS, *KITCHEN_LIGHTS})
        self.assertEqual(zone.manual, {"light.l3"})
        self.assertEqual(rig.last("light.l1").color.xy, (0.2, 0.3))
        self.assertEqual(rig.sent("light.l3"), [])
        self.assertEqual(rig.sent("light.k1"), [])
        self.assertEqual(rig.c.status(ROOM).via, ZONE)

    def test_stop_restores_what_a_zone_draft_superseded(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.activate(KITCHEN, "read")
        rig.echo()
        living, kitchen = rig.own(ROOM).id, rig.own(KITCHEN).id
        rig.c.try_scene(ZONE, DRAFT, {})
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual({s.id for s in rig.store.last.activations}, {living, kitchen})
        rig.c.stop_try(ZONE)
        self.assertIsNone(rig.own(ZONE))
        self.assertEqual((rig.own(ROOM).id, rig.own(KITCHEN).id), (living, kitchen))
        self.assertEqual(rig.last("light.l1").color.kelvin, 2237)
        self.assertEqual(rig.last("light.k1").color.kelvin, 2890)

    def test_trying_twice_keeps_the_original(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        original = rig.own(ROOM).id
        rig.c.try_scene(ROOM, DRAFT, None)
        rig.c.try_scene(ROOM, replace(DRAFT, brightness=20), {})
        rig.c.stop_try(ROOM)
        self.assertEqual(rig.own(ROOM).id, original)

    def test_stop_without_previous_releases(self) -> None:
        rig = Rig()
        rig.c.try_scene(ROOM, DRAFT, {})
        rig.echo()
        rig.clear()
        rig.c.stop_try(ROOM)
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.c.status(ROOM).state, "manual")
        rig.c.stop_try(ROOM)  # nothing to stop

    def test_save_during_try_swaps_the_draft(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.c.try_scene(ROOM, DRAFT, {})
        draft = rig.own(ROOM)
        rig.clear()
        saved = replace(DRAFT, name="Bright")
        rig.c.apply_config(replace(rig.config, scenes={saved.id: saved}))
        self.assertIs(rig.own(ROOM), draft)
        self.assertFalse(draft.draft)
        self.assertEqual(draft.replaced, ())
        self.assertIs(draft.scene, saved)
        self.assertEqual(rig.c.status(ROOM).scene_name, "Bright")
        self.assertEqual(rig.store.last.activations[0].scene_id, DRAFT.id)
        rig.c.stop_try(ROOM)
        self.assertIs(rig.own(ROOM), draft)

    def test_unrelated_config_change_keeps_the_draft(self) -> None:
        rig = Rig()
        rig.c.try_scene(ROOM, DRAFT, {})
        rig.c.apply_config(replace(rig.config, settings=Settings(transition=3)))
        self.assertTrue(rig.own(ROOM).draft)


class LightsChangeTest(unittest.TestCase):
    def test_new_light_in_a_target_is_claimed(self) -> None:
        rig = Rig()
        rig.activate(ZONE, "tokyo")
        rig.index.lights["light.k3"] = make_light("light.k3", bus=FAST)
        rig.host.set_state("light.k3", HA_OFF)
        rig.index.targets[KITCHEN] = make_room("kitchen", (*KITCHEN_LIGHTS, "light.k3"))
        rig.index.targets[ZONE] = make_zone(
            "downstairs", [rig.index.targets[ROOM], rig.index.targets[KITCHEN]]
        )
        rig.index.change(changed=[KITCHEN, ZONE], lights=["light.k3"])
        self.assertIn("light.k3", rig.own(ZONE).lights)
        self.assertEqual(rig.last("light.k3").color.xy, (0.2, 0.3))

    def test_removed_light_is_dropped_silently(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.clear()
        del rig.index.lights["light.l3"]
        rig.index.targets[ROOM] = make_room("living", ("light.l1", "light.l2"))
        rig.index.change(changed=[ROOM], lights=["light.l3"])
        self.assertEqual(rig.own(ROOM).lights, {"light.l1", "light.l2"})
        self.assertNotIn("light.l3", rig.c.owner)
        self.assertEqual(rig.driver.sent, [])

    def test_removed_target_ends_and_is_forwarded(self) -> None:
        rig = Rig()
        rig.activate(KITCHEN, "relax")
        del rig.index.targets[KITCHEN]
        rig.index.change(removed=[KITCHEN])
        self.assertIsNone(rig.own(KITCHEN))
        self.assertEqual(rig.host.targets_calls[-1], (frozenset(), {KITCHEN}))

    def test_caps_change_rerenders_the_light(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "tokyo")
        rig.index.lights["light.l1"] = make_light(
            "light.l1", bus=FAST, caps=make_caps(color=False)
        )
        rig.index.change(lights=["light.l1"])
        self.assertIsNotNone(rig.last("light.l1").color.kelvin)

    def test_renamed_light_stays_as_it_was(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.report("light.l2", HA_OFF)  # switched off by hand
        rig.clear()
        # The registry renames before the index sees it.
        rig.c.rename_light("light.l2", "light.lamp")
        lamp = replace(rig.index.lights.pop("light.l2"), entity_id="light.lamp")
        rig.index.lights["light.lamp"] = lamp
        rig.host.set_state("light.lamp", HA_OFF)
        rig.host.set_state("light.l2", None)
        room = make_room("living", ("light.l1", "light.lamp", "light.l3"))
        rig.index.targets[ROOM] = room
        rig.index.targets[ZONE] = make_zone(
            "downstairs", [room, rig.index.targets[KITCHEN]]
        )
        rig.index.change(changed=[ROOM, ZONE], lights=["light.l2", "light.lamp"])
        rig.host.advance(SETTLE_TIME + 2)
        activation = rig.own(ROOM)
        self.assertEqual(activation.lights, {"light.l1", "light.lamp", "light.l3"})
        self.assertEqual(activation.manual, {"light.lamp"})
        self.assertEqual(rig.c.owner["light.lamp"], activation.id)
        self.assertNotIn("light.l2", rig.c.owner)
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.events(), [])

    def test_shutdown_cancels_timers(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "candle")
        rig.c.shutdown()
        self.assertEqual(rig.host.pending_timers, [])


if __name__ == "__main__":
    unittest.main()
