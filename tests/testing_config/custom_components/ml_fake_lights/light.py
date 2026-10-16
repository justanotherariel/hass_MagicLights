"""Configurable fake lights that record every command they receive.

Per light (all optional but `name`): `modes` (supported colour modes),
`min_kelvin`/`max_kelvin`, `effects` (effect list), `transition` (feature,
default true), `quantize` (report kelvin rounded through mireds, like
Zigbee), `jitter` (report brightness up to this far off), `delay` (seconds
before a command shows in the state), and the initial `on`, `brightness`,
`kelvin`, `xy`, `effect` and `available`.
"""

from __future__ import annotations

import random
import time
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import color as color_util

from . import DOMAIN

CALLS = f"{DOMAIN}_calls"


def calls(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return the command log shared by all fake lights."""
    return hass.data.setdefault(CALLS, [])


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict[str, Any] | None = None,
) -> None:
    """Create the lights listed under `lights:`."""
    async_add_entities(FakeLight(conf) for conf in config.get("lights", []))


class FakeLight(LightEntity):
    """A light that stores what it is told and logs each call."""

    _attr_should_poll = False

    def __init__(self, conf: dict[str, Any]) -> None:
        self._attr_name = conf["name"]
        self._attr_unique_id = conf.get(
            "unique_id", conf["name"].lower().replace(" ", "_")
        )
        modes = {ColorMode(m) for m in conf.get("modes", ["xy", "color_temp"])}
        self._attr_supported_color_modes = modes
        self._attr_min_color_temp_kelvin = conf.get("min_kelvin", 2000)
        self._attr_max_color_temp_kelvin = conf.get("max_kelvin", 6500)
        self._attr_effect_list = conf.get("effects")
        features = LightEntityFeature(0)
        if conf.get("transition", True):
            features |= LightEntityFeature.TRANSITION
        if self._attr_effect_list:
            features |= LightEntityFeature.EFFECT
        self._attr_supported_features = features
        self._quantize = conf.get("quantize", False)
        self._jitter = conf.get("jitter", 0)
        self._delay = conf.get("delay", 0.0)
        self._rng = random.Random(conf["name"])
        self._attr_available = conf.get("available", True)
        self._attr_is_on = conf.get("on", False)
        self._attr_brightness = conf.get(
            "brightness", 255 if self._attr_is_on else None
        )
        self._attr_effect = conf.get("effect")
        self._attr_color_mode = self._default_mode()
        if "kelvin" in conf and ColorMode.COLOR_TEMP in modes:
            self._set_kelvin(conf["kelvin"])
        elif "xy" in conf and ColorMode.XY in modes:
            self._attr_xy_color = tuple(conf["xy"])
            self._attr_color_mode = ColorMode.XY
        elif self._attr_color_mode == ColorMode.COLOR_TEMP:
            self._attr_color_temp_kelvin = 3000
        elif self._attr_color_mode == ColorMode.XY:
            self._attr_xy_color = (0.45, 0.41)
        elif self._attr_color_mode == ColorMode.HS:
            self._attr_hs_color = (30.0, 60.0)

    def _default_mode(self) -> ColorMode:
        for mode in (ColorMode.COLOR_TEMP, ColorMode.XY, ColorMode.HS, ColorMode.RGB):
            if mode in self.supported_color_modes:
                return mode
        return next(iter(self.supported_color_modes))

    def _set_kelvin(self, kelvin: int) -> None:
        if self._quantize:
            kelvin = color_util.color_temperature_mired_to_kelvin(
                color_util.color_temperature_kelvin_to_mired(kelvin)
            )
        self._attr_color_temp_kelvin = kelvin
        self._attr_color_mode = ColorMode.COLOR_TEMP

    def _log(self, service: str, kwargs: dict[str, Any]) -> None:
        calls(self.hass).append(
            {
                "t": time.monotonic(),
                "entity_id": self.entity_id,
                "service": service,
                "data": dict(kwargs),
                "context": self._context.id if self._context else None,
            }
        )

    def _report(self) -> None:
        """Write the state now, or after `delay` like a slow bus."""
        if self._jitter and self._attr_brightness is not None:
            self._attr_brightness = min(
                255,
                max(
                    1,
                    self._attr_brightness
                    + self._rng.randint(-self._jitter, self._jitter),
                ),
            )
        if self._delay:
            self.hass.loop.call_later(self._delay, self._write_later)
        else:
            self.async_write_ha_state()

    @callback
    def _write_later(self) -> None:
        if self.hass is not None and self.platform is not None:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._log("turn_on", kwargs)
        self._attr_is_on = True
        if ATTR_EFFECT in kwargs:
            effect = kwargs[ATTR_EFFECT]
            self._attr_effect = (
                None if effect in ("none", "off", "stop_hue_effect") else effect
            )
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        elif self._attr_brightness is None:
            self._attr_brightness = 255
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._set_kelvin(kwargs[ATTR_COLOR_TEMP_KELVIN])
        elif ATTR_XY_COLOR in kwargs:
            self._attr_xy_color = kwargs[ATTR_XY_COLOR]
            self._attr_color_mode = ColorMode.XY
        elif ATTR_HS_COLOR in kwargs:
            self._attr_hs_color = kwargs[ATTR_HS_COLOR]
            self._attr_color_mode = ColorMode.HS
        elif ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = kwargs[ATTR_RGB_COLOR]
            self._attr_color_mode = ColorMode.RGB
        self._report()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._log("turn_off", kwargs)
        self._attr_is_on = False
        self._report()

    @callback
    def physical_change(self, *, clear_context: bool = False, **state: Any) -> None:
        """Simulate a change made outside HA (wall switch, Hue app).

        Like a real light, the state write keeps the context of our last call
        unless `clear_context`: HA reuses it for 5 s.
        """
        if clear_context:
            self._context = None
        if "on" in state:
            self._attr_is_on = state["on"]
        if "brightness" in state:
            self._attr_brightness = state["brightness"]
        if "kelvin" in state:
            self._set_kelvin(state["kelvin"])
        if "xy" in state:
            self._attr_xy_color = tuple(state["xy"])
            self._attr_color_mode = ColorMode.XY
        if "effect" in state:
            self._attr_effect = state["effect"]
        self.async_write_ha_state()

    @callback
    def set_available(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()

    @callback
    def apply_mqtt(self, payload: dict[str, Any]) -> None:
        """Apply a Zigbee2MQTT `/set` payload, as the device's state echo would."""
        self._context = None
        if "state" in payload:
            self._attr_is_on = payload["state"] == "ON"
        if "effect" in payload:
            effect = payload["effect"]
            self._attr_effect = (
                None if effect in ("none", "stop_hue_effect") else effect
            )
            self._attr_is_on = True
        if "brightness" in payload:
            self._attr_brightness = payload["brightness"]
        if "color" in payload and ColorMode.XY in self.supported_color_modes:
            self._attr_xy_color = (payload["color"]["x"], payload["color"]["y"])
            self._attr_color_mode = ColorMode.XY
        self.async_write_ha_state()
