from custom_components.magic_lights.setup_tasks.task_manager import TaskManager
from custom_components.magic_lights.data_structures.magic import Magic
import logging
from homeassistant.core import callback

from custom_components.magic_lights.const import DOMAIN
from custom_components.magic_lights.magicbase.share import get_magic

_LOGGER = logging.getLogger(__name__)


async def magic_yaml_setup(hass, config):
    magic = get_magic()
    hass.data.update({DOMAIN: magic})

    # Populate Magic object
    magic.hass = hass
    magic.raw = config[DOMAIN]

    # Setup Integration
    task_manager = TaskManager()
    await task_manager.async_load()

    # Register Hass Services
    register_services(magic)

    return True


def register_services(magic: Magic):
    """Register Hass Services."""

    @callback
    async def activate_scene(call):
        magic.scene_manager.init_scene(call.data["zone"], call.data["scene"])

    @callback
    async def toggle_scene(call):
        if magic.living_space[call.data["zone"]].current_scene != call.data["scene"]:
            magic.scene_manager.init_scene(call.data["zone"], call.data["scene"])
        else:
            magic.scene_manager.init_scene(call.data["zone"], "off")

    @callback
    async def enable_entity(call):
        pass

    @callback
    async def disable_entity(call):
        pass

    magic.hass.services.async_register(DOMAIN, "activate_scene", activate_scene)
    magic.hass.services.async_register(DOMAIN, "toggle_scene", toggle_scene)

    # magic.hass.services.async_register(DOMAIN, "enable_entity", enable_entity)
    # magic.hass.services.async_register(DOMAIN, "disable_entity", disable_entity)
