"""Preview: frames over time, defaults, limits, brightness, others, caps."""

from __future__ import annotations

import unittest

from custom_components.magic_lights.engine.preview import MAX_FRAMES, preview
from custom_components.magic_lights.models import Color

from .fakes import BERLIN, START, make_caps
from .test_coordinator import LIVING_LIGHTS, ROOM, SCENES, Rig, deal_brightness

WARM_HEX = Color(kelvin=2237).as_hex()


class PreviewTest(unittest.TestCase):
    def test_static_scene_gives_one_frame(self) -> None:
        rig = Rig()
        frames = rig.c.preview(ROOM, SCENES["relax"], start=START)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["t"], 0)
        self.assertEqual(
            frames[0]["lights"]["light.l1"],
            {"on": True, "brightness": round(143 / 255, 3), "rgb": WARM_HEX},
        )
        self.assertEqual(sorted(frames[0]["lights"]), list(LIVING_LIGHTS))
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.host.events, [])

    def test_dynamic_scene_over_time(self) -> None:
        rig = Rig()
        frames = rig.c.preview(ROOM, SCENES["candle"])
        self.assertEqual(len(frames), 40)
        self.assertEqual((frames[0]["t"], frames[-1]["t"]), (0, 20))
        levels = {f["lights"]["light.l1"]["brightness"] for f in frames}
        self.assertGreater(len(levels), 5)
        self.assertEqual(rig.c.preview(ROOM, SCENES["candle"]), frames)
        short = rig.c.preview(ROOM, SCENES["candle"], duration=2, samples=5)
        self.assertEqual([f["t"] for f in short], [0, 0.5, 1.0, 1.5, 2.0])

    def test_adaptive_defaults_to_a_day(self) -> None:
        rig = Rig()
        frames = rig.c.preview(ROOM, SCENES["adaptive"], start=START)
        self.assertEqual(len(frames), 48)
        self.assertEqual(frames[-1]["t"], 86400)
        self.assertGreater(len({f["lights"]["light.l1"]["rgb"] for f in frames}), 1)

    def test_frame_limit(self) -> None:
        rig = Rig()
        frames = rig.c.preview(ROOM, SCENES["candle"], samples=10_000)
        self.assertEqual(len(frames), MAX_FRAMES)

    def test_brightness_adjust_and_others(self) -> None:
        rig = Rig()
        (frame,) = rig.c.preview(ROOM, SCENES["read"], adjust={"brightness": 50})
        self.assertEqual(frame["lights"]["light.l1"]["brightness"], round(128 / 255, 3))
        # The level of the brightest light, so a dim scene can be shown brighter.
        (frame,) = rig.c.preview(ROOM, SCENES["relax"], adjust={"brightness": 100})
        self.assertEqual(frame["lights"]["light.l1"]["brightness"], 1.0)
        (frame,) = rig.c.preview(ROOM, SCENES["corner"])
        self.assertEqual(
            frame["lights"]["light.l2"],
            {"on": False, "brightness": 0.0, "rgb": "#000000"},
        )
        (frame,) = rig.c.preview(ROOM, SCENES["corner_keep"])
        self.assertEqual(list(frame["lights"]), ["light.l1"])

    def test_states_are_normalised_per_light(self) -> None:
        caps = {
            "light.l1": make_caps(effects=frozenset({"candle"})),
            "light.l2": make_caps(gradient_points=2),
            "light.l3": make_caps(color=False, color_temp=False),
        }
        rig = Rig(caps=caps)
        (frame,) = rig.c.preview(ROOM, SCENES["glow"])
        lights = frame["lights"]
        self.assertEqual(lights["light.l1"]["effect"], "candle")
        self.assertEqual(len(lights["light.l2"]["gradient"]), 2)
        self.assertNotIn("effect", lights["light.l2"])
        self.assertEqual(lights["light.l3"]["rgb"], Color(kelvin=2700).as_hex())

    def test_uses_the_live_salt(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "dealt")
        rig.c.reshuffle(ROOM)
        (frame,) = rig.c.preview(ROOM, SCENES["dealt"])
        self.assertAlmostEqual(
            frame["lights"]["light.l1"]["brightness"],
            deal_brightness("dealt", ROOM, 1),
            delta=1 / 255,
        )

    def test_without_a_coordinator(self) -> None:
        rig = Rig()
        frames = preview(
            SCENES["candle"],
            rig.index.targets[ROOM],
            rig.index,
            rig.registry,
            sun=BERLIN,
            start=START,
            duration=0,
            samples=1,
        )
        self.assertEqual(len(frames), 1)


if __name__ == "__main__":
    unittest.main()
