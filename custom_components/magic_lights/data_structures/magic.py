"""BASE MagicLights class."""
from __future__ import annotations
from dataclasses import dataclass

from typing import Dict, TYPE_CHECKING, List

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from custom_components.magic_lights.data_structures.living_space import Zone
    from custom_components.magic_lights.magicbase.plugin_manager import PluginManager
    from custom_components.magic_lights.magicbase.scene_manager import SceneManager
    from custom_components.magic_lights.magicbase.state_listener import StateListener
    from custom_components.magic_lights.magicbase.context_manager import ContextManager


@dataclass
class Magic:
    """Base MAGIC class."""

    hass: HomeAssistant | None = None
    raw: dict | None = None  # Raw Config/DB Data which should get deserialized
    living_space: Dict[str, Zone] | None = None

    plugin_manager: PluginManager | None = None
    scene_manager: SceneManager | None = None
    context_manager: ContextManager | None = None
    state_listener: StateListener | None = None
