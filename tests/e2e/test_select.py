"""The target selects and the services acting through them."""

from __future__ import annotations

from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from tests.harness import DOMAIN

from . import BEDROOM, KITCHEN, LIVING, LIVING_LIGHTS, HarnessCase

DOWNSTAIRS = {
    "id": "",
    "name": "Downstairs",
    "icon": None,
    "rooms": ["living_room", "kitchen"],
    "lights": [],
}

DEFAULT_MENU = [
    "Off",
    "Energize",
    "Concentrate",
    "Read",
    "Bright",
    "Relax",
    "Dimmed",
    "Nightlight",
    "Adaptive",
    "Savanna sunset",
    "Tropical twilight",
    "Arctic aurora",
    "Spring blossom",
    "Tokyo",
    "Blue lagoon",
    "Cozy candle",
    "Fireplace",
    "Manual",
]


class SelectTest(HarnessCase):
    async def test_one_select_per_room_on_a_service_device(self) -> None:
        hass = self.h.hass
        selects = sorted(hass.states.async_entity_ids("select"))
        self.assertEqual(
            selects,
            [
                "select.bedroom_scene",
                "select.hallway_scene",
                "select.kitchen_scene",
                "select.living_room_scene",
                "select.office_scene",
            ],
        )
        self.assertEqual(self.status(LIVING)["options"], DEFAULT_MENU)
        # The ceiling was on before Magic Lights: nobody's scene, so manual.
        self.assertEqual(self.state(LIVING), "Manual")
        self.assertEqual(self.state(BEDROOM), "Off")
        entry = er.async_get(hass).async_get(LIVING)
        device = dr.async_get(hass).async_get(entry.device_id)
        self.assertEqual(device.area_id, "living_room")
        self.assertEqual(device.model, "Room")
        self.assertEqual(device.entry_type, dr.DeviceEntryType.SERVICE)

        # Renaming the area keeps the entity id and renames the device.
        ar.async_get(hass).async_update("living_room", name="Lounge")
        await self.h.settle(0.1)
        self.assertIsNotNone(hass.states.get(LIVING))
        self.assertEqual(self.status(LIVING)["friendly_name"], "Lounge Scene")
        self.assertEqual(dr.async_get(hass).async_get(entry.device_id).name, "Lounge")

    async def test_a_room_device_is_placed_only_when_created(self) -> None:
        hass = self.h.hass
        devices = dr.async_get(hass)
        device_id = er.async_get(hass).async_get(LIVING).device_id
        devices.async_update_device(device_id, area_id=None)
        (entry,) = hass.config_entries.async_entries(DOMAIN)
        self.assertTrue(await hass.config_entries.async_reload(entry.entry_id))
        await self.h.idle()
        self.assertIsNone(devices.async_get(device_id).area_id)

    async def test_activate_sends_each_light_what_it_can_do(self) -> None:
        await self.h.select(LIVING, "Relax")
        self.assertEqual(self.state(LIVING), "Relax")
        relax = {"brightness": 143, "color_temp_kelvin": 2237, "transition": 1.0}
        self.assertEqual(self.data_of("light.living_ceiling"), [relax])
        self.assertEqual(self.data_of("light.living_floor_lamp"), [relax])
        self.assertEqual(self.data_of("light.living_tv_strip"), [relax])
        self.assertEqual(
            self.data_of("light.living_sofa_lamp"),
            [{"brightness": 143, "transition": 1.0}],
        )
        attrs = self.status(LIVING)
        self.assertEqual(attrs["status"], "scene")
        self.assertEqual(attrs["scene_id"], "relax")
        self.assertEqual(set(attrs["lights"].values()), {"scene"})
        self.assertEqual(
            self.h.events,
            [
                {
                    "type": "activated",
                    "target_id": "room:living_room",
                    "scene_id": "relax",
                }
            ],
        )

        # Relax runs at 56 %: its brightness is that of its brightest light.
        self.assertEqual(attrs["brightness"], 56)

        # The running scene again, at another brightness: everything is sent again.
        self.h.clear_calls()
        await self.h.call("activate_scene", LIVING, scene="Relax", brightness=50)
        self.assertEqual(self.status(LIVING)["brightness"], 50)
        self.assertEqual(
            [c["entity_id"] for c in self.h.calls()].count("light.living_ceiling"), 1
        )
        self.assertEqual(self.data_of("light.living_ceiling")[0]["brightness"], 128)

        self.h.clear_calls()
        await self.h.select(LIVING, "Off")
        self.assertEqual(self.state(LIVING), "Off")
        self.assertEqual(
            sorted(
                c["entity_id"] for c in self.h.calls() if c["service"] == "turn_off"
            ),
            LIVING_LIGHTS,
        )

    async def test_services(self) -> None:
        h = self.h
        await h.call("next_scene", KITCHEN)
        self.assertEqual(self.state(KITCHEN), "Energize")
        await h.call("previous_scene", KITCHEN)
        self.assertEqual(self.state(KITCHEN), "Off")
        await h.call("previous_scene", KITCHEN, cycle=False)
        self.assertEqual(self.state(KITCHEN), "Off")

        await h.call("toggle_scene", KITCHEN, scene="read")
        self.assertEqual(self.state(KITCHEN), "Read")
        await h.call("adjust_brightness", KITCHEN, step=-30)
        self.assertEqual(self.status(KITCHEN)["brightness"], 70)
        await h.call("adjust_brightness", KITCHEN, brightness=20)
        self.assertEqual(self.status(KITCHEN)["brightness"], 20)
        await h.call("toggle_scene", KITCHEN, scene="read")
        self.assertEqual(self.state(KITCHEN), "Off")

        # A hidden preset can still be started; the menu shows it while it runs.
        hidden = [s["id"] for s in self.status(KITCHEN)["hidden_scenes"]]
        self.assertIn("honolulu", hidden)
        self.assertFalse(
            {s["id"] for s in self.status(KITCHEN)["scenes"]} & set(hidden)
        )
        await h.call("activate_scene", KITCHEN, scene="honolulu", dynamic=True)
        attrs = self.status(KITCHEN)
        self.assertEqual(self.state(KITCHEN), "Honolulu")
        self.assertIn("Honolulu", attrs["options"])
        self.assertTrue(attrs["dynamic"])

        h.clear_calls()
        await h.call("release", KITCHEN)
        self.assertEqual(self.state(KITCHEN), "Manual")
        await h.settle(0.5)
        self.assertEqual(h.calls(), [])
        self.assertEqual(h.published, [])

        with self.assertRaises(ServiceValidationError) as err:
            await h.call("activate_scene", KITCHEN, scene="disco")
        self.assertEqual(err.exception.translation_key, "unknown_scene")
        with self.assertRaises(ServiceValidationError):
            await h.select(KITCHEN, "Disco")

    async def test_set_dynamic_leaves_manual_lights_alone(self) -> None:
        h = self.h
        await h.select(LIVING, "Tokyo")
        self.assertFalse(self.status(LIVING)["dynamic"])
        h.entity("light.living_floor_lamp").physical_change(on=False)
        await h.settle()
        await h.call("set_dynamic", LIVING, dynamic=True)
        attrs = self.status(LIVING)
        self.assertTrue(attrs["dynamic"])
        self.assertEqual(attrs["manual"], ["light.living_floor_lamp"])
        self.assertEqual(self.state("light.living_floor_lamp"), "off")
        await h.call("set_dynamic", LIVING, dynamic=False)
        self.assertFalse(self.status(LIVING)["dynamic"])

        with self.assertRaises(ServiceValidationError) as err:
            await h.call("set_dynamic", BEDROOM, dynamic=True)
        self.assertEqual(err.exception.translation_key, "no_scene")

    async def test_set_dynamic_acts_on_the_scene_running_via_another_target(
        self,
    ) -> None:
        h = self.h
        await h.ws({"type": "magic_lights/zone/save", "zone": DOWNSTAIRS})
        await h.select(LIVING, "Tokyo")
        zone = "select.downstairs_scene"
        self.assertEqual(self.status(zone)["via"], "room:living_room")
        await h.call("set_dynamic", zone, dynamic=True)
        self.assertTrue(self.status(LIVING)["dynamic"])
        self.assertEqual(self.status(zone)["via"], "room:living_room")
        self.assertEqual(self.state("light.kitchen_island"), "off")

    async def test_light_changes_are_attributed_to_the_caller(self) -> None:
        h = self.h
        user = await h.hass.auth.async_create_user("Alice")
        call = Context(user_id=user.id)
        await h.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": BEDROOM, "option": "Read"},
            blocking=True,
            context=call,
        )
        await h.idle()
        context = h.hass.states.get("light.bedroom_ceiling").context
        self.assertIn(":mgl:", context.id)
        self.assertEqual((context.user_id, context.parent_id), (user.id, call.id))

    async def test_reclaim_one_light(self) -> None:
        h = self.h
        await h.select(BEDROOM, "Read")
        for light in ("light.bedroom_ceiling", "light.bedroom_nightstand"):
            await h.hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": light, "brightness": 20},
                blocking=True,
            )
        await h.settle()
        self.assertEqual(
            self.status(BEDROOM)["manual"],
            ["light.bedroom_ceiling", "light.bedroom_nightstand"],
        )
        h.clear_calls()
        await h.call("reclaim", BEDROOM, light="light.bedroom_ceiling")
        self.assertEqual(self.status(BEDROOM)["manual"], ["light.bedroom_nightstand"])
        self.assertEqual(len(h.calls("light.bedroom_ceiling")), 1)
        self.assertEqual(h.calls("light.bedroom_nightstand"), [])
        await h.call("reclaim", BEDROOM)
        self.assertEqual(self.status(BEDROOM)["manual"], [])


