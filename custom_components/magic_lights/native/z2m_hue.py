"""Philips Hue lights on Zigbee2MQTT: native effects and gradients over raw MQTT.

HA's MQTT light can start an effect but cannot set its speed and colour in one
go, nor a gradient; Z2M accepts both on the device's `/set` topic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import dataclasses
import json
from typing import Any, Final

from homeassistant.components.light import ATTR_EFFECT
from homeassistant.const import STATE_ON
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from ..models import Caps, Color, Driver, Light, LightState

ORIGIN: Final = "Zigbee2MQTT"
# Only Z2M's Philips definitions list this effect value.
HUE_MARKER: Final = "stop_hue_effect"
GRADIENT_POINTS: Final = 9
STOP: Final = {"effect": "none"}
# Hue lights switch off at brightness 1.
MIN_BRIGHTNESS: Final = 2

type ResolveTopic = Callable[[HomeAssistant, str], str | None]
type Publish = Callable[[HomeAssistant, str, str], Awaitable[None]]


def _discovered_topic(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the command topic from the stored discovery payload (MQTT internals)."""
    try:
        from homeassistant.components.mqtt.models import DATA_MQTT  # noqa: PLC0415

        info = hass.data[DATA_MQTT].debug_info_entities.get(entity_id) or {}
        payload = (info.get("discovery_data") or {}).get("discovery_payload") or {}
        origin = payload.get("origin") or {}
        topic = payload.get("command_topic")
    except Exception:  # noqa: BLE001 - internal API; the registry fallback remains
        return None
    if origin.get("name") != ORIGIN or not isinstance(topic, str):
        return None
    return topic


@callback
def z2m_command_topic(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the Z2M `/set` topic of an MQTT light, or None."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != "mqtt":
        return None
    if (topic := _discovered_topic(hass, entity_id)) is not None:
        return topic
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    ieee = next(
        (
            value.removeprefix("zigbee2mqtt_")
            for domain, value in (device.identifiers if device else ())
            if domain == "mqtt" and value.startswith("zigbee2mqtt_0x")
        ),
        None,
    )
    # Z2M unique ids are "<ieee>_light_<base topic>", and Z2M accepts the IEEE
    # address in place of the friendly name.
    prefix = f"{ieee}_light_"
    if ieee is None or not entry.unique_id.startswith(prefix):
        return None
    return f"{entry.unique_id.removeprefix(prefix)}/{ieee}/set"


async def mqtt_publish(hass: HomeAssistant, topic: str, payload: str) -> None:
    """Publish through HA's MQTT client (imported late: paho is optional)."""
    from homeassistant.components import mqtt  # noqa: PLC0415

    await mqtt.async_publish(hass, topic, payload)


def _brightness(value: float) -> int:
    return max(MIN_BRIGHTNESS, round(value * 255))


def _xy(color: Color) -> dict[str, float]:
    x, y = color.as_xy()
    return {"x": round(x, 4), "y": round(y, 4)}


def _effect_frame(state: LightState, caps: Caps) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": "ON", "effect": state.effect}
    if state.effect_speed is not None:
        payload["effect_speed"] = round(state.effect_speed, 3)
    if state.color is not None and caps.color:
        payload["color"] = _xy(state.color)
    return payload


def hue_payloads(
    state: LightState,
    prev: LightState | None,
    caps: Caps,
    running: str | None = None,
) -> list[dict[str, Any]]:
    """Return the MQTT messages for a normalised state, in send order.

    `running` is the effect the light reports now. The driver sends plain
    on/off and colour states through HA's light service after these.
    """
    stop = [dict(STOP)] if prev is not None and prev.effect else []
    if not state.on:
        return stop
    if state.effect:
        frame = _effect_frame(state, caps)
        # Sending an effect restarts it and resets brightness, so one that
        # already runs as asked only gets the brightness. `prev` alone may be
        # stale after a change by hand.
        unchanged = (
            running == state.effect
            and prev is not None
            and prev.on
            and _effect_frame(prev, caps) == frame
        )
        out = [] if unchanged else [frame]
        # Z2M applies brightness before the effect within one message.
        if state.brightness is not None:
            out.append({"brightness": _brightness(state.brightness)})
        return out
    if state.gradient:
        payload = {
            "state": "ON",
            "gradient": [color.as_hex() for color in state.gradient],
            "gradient_style": "linear",
        }
        if state.brightness is not None:
            payload["brightness"] = _brightness(state.brightness)
        if state.transition:
            payload["transition"] = round(state.transition, 1)
        return [*stop, payload]
    return stop


def _running_effect(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None or state.state != STATE_ON:
        return None
    return state.attributes.get(ATTR_EFFECT)


def _has_gradient(hass: HomeAssistant, entity_id: str) -> bool:
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.device_id is None:
        return False
    # Gradient models get gradient/gradient_scene/gradient_style entities, with
    # unique ids "<ieee>_gradient…_<base topic>".
    return any(
        "_gradient_" in other.unique_id
        for other in er.async_entries_for_device(
            registry, entry.device_id, include_disabled_entities=True
        )
        if other.entity_id != entity_id
    )


class Z2MHueDriver:
    """Native effects and gradients for Hue lights on Z2M; the rest via `fallback`."""

    key = "z2m_hue"

    def __init__(
        self,
        hass: HomeAssistant,
        resolve_topic: ResolveTopic,
        publish: Publish,
        fallback: Driver,
    ) -> None:
        self._hass = hass
        self._resolve_topic = resolve_topic
        self._publish = publish
        self._fallback = fallback
        self.topics: dict[str, str] = {}

    @callback
    def detect(self, hass: HomeAssistant, entity_id: str) -> Caps | None:
        caps = self._fallback.detect(hass, entity_id)
        if caps is None or HUE_MARKER not in caps.effects:
            return None
        if (topic := self._resolve_topic(hass, entity_id)) is None:
            return None
        self.topics[entity_id] = topic
        points = GRADIENT_POINTS if _has_gradient(hass, entity_id) else 0
        return dataclasses.replace(caps, driver=self.key, gradient_points=points)

    async def send(
        self,
        light: Light,
        state: LightState,
        prev: LightState | None,
        context: Context,
    ) -> None:
        if (topic := self.topics.get(light.entity_id)) is None:
            await self._fallback.send(light, state, prev, context)
            return
        running = _running_effect(self._hass, light.entity_id)
        for payload in hue_payloads(state, prev, light.caps, running):
            await self._publish(self._hass, topic, json.dumps(payload))
        if not state.on or not (state.effect or state.gradient):
            # Any stop went out above; HA must not repeat it after the new colour.
            await self._fallback.send(light, state, None, context)
