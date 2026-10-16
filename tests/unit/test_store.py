"""Config and runtime persistence in .storage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from custom_components.magic_lights import store
from custom_components.magic_lights.models import (
    ActivationSnapshot,
    Config,
    RuntimeV1,
    Settings,
)
from homeassistant.core import CoreState


class _ShellConfig:
    def __init__(self, config_dir: str) -> None:
        self.config_dir = config_dir

    def path(self, *parts: str) -> str:
        return str(Path(self.config_dir, *parts))


class _ShellBus:
    def async_listen_once(self, event_type: str, listener: Any) -> Any:
        return lambda: None


class _ShellHass:
    """Just enough of HomeAssistant for helpers.storage.Store."""

    def __init__(self, config_dir: str) -> None:
        self.data: dict[str, Any] = {}
        self.config = _ShellConfig(config_dir)
        self.loop = asyncio.get_running_loop()
        self.state = CoreState.running
        self.bus = _ShellBus()

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


class StoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = Path(tmp.name, ".storage")
        self.storage.mkdir()
        self.hass = _ShellHass(tmp.name)

    def write(self, key: str, version: int, data: Any) -> Path:
        path = self.storage / key
        path.write_text(
            json.dumps(
                {"version": version, "minor_version": 1, "key": key, "data": data}
            )
        )
        return path

    async def test_config_round_trip(self) -> None:
        self.assertIsNone(await store.ConfigStore(self.hass).async_load_config())
        config = Config(settings=Settings(transition=2.0))
        await store.ConfigStore(self.hass).async_save_config(config)
        self.assertEqual(await store.ConfigStore(self.hass).async_load_config(), config)

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
