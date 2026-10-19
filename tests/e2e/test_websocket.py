"""The panel's websocket commands on the real handlers."""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import patch

import voluptuous as vol

from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from tests.harness import DOMAIN, WsError

from . import BEDROOM, LIVING, HarnessCase, part, save, scene

ROOM = "room:living_room"
EVENING = part("p_0000e001", "Evening glow", kelvin=2400, brightness=50)


def evening(**changes: Any) -> dict[str, Any]:
    """Return a living room scene of the part EVENING."""
    base = scene("s_0000e001", "Evening", (["room:living_room"], EVENING["id"]))
    return base | changes


PARTY = part(
    "p_0000d001", "Party", "palette", palette=[{"xy": [0.2, 0.1]}, {"xy": [0.6, 0.35]}]
)


def draft(**params: Any) -> dict[str, Any]:
    """Return a scene being tried, with its new part."""
    return {
        "scene": scene("s_0000d001", "Draft", ("all", PARTY["id"])),
        "parts": [PARTY | {"params": PARTY["params"] | params}],
    }


class ConfigTest(HarnessCase):
    async def test_config(self) -> None:
        config = await self.h.ws({"type": "magic_lights/config"})
        self.assertEqual(
            set(config),
            {
                "zones",
                "targets",
                "parts",
                "scenes",
                "lights",
                "effects",
                "buses",
                "palettes",
                "settings",
                "version",
            },
        )
        self.assertEqual(config["version"], "3.0.0")
        self.assertEqual(len(config["parts"]), 48)
        self.assertEqual(config["scenes"], [])
        self.assertEqual(len(config["palettes"]), 35)
        living = next(t for t in config["targets"] if t["id"] == ROOM)
        self.assertEqual(living["select_entity_id"], LIVING)
        self.assertEqual(living["kind"], "room")
        self.assertEqual(len(living["lights"]), 4)
        # New rooms start empty.
        self.assertEqual(living["scenes"], [])
        self.assertNotIn("hidden", living)
        self.assertNotIn("visible_scenes", living["options"])
        relax = next(p for p in config["parts"] if p["id"] == "relax")
        self.assertEqual(
            {k: relax[k] for k in ("builtin", "edited", "category", "dynamic")},
            {"builtin": True, "edited": False, "category": "white", "dynamic": False},
        )
        self.assertEqual((relax["used_by"], relax["dormant"]), ([], []))
        self.assertEqual(relax["name"], "Relax")
        self.assertTrue(relax["icon"].startswith("mdi:"))
        self.assertRegex(relax["swatches"][0], r"^#[0-9a-f]{6}$")
        tokyo = next(p for p in config["parts"] if p["id"] == "tokyo")
        self.assertTrue(tokyo["dynamic"])
        candle = next(p for p in config["parts"] if p["id"] == "cozy_candle")
        self.assertFalse(candle["dynamic"])  # no motion to switch
        effects = {e["key"]: e for e in config["effects"]}
        self.assertEqual(
            list(effects),
            ["off", "white", "static", "palette", "light_effect", "adaptive"],
        )
        self.assertIn(
            "magic_lights_palette", effects["palette"]["schema"][0]["selector"]
        )
        strip = next(
            light
            for light in config["lights"]
            if light["entity_id"] == "light.living_tv_strip"
        )
        self.assertEqual(strip["caps"]["driver"], "z2m_hue")
        self.assertIn("candle", strip["caps"]["effects"])
        self.assertTrue(all(bus["lights"] for bus in config["buses"]))


