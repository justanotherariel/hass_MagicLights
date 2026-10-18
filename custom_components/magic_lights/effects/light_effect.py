"""Light effect: a firmware effect where the light has it, emulated flames otherwise."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

import voluptuous as vol

from homeassistant.helpers import selector

from ..color import NO_EFFECT
from ..models import Color, Frame, LightState
from .base import Effect, RenderContext
from .schema import PCT, ColorSelector

# Hue's scene effects; its identify effects (blink, breathe…) only flash the
# light once, and can still be typed in.
HUE_EFFECTS = [
    "candle",
    "fireplace",
    "colorloop",
    "sunset",
    "sunrise",
    "sparkle",
    "opal",
    "glisten",
    "underwater",
    "cosmos",
    "sunbeam",
    "enchant",
]

# The Hue bridge's names for Zigbee2MQTT's, and back.
_ALIASES = {
    "fireplace": "fire",
    "fire": "fireplace",
    "colorloop": "prism",
    "prism": "colorloop",
}
# Hue's timed effects; on a Hue bridge the transition sets their duration.
_TIMED = frozenset({"sunrise", "sunset"})

FLAMES: dict[str, tuple[tuple[float, float], ...]] = {
    "candle": ((0.585, 0.395), (0.561, 0.4042), (0.5268, 0.4133)),
    "fireplace": ((0.64, 0.348), (0.605, 0.38), (0.561, 0.4042)),
}


def _native_name(name: str, effects: Collection[str]) -> str | None:
    """Return the light's own spelling of effect `name`, or None if it lacks it."""
    if name in effects:
        return name
    key = name.casefold()
    wanted = {key, _ALIASES.get(key, key)}
    return next((e for e in sorted(effects) if e.casefold() in wanted), None)


def _flames(name: str) -> tuple[tuple[float, float], ...] | None:
    key = name.casefold()
    return FLAMES.get(key) or FLAMES.get(_ALIASES.get(key, key))


def timed_duration(speed: float) -> float:
    """Seconds of a sunrise or sunset: 1 min at speed 0.75 and up, 2 h at 0.2.

    Log-linear between, which matches the speed bytes a Hue bridge sends.
    """
    return min(7200.0, max(60.0, 60 * 120 ** ((0.75 - speed) / 0.55)))


class LightEffect(Effect):
    key = "light_effect"
    name = "Light effect"
    description = (
        "A firmware effect (Hue: candle, fireplace, sunrise, …). Candle and"
        " fireplace are emulated on lights without it."
    )
    icon = "mdi:candle"
    category = "dynamic"
    dynamic = True
    schema = vol.Schema(
        {
            vol.Required(
                "effect", default="candle", description={"label": "Effect"}
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=HUE_EFFECTS, custom_value=True)
            ),
            vol.Required(
                "brightness", default=35, description={"label": "Brightness"}
            ): PCT,
            vol.Required(
                "speed",
                default=0.5,
                description={
                    "label": "Speed",
                    "helper": "sunrise/sunset: 1.0 ≈ 1 min … 0.2 ≈ 2 h",
                },
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1, step=0.05, mode="slider")
            ),
            vol.Required(
                "color",
                default={"xy": [0.561, 0.4042]},
                description={
                    "label": "Colour",
                    "helper": "Used where the effect takes a colour, and by lights without it",
                },
            ): ColorSelector(),
        }
    )

    def render(self, ctx: RenderContext) -> Frame:
        p = ctx.params
        name = p["effect"]
        speed = p["speed"]
        base = p["brightness"] / 100
        color = Color.parse(p["color"])
        flames = _flames(name)
        jitter = 0.15 + 0.35 * speed
        # Slow, gliding steps: each one costs a radio message, a recorder row
        # and a push to every open frontend per light.
        step = max(ctx.rng.uniform(1.5, 3.0) * (1.5 - speed), ctx.min_period)
        timed = timed_duration(speed) if name.casefold() in _TIMED else None
        states: dict[str, LightState] = {}
        emulated = False
        for light in ctx.lights:
            e = light.entity_id
            native = (
                None if name in NO_EFFECT else _native_name(name, light.caps.effects)
            )
            if native is not None:
                # Later frames repeat this state, so the diff keeps the bus
                # quiet while the firmware animates.
                states[e] = LightState(
                    brightness=base,
                    color=color,
                    effect=native,
                    effect_speed=speed,
                    transition=timed,
                )
            elif flames is not None:
                emulated = True
                flame = (
                    Color(xy=flames[ctx.rng.randrange(len(flames))])
                    if light.caps.color
                    else Color(kelvin=2000)
                )
                brightness = base * (1 + ctx.rng.uniform(-jitter, jitter))
                states[e] = LightState(
                    brightness=min(1.0, max(0.02, brightness)),
                    color=flame,
                    # Magic Lights picks a first frame's fade.
                    transition=None if ctx.first else step,
                )
            else:
                states[e] = LightState(brightness=base, color=color)
        return Frame(states, step if emulated else None)

    @classmethod
    def swatches(cls, params: Mapping[str, Any]) -> list[str]:
        flames = _flames(params["effect"])
        if flames is None:
            return [Color.parse(params["color"]).as_hex()]
        return [Color(xy=xy).as_hex() for xy in flames]
