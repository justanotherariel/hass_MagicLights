"""Magic Lights v1 configurations: YAML import and the v1 store."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest import IsolatedAsyncioTestCase

from homeassistant.helpers import issue_registry as ir

from tests.harness import DOMAIN, Harness
from tests.scenario import DEMO_AREAS, DEMO_LIGHTS

from . import LIVING

BRIGHT = [
    {
        "entities": ["light.all"],
        "effect": {"name": "static", "conf": {"preset": "Bright"}},
    }
]


async def read_storage(h: Harness, key: str) -> dict[str, Any]:
    path = h.config_dir / ".storage" / key
    return json.loads(await asyncio.to_thread(path.read_text))


class MigrationTest(IsolatedAsyncioTestCase):
    async def test_yaml_is_imported_with_a_repair_issue(self) -> None:
        yaml = {
            DOMAIN: {
                "reading corner": {
                    "entities": ["light.living_ceiling", "light.living_floor_lamp"],
                    "scenes": {"bright": BRIGHT},
                }
            }
        }
        async with Harness(lights=DEMO_LIGHTS, areas=DEMO_AREAS, yaml=yaml) as h:
            await h.idle()
            self.assertEqual(len(h.hass.config_entries.async_entries(DOMAIN)), 1)
            self.assertIsNotNone(
                ir.async_get(h.hass).async_get_issue(DOMAIN, "yaml_deprecated")
            )
            stored = await read_storage(h, DOMAIN)
            self.assertEqual(stored["version"], 2)
            zone = stored["data"]["zones"]["reading_corner"]
            self.assertEqual(
                zone["lights"], ["light.living_ceiling", "light.living_floor_lamp"]
            )
            (scene,) = stored["data"]["scenes"].values()
            # Names clash with presets case-insensitively, so the import renames it.
            self.assertEqual(scene["name"], "bright (v1)")
            self.assertEqual(scene["scope"], ["zone:reading_corner"])
            select = "select.reading_corner_scene"
            self.assertIn("bright (v1)", h.attributes(select)["options"])
            await h.select(select, "bright (v1)")
            self.assertEqual(
                sorted(c["entity_id"] for c in h.calls()),
                ["light.living_ceiling", "light.living_floor_lamp"],
            )

    async def test_v1_store_is_migrated_with_a_backup(self) -> None:
        v1 = {
            "version": 1,
            "minor_version": 1,
            "key": DOMAIN,
            "data": {
                "config": {
                    "living": {
                        "areas": ["living_room"],
                        "scenes": {
                            "relax": [
                                {
                                    "entities": ["light.all"],
                                    "effect": {
                                        "name": "static",
                                        "conf": {"preset": "Relax"},
                                    },
                                }
                            ]
                        },
                    }
                }
            },
        }
        async with Harness(lights=DEMO_LIGHTS, areas=DEMO_AREAS, storage=v1) as h:
            await h.setup_integration()
            await h.idle()
            backup = await read_storage(h, f"{DOMAIN}.v1.bak")
            self.assertEqual(backup, v1)
            stored = await read_storage(h, DOMAIN)
            self.assertEqual((stored["version"], stored["minor_version"]), (2, 1))
            # A zone that is exactly one area becomes that room's scene.
            self.assertEqual(stored["data"]["zones"], {})
            (scene,) = stored["data"]["scenes"].values()
            self.assertEqual(scene["scope"], ["room:living_room"])
            self.assertTrue(stored["data"]["settings"]["migration_notes"])
            self.assertIn("relax (v1)", h.attributes(LIVING)["options"])
