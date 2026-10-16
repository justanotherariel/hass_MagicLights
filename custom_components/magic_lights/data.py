"""What the loaded entry shares with its select, services and websocket."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_CONFIG
from .effects import Registry
from .engine import Coordinator
from .lights import LightIndexImpl
from .models import Config, Scene
from .store import ConfigStore, RuntimeStoreImpl


@dataclass(slots=True)
class MagicLightsData:
    coordinator: Coordinator
    lights: LightIndexImpl
    store: ConfigStore
    runtime: RuntimeStoreImpl
    registry: Registry
    presets: Mapping[str, Scene]
    version: str

    @callback
    def apply_config(self, hass: HomeAssistant, config: Config) -> None:
        """Make a new config current; storing it is up to the caller."""
        self.lights.apply_config(config)
        self.coordinator.apply_config(config)
        async_dispatcher_send(hass, SIGNAL_CONFIG)

    async def async_commit(self, hass: HomeAssistant, config: Config) -> None:
        """Apply a new config, then store it."""
        # Applied first so concurrent edits build on each other; the store
        # always writes its newest data.
        self.apply_config(hass, config)
        await self.store.async_save_config(config)


type MagicLightsConfigEntry = ConfigEntry[MagicLightsData]
