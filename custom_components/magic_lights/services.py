"""Entity services on the target selects; the select implements each one."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, service

from .const import DOMAIN

BRIGHTNESS = vol.All(vol.Coerce(float), vol.Range(min=1, max=100))
TRANSITION = vol.All(vol.Coerce(float), vol.Range(min=0, max=300))
CYCLE = {vol.Optional("cycle", default=True): cv.boolean}

SCHEMAS: dict[str, vol.Schema | dict] = {
    "activate_scene": {
        vol.Required("scene"): cv.string,
        vol.Optional("brightness"): BRIGHTNESS,
        vol.Optional("dynamic"): cv.boolean,
        vol.Optional("transition"): TRANSITION,
    },
    "turn_off": {vol.Optional("transition"): TRANSITION},
    "toggle_scene": {
        vol.Required("scene"): cv.string,
        vol.Optional("brightness"): BRIGHTNESS,
        vol.Optional("dynamic"): cv.boolean,
    },
    "next_scene": CYCLE,
    "previous_scene": CYCLE,
    "adjust_brightness": vol.All(
        cv.make_entity_service_schema(
            {
                vol.Exclusive("step", "change"): vol.All(
                    vol.Coerce(float), vol.Range(min=-100, max=100)
                ),
                vol.Exclusive("brightness", "change"): BRIGHTNESS,
            }
        ),
        cv.has_at_least_one_key("step", "brightness"),
    ),
    "set_dynamic": {vol.Required("dynamic"): cv.boolean},
    "release": {},
    # Not `entity_id`: that is the select the call targets.
    "reclaim": {vol.Optional("light"): cv.entity_domain(Platform.LIGHT)},
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    for name, schema in SCHEMAS.items():
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=Platform.SELECT,
            schema=schema,
            func=f"async_{name}",
        )
