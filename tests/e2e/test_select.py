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

from . import (
    BEDROOM,
    KITCHEN,
    LIVING,
    LIVING_LIGHTS,
    HarnessCase,
    part,
    save,
    scene,
)

DOWNSTAIRS = {
    "id": "",
    "name": "Downstairs",
    "icon": None,
    "rooms": ["living_room", "kitchen"],
    "lights": [],
}

# New rooms start empty; built-ins still run by name.
DEFAULT_MENU = ["Off", "Manual"]


class SelectTest(HarnessCase):
    async def show(self, target_id: str, *part_ids: str) -> list[str]:
        """Give a target a scene of each built-in, as New scene does; return ids."""
        config = await self.h.ws({"type": "magic_lights/config"})
        names = {p["id"]: p["name"] for p in config["parts"]}
        scenes = [
            scene(f"s_0000000{n}", names[part_id], ("all", part_id))
            for n, part_id in enumerate(part_ids, 1)
        ]
        await save(self.h, target_id, *scenes)
        await self.h.idle()
        return [s["id"] for s in scenes]

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
        self.assertEqual(self.status(LIVING)["scenes"], [])
        self.assertNotIn("hidden_scenes", self.status(LIVING))
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
        (relax_id,) = await self.show("room:living_room", "relax")
        self.assertEqual(self.status(LIVING)["options"], ["Off", "Relax", "Manual"])
        self.h.clear_calls()
        self.h.events.clear()
        await self.h.select(LIVING, "Relax")
        self.assertEqual(self.state(LIVING), "Relax")
        relax = {"brightness": 143, "color_temp_kelvin": 2237, "transition": 0.4}
        self.assertEqual(self.data_of("light.living_ceiling"), [relax])
        self.assertEqual(self.data_of("light.living_floor_lamp"), [relax])
        self.assertEqual(self.data_of("light.living_tv_strip"), [relax])
        self.assertEqual(
            self.data_of("light.living_sofa_lamp"),
            [{"brightness": 143, "transition": 0.4}],
        )
        attrs = self.status(LIVING)
        self.assertEqual(attrs["status"], "scene")
        self.assertEqual(attrs["scene_id"], relax_id)
        self.assertEqual(set(attrs["lights"].values()), {"scene"})
        self.assertEqual(
            self.h.events,
            [
                {
                    "type": "activated",
                    "target_id": "room:living_room",
                    "scene_id": relax_id,
                }
            ],
        )

        # Relax runs at 56 %: its brightness is that of its brightest light.
        self.assertEqual(attrs["brightness"], 56)

        # The running scene again, at another brightness: everything is sent again.
        # Called by the built-in's id, it is the room's scene of it.
        self.h.clear_calls()
        await self.h.call("activate_scene", LIVING, scene="relax", brightness=50)
        self.assertEqual(self.status(LIVING)["scene_id"], relax_id)
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
        await self.show("room:kitchen", "energize", "read")
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

        # Any built-in can be started; the menu shows it while it runs.
        self.assertEqual(
            [s["name"] for s in self.status(KITCHEN)["scenes"]], ["Energize", "Read"]
        )
        await h.call("activate_scene", KITCHEN, scene="honolulu", dynamic=True)
        attrs = self.status(KITCHEN)
        self.assertEqual(self.state(KITCHEN), "Honolulu")
        self.assertEqual(attrs["scene_id"], "honolulu")
        self.assertEqual(
            attrs["options"], ["Off", "Energize", "Read", "Honolulu", "Manual"]
        )
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

    async def test_a_scene_without_lights_here_is_refused(self) -> None:
        h = self.h
        film = scene("s_0000f001", "Film", (["light.kitchen_island"], "read"))
        await save(h, "room:kitchen", film)
        # Its light moved to the kitchen: the living room's scene is left without.
        await save(h, "room:living_room", film | {"id": "s_0000f002"})
        await h.idle()
        self.assertIn("Film", self.status(KITCHEN)["options"])
        self.assertNotIn("Film", self.status(LIVING)["options"])
        self.assertEqual(self.status(LIVING)["scenes"], [])
        h.clear_calls()
        for service in ("activate_scene", "toggle_scene"):
            with (
                self.subTest(service),
                self.assertRaises(ServiceValidationError) as err,
            ):
                await h.call(service, LIVING, scene="film")
            self.assertEqual(err.exception.translation_key, "scene_not_adapted")
            self.assertEqual(
                err.exception.translation_placeholders,
                {"scene": "Film", "target": "Living Room"},
            )
        await h.settle()
        self.assertEqual(h.calls(), [])
        self.assertEqual(self.state(LIVING), "Manual")

    async def test_a_user_part_running_as_default_stays_selectable(self) -> None:
        h = self.h
        await save(h, parts=(part("p_0000a001", "Mine", kelvin=2700),))
        await h.ws(
            {
                "type": "magic_lights/settings/save",
                "settings": {"default_scene": "p_0000a001"},
            }
        )
        await self.light_call("turn_on", "light.bedroom_ceiling")
        self.assertEqual(self.state(BEDROOM), "Mine")
        self.assertEqual(self.status(BEDROOM)["options"], ["Off", "Mine", "Manual"])
        started = self.status(BEDROOM)["since"]
        await h.select(BEDROOM, "Mine")
        attrs = self.status(BEDROOM)
        self.assertEqual((attrs["scene_id"], attrs["via"]), ("p_0000a001", None))
        self.assertNotEqual(attrs["since"], started)
        # Calls still reach only this room's scenes and the built-ins.
        with self.assertRaises(ServiceValidationError) as err:
            await h.call("activate_scene", BEDROOM, scene="Mine")
        self.assertEqual(err.exception.translation_key, "unknown_scene")

    async def test_set_dynamic_leaves_manual_lights_alone(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Tokyo")
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
        await h.call("activate_scene", LIVING, scene="Tokyo")
        zone = "select.downstairs_scene"
        self.assertEqual(self.status(zone)["via"], "room:living_room")
        await h.call("set_dynamic", zone, dynamic=True)
        self.assertTrue(self.status(LIVING)["dynamic"])
        self.assertEqual(self.status(zone)["via"], "room:living_room")
        self.assertEqual(self.state("light.kitchen_island"), "off")

    async def test_light_changes_are_attributed_to_the_caller(self) -> None:
        h = self.h
        # Alice must be the first user, so the owner: show() signs in an admin.
        user = await h.hass.auth.async_create_user("Alice")
        await self.show("room:bedroom", "read")
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
        await h.call("activate_scene", BEDROOM, scene="Read")
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

        await h.call("activate_scene", zone, scene="Savanna sunset")
        for select in (LIVING, KITCHEN, zone):
            self.assertEqual(self.state(select), "Savanna sunset")
        self.assertEqual(self.status(LIVING)["via"], "zone:downstairs")
        self.assertIsNone(self.status(zone)["via"])

        await h.call("activate_scene", LIVING, scene="Relax")
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

        await h.call("activate_scene", zone, scene="Relax")
        for select in (LIVING, KITCHEN):
            self.assertEqual(self.state(select), "Relax")
            self.assertEqual(self.status(select)["via"], "zone:downstairs")

    async def test_a_zone_scene_seen_from_a_room_keeps_running(self) -> None:
        h = self.h
        await h.ws({"type": "magic_lights/zone/save", "zone": DOWNSTAIRS})
        await save(h, "zone:downstairs", scene("s_0000d001", "Up", ("all", "read")))
        await h.settle()
        await h.call("activate_scene", "select.downstairs_scene", scene="Up")
        self.assertIn("Up", self.status(KITCHEN)["options"])
        h.clear_calls()
        await h.select(KITCHEN, "Up")
        await h.settle()
        attrs = self.status(KITCHEN)
        self.assertEqual(self.state(KITCHEN), "Up")
        self.assertEqual(
            (attrs["scene_id"], attrs["via"]), ("s_0000d001", "zone:downstairs")
        )
        self.assertEqual(h.calls(), [])
