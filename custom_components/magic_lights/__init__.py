"""Light Scene Integration for better and dynamic Light Settings."""
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .const import DOMAIN
from .configuration_schema import magic_lights_config_schema
from .magicbase.core import magic_yaml_setup

PLATFORMS = ["sensor"]
# CONFIG_SCHEMA = vol.Schema(
#    {DOMAIN: magic_lights_config_schema()}, extra=vol.ALLOW_EXTRA
# )

# TODO:
# * Figure something out regarding Dependencies. I cannot list all light components to make sure all lights are loaded before my component starts.


async def async_setup(hass: HomeAssistant, config: dict):
    """Setup Magic Lights"""

    await magic_yaml_setup(hass, config)

    # Setup Components (Sensors)
    for component in PLATFORMS:
        hass.helpers.discovery.load_platform(component, DOMAIN, {}, config)

    return True
