from custom_components.magic_lights.data_structures.living_space import Zone
from typing import Dict, List


def substitute_group_names(entities: List[str], zone: Zone) -> List[str]:
    result = []

    for entity in entities:
        if entity == "light.all":
            # TODO Handle other Domains.
            return zone.entities

        if entity in zone.groups:
            result.extend(zone.groups[entity])
        else:
            result.append(entity)
    return result


def get_domain(entity_id: str) -> str:
    return entity_id.rsplit(".")[0]


def get_name(entity_id: str) -> str:
    return entity_id.rsplit(".")[1]


def get_unused_entities(zone: Zone, scene_name: str) -> List[str]:
    zone_entities = set(substitute_group_names(zone.entities, zone))
    used_entities = set()

    for pipe in zone.scenes[scene_name].pipes:
        used_entities.update(substitute_group_names(pipe.entities, zone))

    return zone_entities.difference(used_entities)