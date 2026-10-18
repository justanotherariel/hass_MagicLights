"""A restart keeps every activation, sends nothing and adopts late lights."""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any
from unittest import IsolatedAsyncioTestCase

from homeassistant.components.frontend import async_panel_exists
from homeassistant.const import STATE_ON

from tests.harness import DOMAIN, Harness
from tests.scenario import DEMO_AREAS, DEMO_LIGHTS

from . import KITCHEN, LIVING, LIVING_LIGHTS, HarnessCase


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
            await first.call("activate_scene", LIVING, scene="Relax")
            await first.call("activate_scene", KITCHEN, scene="Savanna sunset")
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


def hell(scene_id: str, area_id: str) -> dict[str, Any]:
    return {
        "id": scene_id,
        "name": "Hell",
        "icon": None,
        "scope": [f"room:{area_id}"],
        "preset": None,
        "layers": [
            {
                "lights": "all",
                "effect": "white",
                "params": {"kelvin": 4000, "brightness": 80},
            }
        ],
        "others": "off",
        "brightness": 100,
        "transition": None,
    }


class UpgradeTest(IsolatedAsyncioTestCase):
    async def test_v2_scenes_merge_and_keep_running(self) -> None:
        def stored(key: str, version: int, data: dict[str, Any]) -> dict[str, Any]:
            return {"version": version, "minor_version": 1, "key": key, "data": data}

        # The living room light did nothing under the kitchen's scope.
        film = hell("s_00000003", "kitchen") | {"name": "Film"}
        film["layers"] = [
            dict(film["layers"][0], lights=["light.kitchen_island", LIVING_LIGHTS[0]])
        ]
        v2 = {
            "scenes": {
                s["id"]: s
                for s in (
                    hell("s_00000002", "living_room"),
                    hell("s_00000001", "kitchen"),
                    film,
                )
            },
            "targets": {"room:kitchen": {"visible_scenes": ["s_00000002"]}},
        }
        running = {
            "id": "01JA0000000000000000000000",
            "target": "room:living_room",
            "scene_id": "s_00000002",
            "started": "2026-10-17T08:00:00+00:00",
            "lights": LIVING_LIGHTS,
            "manual": [],
            "adjust": {},
            "only_on": False,
        }
        runtime = {
            "activations": [running],
            "salts": {"s_00000002|room:living_room": 2},
        }
        h = Harness(
            lights=[
                dict(conf, on=conf["name"].startswith("Living")) for conf in DEMO_LIGHTS
            ],
            areas=DEMO_AREAS,
            storage=stored(DOMAIN, 2, v2),
            extra_storage={
                f"{DOMAIN}.runtime": stored(f"{DOMAIN}.runtime", 1, runtime)
            },
        )
        self.addAsyncCleanup(h.stop)
        await h.start()
        await h.setup_integration()
        await h.idle()

        self.assertEqual(h.hass.states.get(LIVING).state, "Hell")
        self.assertEqual(h.attributes(LIVING)["scene_id"], "s_00000001")
        self.assertEqual(
            h.hass.states.get(KITCHEN).attributes["options"], ["Off", "Hell", "Manual"]
        )
        self.assertEqual(
            h.hass.states.get(LIVING).attributes["options"], ["Off", "Hell", "Manual"]
        )
        config = await h.ws({"type": "magic_lights/config"})
        scenes = {s["id"]: s for s in config["scenes"] if not s["builtin"]}
        self.assertEqual(list(scenes), ["s_00000001", "s_00000003"])
        self.assertEqual(
            scenes["s_00000001"]["layers"][0]["lights"],
            ["room:living_room", "room:kitchen"],
        )
        self.assertEqual(
            scenes["s_00000003"]["layers"][0]["lights"], ["light.kitchen_island"]
        )

        storage = h.config_dir / ".storage"

        def read(key: str) -> dict[str, Any]:
            return json.loads((storage / key).read_text())

        saved = await asyncio.to_thread(read, f"{DOMAIN}.runtime")
        self.assertEqual(saved["data"]["activations"][0]["scene_id"], "s_00000001")
        self.assertIn("s_00000001|room:living_room", saved["data"]["salts"])
        self.assertEqual((await asyncio.to_thread(read, DOMAIN))["version"], 3)
        old = await asyncio.to_thread(read, f"{DOMAIN}.v2.bak")
        self.assertEqual(old["data"], v2)
        old_runtime = await asyncio.to_thread(read, f"{DOMAIN}.runtime.v1.bak")
        self.assertEqual(
            old_runtime["data"]["activations"][0]["scene_id"], "s_00000002"
        )


class ReloadTest(HarnessCase):
    async def test_reload_keeps_the_scenes_and_sends_nothing(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Relax")
        await self.light_call("turn_on", "light.living_ceiling", brightness=30)
        h.clear_calls()
        (entry,) = h.hass.config_entries.async_entries(DOMAIN)
        self.assertTrue(await h.hass.config_entries.async_reload(entry.entry_id))
        await h.idle()
        self.assertEqual(h.calls(), [])
        self.assertEqual(self.state(LIVING), "Relax")
        self.assertEqual(self.status(LIVING)["manual"], ["light.living_ceiling"])
        self.assertTrue(async_panel_exists(h.hass, DOMAIN))
        await h.call("activate_scene", KITCHEN, scene="Read")
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
        await h.call("activate_scene", LIVING, scene="Relax")
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
