"""Lights changed by others become manual; switched-on lights rejoin or adopt."""

from __future__ import annotations

import time

from . import BEDROOM, KITCHEN, LIVING, HarnessCase


class OverrideTest(HarnessCase):
    async def test_turn_on_with_and_without_attributes(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Relax")
        h.clear_calls()
        h.events.clear()

        # Bare turn_on of a light the scene already has on: nothing happens.
        await self.light_call("turn_on", "light.living_ceiling")
        self.assertEqual(self.status(LIVING)["manual"], [])
        self.assertEqual(self.ours("light.living_ceiling"), [])

        # With attributes: the user changed it, it is left alone.
        await self.light_call("turn_on", "light.living_ceiling", brightness=40)
        self.assertEqual(self.status(LIVING)["manual"], ["light.living_ceiling"])
        self.assertEqual(self.state(LIVING), "Relax")
        self.assertEqual(
            h.events[-1],
            {
                "type": "override",
                "target_id": "room:living_room",
                "scene_id": "relax",
                "entity_id": "light.living_ceiling",
                "reason": "changed",
            },
        )
        await h.call("adjust_brightness", LIVING, step=-20)
        self.assertEqual(self.ours("light.living_ceiling"), [])
        self.assertEqual(
            h.hass.states.get("light.living_ceiling").attributes["brightness"], 40
        )

        # The running scene again reclaims it.
        await h.call("activate_scene", LIVING, scene="relax")
        self.assertEqual(self.status(LIVING)["manual"], [])
        self.assertEqual(len(self.ours("light.living_ceiling")), 1)

    async def test_switched_off_lamp_rejoins_on_bare_turn_on(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Relax")
        h.entity("light.living_floor_lamp").physical_change(on=False)
        await h.settle()
        self.assertEqual(self.status(LIVING)["manual"], ["light.living_floor_lamp"])
        self.assertEqual(h.events[-1]["reason"], "turned_off")

        h.clear_calls()
        await self.light_call("turn_on", "light.living_floor_lamp")
        self.assertEqual(self.status(LIVING)["manual"], [])
        (rejoin,) = self.ours("light.living_floor_lamp")
        self.assertEqual(
            rejoin["data"],
            {"brightness": 143, "color_temp_kelvin": 2237, "transition": 0.4},
        )

    async def test_turn_on_of_a_light_the_scene_keeps_off(self) -> None:
        h = self.h
        await h.ws(
            {
                "type": "magic_lights/scene/save",
                "scene": {
                    "id": "s_00000001",
                    "name": "Ceiling only",
                    "scope": ["room:living_room"],
                    "layers": [
                        {
                            "lights": ["light.living_ceiling"],
                            "effect": "white",
                            "params": {"kelvin": 3000, "brightness": 80},
                        }
                    ],
                },
            }
        )
        await h.select(LIVING, "Ceiling only")
        self.assertEqual(
            self.ours("light.living_floor_lamp")[-1]["service"], "turn_off"
        )
        h.clear_calls()
        await self.light_call("turn_on", "light.living_floor_lamp")
        self.assertEqual(self.status(LIVING)["manual"], ["light.living_floor_lamp"])
        self.assertEqual(h.events[-1]["reason"], "turned_on")
        self.assertEqual(self.ours("light.living_floor_lamp"), [])

    async def test_wall_switch_off_during_a_dynamic_scene_stays_off(self) -> None:
        h = self.h
        await h.call("activate_scene", KITCHEN, scene="Cozy candle")
        spot = "light.kitchen_spot_1"
        # Emulated flicker: a new step every 1.5 to 3 s.
        for _ in range(40):
            if len(self.ours(spot)) > 1:
                break
            await h.settle(0.1)
        self.assertGreater(len(self.ours(spot)), 1)
        # The island runs the firmware candle through Zigbee2MQTT.
        topic, payload = h.published[0]
        self.assertEqual(topic, "zigbee2mqtt/kitchen_island/set")
        self.assertEqual(payload["effect"], "candle")
        self.assertEqual(self.ours("light.kitchen_island"), [])

        # Within 5 s of our last command HA still stamps the state with our
        # context; the last-command rule must classify it anyway.
        switched = time.monotonic()
        h.entity(spot).physical_change(on=False)
        await h.settle(3.5)
        self.assertEqual(h.hass.states.get(spot).state, "off")
        self.assertEqual(
            [c for c in self.ours(spot, since=switched) if c["service"] == "turn_on"],
            [],
        )
        attrs = self.status(KITCHEN)
        self.assertEqual(attrs["manual"], [spot])
        self.assertEqual(self.state(KITCHEN), "Cozy candle")
        self.assertTrue(attrs["dynamic"])

        # The last lit lamps switched off too: the scene ends.
        for light in ("light.kitchen_spot_2", "light.kitchen_island"):
            h.entity(light).physical_change(on=False)
        await h.settle(0.2)
        self.assertEqual(self.state(KITCHEN), "Off")
        self.assertEqual(h.events[-1]["type"], "ended")
        self.assertEqual(h.events[-1]["reason"], "all_off")

    async def test_default_scene_adopts_switched_on_lights(self) -> None:
        h = self.h
        await h.ws(
            {
                "type": "magic_lights/settings/save",
                "settings": {"default_scene": "adaptive"},
            }
        )
        await self.light_call("turn_on", "light.bedroom_ceiling")
        self.assertEqual(self.state(BEDROOM), "Adaptive")
        (adopted,) = self.ours("light.bedroom_ceiling")
        self.assertEqual(
            set(adopted["data"]), {"brightness", "color_temp_kelvin", "transition"}
        )
        self.assertEqual(h.calls("light.bedroom_nightstand"), [])
        self.assertTrue(self.status(BEDROOM)["partial"])

        # The next lamp switched on by hand joins the same activation.
        h.entity("light.bedroom_nightstand").physical_change(
            on=True, clear_context=True
        )
        await h.idle()
        self.assertEqual(len(self.ours("light.bedroom_nightstand")), 1)
        self.assertFalse(self.status(BEDROOM)["partial"])

        # A room can opt out of the global default.
        await h.ws(
            {
                "type": "magic_lights/target/options",
                "target_id": "room:hallway",
                "options": {"default_scene": "none"},
            }
        )
        await self.light_call("turn_on", "light.hallway")
        self.assertEqual(self.state("select.hallway_scene"), "Manual")
        self.assertEqual(self.ours("light.hallway"), [])


class DriftTest(HarnessCase):
    """Changes reported outside the settle window (shortened here)."""

    settle = 0.3

    async def test_noise_is_ignored_and_real_changes_are_overrides(self) -> None:
        h = self.h
        office = "select.office_scene"
        # The desk reports late and with a little brightness noise.
        await h.call("activate_scene", office, scene="Read")
        await h.settle(1.6)
        self.assertEqual(self.status(office)["manual"], [])
        h.entity("light.office_desk").physical_change(kelvin=2890, brightness=245)
        await h.settle(0.1)
        self.assertEqual(self.status(office)["manual"], [])

        # Someone turns the desk to cold white in another app.
        h.entity("light.office_desk").physical_change(kelvin=5000, clear_context=True)
        await h.settle(0.1)
        self.assertEqual(self.status(office)["manual"], ["light.office_desk"])
        self.assertEqual(h.events[-1]["reason"], "changed")

        # With detect_changes off, the same goes unnoticed.
        await h.call("reclaim", office)
        await h.ws(
            {
                "type": "magic_lights/target/options",
                "target_id": "room:office",
                "options": {"detect_changes": False},
            }
        )
        await h.settle(1.6)
        h.entity("light.office_desk").physical_change(kelvin=5000, clear_context=True)
        await h.settle(0.1)
        self.assertEqual(self.status(office)["manual"], [])
