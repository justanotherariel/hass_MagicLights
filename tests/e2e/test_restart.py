"""A restart keeps every activation, sends nothing and adopts late lights."""

from __future__ import annotations

import shutil
from typing import Any
from unittest import IsolatedAsyncioTestCase

from homeassistant.components.frontend import async_panel_exists
from homeassistant.const import STATE_ON

from tests.harness import DOMAIN, Harness
from tests.scenario import DEMO_AREAS, DEMO_LIGHTS

from . import KITCHEN, LIVING, HarnessCase


def lights_as_left(h: Harness) -> list[dict[str, Any]]:
    """Return the demo lights configured to boot as they are now."""
    out = []
    for conf in DEMO_LIGHTS:
        state = h.hass.states.get("light." + conf["name"].lower().replace(" ", "_"))
        conf = dict(conf, on=state.state == STATE_ON)
        if conf["on"]:
            attrs = state.attributes
            conf["brightness"] = attrs.get("brightness")
            if attrs.get("color_mode") == "color_temp":
                conf["kelvin"] = attrs["color_temp_kelvin"]
            elif attrs.get("color_mode") == "xy":
                conf["xy"] = attrs["xy_color"]
        out.append(conf)
    return out


class RestartTest(IsolatedAsyncioTestCase):
    async def test_restart_sends_nothing(self) -> None:
        first = Harness(lights=DEMO_LIGHTS, areas=DEMO_AREAS, keep_dir=True)
        self.addCleanup(shutil.rmtree, first.config_dir, True)
        await first.start()
        try:
            await first.setup_integration()
            await first.select(LIVING, "Relax")
            await first.select(KITCHEN, "Savanna sunset")
            first.entity("light.living_floor_lamp").physical_change(on=False)
            await first.idle()
            lights = lights_as_left(first)
        finally:
            await first.stop()

        by_name = {conf["name"]: conf for conf in lights}
        by_name["Living TV Strip"]["available"] = False  # its bridge is still starting
        by_name["Living Sofa Lamp"]["on"] = False  # switched off while HA was down
        second = Harness(lights=lights, areas=DEMO_AREAS, config_dir=first.config_dir)
        await second.start()
        try:
            await second.idle()
            self.assertEqual(second.calls(), [])
            self.assertEqual(second.published, [])
            self.assertEqual(second.hass.states.get(LIVING).state, "Relax")
            self.assertEqual(second.hass.states.get(KITCHEN).state, "Savanna sunset")
            attrs = second.attributes(LIVING)
            self.assertEqual(
                attrs["manual"], ["light.living_floor_lamp", "light.living_sofa_lamp"]
            )
            self.assertEqual(attrs["lights"]["light.living_tv_strip"], "unavailable")

            second.entity("light.living_tv_strip").set_available(True)
            await second.idle()
            self.assertEqual(second.calls(), [])
            self.assertEqual(
                second.attributes(LIVING)["lights"]["light.living_tv_strip"], "scene"
            )
            self.assertEqual(second.events, [])
        finally:
            await second.stop()


class ReloadTest(HarnessCase):
    async def test_reload_keeps_the_scenes_and_sends_nothing(self) -> None:
        h = self.h
        await h.select(LIVING, "Relax")
        await self.light_call("turn_on", "light.living_ceiling", brightness=30)
        h.clear_calls()
        (entry,) = h.hass.config_entries.async_entries(DOMAIN)
        self.assertTrue(await h.hass.config_entries.async_reload(entry.entry_id))
        await h.idle()
        self.assertEqual(h.calls(), [])
        self.assertEqual(self.state(LIVING), "Relax")
        self.assertEqual(self.status(LIVING)["manual"], ["light.living_ceiling"])
        self.assertTrue(async_panel_exists(h.hass, DOMAIN))
        await h.select(KITCHEN, "Read")
        self.assertEqual(len(h.calls()), 3)


class RemoveTest(HarnessCase):
    async def test_adding_again_forgets_what_ran(self) -> None:
        h = self.h
        hass = h.hass
        await h.ws(
            {
                "type": "magic_lights/zone/save",
                "zone": {"id": "", "name": "Upstairs", "rooms": ["bedroom"]},
            }
        )
        await h.select(LIVING, "Relax")
        (entry,) = hass.config_entries.async_entries(DOMAIN)
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.services.async_call(
            "light",
            "turn_on",
            {
                "entity_id": "light.living_ceiling",
                "brightness": 255,
                "color_temp_kelvin": 6000,
            },
            blocking=True,
        )
        await h.settle()
        h.clear_calls()

        await h.setup_integration()
        await h.idle()
        self.assertEqual(h.calls(), [])
        self.assertEqual(self.state(LIVING), "Manual")
        # The rooms, zones and scenes stay.
        self.assertIsNotNone(hass.states.get("select.upstairs_scene"))
