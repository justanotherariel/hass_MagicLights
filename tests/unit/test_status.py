"""compute_status: own, via, mixed, manual, off, partial, drafts, unavailable."""

from __future__ import annotations

from collections.abc import Iterable
import random
import unittest

from custom_components.magic_lights.engine.activation import Activation, LayerRun
from custom_components.magic_lights.effects.base import Effect, RenderContext
from custom_components.magic_lights.engine.status import compute_status
from custom_components.magic_lights.models import HaLight, Layer, LightState, Scene

from .fakes import START, ha_light, make_room, make_zone

LIVING = make_room("living", ["light.l1", "light.l2"])
KITCHEN = make_room("kitchen", ["light.k1"])
DOWNSTAIRS = make_zone("downstairs", [LIVING, KITCHEN])


class Wave(Effect):
    key = "wave"
    name = "Wave"
    dynamic = True

    def render(self, ctx: RenderContext) -> dict:
        return {}


def scene(scene_id: str, name: str, brightness: int = 100) -> Scene:
    return Scene(
        scene_id, name, None, (Layer("all", "white", {}),), brightness=brightness
    )


def activation(
    activation_id: str,
    target_id: str,
    sc: Scene,
    lights: Iterable[str],
    *,
    manual: Iterable[str] = (),
    draft: bool = False,
) -> Activation:
    return Activation(
        id=activation_id,
        target_id=target_id,
        scene_id=sc.public_id,
        scene=sc,
        started=START,
        transition=1.0,
        draft=draft,
        lights=set(lights),
        manual=set(manual),
    )


class StatusRig:
    def __init__(self, *activations: Activation) -> None:
        self.activations = {a.id: a for a in activations}
        self.by_target = {a.target_id: a.id for a in activations}
        self.owner = {e: a.id for a in activations for e in a.lights}
        self.states: dict[str, HaLight] = {e: ha_light(True) for e in DOWNSTAIRS.lights}

    def status(self, target=DOWNSTAIRS):
        return compute_status(
            target, self.activations, self.by_target, self.owner, self.states.get
        )


RELAX = scene("relax", "Relax", brightness=56)
TOKYO = scene("tokyo", "Tokyo")


class OwnActivationTest(unittest.TestCase):
    def test_own_scene(self) -> None:
        a = activation(
            "a", DOWNSTAIRS.id, TOKYO, DOWNSTAIRS.lights, manual=["light.k1"]
        )
        a.errors.append("layer 2: unknown effect strobe")
        rig = StatusRig(a)
        rig.states["light.l2"] = ha_light(True, available=False)
        st = rig.status()
        self.assertEqual(st.state, "scene")
        self.assertEqual((st.scene_id, st.scene_name, st.via), ("tokyo", "Tokyo", None))
        self.assertFalse(st.partial)
        self.assertEqual(st.brightness, 100)
        self.assertEqual(st.manual, ("light.k1",))
        self.assertEqual(
            st.lights,
            {"light.k1": "manual", "light.l1": "scene", "light.l2": "unavailable"},
        )
        self.assertEqual(st.since, START)
        self.assertEqual(st.errors, ("layer 2: unknown effect strobe",))
        self.assertEqual(st.elsewhere, {})

    def test_adjusted_brightness_and_dynamic(self) -> None:
        a = activation("a", LIVING.id, RELAX, LIVING.lights)
        a.runs.append(LayerRun(0, Wave(), {}, ("light.l1",), random.Random(1)))
        st = StatusRig(a).status(LIVING)
        self.assertEqual(st.brightness, 56)
        self.assertFalse(st.dynamic)  # dynamic effect, but not animating
        a.adjust["scale"] = 0.2
        a.runs[0].due = 5.0
        st = StatusRig(a).status(LIVING)
        self.assertEqual(st.brightness, 20)
        self.assertTrue(st.dynamic)
        # The level of the brightest active light the effect last rendered.
        a.runs[0].members = ("light.l1", "light.l2")
        a.runs[0].steady = {
            "light.l1": LightState(brightness=0.5),
            "light.l2": LightState(brightness=0.9),
        }
        self.assertEqual(StatusRig(a).status(LIVING).brightness, 18)
        a.manual.add("light.l2")
        self.assertEqual(StatusRig(a).status(LIVING).brightness, 10)

    def test_motion_is_the_play_state_not_any_animation(self) -> None:
        a = activation("a", LIVING.id, RELAX, LIVING.lights)
        palette = LayerRun(
            0, Wave(), {"motion": "static"}, ("light.l1",), random.Random(1)
        )
        candle = LayerRun(1, Wave(), {}, ("light.l2",), random.Random(1), due=5.0)
        a.runs += [palette, candle]
        st = StatusRig(a).status(LIVING)
        self.assertTrue(st.dynamic)  # the candle flickers
        self.assertFalse(st.motion)  # the palette holds
        a.adjust["dynamic"] = True
        self.assertTrue(StatusRig(a).status(LIVING).motion)
        a.adjust["dynamic"] = False
        palette.params = {"motion": "dynamic"}
        self.assertFalse(StatusRig(a).status(LIVING).motion)
        del a.adjust["dynamic"]
        self.assertTrue(StatusRig(a).status(LIVING).motion)

    def test_lights_elsewhere(self) -> None:
        zone = activation("z", DOWNSTAIRS.id, TOKYO, ["light.k1"])
        living = activation("l", LIVING.id, RELAX, LIVING.lights)
        st = StatusRig(zone, living).status()
        self.assertEqual(st.state, "scene")
        self.assertEqual(st.scene_id, "tokyo")
        self.assertEqual(
            st.lights,
            {"light.k1": "scene", "light.l1": "elsewhere", "light.l2": "elsewhere"},
        )
        self.assertEqual(
            st.elsewhere,
            {"light.l1": (LIVING.id, "relax"), "light.l2": (LIVING.id, "relax")},
        )
        self.assertFalse(st.partial)

    def test_a_draft_elsewhere_hides_its_id(self) -> None:
        zone = activation("z", DOWNSTAIRS.id, TOKYO, ["light.k1"])
        mine = scene("s_0000abcd", "Mine")
        living = activation("l", LIVING.id, mine, LIVING.lights, draft=True)
        st = StatusRig(zone, living).status()
        self.assertEqual(st.elsewhere["light.l1"], (LIVING.id, None))

    def test_partial_with_unowned_lights(self) -> None:
        a = activation("a", DOWNSTAIRS.id, TOKYO, ["light.l1"])
        rig = StatusRig(a)
        rig.states["light.k1"] = ha_light(False)
        st = rig.status()
        self.assertTrue(st.partial)
        self.assertEqual(
            st.lights, {"light.k1": "off", "light.l1": "scene", "light.l2": "on"}
        )
        self.assertEqual(st.manual, ())  # nothing to reclaim

    def test_draft(self) -> None:
        a = activation(
            "a", LIVING.id, scene("s_0000abcd", "Mine"), LIVING.lights, draft=True
        )
        st = StatusRig(a).status(LIVING)
        self.assertEqual(
            (st.state, st.scene_id, st.scene_name), ("scene", None, "Draft")
        )


