"""Config and runtime persistence in .storage."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
import logging
from pathlib import Path
import shutil
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import STORAGE_DIR, Store

from .const import DOMAIN, RUNTIME_SAVE_DELAY
from .migrate_v3 import migrate
from .models import Config, EffectLookup, RuntimeV1, Scene
from .validation import validate

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 3
STORAGE_MINOR_VERSION = 1
RUNTIME_KEY = f"{DOMAIN}.runtime"
RUNTIME_VERSION = 1


class _AsStored:
    @staticmethod
    def validate(params: Mapping[str, Any]) -> dict[str, Any]:
        return dict(params)


class _LenientEffects:
    """The effects for checking a migrated config.

    Plugins register their effects after the config loads, so an unknown
    effect keeps its params as stored, as it does on any other load.
    """

    def __init__(self, effects: EffectLookup) -> None:
        self._effects = effects

    def get(self, key: str) -> Any:
        return self._effects.get(key) or _AsStored


class ConfigStore(Store[dict[str, Any]]):
    """The config store; older versions refuse its file instead of rewriting it."""

    def __init__(
        self,
        hass: HomeAssistant,
        effects: EffectLookup,
        presets: Mapping[str, Scene],
        light_areas: Callable[[], Mapping[str, Collection[str]]],
    ) -> None:
        super().__init__(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            minor_version=STORAGE_MINOR_VERSION,
            atomic_writes=True,
        )
        self._effects = effects
        self._presets = presets
        self._light_areas = light_areas
        # Scenes merged by the last migration: old id -> kept id.
        self.remap: dict[str, str] = {}

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: Any
    ) -> dict[str, Any]:
        if old_major_version != 2:
            raise NotImplementedError
        await self.hass.async_add_executor_job(self._backup)
        # HA saves what this returns right away; a bad result must not get there.
        try:
            data, remap = migrate(
                old_data, self._effects, self._presets, self._light_areas()
            )
            validate(data, _LenientEffects(self._effects), presets=self._presets)
        except vol.Invalid as err:
            raise HomeAssistantError(
                f"Magic Lights cannot upgrade its config ({err}); "
                f"it is unchanged and copied to {STORAGE_KEY}.v2.bak"
            ) from err
        self.remap = remap
        return data

    def _backup(self) -> None:
        storage = Path(self.hass.config.path(STORAGE_DIR))
        for key, version in ((STORAGE_KEY, 2), (RUNTIME_KEY, RUNTIME_VERSION)):
            if (source := storage / key).exists():
                shutil.copyfile(source, storage / f"{key}.v{version}.bak")

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

    async def save(self, snapshot: RuntimeV1) -> None:
        self._snapshot = snapshot
        await self._store.async_save(self._serialize())

    async def remove(self) -> None:
        await self._store.async_remove()

    def _serialize(self) -> dict[str, Any]:
        self._dirty = False
        assert self._snapshot is not None
        return self._snapshot.to_dict()
