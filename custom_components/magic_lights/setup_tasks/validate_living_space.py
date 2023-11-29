from custom_components.magic_lights.helpers.entity_id import (
    substitute_group_names,
)
import math
from custom_components.magic_lights.magicbase.share import get_magic
import logging
import asyncio
from custom_components.magic_lights.setup_tasks.task import SetupTask
from homeassistant.core import HomeAssistant


class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()
        self.log = logging.getLogger(__name__)

        self.stage = 1

        self.validation_successful = True

    async def execute(self):
        await asyncio.gather(
            self.validate_entity_ids(),
            self.validate_entities_exclusive_in_zones(),
            self.validate_pipe_entities_in_zone(),
        )

        if not self.validation_successful:
            self.log.error(
                "Validation was not successful. Please reconfigure your setup."
            )

    async def validate_entity_ids(self):
        for zone in self.magic.living_space.values():
            for entity in zone.entities:
                if not await _valid_entity_id(self.magic.hass, entity):
                    self.log.warn(
                        "Entity %s in your zone %s not available in HomeAssistant. Removing...",
                        entity,
                        zone.name,
                    )

                    self.validation_successful = False

    async def validate_entities_exclusive_in_zones(self):
        for zone in self.magic.living_space.values():
            zone_entities = set(substitute_group_names(zone.entities, zone))

            for zone_compare in self.magic.living_space.values():
                if zone == zone_compare:
                    break

                zone_compare_entities = set(
                    substitute_group_names(zone_compare.entities, zone_compare)
                )

                duplicates = zone_entities.intersection(zone_compare_entities)
                if duplicates:
                    self.log.warn(
                        "Entity duplicates in zones found. %s have been found in Zones '%s' and '%s'",
                        duplicates,
                        zone.name,
                        zone_compare.name,
                    )
                    self.validation_successful = False

    async def validate_pipe_entities_in_zone(self):
        for zone in self.magic.living_space.values():
            for scene in zone.scenes.values():
                for pipe in scene.pipes:
                    for entity in substitute_group_names(pipe.entities, zone):
                        if entity not in zone.entities:
                            self.log.warn(
                                "Entity %s in Scene %s not found in zone. Please assign all entities used in a Scene also in their zone.",
                                entity,
                                scene.name,
                            )

                            self.validation_successful = False


async def _valid_entity_id(hass: HomeAssistant, entity_id: str) -> bool:
    timeout_s = 2
    check_interval_s = 0.1

    for _ in range(0, math.ceil(timeout_s / check_interval_s)):
        state = hass.states.get(entity_id)
        if state == None:
            await asyncio.sleep(check_interval_s)
        else:
            return True

    return False
