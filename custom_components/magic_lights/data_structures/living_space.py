from __future__ import annotations

import asyncio
from collections.abc import Callable

from dataclasses import dataclass, field
from custom_components.magic_lights.plugin_effects import Effect
from custom_components.magic_lights.plugin_modifiers import Modifier


@dataclass
class Pipe:
    scene: Scene = None
    entities: list[str] = field(default_factory=list)

    effect: Effect = None
    effect_conf: dict = None

    modifiers: list[Modifier] = field(default_factory=list)
    modifier_conf: list[dict] = None


@dataclass
class Scene:
    name: str = None
    pipes: list[Pipe] = field(default_factory=list)
    zone: Zone = None

    unused_entities: list[str] = field(default_factory=list)


@dataclass
class Zone:
    name: str = None
    entities: list[str] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)
    disabled_entities: list[str] = field(default_factory=list)
    scenes: dict[str, Scene] = field(default_factory=dict)

    zone_sensor_callback: Callable = None

    current_scene: str = None
    current_states: dict[str, str] = field(default_factory=dict)
    current_tasks: list[asyncio.Task] = field(default_factory=list)
