"""Modifier Plugins used in Magic Lights"""
import logging
from collections.abc import Callable

from custom_components.magic_lights.data_structures.magic import Magic
from custom_components.magic_lights.magicbase.share import get_magic


class Modifier:
    def __init__(
        self,
        async_call_service: Callable[[str, str, dict], bool],
        entities: list[str],
        conf: any,
    ):
        self.log = logging.getLogger(__name__)

        self.magic: Magic = get_magic()
        self.async_call_service = async_call_service
        self.entities = entities
        self.conf = conf

    async def async_start(self):
        raise NotImplementedError

    def update(
        self, domain: str, service: str, service_data: dict
    ) -> tuple[str, str, dict]:
        raise NotImplementedError
