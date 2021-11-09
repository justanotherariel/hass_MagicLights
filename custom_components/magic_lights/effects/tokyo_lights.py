import logging
import math
import typing
from datetime import datetime, timedelta
from typing import List
from random import randrange

from custom_components.magic_lights.entity_management import StateDict
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)

MAIN_COLORS = {"blue": [255, 101, 178], "red": [118, 177, 255]}
MICROSECOND = 1000000


class Transition:
    class Channel:
        def __init__(self, value1: int, value2: int):
            self.value1 = value1
            self.value2 = value2

            self.step_value = abs(self.value1 - self.value2) / 256
            self.step_multiplier = 0

        def update(self, step_multiplier) -> int:
            a = self.value1
            b = self.step_value * step_multiplier

            if self.value1 == self.value2:
                result = self.value1

            if self.value1 < self.value2:
                result = math.ceil(a + b)
            else:
                result = math.ceil(a - b)
            return result

    def __init__(self, start_color: List[int], end_color: List[int]):
        self.r_channel = self.Channel(start_color[0], end_color[0])
        self.g_channel = self.Channel(start_color[1], end_color[1])
        self.b_channel = self.Channel(start_color[2], end_color[2])

    def update(self, progess):
        result = []
        result.append(self.r_channel.update(progess))
        result.append(self.g_channel.update(progess))
        result.append(self.b_channel.update(progess))
        return result


class TransitionLoop:
    def __init__(self):
        self.colors = [[245, 102, 105], [139, 168, 250]]

        self.transitions = []
        for i in range(len(self.colors)):
            if i == len(self.colors) - 1:
                self.transitions.append(Transition(self.colors[i], self.colors[0]))
            else:
                self.transitions.append(Transition(self.colors[i], self.colors[i + 1]))

        self.seed = randrange(256)

    def update(self, progess):
        transition_num = math.floor(progess / 256)

        transition_progress = progess - (256 * transition_num)
        _LOGGER.info(f"Update Value: {transition_num}, {transition_progress}")
        result = self.transitions[transition_num].update(transition_progress)
        return result


class Main:
    def __init__(self, entity_id_list: List[str], effect_config: dict):
        self.entity_id_list = entity_id_list

        self.transition_time_s = 30
        self.start_time = datetime.now()
        self.transition_loop = TransitionLoop()

        self.max_progress = len(self.transition_loop.colors) * 256 - 1

    async def update(self, entity_states: StateDict):
        _LOGGER.info("Updating Tokyo Lights...")
        delta = datetime.now() - self.start_time

        if delta.seconds > self.transition_time_s:
            _LOGGER.info("Resetting...")
            self.start_time = self.start_time + timedelta(
                seconds=self.transition_time_s
            )
            delta = datetime.now() - self.start_time

        progress = min(
            math.ceil(
                (
                    ((delta.seconds * MICROSECOND) + delta.microseconds)
                    / (MICROSECOND * self.transition_time_s)
                )
                * self.max_progress
            ),
            self.max_progress,
        )
        _LOGGER.info(f"Seconds: {delta.seconds}, Progress: {progress}")

        self.state_attributes = {
            "brightness": 255,
            "rgb_color": self.transition_loop.update(progress),
        }

        for entity_id in self.entity_id_list:
            new_state = State(entity_id, STATE_ON)
            new_state.attributes = self.state_attributes
            entity_states.update(new_state)
