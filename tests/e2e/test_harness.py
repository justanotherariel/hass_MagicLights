"""A failed boot or setup leaves nothing behind that breaks later tests."""

from __future__ import annotations

import asyncio
import shutil
import threading
from unittest import IsolatedAsyncioTestCase, TestCase, TestResult, mock

from homeassistant import block_async_io

from tests.harness import Harness
from tests.scenario import DEMO_AREAS, DEMO_LIGHTS

from . import HarnessCase


def log_listener_threads() -> list[str]:
    """Return HA's log queue listener threads that are still alive."""
    return [t.name for t in threading.enumerate() if t.name.endswith("(_monitor)")]


class HarnessCleanupTest(IsolatedAsyncioTestCase):
    async def test_failed_start_cleans_up(self) -> None:
        h = Harness(lights=DEMO_LIGHTS, areas=DEMO_AREAS)
        self.addAsyncCleanup(h.stop)
        with (
            mock.patch.object(h, "_assign_areas", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            await h.start()
        self.assertIsNone(h.hass)
        self.assertEqual(block_async_io._BLOCKED_CALLS.calls, set())  # noqa: SLF001
        self.assertEqual(log_listener_threads(), [])
        self.assertFalse(await asyncio.to_thread(h.config_dir.exists))

    async def test_stop_cleans_up_when_hass_fails_to_stop(self) -> None:
        h = Harness(lights=[])
        self.addCleanup(shutil.rmtree, h.config_dir, True)
        h.hass = mock.Mock(async_stop=mock.AsyncMock(side_effect=RuntimeError))
        patch = mock.Mock()
        h._patches = [patch]  # noqa: SLF001
        with self.assertRaises(RuntimeError):
            await h.stop()
        self.assertIsNone(h.hass)
        patch.stop.assert_called_once()
        self.assertFalse(await asyncio.to_thread(h.config_dir.exists))

    async def test_ws_send_times_out_without_reply(self) -> None:
        h = Harness(lights=[])
        self.addCleanup(shutil.rmtree, h.config_dir, True)
        h._ws = mock.Mock()  # noqa: SLF001
        # The outer bound turns a hang into an error rather than a pass.
        async with asyncio.timeout(2):
            with (
                mock.patch("tests.harness.WS_TIMEOUT", 0.05),
                self.assertRaises(TimeoutError),
            ):
                await h.ws_send({"type": "magic_lights/settings/get"})


class HarnessCaseCleanupTest(TestCase):
    def test_failed_setup_cleans_up(self) -> None:
        class FailingSetup(HarnessCase):
            async def asyncSetUp(self) -> None:
                await super().asyncSetUp()
                raise RuntimeError("boom")

            async def test_nothing(self) -> None:
                """Never runs: the setup fails."""

        case = FailingSetup("test_nothing")
        result = TestResult()
        case.run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIsNone(case.h.hass)
        self.assertEqual(block_async_io._BLOCKED_CALLS.calls, set())  # noqa: SLF001
        self.assertEqual(log_listener_threads(), [])
        self.assertFalse(case.h.config_dir.exists())
