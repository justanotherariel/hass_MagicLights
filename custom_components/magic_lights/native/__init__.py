"""Light drivers: capability detection and sending normalised states."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DOMAIN as LIGHT_DOMAIN,
    LightEntityFeature,
    brightness_supported,
    color_supported,
    color_temp_supported,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from ..models import Caps, Driver, Light, LightState
from .z2m_hue import Z2MHueDriver, mqtt_publish, z2m_command_topic

# Effect values that stop a running effect, in order of preference; "Solid"
# is WLED's resting effect.
STOP_EFFECTS: Final = ("off", "None", "none", "stop_hue_effect", "Solid")
# Zigbee and Z-Wave transition times are 16-bit deciseconds.
MAX_TRANSITION: Final = 6553


def stop_effect_value(caps: Caps) -> str | None:
    """Return the effect value that stops this light's running effect."""
    return next((value for value in STOP_EFFECTS if value in caps.effects), None)


def _transition(seconds: float) -> float:
    return round(min(seconds, MAX_TRANSITION), 2)


def to_service_data(
    norm: LightState, prev: LightState | None, caps: Caps
) -> tuple[str, dict[str, Any]]:
    """Return the light service and data for a normalised state."""
    data: dict[str, Any] = {}
    if not norm.on:
        if norm.transition is not None:
            data[ATTR_TRANSITION] = _transition(norm.transition)
        return SERVICE_TURN_OFF, data
    if norm.brightness is not None:
        data[ATTR_BRIGHTNESS] = max(1, round(norm.brightness * 255))
    if (color := norm.color) is not None:
        if color.kelvin is not None:
            data[ATTR_COLOR_TEMP_KELVIN] = color.kelvin
        else:
            data[ATTR_XY_COLOR] = list(color.as_xy())
    if norm.effect:
        data[ATTR_EFFECT] = norm.effect
    elif prev is not None and prev.effect and (stop := stop_effect_value(caps)):
        # Leaving a native effect: most lights keep running it otherwise.
        data[ATTR_EFFECT] = stop
    if norm.transition is not None:
        data[ATTR_TRANSITION] = _transition(norm.transition)
    return SERVICE_TURN_ON, data


def _capability_attributes(hass: HomeAssistant, entity_id: str) -> Mapping[str, Any]:
    if (state := hass.states.get(entity_id)) is not None and (
        ATTR_SUPPORTED_COLOR_MODES in state.attributes
    ):
        return state.attributes
    # Not set up yet (or a bare unavailable state): the registry keeps the
    # capabilities of the last run.
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return state.attributes if state is not None else {}
    return {
        **(entry.capabilities or {}),
        ATTR_SUPPORTED_FEATURES: entry.supported_features,
    }


class HaDriver:
    """Sends through the light services; works for every light."""

    key = "ha"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    @callback
    def detect(self, hass: HomeAssistant, entity_id: str) -> Caps:
        attrs = _capability_attributes(hass, entity_id)
        modes = attrs.get(ATTR_SUPPORTED_COLOR_MODES) or ()
        features = attrs.get(ATTR_SUPPORTED_FEATURES) or 0
        return Caps(
            brightness=brightness_supported(modes),
            color=color_supported(modes),
            color_temp=color_temp_supported(modes),
            min_kelvin=int(attrs.get(ATTR_MIN_COLOR_TEMP_KELVIN) or DEFAULT_MIN_KELVIN),
            max_kelvin=int(attrs.get(ATTR_MAX_COLOR_TEMP_KELVIN) or DEFAULT_MAX_KELVIN),
            transition=bool(features & LightEntityFeature.TRANSITION),
            effects=frozenset(attrs.get(ATTR_EFFECT_LIST) or ()),
        )

    async def send(
        self,
        light: Light,
        state: LightState,
        prev: LightState | None,
        context: Context,
    ) -> None:
        service, data = to_service_data(state, prev, light.caps)
        # Blocking, so integration errors reach the caller rather than HA's
        # log; callers send from their own task.
        await self._hass.services.async_call(
            LIGHT_DOMAIN,
            service,
            {ATTR_ENTITY_ID: light.entity_id, **data},
            blocking=True,
            context=context,
        )


def build_drivers(hass: HomeAssistant) -> dict[str, Driver]:
    """Return every driver by key; "ha" is the fallback of the others."""
    ha = HaDriver(hass)
    return {
        ha.key: ha,
        Z2MHueDriver.key: Z2MHueDriver(hass, z2m_command_topic, mqtt_publish, ha),
    }
