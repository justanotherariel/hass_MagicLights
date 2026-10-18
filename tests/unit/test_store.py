"""Config and runtime persistence in .storage."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from custom_components.magic_lights import store
from custom_components.magic_lights.effects import BUILTIN, Registry
from custom_components.magic_lights.models import (
    ActivationSnapshot,
    Config,
    RuntimeV1,
    Settings,
)

from .fakes import ShellHass


class StoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = Path(tmp.name, ".storage")
        self.storage.mkdir()
        self.hass = ShellHass(tmp.name)

    def write(self, key: str, version: int, data: Any) -> Path:
        path = self.storage / key
        path.write_text(
            json.dumps(
                {"version": version, "minor_version": 1, "key": key, "data": data}
            )
        )
        return path

    async def test_config_round_trip(self) -> None:
        def config_store() -> store.ConfigStore:
            return store.ConfigStore(self.hass, Registry(BUILTIN), {}, dict)

        self.assertIsNone(await config_store().async_load_config())
        config = Config(settings=Settings(transition=2.0))
        await config_store().async_save_config(config)
        self.assertEqual(await config_store().async_load_config(), config)

    async def test_runtime_store(self) -> None:
        runtime = store.RuntimeStoreImpl(self.hass)
        self.assertIsNone(await runtime.load())
        snapshot = RuntimeV1(
            (
                ActivationSnapshot(
                    "a1",
                    "room:living",
                    "relax",
                    datetime(2026, 5, 1, tzinfo=UTC),
                    ("light.l1",),
                    (),
                    {},
                    False,
                ),
            ),
            {"relax|room:living": 2},
        )
        runtime.schedule_save(snapshot)
        await runtime.flush()
        self.assertEqual(await store.RuntimeStoreImpl(self.hass).load(), snapshot)
        self.write("magic_lights.runtime", 1, {"activations": [{"id": "broken"}]})
        with self.assertLogs("custom_components.magic_lights.store", "WARNING"):
            self.assertIsNone(await store.RuntimeStoreImpl(self.hass).load())


if __name__ == "__main__":
    unittest.main()
