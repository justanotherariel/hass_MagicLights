"""Pacing: lights moved onto one Zigbee bus share its limits."""

from __future__ import annotations

import itertools

from tests.scenario import DEMO_SETTINGS

from . import KITCHEN, HarnessCase

MIN_INTERVAL = 1.5
SPOTS = ["light.kitchen_spot_1", "light.kitchen_spot_2"]


class BusTest(HarnessCase):
    storage = {
        "version": 2,
        "minor_version": 1,
        "key": "magic_lights",
        "data": {
            "settings": {
                **DEMO_SETTINGS,
                "buses": {"zigbee:test": {"min_interval": MIN_INTERVAL}},
            }
        },
    }

    async def test_flicker_is_paced_per_light(self) -> None:
        h = self.h
        config = await h.ws({"type": "magic_lights/config"})
        (bus,) = [b for b in config["buses"] if b["id"] == "zigbee:test"]
        self.assertEqual(
            bus["lights"],
            ["light.kitchen_island", "light.kitchen_spot_1", "light.kitchen_spot_2"],
        )
        self.assertEqual(bus["limits"]["min_interval"], MIN_INTERVAL)
        self.assertEqual(bus["defaults"]["min_interval"], 0.5)

        # The emulated candle wants a frame every 0.3-0.9 s.
        await h.select(KITCHEN, "Cozy candle")
        await h.settle(4.2)
        for spot in SPOTS:
            times = [call["t"] for call in self.ours(spot)]
            self.assertGreaterEqual(len(times), 2, spot)
            self.assertLessEqual(len(times), 4, spot)
            for earlier, later in itertools.pairwise(times):
                self.assertGreaterEqual(later - earlier, MIN_INTERVAL - 0.05, spot)
            # Fades span the period the bus allows.
            self.assertGreaterEqual(
                self.ours(spot)[-1]["data"]["transition"], MIN_INTERVAL
            )
