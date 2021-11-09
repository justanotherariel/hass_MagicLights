import asyncio
from custom_components.magic_lights.helpers.entity_id import (
    get_domain,
    get_unused_entities,
)
from typing import List

from custom_components.magic_lights.data_structures.living_space import Pipe, Scene
from custom_components.magic_lights.magicbase.share import get_magic
import logging

from homeassistant.core import Context
from custom_components.magic_lights.const import DOMAIN


class SceneManager:
    def init_scene(self, zone_name: str, scene_name: str):
        """Initializes a scene in a given zone."""
        self.magic = get_magic()
        self.log = logging.getLogger(__name__)

        if zone_name not in self.magic.living_space:
            self.log.error("Zone %s not available.", zone_name)
            return

        if scene_name not in self.magic.living_space[zone_name].scenes:
            self.log.error("Scene %s in %s not available.", scene_name, zone_name)
            return

        # Stop currently running scene
        self.log.debug("Stopping Scene. %s", zone_name)
        self.stop_scene(zone_name)

        # Reset disabled_entities
        self.magic.living_space[zone_name].disabled_entities = []

        # Setup new scene
        self.log.info("Starting Scene. %s, %s", zone_name, scene_name)
        scene: Scene = self.magic.living_space[zone_name].scenes[scene_name]

        # Turn off all lights not specified in scene
        self.log.debug("Turn off unused Entities. %s, %s", zone_name, scene_name)
        unused_entities = get_unused_entities(scene.zone, scene_name)

        for entity in unused_entities:
            if get_domain(entity) == "light":
                service_data = {"entity_id": entity}
                context = Context(None, DOMAIN)
                self.magic.hass.async_create_task(
                    self.magic.hass.services.async_call(
                        "light", "turn_off", service_data, context=context
                    )
                )

        # Start Scene
        tasks = []
        for pipe in scene.pipes:
            self.log.debug("Starting Pipe. %s, %s", zone_name, scene_name)
            tasks.extend(self._start_pipe(pipe))

        self.magic.living_space[zone_name].current_scene = scene_name
        self.magic.living_space[zone_name].current_tasks = tasks

        # Update Sensor
        scene.zone.zone_sensor_callback()

    def stop_scene(self, zone_name: str):
        """Stops a scene in a given zone. (only relevant for dynamic scenes)"""
        for task in self.magic.living_space[zone_name].current_tasks:
            if not task.cancelled():
                task.cancel()

    def _start_pipe(self, pipe: Pipe) -> List[asyncio.Task]:
        tasks = []

        # Start Modifers
        for modifier in pipe.modifiers:
            tasks.append(self.magic.hass.loop.create_task(modifier.async_start()))

        # Start Effect
        tasks.append(self.magic.hass.loop.create_task(pipe.effect.async_start()))

        return tasks
