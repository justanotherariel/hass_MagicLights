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

from . import BEDROOM, LIVING, HarnessCase


def evening(**changes: Any) -> dict[str, Any]:
    scene = {
        "id": "s_0000e001",
        "name": "Evening",
        "icon": None,
        "scope": ["room:living_room"],
        "preset": None,
        "layers": [
            {
                "lights": "all",
                "effect": "white",
                "params": {"kelvin": 2400, "brightness": 50},
            }
        ],
        "others": "off",
        "brightness": 100,
        "transition": None,
    }
    return scene | changes


def palette(**params: Any) -> dict[str, Any]:
    return evening(
        id="s_0000d001",
        name="Draft",
        layers=[
            {
                "lights": "all",
                "effect": "palette",
                "params": {
                    "palette": [{"xy": [0.2, 0.1]}, {"xy": [0.6, 0.35]}],
                    **params,
                },
            }
        ],
    )


class ConfigTest(HarnessCase):
    async def test_config(self) -> None:
        config = await self.h.ws({"type": "magic_lights/config"})
        self.assertEqual(
            set(config),
            {
                "zones",
                "targets",
                "scenes",
                "lights",
                "effects",
                "buses",
                "palettes",
                "settings",
                "version",
            },
        )
        self.assertEqual(config["version"], "2.0.0")
        self.assertEqual(len(config["scenes"]), 48)
        self.assertEqual(len(config["palettes"]), 35)
        living = next(t for t in config["targets"] if t["id"] == "room:living_room")
        self.assertEqual(living["select_entity_id"], LIVING)
        self.assertEqual(living["kind"], "room")
        self.assertEqual(len(living["lights"]), 4)
        # Presets wait in "hidden" until picked.
        self.assertEqual((living["scenes"], len(living["hidden"])), ([], 48))
        relax = next(s for s in config["scenes"] if s["id"] == "relax")
        self.assertEqual(
            {
                k: relax[k]
                for k in (
                    "builtin",
                    "category",
                    "dynamic",
                    "customised_in",
                )
            },
            {
                "builtin": True,
                "category": "white",
                "dynamic": False,
                "customised_in": [],
            },
        )
        self.assertRegex(relax["swatches"][0], r"^#[0-9a-f]{6}$")
        tokyo = next(s for s in config["scenes"] if s["id"] == "tokyo")
        self.assertTrue(tokyo["dynamic"])
        candle = next(s for s in config["scenes"] if s["id"] == "cozy_candle")
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

    async def test_save_edit_and_delete_a_running_scene(self) -> None:
        h = self.h
        saved = await h.ws({"type": "magic_lights/scene/save", "scene": evening()})
        self.assertEqual(saved["scene"]["swatches"], [saved["scene"]["swatches"][0]])
        self.assertIn("Evening", self.status(LIVING)["options"])
        self.assertNotIn("Evening", self.status(BEDROOM)["options"])
        await h.select(LIVING, "Evening")
        self.assertEqual(
            self.data_of("light.living_ceiling")[-1]["color_temp_kelvin"], 2400
        )

        # Editing the running scene updates the room in place.
        h.clear_calls()
        layer = {
            "lights": "all",
            "effect": "white",
            "params": {"kelvin": 3000, "brightness": 50},
        }
        await h.ws(
            {"type": "magic_lights/scene/save", "scene": evening(layers=[layer])}
        )
        await h.idle()
        self.assertEqual(
            self.data_of("light.living_ceiling")[-1]["color_temp_kelvin"], 3000
        )
        self.assertEqual(self.state(LIVING), "Evening")

        # Deleting it ends the activation; the lights stay as they are.
        h.clear_calls()
        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000e001"})
        await h.idle()
        self.assertEqual(self.state(LIVING), "Manual")
        self.assertNotIn("Evening", self.status(LIVING)["options"])
        self.assertEqual(h.calls(), [])

    async def test_customise_and_reset_a_preset(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Relax")
        copy = evening(
            id="s_0000c001",
            name="Relax",
            preset="relax",
            layers=[
                {
                    "lights": "all",
                    "effect": "white",
                    "params": {"kelvin": 2237, "brightness": 20},
                }
            ],
        )
        h.clear_calls()
        await h.ws({"type": "magic_lights/scene/save", "scene": copy})
        await h.idle()
        # The running preset follows its copy; the menu and ids stay.
        self.assertEqual(self.data_of("light.living_ceiling")[-1]["brightness"], 51)
        self.assertEqual(self.status(LIVING)["scene_id"], "relax")
        self.assertEqual(self.status(LIVING)["options"].count("Relax"), 1)
        config = await h.ws({"type": "magic_lights/config"})
        scenes = {s["id"]: s for s in config["scenes"]}
        relax = scenes["relax"]
        self.assertEqual(relax["customised_in"], ["room:living_room"])
        # Each target lists the scene it runs: the copy here, the preset elsewhere.
        self.assertEqual(scenes["s_0000c001"]["fits"], ["room:living_room"])
        self.assertIn("room:bedroom", relax["fits"])
        self.assertNotIn("room:living_room", relax["fits"])

        h.clear_calls()
        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000c001"})
        await h.idle()
        self.assertEqual(self.data_of("light.living_ceiling")[-1]["brightness"], 143)
        self.assertEqual(self.state(LIVING), "Relax")

    async def test_errors_are_readable(self) -> None:
        h = self.h
        cases = [
            (
                evening(name="relax"),
                "duplicate_name",
                "a scene named 'relax' already exists (room:living_room)",
            ),
            (evening(name="Off"), "reserved_name", "'Off' is a reserved name"),
            (
                evening(layers=[{"lights": "all", "effect": "strobe", "params": {}}]),
                "unknown_effect",
                "unknown effect 'strobe' (layer 1, effect)",
            ),
            (evening(id="relax"), "invalid", None),
        ]
        for scene, code, message in cases:
            with self.subTest(code=code), self.assertRaises(WsError) as err:
                await h.ws({"type": "magic_lights/scene/save", "scene": scene})
            self.assertEqual(err.exception.code, code)
            if message is not None:
                self.assertEqual(err.exception.message, message)
        bad = evening(
            layers=[{"lights": "all", "effect": "white", "params": {"brightness": 400}}]
        )
        with self.assertRaises(WsError) as err:
            await h.ws({"type": "magic_lights/scene/save", "scene": bad})
        self.assertEqual(err.exception.code, "invalid_params")
        self.assertTrue(err.exception.message.endswith("(layer 1, brightness)"))
        with self.assertRaises(WsError) as err:
            await h.ws({"type": "magic_lights/scene/delete", "scene_id": "relax"})
        self.assertEqual(err.exception.code, "not_found")
        with self.assertRaises(WsError) as err:
            await h.ws(
                {
                    "type": "magic_lights/try",
                    "target_id": "room:attic",
                    "scene": palette(),
                }
            )
        self.assertEqual(err.exception.code, "home_assistant_error")
        self.assertEqual(err.exception.message, "There is no room or zone room:attic")

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

        await h.ws({"type": "magic_lights/zone/delete", "zone_id": "upstairs"})
        await h.idle()
        self.assertIsNone(h.hass.states.get(zone))
        self.assertIsNone(entities.async_get(zone))
        self.assertIsNone(devices.async_get(device.id))

    async def test_deleting_a_zone_deletes_the_scenes_only_it_had(self) -> None:
        h = self.h
        await h.ws(
            {
                "type": "magic_lights/zone/save",
                "zone": {
                    "id": "",
                    "name": "Downstairs",
                    "icon": None,
                    "rooms": ["living_room", "kitchen"],
                    "lights": [],
                },
            }
        )
        for scene in (
            evening(id="s_0000e002", name="Cozy", scope=["zone:downstairs"]),
            evening(scope=["zone:downstairs", "room:living_room"]),
        ):
            await h.ws({"type": "magic_lights/scene/save", "scene": scene})
        await h.ws({"type": "magic_lights/zone/delete", "zone_id": "downstairs"})
        config = await h.ws({"type": "magic_lights/config"})
        self.assertEqual(
            {s["id"]: s["scope"] for s in config["scenes"] if not s["builtin"]},
            {"s_0000e001": ["room:living_room"]},
        )
        # The name is free again.
        await h.ws(
            {
                "type": "magic_lights/scene/save",
                "scene": evening(id="s_0000e003", name="Cozy", scope=[]),
            }
        )

    async def test_scenes_of_a_deleted_area_do_not_block_edits(self) -> None:
        h = self.h
        both = evening(scope=["room:bedroom", "room:living_room"])
        for scene in (
            both,
            evening(id="s_0000e002", name="Cozy", scope=["room:bedroom"]),
        ):
            await h.ws({"type": "magic_lights/scene/save", "scene": scene})
        ar.async_get(h.hass).async_delete("bedroom")
        await h.idle()
        self.assertIsNone(h.hass.states.get(BEDROOM))
        # Saved as it is, and the unreachable scene's name is free again.
        await h.ws(
            {"type": "magic_lights/scene/save", "scene": both | {"brightness": 40}}
        )
        await h.ws(
            {
                "type": "magic_lights/scene/save",
                "scene": evening(id="s_0000e003", name="Cozy", scope=[]),
            }
        )

    async def test_target_options_and_settings(self) -> None:
        h = self.h
        reply = await h.ws(
            {
                "type": "magic_lights/target/options",
                "target_id": "room:living_room",
                "options": {
                    "excluded_lights": ["light.living_sofa_lamp"],
                    "visible_scenes": ["tokyo", "relax"],
                },
            }
        )
        await h.idle()
        self.assertEqual(reply["target"]["scenes"], ["tokyo", "relax"])
        self.assertNotIn("light.living_sofa_lamp", reply["target"]["lights"])
        self.assertEqual(
            self.status(LIVING)["options"], ["Off", "Tokyo", "Relax", "Manual"]
        )
        self.assertNotIn("light.living_sofa_lamp", self.status(LIVING)["lights"])

        # A new scene shows up in a menu the user has ordered.
        await h.ws({"type": "magic_lights/scene/save", "scene": evening()})
        self.assertEqual(
            self.status(LIVING)["options"],
            ["Off", "Tokyo", "Relax", "Evening", "Manual"],
        )

        await h.ws(
            {
                "type": "magic_lights/settings/save",
                "settings": {
                    "light_buses": {"light.office_desk": "zigbee:test"},
                    "transition": 2,
                },
            }
        )
        await h.ws(
            {"type": "magic_lights/settings/save", "settings": {"light_buses": {}}}
        )
        config = await h.ws({"type": "magic_lights/config"})
        self.assertEqual(config["settings"]["light_buses"], {})
        self.assertEqual(config["settings"]["transition"], 2)
        with self.assertRaises(WsError) as err:
            await h.ws(
                {"type": "magic_lights/settings/save", "settings": {"transition": -1}}
            )
        self.assertEqual(err.exception.code, "invalid")

    async def test_menus_are_saved_in_one_commit(self) -> None:
        h = self.h
        events: list[Any] = []
        await h.ws_send({"type": "magic_lights/subscribe"}, events.append)
        await h.ws({"type": "magic_lights/scene/save", "scene": evening()})
        await h.idle()
        events.clear()
        # What one target's write announces is the most a batch may announce.
        await h.ws(
            {
                "type": "magic_lights/target/options",
                "target_id": "room:office",
                "options": {"visible_scenes": ["read"]},
            }
        )
        await h.idle()
        single = len(events)
        events.clear()
        store = h.data().store
        with patch.object(
            store, "async_save_config", wraps=store.async_save_config
        ) as save:
            reply = await h.ws(
                {
                    "type": "magic_lights/menus/save",
                    "menus": {
                        "room:living_room": ["tokyo", "s_0000e001", "relax"],
                        "room:kitchen": ["read"],
                        "room:bedroom": None,
                    },
                }
            )
            await h.idle()
        self.assertEqual(save.call_count, 1)
        self.assertEqual(len(events), single)
        self.assertEqual(
            {t["id"]: t["scenes"] for t in reply["targets"]},
            {
                "room:living_room": ["tokyo", "s_0000e001", "relax"],
                "room:kitchen": ["read"],
                "room:bedroom": [],
            },
        )
        self.assertEqual(
            self.status(LIVING)["options"],
            ["Off", "Tokyo", "Evening", "Relax", "Manual"],
        )
        events.clear()

        cases = [
            ({"room:attic": ["relax"]}, "not_found"),
            ({"room:kitchen": ["relax", "disco"]}, "unknown_scene"),
            ({"room:kitchen": ["relax", "relax"]}, "invalid"),
            ({"room:kitchen": [{}]}, "invalid_format"),
        ]
        for menus, code in cases:
            with self.subTest(code=code), self.assertRaises(WsError) as err:
                await h.ws(
                    {
                        "type": "magic_lights/menus/save",
                        # A valid entry first: nothing of a refused call is kept.
                        "menus": {"room:bedroom": ["read"], **menus},
                    }
                )
            self.assertEqual(err.exception.code, code)
        await h.idle()
        self.assertEqual(events, [])
        config = await h.ws({"type": "magic_lights/config"})
        bedroom = next(t for t in config["targets"] if t["id"] == "room:bedroom")
        self.assertIsNone(bedroom["options"]["visible_scenes"])

    async def test_deleting_a_scene_forgets_where_it_was_picked(self) -> None:
        h = self.h
        copy = evening(id="s_0000c001", name="Relax", preset="relax", scope=[])
        for scene in (evening(), copy):
            await h.ws({"type": "magic_lights/scene/save", "scene": scene})
        await h.ws(
            {
                "type": "magic_lights/menus/save",
                "menus": {
                    "room:living_room": ["s_0000e001", "relax"],
                    "room:kitchen": ["relax"],
                },
            }
        )
        for target_id, default in (
            ("room:living_room", "s_0000e001"),
            ("room:kitchen", "relax"),
        ):
            await h.ws(
                {
                    "type": "magic_lights/target/options",
                    "target_id": target_id,
                    "options": {"default_scene": default},
                }
            )
        await h.ws(
            {
                "type": "magic_lights/settings/save",
                "settings": {"default_scene": "s_0000e001"},
            }
        )

        async def options() -> tuple[dict[str, Any], str | None]:
            config = await h.ws({"type": "magic_lights/config"})
            targets = {t["id"]: t["options"] for t in config["targets"]}
            return targets, config["settings"]["default_scene"]

        # Resetting a preset keeps the references: they name the preset.
        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000c001"})
        targets, default = await options()
        self.assertEqual(
            targets["room:living_room"]["visible_scenes"], ["s_0000e001", "relax"]
        )
        self.assertEqual(targets["room:kitchen"]["default_scene"], "relax")
        self.assertEqual(default, "s_0000e001")

        await h.ws({"type": "magic_lights/scene/delete", "scene_id": "s_0000e001"})
        targets, default = await options()
        living = targets["room:living_room"]
        self.assertEqual(
            (living["visible_scenes"], living["default_scene"]), (["relax"], None)
        )
        self.assertEqual(
            (
                targets["room:kitchen"]["visible_scenes"],
                targets["room:kitchen"]["default_scene"],
            ),
            (["relax"], "relax"),
        )
        self.assertIsNone(default)

    async def test_preview(self) -> None:
        h = self.h
        reply = await h.ws(
            {
                "type": "magic_lights/preview",
                "target_id": "room:living_room",
                "scene": evening(),
            }
        )
        (frame,) = reply["frames"]
        self.assertEqual(frame["lights"]["light.living_ceiling"]["brightness"], 0.502)
        reply = await h.ws(
            {
                "type": "magic_lights/preview",
                "target_id": "room:living_room",
                "scene": palette(motion="dynamic", speed=1),
                "duration": 30,
                "samples": 31,
                "start": "2026-10-11T18:00:00Z",
            }
        )
        frames = reply["frames"]
        self.assertEqual(len(frames), 31)
        colours = {f["lights"]["light.living_ceiling"]["rgb"] for f in frames}
        self.assertGreater(len(colours), 1)
        self.assertEqual(h.calls(), [])


class StoredParamsTest(HarnessCase):
    # Stored without a colour: the effect's default applies.
    storage = {
        "version": 2,
        "minor_version": 1,
        "key": DOMAIN,
        "data": {
            "scenes": {
                "s_0000a001": evening(
                    id="s_0000a001",
                    name="Warm",
                    scope=[],
                    layers=[
                        {
                            "lights": "all",
                            "effect": "static",
                            "params": {"brightness": 47},
                        }
                    ],
                )
            }
        },
    }

    async def test_swatches_use_the_effect_defaults(self) -> None:
        with self.assertNoLogs("custom_components.magic_lights", logging.ERROR):
            config = await self.h.ws({"type": "magic_lights/config"})
        warm = next(s for s in config["scenes"] if s["id"] == "s_0000a001")
        self.assertEqual(len(warm["swatches"]), 1)


class AdminTest(HarnessCase):
    # Every required field a magic_lights/* command takes.
    SAMPLES: dict[str, Any] = {
        "scene": {},
        "scene_id": "s_0000e001",
        "zone": {},
        "zone_id": "upstairs",
        "target_id": "room:living_room",
        "options": {},
        "menus": {},
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
        self.assertGreaterEqual(len(codes), 12)
        self.assertEqual(set(codes.values()), {"unauthorized"})


class TryTest(HarnessCase):
    async def test_try_and_stop_bring_back_what_ran(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Relax")
        h.clear_calls()
        await h.ws(
            {
                "type": "magic_lights/try",
                "target_id": "room:living_room",
                "scene": palette(),
            }
        )
        await h.idle()
        attrs = self.status(LIVING)
        self.assertEqual((attrs["scene_id"], attrs["scene_name"]), (None, "Draft"))
        self.assertEqual(self.state(LIVING), "Manual")
        self.assertIn("xy_color", self.data_of("light.living_ceiling")[-1])

        h.clear_calls()
        await h.ws({"type": "magic_lights/try/stop", "target_id": "room:living_room"})
        await h.idle()
        self.assertEqual(self.state(LIVING), "Relax")
        self.assertEqual(
            self.data_of("light.living_ceiling")[-1]["color_temp_kelvin"], 2237
        )

    async def test_saving_while_trying_keeps_the_scene(self) -> None:
        h = self.h
        draft = palette()
        await h.ws(
            {
                "type": "magic_lights/try",
                "target_id": "room:living_room",
                "scene": draft,
            }
        )
        await h.ws(
            {"type": "magic_lights/scene/save", "scene": {**draft, "name": "Party"}}
        )
        await h.idle()
        self.assertEqual(self.state(LIVING), "Party")
        h.ws_close()
        await h.idle()
        self.assertEqual(self.state(LIVING), "Party")

    async def test_closing_the_editor_stops_trying(self) -> None:
        h = self.h
        await h.call("activate_scene", LIVING, scene="Read")
        await h.ws(
            {
                "type": "magic_lights/try",
                "target_id": "room:living_room",
                "scene": palette(),
            }
        )
        await h.idle()
        self.assertEqual(self.status(LIVING)["scene_name"], "Draft")
        h.ws_close()
        await h.idle()
        self.assertEqual(self.state(LIVING), "Read")

    async def test_repeated_tries_keep_one_subscription(self) -> None:
        h = self.h
        for _ in range(3):
            await h.ws(
                {
                    "type": "magic_lights/try",
                    "target_id": "room:living_room",
                    "scene": palette(),
                }
            )
        connection = h._ws  # noqa: SLF001
        self.assertEqual(len(connection.subscriptions), 1)
        await h.ws({"type": "magic_lights/try/stop", "target_id": "room:living_room"})
        self.assertEqual(connection.subscriptions, {})

    async def test_lights_are_attributed_to_the_user(self) -> None:
        h = self.h
        await h.ws(
            {
                "type": "magic_lights/try",
                "target_id": "room:bedroom",
                "scene": palette(),
            }
        )
        await h.idle()
        (admin,) = [
            user
            for user in await h.hass.auth.async_get_users()
            if user.name == "Test admin"
        ]
        context = h.hass.states.get("light.bedroom_ceiling").context
        self.assertIn(":mgl:", context.id)
        self.assertEqual(context.user_id, admin.id)
