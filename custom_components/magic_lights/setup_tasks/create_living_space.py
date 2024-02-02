from __future__ import annotations

import logging
from custom_components.magic_lights.data_structures.magic import Magic
from custom_components.magic_lights.setup_tasks.task import SetupTask

from custom_components.magic_lights.magicbase.share import get_magic
from custom_components.magic_lights.data_structures.living_space import (
    Zone,
    Scene,
    Pipe,
)

_LOGGER = logging.getLogger(__name__)


def _init_pipe(scene: Scene, conf: dict) -> Pipe:
    obj = Pipe()

    obj.scene = scene
    obj.entities = conf["entities"]
    obj.modifier_conf = conf.get("modifiers", {})
    obj.effect_conf = conf.get("effect", None)

    # Effect conf mandatory
    # TODO Check with voluptous.
    if not obj.effect_conf:
        _LOGGER.warn(
            "Pipe in Scene %s in Zone %s has no effect configuration",
            scene.name,
            scene.zone.name,
        )

    return obj


def _init_scene(zone, name, conf: dict) -> Scene:
    obj = Scene()

    obj.name = name
    obj.zone = zone

    # Init Pipes
    for pipe_conf in conf:
        obj.pipes.append(_init_pipe(obj, pipe_conf))

    # Set unused entities
    zone_entities = set(obj.zone.entities)
    used_entities = []
    for pipe in obj.pipes:
        used_entities.append(pipe.entities)
    obj.unused_entities = [
        entity for entity in zone_entities if entity not in used_entities
    ]

    return obj


def _init_zone(magic: Magic, name: str, conf: dict) -> Zone:
    obj = Zone()

    obj.name = name
    obj.groups = conf.get("groups", {})
    obj.entities = conf["entities"]

    for scene_name, scene_conf in conf["scenes"].items():
        obj.scenes.update({scene_name: _init_scene(obj, scene_name, scene_conf)})

    current_scene_off = True
    for entity in obj.entities:
        if magic.hass.states.get(entity) != "off":
            current_scene_off = False
    obj.current_scene = "off" if current_scene_off else "custom"

    return obj


class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()

        self.stage = 0

    async def execute(self):
        zones: dict[str, Zone] = {}

        for zone_name, zone_conf in self.magic.raw.items():
            zones.update({zone_name: _init_zone(self.magic, zone_name, zone_conf)})

        self.magic.living_space = zones
