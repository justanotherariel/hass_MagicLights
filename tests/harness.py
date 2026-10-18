"""Boot a real, headless Home Assistant with fake lights for integration tests.

pytest-homeassistant-custom-component is not available here, so tests use
stdlib unittest and this harness instead: a throwaway config dir, the real
HA bootstrap, the integration under test loaded from the repository.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from typing import Any
from unittest import mock

from homeassistant import block_async_io, bootstrap
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.runner import RuntimeConfig

REPO = Path(__file__).resolve().parent.parent
TESTING_CONFIG = REPO / "tests" / "testing_config"
DOMAIN = "magic_lights"
WS_TIMEOUT = 10.0

if importlib.util.find_spec("hass_frontend") is None:
    # The frontend's requirement; HA would install it, but boots here skip pip.
    raise ImportError("The harness needs `pip install -r requirements_test.txt`")

# HA mounts each config dir only while it first imports `custom_components`,
# so both namespace portions (the integration and the fake lights) stay on
# the path for every instance this process boots.
for _path in (str(REPO), str(TESTING_CONFIG)):
    if _path not in sys.path:
        sys.path.append(_path)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _yaml(value: Any) -> str:
    # JSON is valid YAML; keeps the harness free of a YAML writer.
    return json.dumps(value)


def _disable_blocking_call_detection() -> None:
    """Undo `block_async_io.enable()`, which HA allows once per process."""
    blocked = block_async_io._BLOCKED_CALLS.calls  # noqa: SLF001
    for call in blocked:
        setattr(call.object, call.function, call.original_func)
    blocked.clear()


class WsError(Exception):
    """An error reply of a websocket command."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Harness:
    """One HA instance; use `async with Harness(lights=[...]) as h:`.

    `config_dir` boots an existing directory again (a restart); `yaml` adds
    top-level configuration.yaml entries; `settle` shortens the engine's
    settle window. Lights with `stop_hue_effect` in their effect list run
    the Zigbee2MQTT Hue path: publishes land in `published` and are applied
    to the fake light like Z2M's state echo.
    """

    def __init__(
        self,
        lights: list[dict[str, Any]],
        areas: dict[str, list[str]] | None = None,
        storage: dict[str, Any] | None = None,
        extra_storage: dict[str, dict[str, Any]] | None = None,
        yaml: dict[str, Any] | None = None,
        port: int | None = None,
        config_dir: Path | None = None,
        keep_dir: bool = False,
        settle: float | None = None,
    ) -> None:
        self.lights = lights
        self.areas = areas or {}
        self.storage = storage
        self.extra_storage = extra_storage or {}
        self.yaml = yaml or {}
        self.port = port or free_port()
        self.keep_dir = keep_dir or config_dir is not None
        self.config_dir = config_dir or Path(tempfile.mkdtemp(prefix="ml-hass-"))
        self.settle_time = settle
        self.hass: HomeAssistant | None = None
        self.events: list[dict[str, Any]] = []
        self.published: list[tuple[str, dict[str, Any]]] = []
        self._patches: list[Any] = []
        self._ws: ActiveConnection | None = None
        self._ws_replies: dict[int, dict[str, Any]] = {}
        self._ws_listeners: dict[int, Callable[[Any], None]] = {}
        self._ws_id = 0

    async def __aenter__(self) -> Harness:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    def _write_config(self) -> None:
        config = {
            "homeassistant": {
                "name": "Test",
                "latitude": 47.37,
                "longitude": 8.54,
                "elevation": 400,
                "unit_system": "metric",
                "time_zone": "Europe/Zurich",
                "country": "CH",
            },
            "http": {"server_host": "127.0.0.1", "server_port": self.port},
            "logger": {
                "default": "warning",
                "logs": {f"custom_components.{DOMAIN}": "debug"},
            },
            "light": [{"platform": "ml_fake_lights", "lights": self.lights}],
            **self.yaml,
        }
        (self.config_dir / "configuration.yaml").write_text(
            "\n".join(f"{key}: {_yaml(value)}" for key, value in config.items()) + "\n"
        )
        files = dict(self.extra_storage)
        if self.storage is not None:
            files[DOMAIN] = self.storage
        if files:
            storage_dir = self.config_dir / ".storage"
            storage_dir.mkdir(exist_ok=True)
            for key, content in files.items():
                (storage_dir / key).write_text(json.dumps(content))

    def _patch_engine(self) -> None:
        from custom_components.magic_lights import native  # noqa: PLC0415
        from custom_components.magic_lights.engine import sender  # noqa: PLC0415

        def topic(hass: HomeAssistant, entity_id: str) -> str:
            return f"zigbee2mqtt/{entity_id.removeprefix('light.')}/set"

        async def publish(hass: HomeAssistant, topic: str, payload: str) -> None:
            data = json.loads(payload)
            self.published.append((topic, data))
            entity_id = "light." + topic.split("/")[1]
            self.entity(entity_id).apply_mqtt(data)

        self._patches = [
            mock.patch.object(native, "z2m_command_topic", topic),
            mock.patch.object(native, "mqtt_publish", publish),
        ]
        if self.settle_time is not None:
            self._patches.append(
                mock.patch.object(sender, "SETTLE_TIME", self.settle_time)
            )
        for patch in self._patches:
            patch.start()

    async def start(self) -> None:
        try:
            await asyncio.to_thread(self._write_config)
            self._patch_engine()
            hass = await bootstrap.async_setup_hass(
                RuntimeConfig(config_dir=str(self.config_dir), skip_pip=True)
            )
            assert hass is not None, "HA bootstrap failed"
            self.hass = hass
            hass.bus.async_listen(f"{DOMAIN}_event", self._on_event)
            await hass.async_start()
            await hass.async_block_till_done()
            await self._confirm_http_config()
            self._assign_areas()
            await hass.async_block_till_done()
        except BaseException:
            # A half-booted instance would break every later boot.
            await self.stop()
            raise

    @callback
    def _on_event(self, event: Event) -> None:
        self.events.append(dict(event.data))

    async def _confirm_http_config(self) -> None:
        # The YAML http block becomes a trial config; confirm it so the
        # frontend shows no "keep new server settings?" dialog.
        from homeassistant.components.http.config import DATA_STORE  # noqa: PLC0415

        store = self.hass.data.get(DATA_STORE)
        if store is not None and store.pending is not None:
            await store.async_promote_pending()

    def _assign_areas(self) -> None:
        area_reg = ar.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        for area_name, entity_ids in self.areas.items():
            area = area_reg.async_get_area_by_name(area_name) or area_reg.async_create(
                area_name
            )
            for entity_id in entity_ids:
                if ent_reg.async_get(entity_id) is not None:
                    ent_reg.async_update_entity(entity_id, area_id=area.id)

    async def setup_integration(self) -> None:
        """Create the single Magic Lights config entry through its flow."""
        flow = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        if flow.get("type") == "form":
            await self.hass.config_entries.flow.async_configure(flow["flow_id"], {})
        await self.hass.async_block_till_done()

    async def stop(self) -> None:
        """Stop HA and undo its process-wide changes; safe to call repeatedly."""
        try:
            if self.hass is not None:
                await self.hass.async_stop(force=True)
        finally:
            self.hass = None
            for patch in reversed(self._patches):
                patch.stop()
            self._patches = []
            _disable_blocking_call_detection()
            # Closing HA's queue handler stops its listener thread.
            for handler in logging.root.handlers[:]:
                logging.root.removeHandler(handler)
                handler.close()
            if not self.keep_dir:
                # HA flags blocking I/O inside the event loop, even after stop.
                await asyncio.to_thread(
                    shutil.rmtree, self.config_dir, ignore_errors=True
                )

    # ---- lights

    def calls(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        """Commands the fake lights received, oldest first."""
        log = self.hass.data.get("ml_fake_lights_calls", [])
        return [c for c in log if entity_id is None or c["entity_id"] == entity_id]

    def clear_calls(self) -> None:
        self.hass.data.get("ml_fake_lights_calls", []).clear()
        self.published.clear()

    def entity(self, entity_id: str):
        """Return the FakeLight object behind an entity id."""
        light = self.hass.data["entity_components"]["light"].get_entity(entity_id)
        assert light is not None, entity_id
        return light

    async def settle(self, seconds: float = 0.05) -> None:
        await asyncio.sleep(seconds)
        await self.hass.async_block_till_done()

    async def idle(self, timeout: float = 5.0) -> None:
        """Wait until no command waits for its bus or light pacing."""
        scheduler = self.data().coordinator._scheduler  # noqa: SLF001
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        await self.settle()
        while loop.time() < deadline and any(
            scheduler.pending(entity_id) for entity_id in self.data().lights.lights
        ):
            await self.settle(0.02)

    # ---- integration

    def data(self):
        """Return the loaded entry's MagicLightsData."""
        (entry,) = self.hass.config_entries.async_loaded_entries(DOMAIN)
        return entry.runtime_data

    def attributes(self, entity_id: str) -> dict[str, Any]:
        state = self.hass.states.get(entity_id)
        assert state is not None, entity_id
        return dict(state.attributes)

    async def call(self, service: str, entity_id: str, **data: Any) -> None:
        """Call a magic_lights service on a select and let the lights settle."""
        await self.hass.services.async_call(
            DOMAIN, service, data, target={"entity_id": entity_id}, blocking=True
        )
        await self.idle()

    async def select(self, entity_id: str, option: str) -> None:
        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": option},
            blocking=True,
        )
        await self.idle()

    async def ws(self, msg: dict[str, Any]) -> Any:
        """Send one websocket command as an admin; return its result."""
        msg_id = await self.ws_send(msg)
        reply = self._ws_replies.pop(msg_id)
        if not reply["success"]:
            raise WsError(reply["error"]["code"], reply["error"]["message"])
        return reply["result"]

    async def ws_send(
        self, msg: dict[str, Any], on_event: Callable[[Any], None] | None = None
    ) -> int:
        """Send a command and wait for its reply; events go to `on_event`."""
        if self._ws is None:
            user = await self.hass.auth.async_create_user(
                "Test admin", group_ids=["system-admin"]
            )
            self._ws = ActiveConnection(
                logging.getLogger(__name__), self.hass, self._ws_route, user, None, None
            )
        self._ws_id += 1
        msg_id = self._ws_id
        if on_event is not None:
            self._ws_listeners[msg_id] = on_event
        self._ws.async_handle({**msg, "id": msg_id})
        async with asyncio.timeout(WS_TIMEOUT):
            while msg_id not in self._ws_replies:
                await asyncio.sleep(0.01)
        return msg_id

    def _ws_route(self, message: Any) -> None:
        if callable(message):
            message = message()
        if isinstance(message, bytes | str):
            message = json.loads(message)
        if message.get("type") == "event":
            if listener := self._ws_listeners.get(message["id"]):
                listener(message["event"])
        else:
            self._ws_replies[message["id"]] = message

    def ws_close(self) -> None:
        """Close the websocket connection, as a browser tab does."""
        if self._ws is not None:
            self._ws.async_handle_close()
            self._ws = None
