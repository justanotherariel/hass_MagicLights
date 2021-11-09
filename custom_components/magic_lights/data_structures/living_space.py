from __future__ import annotations

import asyncio
from typing import Callable, Dict, List

from dataclasses import dataclass, field
from custom_components.magic_lights.plugin_effects import Effect
from custom_components.magic_lights.plugin_modifiers import Modifier


@dataclass
class Pipe:
    scene: Scene = None
    entities: List[str] = field(default_factory=list)

    effect: Effect = None
    effect_conf: dict = None

    modifiers: List[Modifier] = field(default_factory=list)
    modifier_conf: List[dict] = None


@dataclass
class Scene:
    name: str = None
    pipes: List[Pipe] = field(default_factory=list)
    zone: Zone = None

    unused_entities: List[str] = field(default_factory=list)


@dataclass
class Zone:
    name: str = None
    entities: List[str] = field(default_factory=list)
    groups: Dict[str, List[str]] = field(default_factory=dict)
    disabled_entities: List[str] = field(default_factory=list)
    scenes: Dict[str, Scene] = field(default_factory=dict)

    zone_sensor_callback: Callable = None

    current_scene: str = None
    current_states: Dict[str, str] = field(default_factory=dict)
    current_tasks: List[asyncio.Task] = field(default_factory=list)
