from typing import Dict

from homeassistant.components.light import (
    COLOR_MODE_ONOFF,
    COLOR_MODE_BRIGHTNESS,
    COLOR_MODE_COLOR_TEMP,
    COLOR_MODE_XY,
)
from custom_components.magic_lights.plugin_effects import Effect
from .helpers.io_helper import load_presets

CONFIG_SCHEMA = None
PLUGIN_DOMAIN = "static"
PLUGIN_NAME = "Static"
PLUGIN_DESCRIPTION = "Simple static light scene."


class Static(Effect):
    def __init__(self, async_call_service, entities, conf):
        super().__init__(async_call_service, entities, conf)

        supported_scenes = [
            "Bright",
            "Energize",
            "Concentrate",
            "Read",
            "Relax",
            "Dimmed",
            "Nightlight",
        ]

        self.service_data: Dict[str, Dict] = {}

        if "preset" in self.conf:
            if self.conf["preset"] not in supported_scenes:
                self.log.error("Preset %s not supported")
                return

            self.magic.hass.loop.create_task(self.async_preset_setup())

    async def async_preset_setup(self):
        self.selected_preset = self.conf["preset"]
        self.presets = await load_presets()

        for entity in self.entities:
            # Check if entity is light
            domain = entity.rsplit(".")[0]
            if domain != "light":
                self.log.warn("Only Light Entites are supported currently.")

            # Get State and detect light type
            state = self.magic.hass.states.get(entity)
            try:
                light_type = state.attributes["supported_color_modes"]
            except KeyError:
                self.log.warn(
                    "Entity %s does not specify its supported color modes", entity
                )

            # Save function and service data
            if COLOR_MODE_ONOFF in light_type:
                self.service_data.update({entity: self.get_preset_value("onoff")})

            elif COLOR_MODE_BRIGHTNESS in light_type:
                self.service_data.update({entity: self.get_preset_value("brightness")})

            elif COLOR_MODE_COLOR_TEMP in light_type:
                self.service_data.update({entity: self.get_preset_value("color_temp")})

            elif COLOR_MODE_XY in light_type:
                self.service_data.update({entity: self.get_preset_value("xy")})

        self.log.debug("Effect Static Initialized")

    def get_preset_value(self, light_type: str):
        if self.selected_preset not in self.presets[light_type]:
            return "off"
        else:
            return self.presets[light_type][self.selected_preset]

    async def async_start(self):
        self.log.debug(f"Static Effect started. \nConfig: {self.conf}")

        for entity in self.entities:
            s_data = {"entity_id": entity}

            if self.service_data[entity] == "off":
                await self.async_call_service("light", "turn_off", s_data)
            else:
                s_data.update(self.service_data[entity])
                await self.async_call_service("light", "turn_on", s_data)
