"""Config and runtime persistence in .storage."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import shutil
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.storage import Store

from .const import DOMAIN, RUNTIME_SAVE_DELAY
from .migrate_v1 import migrate_v1
from .models import Config, RuntimeV1
from .presets import preset_rows

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 2
STORAGE_MINOR_VERSION = 1
RUNTIME_KEY = f"{DOMAIN}.runtime"
RUNTIME_VERSION = 1


@callback
def area_lights(hass: HomeAssistant) -> dict[str, frozenset[str]]:
    """Map every area id to its lights (entity area, else device area).

    Groups are only recognisable by platform here, before states exist.
    """
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    out: dict[str, set[str]] = {area_id: set() for area_id in ar.async_get(hass).areas}
    for entry in entities.entities.values():
        if (
            entry.domain != "light"
            or entry.disabled_by is not None
            or entry.entity_category is not None
            or entry.platform == "group"
        ):
            continue
        area_id = entry.area_id
        if area_id is None and entry.device_id is not None:
            device = devices.async_get(entry.device_id)
            area_id = device.area_id if device else None
        if area_id is not None:
            out.setdefault(area_id, set()).add(entry.entity_id)
    return {area_id: frozenset(lights) for area_id, lights in out.items()}


class ConfigStore(Store[dict[str, Any]]):
    """The ConfigV2 store; migrates the v1 payload on first load."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            minor_version=STORAGE_MINOR_VERSION,
            atomic_writes=True,
        )

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: Any
    ) -> dict[str, Any]:
        if old_major_version != 1:
            raise NotImplementedError
        # Registries are loaded before integrations, so areas resolve here.
        await self.hass.async_add_executor_job(
            shutil.copyfile, self.path, f"{self.path}.v1.bak"
        )
        config, notes = await self._async_migrate_v1(old_data or {})
        for note in notes:
            _LOGGER.warning("Migration from v1: %s", note)
        return config

    async def _async_migrate_v1(
        self, data: Mapping[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        presets = await self.hass.async_add_executor_job(preset_rows)
        return migrate_v1(data, area_lights(self.hass), presets=presets)

    async def async_load_config(self) -> Config | None:
        """Return the stored config, or None when nothing is stored yet."""
        data = await self.async_load()
        return None if data is None else Config.from_dict(data)

    async def async_save_config(self, config: Config) -> None:
        await self.async_save(config.to_dict())

    async def async_import_v1(self, data: Mapping[str, Any]) -> Config:
        """Convert and store a v1 YAML configuration."""
        config, notes = await self._async_migrate_v1(data)
        for note in notes:
            _LOGGER.warning("Import of YAML configuration: %s", note)
        await self.async_save(config)
        return Config.from_dict(config)


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
