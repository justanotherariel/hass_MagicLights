import logging
import typing
from typing import List

from custom_components.magic_lights.entity_management import StateDict
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)


# R, G, B, brightness
PRESETS = {
    "bright": [255, 207, 120, 255],
    "relax": [255, 188, 85, 145],
    "read": [255, 210, 129, 255],
    "concentrate": [255, 240, 198, 255],
    "energize": [246, 255, 254, 255],
    "dimmed": [255, 205, 120, 77],
    "nightlight": [255, 160, 39, 1],
}


class Main:
    def __init__(self, entity_id_list: List[str], effect_config: dict):
        self.init_success = False

        config = dict(effect_config)

        self.preset_name = config.pop("preset", None)
        if self.preset_name:
            try:
                self.state_state = STATE_ON
                brightness = PRESETS[self.preset_name][3]
                rgb_color = [PRESETS[self.preset_name][i] for i in (0, 1, 2)]
                self.state_attributes = {
                    "brightness": brightness,
                    "rgb_color": rgb_color,
                }
            except KeyError:
                _LOGGER.error("Preset not found. Effect couldn't initialize.")
                self.init_success = False
        else:
            self.state_state = config.pop("state", STATE_ON)
            self.state_attributes = config
            self.init_success = True

        self.entity_id_list = entity_id_list

    async def update(self, entity_states: StateDict):
        for entity_id in self.entity_id_list:
            new_state = State(entity_id, self.state_state)
            new_state.attributes = self.state_attributes
            entity_states.update(new_state)
