"""Effect Plugins used in Magic Lights"""


import logging
from typing import Callable, List

from custom_components.magic_lights.data_structures.magic import Magic
from custom_components.magic_lights.magicbase.share import get_magic


class Effect:
    def __init__(
        self,
        async_call_service: Callable[[str, str, dict], bool],
        entities: List[str],
        conf: any,
    ):
        self.log = logging.getLogger(__name__)

        self.magic: Magic = get_magic()
        self.async_call_service = async_call_service
        self.entities = entities
        self.conf = conf

    async def async_start(self):
        raise NotImplementedError
