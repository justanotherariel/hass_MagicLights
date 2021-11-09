from custom_components.magic_lights.plugin_effects import Effect
import logging
from typing import Callable, List

from homeassistant.core import HomeAssistant


CONFIG_SCHEMA = None
PLUGIN_DOMAIN = "off"
PLUGIN_NAME = "Off"
PLUGIN_DESCRIPTION = "Turns all lights off."


class Off(Effect):
    def __init__(
        self,
        async_call_service: Callable[[str, str, dict], bool],
        entities: List[str],
        conf: any,
    ):
        super().__init__(async_call_service, entities, conf)

    async def async_start(self):
        for entity in self.entities:
            await self.async_call_service("light", "turn_off", {"entity_id": entity})
