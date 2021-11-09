from __future__ import annotations
from custom_components.magic_lights.const import DOMAIN
from custom_components.magic_lights.magicbase.share import get_magic
import logging
from typing import TYPE_CHECKING

from homeassistant.core import Context

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from custom_components.magic_lights.data_structures.living_space import Pipe, Zone


def create_async_call(pipe: Pipe) -> callable:
    async def async_call(domain: str, service: str, service_data: dict):
        for modifier in pipe.modifiers:
            domain, service, service_data = modifier.update(
                domain, service, service_data
            )

        if disabled_entity(pipe, service_data):
            return

        return await async_call_service(domain, service, service_data)

    return async_call


def disabled_entity(pipe: Pipe, service_data: dict) -> bool:
    if "entity_id" in service_data:
        if service_data["entity_id"] in pipe.scene.zone.disabled_entities:
            _LOGGER.debug(
                "Entity %s disabled... skipping update.", service_data["entity_id"]
            )
            return True
    return False


async def async_call_service(domain: str, service: str, service_data: dict):
    _LOGGER.debug("Updating state: %s", service_data)

    context = Context(None, DOMAIN)

    magic = get_magic()
    return await magic.hass.services.async_call(
        domain, service, service_data, context=context
    )
