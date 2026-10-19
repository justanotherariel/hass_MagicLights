"""The scene engine: activations, pacing, sending and override detection."""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.core import HomeAssistant

from ..models import (
    Config,
    Driver,
    EffectLookup,
    Host,
    LightIndex,
    Part,
    RuntimeStore,
)
from .coordinator import Coordinator
from .host import HassHost
from .sender import Sender
from .watcher import Watcher

__all__ = ["Coordinator", "HassHost", "build_coordinator", "create_engine"]


def build_coordinator(
    host: Host,
    lights: LightIndex,
    effects: EffectLookup,
    drivers: Mapping[str, Driver],
    config: Config,
    builtins: Mapping[str, Part],
    runtime: RuntimeStore,
) -> Coordinator:
    """Wire sender, scheduler and coordinator over a host."""
    sender = Sender(host, lights, drivers)
    return Coordinator(host, lights, effects, sender, config, builtins, runtime)


def create_engine(
    hass: HomeAssistant,
    lights: LightIndex,
    effects: EffectLookup,
    drivers: Mapping[str, Driver],
    config: Config,
    builtins: Mapping[str, Part],
    runtime: RuntimeStore,
) -> Coordinator:
    """Build the engine on a running HomeAssistant; its watcher starts in start()."""
    coordinator = build_coordinator(
        HassHost(hass), lights, effects, drivers, config, builtins, runtime
    )
    coordinator.watcher = Watcher(hass, coordinator, lights)
    return coordinator
