import asyncio
import importlib.util
import logging
import os
import sys
from datetime import datetime
from typing import Awaitable, Callable, List

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback

from .const import DOMAIN, PIPE_TYPES, PREDEFINED_SCENES
from .entity_management import EntitityController, StateDict
from .data_objects import ZoneData

_LOGGER = logging.getLogger(__name__)


class LivingSpace:
    """Representation of the Living Space"""

    def __init__(self, hass: HomeAssistant, config: dict):
        self.zones: List[Zone] = []
        self._hass: HomeAssistant = hass

        # TODO: Check if entities in zones are exclusive to each other

        # Create Zones
        for zone_name, zone_data in config.items():
            zone = Zone(hass, zone_name, zone_data)
            self.zones.append(zone)

        # Register Services
        self._hass.services.async_register(
            DOMAIN, "activate_scene", self.activate_scene
        )
        self._hass.services.async_register(
            DOMAIN, "enable_entities", self.enable_entities
        )
        self._hass.services.async_register(
            DOMAIN, "disable_entities", self.disable_entities
        )

    @callback
    async def activate_scene(self, call):
        """Activates a scene in the given room"""
        selected_zone = call.data["zone"]
        selected_scene = call.data["scene"]

        for zone in self.zones:
            if zone.name == selected_zone:
                await zone.activate_scene(selected_scene)

    @callback
    async def enable_entities(self, call):
        _LOGGER.info("Enable Entity data: {}".format(call.data))
        selected_zone = call.data["zone"]
        selected_entity = call.data["entity_id"]

        for zone in self.zones:
            if zone.name == selected_zone:
                zone.entity_controller.disabled_entities.append(selected_entity)

    @callback
    async def disable_entities(self, call):
        _LOGGER.info("Disable Entity data: {}".format(call.data))

        selected_zone = call.data["zone"]
        selected_entities = call.data["entity_ids"]

        for zone in self.zones:
            if zone.name == selected_zone:
                zone.entity_controller.disabled_entities.append(selected_entities)


