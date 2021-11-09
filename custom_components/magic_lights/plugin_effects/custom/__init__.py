from custom_components.magic_lights.plugin_effects import Effect
import logging
from typing import Callable, List

from homeassistant.core import HomeAssistant


CONFIG_SCHEMA = None
PLUGIN_DOMAIN = "custom"
PLUGIN_NAME = "Custom"
PLUGIN_DESCRIPTION = "Active if no scene was set. Doesn't do anything."


class Custom(Effect):
    def __init__(
        self,
        async_call_service: Callable[[str, str, dict], bool],
        entities: List[str],
        conf: any,
    ):
        super().__init__(async_call_service, entities, conf)

    async def async_start(self):
        pass
