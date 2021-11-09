from __future__ import annotations

from dataclasses import dataclass
from custom_components.magic_lights import plugin_effects, plugin_modifiers


@dataclass
class Plugin:
    domain: str | None = None
    name: str | None = None
    description: str | None = None
    init: plugin_effects.Effect | plugin_modifiers.Modifier | None = None