class OverlapTest(HarnessCase):
    """How a room and a zone that covers it share their lights."""

    async def test_room_and_zone(self) -> None:
        h = self.h
        await h.ws({"type": "magic_lights/zone/save", "zone": DOWNSTAIRS})
        await h.settle()
        zone = "select.downstairs_scene"
        self.assertEqual(self.state(zone), "Manual")  # the living ceiling is on

        await h.select(zone, "Savanna sunset")
        for select in (LIVING, KITCHEN, zone):
            self.assertEqual(self.state(select), "Savanna sunset")
        self.assertEqual(self.status(LIVING)["via"], "zone:downstairs")
        self.assertIsNone(self.status(zone)["via"])

        await h.select(LIVING, "Relax")
        self.assertEqual(self.state(LIVING), "Relax")
        self.assertEqual(self.state(KITCHEN), "Savanna sunset")
        attrs = self.status(zone)
        self.assertEqual(attrs["scene_id"], "savanna_sunset")
        self.assertEqual(
            attrs["elsewhere"]["light.living_ceiling"],
            {"target_id": "room:living_room", "scene_id": "relax"},
        )

        await h.select(KITCHEN, "Off")
        self.assertEqual(self.state(KITCHEN), "Off")
        self.assertEqual(self.state(LIVING), "Relax")
        attrs = self.status(zone)
        self.assertEqual(
            (attrs["scene_id"], attrs["via"]), ("relax", "room:living_room")
        )
        self.assertTrue(attrs["partial"])

        await h.select(zone, "Relax")
        for select in (LIVING, KITCHEN):
            self.assertEqual(self.state(select), "Relax")
            self.assertEqual(self.status(select)["via"], "zone:downstairs")
