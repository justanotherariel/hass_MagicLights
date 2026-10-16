"""Entity registry changes to lights the configuration refers to."""

from __future__ import annotations

import asyncio
import json

from homeassistant.helpers import entity_registry as er

from tests.harness import DOMAIN

from . import LIVING, HarnessCase

RENAMES = {
    "light.living_sofa_lamp": "light.sofa",
    "light.living_tv_strip": "light.tv",
    "light.office_desk": "light.desk",
    "light.hallway": "light.hall",
}


class RenameTest(HarnessCase):
    async def test_a_renamed_light_keeps_its_place_in_the_config(self) -> None:
        h = self.h
        await h.ws(
            {
                "type": "magic_lights/target/options",
                "target_id": "room:living_room",
                "options": {"excluded_lights": ["light.living_sofa_lamp"]},
            }
        )
        await h.ws(
            {
                "type": "magic_lights/zone/save",
                "zone": {
                    "id": "",
                    "name": "Downstairs",
                    "rooms": ["kitchen"],
                    "lights": ["light.office_desk"],
                },
            }
        )
        red = {"xy": [0.68, 0.31]}
        await h.ws(
            {
                "type": "magic_lights/scene/save",
                "scene": {
                    "id": "s_0000f001",
                    "name": "TV red",
                    "scope": ["room:living_room"],
                    "layers": [
                        {"lights": "all", "effect": "white", "params": {}},
                        {
                            "lights": ["light.living_tv_strip"],
                            "effect": "static",
                            "params": {"color": red},
                        },
                    ],
                },
            }
        )
        await h.ws(
            {
                "type": "magic_lights/settings/save",
                "settings": {"light_buses": {"light.hallway": "zigbee:test"}},
            }
        )
        await h.select(LIVING, "Relax")
        h.clear_calls()

        registry = er.async_get(h.hass)
        for old, new in RENAMES.items():
            registry.async_update_entity(old, new_entity_id=new)
        await h.idle()

        # The excluded lamp stays out of the room, and off.
        self.assertEqual(h.calls("light.sofa"), [])
        self.assertNotIn("light.sofa", self.status(LIVING)["lights"])
        self.assertIn("light.desk", self.status("select.downstairs_scene")["lights"])
        path = h.config_dir / ".storage" / DOMAIN
        stored = json.loads(await asyncio.to_thread(path.read_text))["data"]
        self.assertEqual(
            stored["targets"]["room:living_room"]["excluded_lights"], ["light.sofa"]
        )
        self.assertEqual(stored["zones"]["downstairs"]["lights"], ["light.desk"])
        layers = stored["scenes"]["s_0000f001"]["layers"]
        self.assertEqual(layers[1]["lights"], ["light.tv"])
        self.assertEqual(
            stored["settings"]["light_buses"], {"light.hall": "zigbee:test"}
        )

        h.clear_calls()
        await h.select(LIVING, "TV red")
        self.assertIn("xy_color", h.calls("light.tv")[-1]["data"])

    async def test_a_renamed_manual_lamp_stays_off(self) -> None:
        h = self.h
        await h.select(LIVING, "Relax")
        await h.settle(3.0)
        await self.light_call("turn_off", "light.living_floor_lamp")
        await h.settle(3.0)
        self.assertEqual(self.status(LIVING)["manual"], ["light.living_floor_lamp"])
        h.clear_calls()

        er.async_get(h.hass).async_update_entity(
            "light.living_floor_lamp", new_entity_id="light.floor"
        )
        await h.idle()
        await h.settle(1.0)

        self.assertEqual(self.state("light.floor"), "off")
        self.assertEqual(self.ours("light.floor"), [])
        self.assertEqual(self.status(LIVING)["manual"], ["light.floor"])
        self.assertEqual(self.state(LIVING), "Relax")
