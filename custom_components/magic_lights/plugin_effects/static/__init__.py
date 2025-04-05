import aiofiles
import json
import os
from typing import Any
import asyncio

from homeassistant.components.light import ColorMode
from custom_components.magic_lights.plugin_effects import Effect


CONFIG_SCHEMA = None
PLUGIN_DOMAIN = "static"
PLUGIN_NAME = "Static"
PLUGIN_DESCRIPTION = "Simple static light scene."


class Static(Effect):
    def __init__(self, async_call_service, entities: list[str], conf: dict[str, Any]):
        super().__init__(async_call_service, entities, conf)

        self.service_data: dict[str, dict] = {}

        if "preset" in self.conf:
            selected_preset = self.conf["preset"]
            self.magic.hass.loop.create_task(
                self.async_preset_setup(selected_preset))
        else:
            self.service_data = conf

    async def async_preset_setup(self, selected_preset: str):
        # Load presets
        presets = await load_presets()
        selected_preset_conf: dict[str, Any] | None = presets.get(
            selected_preset, None)
        if selected_preset_conf is None:
            self.log.error("Unknown preset '%s'", self.selected_preset)
            return

        # Go through all entities
        for entity in self.entities:
            # Check if entity is light
            domain = entity.rsplit(".")[0]
            if domain != "light":
                self.log.warning(
                    "Only Light Entites are supported within preset scenes for '%s' plugin currently.", PLUGIN_NAME)

            # Get State and detect light type
            state = self.magic.hass.states.get(entity)
            entity_supported_modes: list[ColorMode] = state.attributes.get(
                "supported_color_modes", [])

            if len(entity_supported_modes) == 0:
                self.log.warning(
                    "Entity %s does not specify its supported color modes. Defaulting to 'ONOFF'/ON.", entity
                )
                self.service_data[entity] = {}

            if set(entity_supported_modes).isdisjoint(selected_preset_conf):
                self.log.warning(
                    "Entity %s does not support preset '%s' with modes [%s]. Defaulting to 'ONOFF'/ON.", entity, selected_preset, entity_supported_modes
                )
                self.service_data[entity] = {}

            # Get the first supported mode
            for scene_supported_mode in selected_preset_conf:
                if scene_supported_mode in entity_supported_modes:
                    self.log.debug(
                        "Entity %s supports preset '%s' with mode '%s'", entity, selected_preset, scene_supported_mode
                    )
                    self.service_data[entity] = selected_preset_conf[scene_supported_mode][0]
                    break

        self.log.debug("Effect Static Initialized. Preset: %s, Number of Entities: %s",
                       selected_preset, str(len(self.service_data)))

    async def async_start(self):
        self.log.debug("Static Effect started."
                       f"\nConfig: {self.conf}"
                       f"\nEntities: {self.entities}")

        jobs = []
        for entity in self.entities:
            s_data = {"entity_id": entity}
            conf_data: str | dict[str, str] = self.service_data.get(
                entity, None)

            self.log.debug("Adjusting Entity: %s. Conf Data: %s",
                           entity, conf_data)
            if conf_data == "off":
                jobs.append(
                    self.async_call_service("light", "turn_off", s_data))
            else:
                s_data.update(conf_data)
                jobs.append(
                    self.async_call_service("light", "turn_on", s_data))

        await asyncio.gather(*jobs)


async def load_presets() -> dict:
    async with aiofiles.open(
        os.path.join(os.path.dirname(__file__), "values.json"), mode="r"
    ) as f:
        values = await f.read()

    return json.loads(values)
