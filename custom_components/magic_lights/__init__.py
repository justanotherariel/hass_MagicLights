"""Light Scene Integration for better and dynamic Light Settings."""
import asyncio

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .living_space import LivingSpace


PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Setup House"""

    # Setup the Living Space and it's Light Scene Logic
    living_space = LivingSpace(hass, config[DOMAIN])
    hass.data[DOMAIN] = {}
    hass.data[DOMAIN].update({"living_space": living_space})

    # Setup Components (Sensors)
    for component in PLATFORMS:
        hass.helpers.discovery.load_platform(component, DOMAIN, {}, config)

    return True
