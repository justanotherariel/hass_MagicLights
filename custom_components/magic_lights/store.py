"""Config and runtime persistence in .storage."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, RUNTIME_SAVE_DELAY
from .models import Config, RuntimeV1

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 2
STORAGE_MINOR_VERSION = 1
RUNTIME_KEY = f"{DOMAIN}.runtime"
RUNTIME_VERSION = 1


class ConfigStore(Store[dict[str, Any]]):
    """The config store."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            minor_version=STORAGE_MINOR_VERSION,
            atomic_writes=True,
        )

    async def async_load_config(self) -> Config | None:
        """Return the stored config, or None when nothing is stored yet."""
        data = await self.async_load()
        return None if data is None else Config.from_dict(data)

    async def async_save_config(self, config: Config) -> None:
        await self.async_save(config.to_dict())


class RuntimeStoreImpl:
    """Activation snapshots and salts; saved at most every RUNTIME_SAVE_DELAY s."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, RUNTIME_VERSION, RUNTIME_KEY)
        self._snapshot: RuntimeV1 | None = None
        self._dirty = False

    async def load(self) -> RuntimeV1 | None:
        data = await self._store.async_load()
        if data is None:
            return None
        try:
            return RuntimeV1.from_dict(data)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Ignoring unreadable runtime state: %s", err)
            return None

    @callback
    def schedule_save(self, snapshot: RuntimeV1) -> None:
        self._snapshot = snapshot
        self._dirty = True
        self._store.async_delay_save(self._serialize, RUNTIME_SAVE_DELAY)

    async def flush(self) -> None:
        if self._dirty:
            await self._store.async_save(self._serialize())

    async def remove(self) -> None:
        await self._store.async_remove()

    def _serialize(self) -> dict[str, Any]:
        self._dirty = False
        assert self._snapshot is not None
        return self._snapshot.to_dict()