class ForeignActivationTest(unittest.TestCase):
    def test_via_zone(self) -> None:
        zone = activation("z", DOWNSTAIRS.id, TOKYO, DOWNSTAIRS.lights)
        st = StatusRig(zone).status(LIVING)
        self.assertEqual(st.state, "scene")
        self.assertEqual((st.scene_id, st.via), ("tokyo", DOWNSTAIRS.id))
        self.assertEqual(st.lights, {"light.l1": "scene", "light.l2": "scene"})
        self.assertEqual(st.since, START)

    def test_via_partial_after_kitchen_off(self) -> None:
        living = activation("l", LIVING.id, RELAX, LIVING.lights)
        rig = StatusRig(living)
        rig.states["light.k1"] = ha_light(False)
        st = rig.status()
        self.assertEqual((st.state, st.scene_id, st.via), ("scene", "relax", LIVING.id))
        self.assertTrue(st.partial)

    def test_via_draft(self) -> None:
        zone = activation("z", DOWNSTAIRS.id, TOKYO, DOWNSTAIRS.lights, draft=True)
        st = StatusRig(zone).status(KITCHEN)
        self.assertEqual(
            (st.scene_id, st.scene_name, st.via), (None, "Draft", DOWNSTAIRS.id)
        )

    def test_mixed(self) -> None:
        living = activation("l", LIVING.id, RELAX, LIVING.lights)
        kitchen = activation("k", KITCHEN.id, TOKYO, KITCHEN.lights)
        st = StatusRig(living, kitchen).status()
        self.assertEqual(st.state, "mixed")
        self.assertIsNone(st.scene_id)
        self.assertEqual(
            set(st.parts),
            {
                (KITCHEN.id, "tokyo", "Tokyo", ("light.k1",)),
                (LIVING.id, "relax", "Relax", ("light.l1", "light.l2")),
            },
        )
        self.assertEqual(
            st.to_dict()["parts"][0].keys(),
            {"target_id", "scene_id", "scene_name", "lights"},
        )


class NoActivationTest(unittest.TestCase):
    def test_manual_when_a_light_is_on(self) -> None:
        rig = StatusRig()
        rig.states["light.l1"] = ha_light(False)
        st = rig.status(LIVING)
        self.assertEqual(st.state, "manual")
        self.assertEqual(st.lights, {"light.l1": "off", "light.l2": "on"})
        self.assertEqual(st.manual, ())

    def test_off(self) -> None:
        rig = StatusRig()
        rig.states = {e: ha_light(False) for e in DOWNSTAIRS.lights}
        del rig.states["light.k1"]
        st = rig.status()
        self.assertEqual(st.state, "off")
        self.assertEqual(st.lights["light.k1"], "unavailable")
        self.assertFalse(st.partial)
        self.assertEqual(
            set(st.to_dict()),
            {
                "status", "scene_id", "scene_name", "via", "partial", "dynamic",
                "motion", "brightness", "manual", "lights", "elsewhere", "parts",
                "since", "errors",
            },
        )  # fmt: skip


if __name__ == "__main__":
    unittest.main()