class LibraryTest(HarnessCase):
    async def test_save_edit_and_delete_a_running_scene(self) -> None:
        h = self.h
        saved = await save(h, ROOM, evening(), parts=(EVENING,))
        (info,) = saved["scenes"]
        self.assertEqual(
            {k: info[k] for k in ("target", "fits", "dormant")},
            {"target": ROOM, "fits": True, "dormant": False},
        )
        self.assertEqual(len(info["swatches"]), 1)
        (part_info,) = saved["parts"]
        self.assertEqual(
            (part_info["used_by"], part_info["builtin"]), (["s_0000e001"], False)
        )
        self.assertIn("Evening", self.status(LIVING)["options"])
        self.assertNotIn("Evening", self.status(BEDROOM)["options"])
        await h.select(LIVING, "Evening")
        self.assertEqual(
            self.data_of("light.living_ceiling")[-1]["color_temp_kelvin"], 2400
        )

        # Editing the part updates the room in place.
        h.clear_calls()
        await save(h, parts=(EVENING | {"params": {"kelvin": 3000}},))
        await h.idle()
        self.assertEqual(
            self.data_of("light.living_ceiling")[-1]["color_temp_kelvin"], 3000
        )
        self.assertEqual(self.state(LIVING), "Evening")

        # Deleting the scene ends the activation; the lights stay as they are.
        h.clear_calls()
        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000e001"})
        await h.idle()
        self.assertEqual(self.state(LIVING), "Manual")
        self.assertNotIn("Evening", self.status(LIVING)["options"])
        self.assertEqual(h.calls(), [])
        # The part stays without a scene.
        config = await h.ws({"type": "magic_lights/config"})
        kept = next(p for p in config["parts"] if p["id"] == EVENING["id"])
        self.assertEqual(kept["used_by"], [])

    async def test_parts_and_scenes_save_together_or_not_at_all(self) -> None:
        h = self.h
        store = h.data().store
        events: list[Any] = []
        await h.ws_send({"type": "magic_lights/subscribe"}, events.append)
        bedroom = scene("s_0000e002", "Evening", ("all", EVENING["id"]))
        with patch.object(
            store, "async_save_config", wraps=store.async_save_config
        ) as saved:
            await h.ws(
                {
                    "type": "magic_lights/library/save",
                    "parts": [EVENING],
                    "scenes": [
                        {"target": ROOM, "scene": evening()},
                        {"target": "room:bedroom", "scene": bedroom},
                    ],
                }
            )
            await h.idle()
        self.assertEqual(saved.call_count, 1)
        self.assertEqual(events.count({"config_changed": True}), 1)
        for select in (LIVING, BEDROOM):
            self.assertIn("Evening", self.status(select)["options"])

        events.clear()
        new_part = part("p_0000e003", "Night")
        late = scene("s_0000e003", "Off", ("all", new_part["id"]))
        with self.assertRaises(WsError) as err:
            await save(h, ROOM, evening(name="Late"), late, parts=(new_part,))
        self.assertEqual(err.exception.code, "reserved_name")
        await h.idle()
        self.assertEqual(events, [])
        config = await h.ws({"type": "magic_lights/config"})
        self.assertNotIn("p_0000e003", {p["id"] for p in config["parts"]})
        self.assertEqual(
            [s["name"] for s in config["scenes"] if s["target"] == ROOM], ["Evening"]
        )

    async def test_an_edited_built_in_applies_everywhere_until_reset(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Relax")
        await h.call("activate_scene", BEDROOM, scene="Relax")
        h.clear_calls()
        edit = part("relax", "Relax", kelvin=2237, brightness=20)
        saved = await save(h, parts=(edit,))
        await h.idle()
        self.assertTrue(saved["parts"][0]["edited"])
        # Every room running it follows; ids and names stay.
        for light in ("light.living_ceiling", "light.bedroom_ceiling"):
            self.assertEqual(self.data_of(light)[-1]["brightness"], 51)
        self.assertEqual(self.status(LIVING)["scene_id"], "relax")
        self.assertEqual(self.state(BEDROOM), "Relax")

        h.clear_calls()
        await h.ws({"type": "magic_lights/part/delete", "part_id": "relax"})
        await h.idle()
        for light in ("light.living_ceiling", "light.bedroom_ceiling"):
            self.assertEqual(self.data_of(light)[-1]["brightness"], 143)
        self.assertEqual(self.state(LIVING), "Relax")
        with self.assertRaises(WsError) as err:
            await h.ws({"type": "magic_lights/part/delete", "part_id": "relax"})
        self.assertEqual(err.exception.code, "not_found")

    async def test_an_edit_equal_to_its_built_in_is_not_kept(self) -> None:
        h = self.h
        config = await h.ws({"type": "magic_lights/config"})
        relax = next(p for p in config["parts"] if p["id"] == "relax")
        same = {k: relax[k] for k in ("id", "name", "effect", "params")}
        reply = await save(h, parts=(same,))
        self.assertFalse(reply["parts"][0]["edited"])
        dimmer = same | {"params": same["params"] | {"brightness": 50}}
        self.assertTrue((await save(h, parts=(dimmer,)))["parts"][0]["edited"])
        # Setting it back removes the edit.
        self.assertFalse((await save(h, parts=(same,)))["parts"][0]["edited"])
        stored = h.data().coordinator.config
        self.assertNotIn("relax", stored.parts)

    async def test_a_part_saves_in_the_shape_it_is_read(self) -> None:
        h = self.h
        await save(h, parts=(part("p_0000e004", "Mine"),))
        config = await h.ws({"type": "magic_lights/config"})
        shape = ("id", "name", "effect", "params", "icon", "category")
        mine, relax = (
            next({k: p[k] for k in shape} for p in config["parts"] if p["id"] == i)
            for i in ("p_0000e004", "relax")
        )
        dimmer = relax | {"params": relax["params"] | {"brightness": 50}}
        reply = await save(h, parts=(mine | {"name": "Ours"}, dimmer))
        self.assertEqual([p["name"] for p in reply["parts"]], ["Ours", "Relax"])
        self.assertEqual(reply["parts"][1]["icon"], relax["icon"])
        stored = h.data().coordinator.config.parts
        for part_id in ("p_0000e004", "relax"):
            self.assertIsNone(stored[part_id].icon)
            self.assertIsNone(stored[part_id].category)
        with self.assertRaises(WsError) as err:
            await save(h, parts=(mine | {"builtin": False},))
        self.assertEqual(err.exception.code, "invalid")

    async def test_swatches_show_what_reaches_the_target(self) -> None:
        h = self.h
        cold = part("p_0000e004", "Cold", kelvin=5000, brightness=50)
        warm = part("p_0000e005", "Warm", kelvin=2200, brightness=50)
        hell = scene(
            "s_0000e004",
            "Hell",
            ("all", cold["id"]),
            (["light.kitchen_island"], warm["id"]),
        )
        saved = await save(h, ROOM, hell, parts=(cold, warm))
        await h.idle()
        cold_swatch = saved["parts"][0]["swatches"]
        self.assertEqual(saved["scenes"][0]["swatches"], cold_swatch)
        (summary,) = self.status(LIVING)["scenes"]
        self.assertEqual(
            (summary["id"], summary["swatches"]), ("s_0000e004", cold_swatch)
        )

    async def test_errors_are_readable(self) -> None:
        h = self.h
        cases = [
            (
                {"parts": [part("p_0000e009", "relax")]},
                "duplicate_name",
                "a part named 'relax' already exists (a built-in part)",
            ),
            (
                {"parts": [part("relax", "My relax")]},
                "builtin_name",
                "an edited built-in part keeps its name 'Relax'",
            ),
            (
                {"parts": [part("p_0000e009", "Strobe", "strobe")]},
                "unknown_effect",
                "unknown effect 'strobe' (effect)",
            ),
            ({"parts": [part("disco", "Disco")]}, "unknown_part", None),
            (
                {"scenes": [{"target": ROOM, "scene": evening(name="Off")}]},
                "reserved_name",
                "'Off' is a reserved name",
            ),
            (
                {"scenes": [{"target": ROOM, "scene": evening()}]},
                "unknown_part",
                "unknown part 'p_0000e001' (layer 1, part)",
            ),
            (
                {
                    "scenes": [
                        {
                            "target": ROOM,
                            "scene": evening(scope=[ROOM]),
                        }
                    ]
                },
                "invalid",
                None,
            ),
            (
                {"scenes": [{"target": ROOM, "scene": evening(id="relax")}]},
                "invalid",
                None,
            ),
            (
                {"scenes": [{"target": "room:attic", "scene": evening()}]},
                "unknown_target",
                None,
            ),
        ]
        for payload, code, message in cases:
            with self.subTest(code=code), self.assertRaises(WsError) as err:
                await h.ws({"type": "magic_lights/library/save", **payload})
            self.assertEqual(err.exception.code, code)
            if message is not None:
                self.assertEqual(err.exception.message, message)
        bad = EVENING | {"params": {"brightness": 400}}
        with self.assertRaises(WsError) as err:
            await save(h, parts=(bad,))
        self.assertEqual(err.exception.code, "invalid_params")
        self.assertTrue(err.exception.message.endswith("(brightness)"))
        # A scene stays in its room.
        await save(h, ROOM, evening(), parts=(EVENING,))
        with self.assertRaises(WsError) as err:
            await save(h, "room:bedroom", evening())
        self.assertEqual(err.exception.code, "invalid")
        with self.assertRaises(WsError) as err:
            await h.ws({"type": "magic_lights/scene/delete", "scene_id": "relax"})
        self.assertEqual(err.exception.code, "not_found")
        with self.assertRaises(WsError) as err:
            await h.ws(
                {"type": "magic_lights/try", "target_id": "room:attic", **draft()}
            )
        self.assertEqual(err.exception.code, "home_assistant_error")
        self.assertEqual(err.exception.message, "There is no room or zone room:attic")

    async def test_names_are_unique_per_target(self) -> None:
        h = self.h
        await save(h, ROOM, evening(), parts=(EVENING,))
        bedroom = evening(id="s_0000e002", layers=[{"lights": "all", "part": "read"}])
        await save(h, "room:bedroom", bedroom)
        self.assertIn("Evening", self.status(BEDROOM)["options"])
        twin = evening(id="s_0000e003", name="EVENING")
        with self.assertRaises(WsError) as err:
            await save(h, ROOM, twin)
        self.assertEqual(err.exception.code, "duplicate_name")
        self.assertIn(ROOM, err.exception.message)
        # A scene may share a part's name.
        await save(h, ROOM, twin | {"name": "Relax"})
        self.assertIn("Relax", self.status(LIVING)["options"])

    async def test_scenes_of_a_deleted_area_stay_dormant(self) -> None:
        h = self.h
        cozy = part("p_0000e006", "Cozy")
        bedroom = scene("s_0000e002", "Cozy", ("all", cozy["id"]))
        await save(h, "room:bedroom", bedroom, parts=(cozy,))
        ar.async_get(h.hass).async_delete("bedroom")
        await h.idle()
        self.assertIsNone(h.hass.states.get(BEDROOM))
        config = await h.ws({"type": "magic_lights/config"})
        (dormant,) = config["scenes"]
        self.assertEqual(
            {k: dormant[k] for k in ("target", "fits", "dormant")},
            {"target": "room:bedroom", "fits": False, "dormant": True},
        )
        (info,) = [p for p in config["parts"] if p["id"] == cozy["id"]]
        self.assertEqual((info["used_by"], info["dormant"]), ([], ["s_0000e002"]))
        # It blocks nothing: the same name elsewhere, an edit of its part.
        await save(h, ROOM, scene("s_0000e003", "Cozy", ("all", cozy["id"])))
        await save(h, parts=(cozy | {"params": {"kelvin": 2500}},))
        # Saved itself, it has nowhere to be.
        with self.assertRaises(WsError) as err:
            await save(h, "room:bedroom", bedroom)
        self.assertEqual(err.exception.code, "unknown_target")
        # Deleting its part takes it along once no live scene uses the part.
        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000e003"})
        await h.ws({"type": "magic_lights/part/delete", "part_id": cozy["id"]})
        config = await h.ws({"type": "magic_lights/config"})
        self.assertEqual(config["scenes"], [])

    async def test_scenes_order(self) -> None:
        h = self.h
        read = scene("s_0000e002", "Reading", ("all", "read"))
        await save(h, ROOM, evening(), read, parts=(EVENING,))
        reply = await h.ws(
            {
                "type": "magic_lights/scenes/order",
                "target_id": ROOM,
                "scene_ids": ["s_0000e002", "s_0000e001"],
            }
        )
        await h.idle()
        self.assertEqual(reply["target"]["scenes"], ["s_0000e002", "s_0000e001"])
        self.assertEqual(
            self.status(LIVING)["options"], ["Off", "Reading", "Evening", "Manual"]
        )
        for target_id, ids, code in (
            (ROOM, ["s_0000e002"], "invalid"),
            (ROOM, ["s_0000e002", "s_0000e002"], "invalid"),
            (ROOM, ["s_0000e002", "s_0000e001", "s_0000dead"], "invalid"),
            ("room:attic", [], "not_found"),
        ):
            with self.subTest(ids=ids), self.assertRaises(WsError) as err:
                await h.ws(
                    {
                        "type": "magic_lights/scenes/order",
                        "target_id": target_id,
                        "scene_ids": ids,
                    }
                )
            self.assertEqual(err.exception.code, code)

    async def test_a_part_in_use_stays(self) -> None:
        h = self.h
        await save(h, ROOM, evening(), parts=(EVENING,))
        await h.ws(
            {
                "type": "magic_lights/settings/save",
                "settings": {"default_scene": EVENING["id"]},
            }
        )
        delete = {"type": "magic_lights/part/delete", "part_id": EVENING["id"]}
        with self.assertRaises(WsError) as err:
            await h.ws(delete)
        self.assertEqual(
            (err.exception.code, err.exception.message),
            ("part_in_use", "Used by Living Room · Evening, the switch-on default"),
        )
        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000e001"})
        with self.assertRaises(WsError) as err:
            await h.ws(delete)
        self.assertEqual(err.exception.message, "Used by the switch-on default")
        await h.ws(
            {"type": "magic_lights/settings/save", "settings": {"default_scene": None}}
        )
        await h.ws(delete)
        with self.assertRaises(WsError) as err:
            await h.ws(delete)
        self.assertEqual(err.exception.code, "not_found")


class TargetTest(HarnessCase):
    async def test_zones(self) -> None:
        h = self.h
        events: list[Any] = []
        await h.ws_send({"type": "magic_lights/subscribe"}, events.append)
        reply = await h.ws(
            {
                "type": "magic_lights/zone/save",
                "zone": {
                    "id": "",
                    "name": "Upstairs",
                    "icon": None,
                    "rooms": ["bedroom", "office"],
                    "lights": [],
                },
            }
        )
        await h.idle()
        self.assertEqual(reply["zone"]["id"], "upstairs")
        self.assertIn({"targets_changed": True}, events)
        self.assertIn({"config_changed": True}, events)
        zone = "select.upstairs_scene"
        self.assertEqual(self.state(zone), "Off")
        entities = er.async_get(h.hass)
        devices = dr.async_get(h.hass)
        device = devices.async_get(entities.async_get(zone).device_id)
        self.assertEqual((device.model, device.area_id), ("Zone", None))

        await h.ws(
            {
                "type": "magic_lights/zone/save",
                "zone": {**reply["zone"], "name": "First floor", "rooms": ["bedroom"]},
            }
        )
        await h.idle()
        self.assertEqual(devices.async_get(device.id).name, "First floor")
        self.assertEqual(
            self.status(zone)["lights"],
            {"light.bedroom_ceiling": "off", "light.bedroom_nightstand": "off"},
        )
        await save(
            h, "zone:upstairs", scene("s_0000e007", "Up", (["room:bedroom"], "read"))
        )
        self.assertIn("Up", self.status(zone)["options"])

        await h.ws({"type": "magic_lights/zone/delete", "zone_id": "upstairs"})
        await h.idle()
        self.assertIsNone(h.hass.states.get(zone))
        self.assertIsNone(entities.async_get(zone))
        self.assertIsNone(devices.async_get(device.id))
        # Its scenes go with it.
        config = await h.ws({"type": "magic_lights/config"})
        self.assertEqual(config["scenes"], [])

    async def test_target_options_and_settings(self) -> None:
        h = self.h
        await save(h, ROOM, evening(), parts=(EVENING,))
        reply = await h.ws(
            {
                "type": "magic_lights/target/options",
                "target_id": ROOM,
                "options": {
                    "excluded_lights": ["light.living_sofa_lamp"],
                    "default_scene": "s_0000e001",
                },
            }
        )
        await h.idle()
        self.assertNotIn("light.living_sofa_lamp", reply["target"]["lights"])
        self.assertEqual(reply["target"]["options"]["default_scene"], "s_0000e001")
        self.assertNotIn("light.living_sofa_lamp", self.status(LIVING)["lights"])
        cases = [
            (ROOM, {"visible_scenes": ["relax"]}, "invalid"),
            (ROOM, {"default_scene": "relax"}, "invalid"),
            ("room:bedroom", {"default_scene": "s_0000e001"}, "unknown_scene"),
        ]
        for target_id, options, code in cases:
            with self.subTest(options=options), self.assertRaises(WsError) as err:
                await h.ws(
                    {
                        "type": "magic_lights/target/options",
                        "target_id": target_id,
                        "options": options,
                    }
                )
            self.assertEqual(err.exception.code, code)

        # Deleting the scene clears the default that names it.
        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000e001"})
        config = await h.ws({"type": "magic_lights/config"})
        living = next(t for t in config["targets"] if t["id"] == ROOM)
        self.assertIsNone(living["options"]["default_scene"])

        await h.ws(
            {
                "type": "magic_lights/settings/save",
                "settings": {
                    "light_buses": {"light.office_desk": "zigbee:test"},
                    "transition": 2,
                    "default_scene": "relax",
                },
            }
        )
        await h.ws(
            {"type": "magic_lights/settings/save", "settings": {"light_buses": {}}}
        )
        config = await h.ws({"type": "magic_lights/config"})
        self.assertEqual(config["settings"]["light_buses"], {})
        self.assertEqual(config["settings"]["transition"], 2)
        self.assertEqual(config["settings"]["default_scene"], "relax")
        for settings, code in (
            ({"transition": -1}, "invalid"),
            ({"default_scene": "p_0000dead"}, "unknown_part"),
            ({"default_scene": "s_0000e001"}, "invalid"),
        ):
            with self.subTest(settings=settings), self.assertRaises(WsError) as err:
                await h.ws({"type": "magic_lights/settings/save", "settings": settings})
            self.assertEqual(err.exception.code, code)

    async def test_preview(self) -> None:
        h = self.h
        reply = await h.ws(
            {
                "type": "magic_lights/preview",
                "target_id": ROOM,
                "scene": evening(),
                "parts": [EVENING],
            }
        )
        (frame,) = reply["frames"]
        self.assertEqual(frame["lights"]["light.living_ceiling"]["brightness"], 0.502)
        reply = await h.ws(
            {
                "type": "magic_lights/preview",
                "target_id": ROOM,
                **draft(motion="dynamic", speed=1),
                "duration": 30,
                "samples": 31,
                "start": "2026-10-11T18:00:00Z",
            }
        )
        frames = reply["frames"]
        self.assertEqual(len(frames), 31)
        colours = {f["lights"]["light.living_ceiling"]["rgb"] for f in frames}
        self.assertGreater(len(colours), 1)
        # A draft may only use parts that exist or come along.
        with self.assertRaises(WsError) as err:
            await h.ws(
                {"type": "magic_lights/preview", "target_id": ROOM, "scene": evening()}
            )
        self.assertEqual(
            (err.exception.code, err.exception.message),
            ("unknown_part", "unknown part 'p_0000e001' (layer 1, part)"),
        )
        self.assertEqual(h.calls(), [])
        # Nothing of a draft is stored.
        config = await h.ws({"type": "magic_lights/config"})
        self.assertNotIn(PARTY["id"], {p["id"] for p in config["parts"]})


class StoredPartsTest(HarnessCase):
    # Stored without a colour: the effect's default applies.
    storage = {
        "version": 4,
        "minor_version": 1,
        "key": DOMAIN,
        "data": {
            "parts": {
                "p_0000a001": part("p_0000a001", "Warm", "static", brightness=47)
            },
            "scenes": {
                ROOM: [scene("s_0000a001", "Warm", ("all", "p_0000a001"))],
            },
        },
    }

    async def test_swatches_use_the_effect_defaults(self) -> None:
        with self.assertNoLogs("custom_components.magic_lights", logging.ERROR):
            config = await self.h.ws({"type": "magic_lights/config"})
        warm = next(p for p in config["parts"] if p["id"] == "p_0000a001")
        self.assertEqual(len(warm["swatches"]), 1)
        (stored,) = config["scenes"]
        self.assertEqual(stored["swatches"], warm["swatches"])
        self.assertEqual(self.status(LIVING)["options"], ["Off", "Warm", "Manual"])


class AdminTest(HarnessCase):
    # Every required field a magic_lights/* command takes.
    SAMPLES: dict[str, Any] = {
        "scene": {},
        "scene_id": "s_0000e001",
        "scene_ids": [],
        "part_id": "p_0000e001",
        "zone": {},
        "zone_id": "upstairs",
        "target_id": ROOM,
        "options": {},
        "settings": {},
    }

    async def test_only_subscribe_is_open_to_non_admins(self) -> None:
        h = self.h
        hass = h.hass
        # The first user is the owner, an admin whatever its groups.
        await h.ws({"type": "magic_lights/config"})
        user = await hass.auth.async_create_user("Kid", group_ids=["system-users"])
        self.assertFalse(user.is_admin)
        replies: dict[int, dict[str, Any]] = {}

        def route(message: Any) -> None:
            if callable(message):
                message = message()
            if isinstance(message, bytes | str):
                message = json.loads(message)
            replies[message["id"]] = message

        connection = ActiveConnection(
            logging.getLogger(__name__), hass, route, user, None, None
        )
        commands = sorted(
            (command, schema)
            for command, (_, schema) in hass.data["websocket_api"].items()
            if command.startswith(f"{DOMAIN}/")
        )
        for msg_id, (command, schema) in enumerate(commands, 1):
            msg = {"id": msg_id, "type": command}
            # A command without fields has no schema (False).
            for key in schema.schema if schema else ():
                if isinstance(key, vol.Required) and key.schema not in ("id", "type"):
                    msg[key.schema] = self.SAMPLES[key.schema]
            connection.async_handle(msg)
        await h.settle()
        connection.async_handle_close()
        codes = {
            command: replies[msg_id].get("error", {}).get("code")
            for msg_id, (command, _) in enumerate(commands, 1)
        }
        self.assertEqual(codes.pop(f"{DOMAIN}/subscribe"), None)
        self.assertEqual(len(codes), 14)
        self.assertNotIn(f"{DOMAIN}/scene/save", codes)
        self.assertNotIn(f"{DOMAIN}/menus/save", codes)
        self.assertEqual(set(codes.values()), {"unauthorized"})


class TryTest(HarnessCase):
    async def test_try_and_stop_bring_back_what_ran(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Relax")
        h.clear_calls()
        await h.ws({"type": "magic_lights/try", "target_id": ROOM, **draft()})
        await h.idle()
        attrs = self.status(LIVING)
        self.assertEqual((attrs["scene_id"], attrs["scene_name"]), (None, "Draft"))
        self.assertEqual(self.state(LIVING), "Manual")
        self.assertIn("xy_color", self.data_of("light.living_ceiling")[-1])

        h.clear_calls()
        await h.ws({"type": "magic_lights/try/stop", "target_id": ROOM})
        await h.idle()
        self.assertEqual(self.state(LIVING), "Relax")
        self.assertEqual(
            self.data_of("light.living_ceiling")[-1]["color_temp_kelvin"], 2237
        )

    async def test_saving_while_trying_keeps_the_scene(self) -> None:
        h = self.h
        tried = draft()
        await h.ws({"type": "magic_lights/try", "target_id": ROOM, **tried})
        await save(h, ROOM, tried["scene"] | {"name": "Party"}, parts=tried["parts"])
        await h.idle()
        self.assertEqual(self.state(LIVING), "Party")
        h.ws_close()
        await h.idle()
        self.assertEqual(self.state(LIVING), "Party")

    async def test_saving_a_tried_part_keeps_it(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Read")
        edit = part("relax", "Relax", kelvin=2237, brightness=20)
        await h.ws(
            {
                "type": "magic_lights/try",
                "target_id": ROOM,
                "scene": scene("relax", "Relax", ("all", "relax")),
                "parts": [edit],
            }
        )
        await h.idle()
        self.assertEqual(self.state(LIVING), "Manual")
        await save(h, parts=(edit,))
        await h.idle()
        self.assertEqual(self.state(LIVING), "Relax")
        h.ws_close()
        await h.idle()
        self.assertEqual(self.state(LIVING), "Relax")
        self.assertEqual(self.data_of("light.living_ceiling")[-1]["brightness"], 51)

    async def test_closing_the_editor_stops_trying(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Read")
        await h.ws({"type": "magic_lights/try", "target_id": ROOM, **draft()})
        await h.idle()
        self.assertEqual(self.status(LIVING)["scene_name"], "Draft")
        h.ws_close()
        await h.idle()
        self.assertEqual(self.state(LIVING), "Read")

    async def test_repeated_tries_keep_one_subscription(self) -> None:
        h = self.h
        for _ in range(3):
            await h.ws({"type": "magic_lights/try", "target_id": ROOM, **draft()})
        connection = h._ws  # noqa: SLF001
        self.assertEqual(len(connection.subscriptions), 1)
        await h.ws({"type": "magic_lights/try/stop", "target_id": ROOM})
        self.assertEqual(connection.subscriptions, {})

    async def test_lights_are_attributed_to_the_user(self) -> None:
        h = self.h
        await h.ws({"type": "magic_lights/try", "target_id": "room:bedroom", **draft()})
        await h.idle()
        (admin,) = [
            user
            for user in await h.hass.auth.async_get_users()
            if user.name == "Test admin"
        ]
        context = h.hass.states.get("light.bedroom_ceiling").context
        self.assertIn(":mgl:", context.id)
        self.assertEqual(context.user_id, admin.id)