class Zone:
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        zone_config: dict,
    ):
        self._hass: HomeAssistant = hass
        self.name: str = name

        self.entity_controller: EntitityController = EntitityController(
            hass=hass,
            state_dict=StateDict.dict_from_list(zone_config["entities"]),
            groups=zone_config.get("groups", None),
            async_state_change_callback=self.outside_state_change,
        )

        self.scenes_config: dict = zone_config["scenes"]
        self.add_predefined_scenes()
        self.current_scene: Scene = None

        self._callback_sensor_change: List[Callable[[], None]] = []
        self._update_loop_task: Awaitable = None

    def update_sensor(self):
        for callback in self._callback_sensor_change:

            # TODO Remove this hack... To circumvent device_state_attributes not being updated.
            state = self.current_scene.name
            self.current_scene.name = ""
            callback()
            self.current_scene.name = state
            callback()

    def add_predefined_scenes(self) -> None:
        """Adds predefined scenes, such as off and custom, to the config of the user"""
        for scene in self.scenes_config:
            if scene == "off" or scene == "custom":
                _LOGGER.warning(f"Defining Scene {scene} is not allowed!")
        self.scenes_config.update(PREDEFINED_SCENES)

    async def activate_scene(self, name: str) -> None:
        """Starts the updateloop with the corresponding scene"""

        # Enable all entities
        self.entity_controller.disabled_entities = []

        _LOGGER.info("Changing scene of {} to {}".format(self.name, name))

        if self._update_loop_task != None and not self._update_loop_task.cancelled():
            self._update_loop_task.cancel()

        for scene_name, scene_data in self.scenes_config.items():
            if scene_name == name:
                self.current_scene = Scene(
                    self.entity_controller, scene_name, scene_data
                )

        # Update Sensor
        self.update_sensor()

        # Run UpdateLoop
        self._update_loop_task = self._hass.loop.create_task(self.update_loop())

    async def update_loop(self) -> None:
        """Entity Update Loop"""
        while True:
            # Set new Light states
            await self.current_scene.update()

            # Jump out of Loop if scene is static
            if self.current_scene.update_interval_s == 0:
                _LOGGER.info(
                    f"Exiting Update Loop of {self.name}. Reason: Static Scene"
                )
                break

            try:
                _LOGGER.info(f"Waiting {self.current_scene.update_interval_s} seconds")
                await asyncio.sleep(self.current_scene.update_interval_s)
            except asyncio.CancelledError:
                _LOGGER.info(
                    f"Exiting Update Loop of {self.name}. Reason: Scene cancelled"
                )
                raise

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Callback for updates in scene changes"""
        self._callback_sensor_change.append(callback)

    async def outside_state_change(self, new_state: State) -> None:

        # Startup - Set scene
        if self.current_scene == None:
            if self.check_entities_off():
                await self.activate_scene("off")
            else:
                await self.activate_scene("custom")
            return

        # Do nothing if new_state is "off" and current_scene is "off"
        if new_state.state == "off" and self.current_scene.name == "off":
            return

        # Set Scene to "off" if all entites are "off"
        if (
            new_state.state == "off"
            and self.current_scene.name != "off"
            and self.check_entities_off()
        ):
            await self.activate_scene("off")
            return

        # Set Scene to "custom" if new_state is "on" and current_scene is "off"
        if new_state.state == "on" and self.current_scene.name == "off":
            await self.activate_scene("custom")
            return

        # Disable Entity if it got changed manually
        if self.current_scene.name != "custom":
            self.entity_controller.disable_entity(new_state.entity_id)
            self.update_sensor()

    def check_entities_off(self):
        """Check if all entites in zone are off"""
        for entity_id in self.entity_controller.entity_id_list:

            current_state = self._hass.states.get(entity_id)
            if current_state == None:
                return False

            if current_state.state == STATE_ON:
                return False

        return True


class Scene:
    def __init__(self, entity_controller, name, scene_config):
        self.name = name
        self.entity_controller = entity_controller

        # TODO Clean up this mess with "config"
        self.update_interval_s = scene_config["config"]["update_interval_s"]

        # TODO Check if entities are exlcusive in scenes

        self._pipes = []
        for pipe in scene_config["pipes"]:

            pipe_type = pipe["pipe_type"]
            pipe_effects = pipe["effects"]
            pipe_entities = self.entity_controller.entity_pseudonyms(pipe["entities"])

            self._pipes.append(
                Pipe(entity_controller, pipe_type, pipe_entities, pipe_effects)
            )

    async def update(self):
        _LOGGER.info(f"Updating Scene {self.name}")

        pipe_tasks = []
        for pipe in self._pipes:
            pipe_tasks.append(pipe.update())
        await asyncio.gather(*pipe_tasks)


class Pipe:
    effect_folder_path = "custom_components/magic_lights/effects/"
    effect_main_class = "Main"

    def __init__(
        self,
        entity_controller: EntitityController,
        pipe_type: str,
        entity_id_list: List[str],
        effects: dict,
    ):
        self.entity_controller: EntitityController = entity_controller
        self._pipe_type: str = pipe_type
        self._pipe_type_domain: str = self._pipe_type.split(".")[0]
        self.entity_id_list: List[str] = entity_id_list

        self._update_dict = {
            "light": self.light,
            "switch": self.switch,
            "no_op": self.no_op,
        }

        self._effects = []
        for effect_config in effects:
            module_path = Pipe.effect_folder_path + effect_config["name"] + ".py"
            spec = importlib.util.spec_from_file_location(
                Pipe.effect_main_class, module_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            cleaned_config = dict(effect_config)
            cleaned_config.pop("name", None)

            self._effects.append(module.Main(self.entity_id_list, cleaned_config))

    async def update(self):
        _LOGGER.debug(f"Begin updating Pipe...")
        state_dict: StateDict = StateDict.dict_from_list(self.entity_id_list)

        for effect in self._effects:
            await effect.update(state_dict)

        await self._update_dict[self._pipe_type_domain](state_dict)
        _LOGGER.debug(f"Successfully updated pipe.")

    async def light(self, state_dict: StateDict):
        for state in state_dict:
            for attribute in PIPE_TYPES[self._pipe_type]["required"]:
                if attribute not in state.attributes.keys():
                    _LOGGER.error(f'Required Attribute "{attribute}" not in State')
                    return

            keys_to_remove = []
            for attribute in state.attributes.keys():
                if (
                    attribute not in PIPE_TYPES[self._pipe_type]["required"]
                    and attribute not in PIPE_TYPES[self._pipe_type]["optional"]
                ):
                    _LOGGER.warn(
                        f"Attribute {attribute} not supported with PipeType {self._pipe_type}"
                    )
                    keys_to_remove.append(attribute)

            for key in keys_to_remove:
                del state.attributes[key]
            await self.entity_controller.set_entity(state)

    async def switch(self, state_dict: StateDict):
        pass

    async def no_op(self, state_dict: StateDict):
        pass
