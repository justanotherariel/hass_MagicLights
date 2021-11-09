import logging
import typing
from datetime import datetime, timedelta
from typing import List

from custom_components.light_scene.entity_management import StateDict
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)


class Main:
    def __init__(self, entity_id_list: List[str], effect_config: dict):
        self.init_success = False
        self.entity_id_list = entity_id_list

        fade_in_start = effect_config.get("fade-in_start_time", None)
        fade_in_delay = effect_config.get("fade-in_delay_s", None)
        self.fade_in = timedelta(seconds=effect_config.get("fade-in_s", 5))

        if fade_in_start and fade_in_delay:
            _LOGGER.warning("Cannot set both Start Time and Delay")

        if fade_in_start != None:
            current_time = datetime.now()
            self.input_time = datetime.strptime(fade_in_start, "%H:%M:%S").replace(
                year=current_time.year, month=current_time.month, day=current_time.day
            )

        if fade_in_delay != None:
            current_time = datetime.now()
            self.input_time = current_time + timedelta(seconds=fade_in_delay)

        self.init_success = True

    async def update(self, entity_states: StateDict):
        for state in entity_states:
            target_brightness = state.attributes.get("brightness", 255)

            x = (datetime.now() - self.input_time) / self.fade_in
            brightness = min(target_brightness, max(0, int(target_brightness * x)))

            attributes = {"brightness": brightness}
            entity_states.update_attributes(state.entity_id, attributes)