import asyncio
import logging
import typing
from typing import Callable, List

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, State

_LOGGER = logging.getLogger(__name__)


def rgb_color_equal(rgb_color_1, rgb_color_2):
    tolerance = 50  # In both directions

    for i in range(3):
        if not (
            rgb_color_2[i] + tolerance > rgb_color_1[i]
            and rgb_color_2[i] - tolerance < rgb_color_1[i]
        ):
            _LOGGER.info("Hello :)")
            return False
    return True


class StateDict:
    def __init__(self):
        self.state_list: List[State] = []
        self.entity_id_list: List[str] = []

    @classmethod
    def dict_from_list(cls, entity_id_list: List[str]) -> "EntityDict":
        result = cls()
        for enitity_id in entity_id_list:
            state = State(enitity_id, None)
            state.state = None
            result.update(state)
        return result

    def __iter__(self):
        return EntityDictIterator(self)

    def update(self, passed_state: State) -> None:
        """Adds or Updates an entry to or from the EntityDict"""

        if passed_state.entity_id == None:
            _LOGGER.error("No Entity ID provided, can't add Entity to EntityDict")
            return

        for state in self.state_list:
            if state.entity_id == passed_state.entity_id:
                state.state = passed_state.state
                new_attributes = {}
                new_attributes.update(state.attributes)
                new_attributes.update(passed_state.attributes)
                state.attributes = new_attributes
                return

        self.state_list.append(passed_state)
        self.entity_id_list.append(passed_state.entity_id)

    def update_attributes(self, entity_id, attributes: dict):
        for state in self.state_list:
            if state.entity_id == entity_id:
                new_attributes = {}
                new_attributes.update(state.attributes)
                new_attributes.update(attributes)
                state.attributes = new_attributes


    def remove(self, entity_id: str) -> None:
        """Removes an Entity from the Dict"""

        to_remove: State = None
        for state in self.state_list:
            if state.entity_id == entity_id:
                to_remove = state
                break

        if to_remove != None:
            self.entity_id_list.remove(to_remove.name)
            self.state_list.remove(to_remove)

    def get(self, entity_id: str) -> State:
        """Gets an State with given entity_id from the Dict. 'None' if it couldn't be found."""

        for state in self.state_list:
            if state.entity_id == entity_id:
                return state

        return None


class EntityDictIterator:
    def __init__(self, entity_dict: StateDict):
        self._entity_dict = entity_dict
        self._index = 0

    def __next__(self) -> State:
        """Returns the next value from StateDict object's lists"""
        if self._index < len(self._entity_dict.state_list):
            result = self._entity_dict.state_list[self._index]
            self._index += 1
            return result

        raise StopIteration


class EntitityController:
    def __init__(
        self,
        hass: HomeAssistant,
        state_dict: StateDict,
        groups: dict,
        async_state_change_callback: Callable,
    ):
        self._hass: HomeAssistant = hass

        self.state_dict: StateDict = state_dict
        self.entity_id_list: List[str] = state_dict.entity_id_list
        self.async_sc_callback = async_state_change_callback

        self.entity_groups: dict = groups
        self.disabled_entities: List[str] = []

        # Listen to Entity State Changes
        self._hass.bus.async_listen("state_changed", self.state_change)

    async def state_change(self, event: Event):
        corresponding_state = self.state_dict.get(event.data["entity_id"])

        # Entity not found - Not important
        if corresponding_state == None:
            return

        new_state = event.data["new_state"]

        # Entity state not set - Entity not set by EC
        if corresponding_state.state == None:
            await self.async_sc_callback(new_state)
            return

        # State was set by EC - updating value
        if corresponding_state.state == "undef":
            self.state_dict.update(new_state)
            return
        else:
            # Entity not set by EC
            _LOGGER.info("Callback...")
            await self.async_sc_callback(new_state)
            return

    def disable_entity(self, entity_id: str):
        if entity_id not in self.disabled_entities:
            self.disabled_entities.append(entity_id)

    def entity_pseudonyms(self, input_list: List[str]) -> List[str]:
        def get_type(entity_type: str):
            result = []
            for entity in self.entity_id_list:
                if entity.split(".")[0] == entity_type:
                    result.append(entity)
            return result

        switcher = {
            "all": self.entity_id_list,
            "all.lights": get_type("light"),
            "all.switches": get_type("switch"),
        }

        result = []

        for name in input_list:
            if self.entity_groups != None and name in self.entity_groups:
                result.extend(self.entity_groups[name])
                continue
            if name in switcher:
                result.extend(switcher[name])
            else:
                result.append(name)

        return result  # TODO: Remove duplicate Entity_IDsclear

    async def set_entity(self, state: State):
        _LOGGER.debug(
            f"Updating {state.entity_id}, State {state.state}, Attributes {state.attributes}"
        )

        if state.entity_id not in self.entity_id_list:
            _LOGGER.error("Setting entity that is not in this EntityController")
            return

        if state.entity_id in self.disabled_entities:
            return

        # Check if Attributes is empty if state is "off"
        if state.state == STATE_OFF and state.attributes:
            _LOGGER.warn(f'Cannot set Attributes if "state" is set to {STATE_OFF}')

        new_state = self._hass.states.get(state.entity_id)
        new_state.state = "undef"
        self.state_dict.update(new_state)

        domain = state.entity_id.split(".")[0]
        if domain == "light":
            await self.update_ligt(state)
        elif domain == "switch":
            await self.update_switch(state)

        _LOGGER.info("Done...")

    async def update_ligt(self, state: State):
        if state.state == STATE_ON:
            data = {"entity_id": state.entity_id}
            data.update(state.attributes)
            await self._hass.services.async_call("light", "turn_on", data)
        else:
            await self._hass.services.async_call(
                "light", "turn_off", {"entity_id": state.entity_id}
            )

    async def update_switch(self, entity: State):
        if entity.state == STATE_ON:
            data = {"entity_id": entity.entity_id}
            data.update(entity.attributes)
            await self._hass.services.async_call("switch", "turn_on", data)
        else:
            await self._hass.services.async_call(
                "switch", "turn_off", {"entity_id": entity.entity_id}
            )
