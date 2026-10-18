"""Adaptive: colour temperature, and brightness if wanted, follow the sun."""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from datetime import time, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.helpers import config_validation as cv, selector

from ..models import Color, Frame, LightState
from .adaptive_curve import AdaptiveParams, adaptive_values
from .base import Effect, RenderContext
from .schema import KELVIN, PCT

# Offsets or clamps can put the sun's events out of order; then use the plain sun.
_UNSHIFTED = {
    "sunrise_offset": timedelta(),
    "sunset_offset": timedelta(),
    "min_sunrise": None,
    "max_sunrise": None,
    "min_sunset": None,
    "max_sunset": None,
}

# Kelvin follows the sun in every mode, so only the brightness fields depend on it.
_STATIC = {"field": "brightness_mode", "value": "static"}
_CURVE = {"field": "brightness_mode", "operator": "not_eq", "value": "static"}
_RAMPS = {"field": "brightness_mode", "operator": "in", "value": ["linear", "tanh"]}


def _number(lo: float, hi: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=lo, max=hi, step=1, unit_of_measurement=unit, mode="slider"
        )
    )


def _steps(short: int, medium: int, long: int) -> list[dict[str, Any]]:
    return [
        {"value": short, "label": f"Short {short} min"},
        {"value": medium, "label": f"Medium {medium} min"},
        {"value": long, "label": f"Long {_hours(long)}"},
    ]


def _hours(minutes: int) -> str:
    return f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"


def _time(value: str | time | None) -> time | None:
    return None if value is None else cv.time(value)


def _curve_params(params: Mapping[str, Any]) -> AdaptiveParams:
    return AdaptiveParams(
        brightness=params["brightness"],
        min_brightness=params["min_brightness"],
        max_brightness=params["max_brightness"],
        min_kelvin=round(params["min_kelvin"]),
        max_kelvin=round(params["max_kelvin"]),
        brightness_mode=params["brightness_mode"],
        ramp_dark=timedelta(minutes=params["ramp_dark"]),
        ramp_light=timedelta(minutes=params["ramp_light"]),
        sunrise_offset=timedelta(minutes=params["sunrise_offset"]),
        sunset_offset=timedelta(minutes=params["sunset_offset"]),
        min_sunrise=_time(params.get("min_sunrise")),
        max_sunrise=_time(params.get("max_sunrise")),
        min_sunset=_time(params.get("min_sunset")),
        max_sunset=_time(params.get("max_sunset")),
    )


class Adaptive(Effect):
    key = "adaptive"
    name = "Adaptive"
    description = (
        "Colour temperature follows the sun, like Adaptive Lighting; brightness"
        " can follow it too."
    )
    icon = "mdi:theme-light-dark"
    category = "adaptive"
    schema = vol.Schema(
        {
            vol.Required(
                "brightness_mode",
                default="static",
                description={
                    "label": "Brightness curve",
                    "hints": {
                        "static": "One brightness all day",
                        "default": "Day level until sunset, down to Night by"
                        " midnight, back up by sunrise",
                        "linear": "Even ramps between Night and Day around"
                        " sunrise and sunset",
                        "tanh": "Gentle ramps between Night and Day around"
                        " sunrise and sunset",
                    },
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value="static", label="None (static)"
                        ),
                        selector.SelectOptionDict(
                            value="default", label="Dim after sunset"
                        ),
                        selector.SelectOptionDict(value="linear", label="Linear"),
                        selector.SelectOptionDict(value="tanh", label="Smooth"),
                    ]
                )
            ),
            vol.Required(
                "brightness",
                default=100,
                description={"label": "Brightness", "visible": _STATIC},
            ): PCT,
            vol.Required(
                "min_brightness",
                default=40,
                description={"label": "Night", "visible": _CURVE, "row": "levels"},
            ): PCT,
            vol.Required(
                "max_brightness",
                default=100,
                description={"label": "Day", "visible": _CURVE, "row": "levels"},
            ): PCT,
            vol.Required(
                "ramp_dark",
                default=60,
                description={
                    "label": "Ramp on the dark side",
                    "helper": "Minutes after sunset and before sunrise",
                    "visible": _RAMPS,
                    "row": "ramps",
                    "steps": _steps(30, 60, 120),
                },
            ): _number(5, 240, "min"),
            vol.Required(
                "ramp_light",
                default=90,
                description={
                    "label": "Ramp on the light side",
                    "helper": "Minutes before sunset and after sunrise",
                    "visible": _RAMPS,
                    "row": "ramps",
                    "steps": _steps(45, 90, 180),
                },
            ): _number(5, 240, "min"),
            vol.Required(
                "min_kelvin",
                default=2200,
                description={
                    "label": "Warmest white",
                    "pair": {"max": "max_kelvin", "label": "Warmest ↔ coolest white"},
                },
            ): KELVIN(1000, 10000),
            vol.Required(
                "max_kelvin", default=5000, description={"label": "Coolest white"}
            ): KELVIN(1000, 10000),
            vol.Required(
                "sunrise_offset",
                default=0,
                description={"label": "Sunrise offset", "advanced": True},
            ): _number(-180, 180, "min"),
            vol.Required(
                "sunset_offset",
                default=0,
                description={"label": "Sunset offset", "advanced": True},
            ): _number(-180, 180, "min"),
            vol.Optional(
                "min_sunrise",
                description={"label": "Sunrise not before", "advanced": True},
            ): selector.TimeSelector(),
            vol.Optional(
                "max_sunrise",
                description={"label": "Sunrise not after", "advanced": True},
            ): selector.TimeSelector(),
            vol.Optional(
                "min_sunset",
                description={"label": "Sunset not before", "advanced": True},
            ): selector.TimeSelector(),
            vol.Optional(
                "max_sunset",
                description={"label": "Sunset not after", "advanced": True},
            ): selector.TimeSelector(),
            vol.Required(
                "interval",
                default=90,
                description={"label": "Update every", "advanced": True},
            ): _number(30, 600, "s"),
            vol.Required(
                "transition",
                default=45,
                description={"label": "Glide between updates", "advanced": True},
            ): _number(1, 120, "s"),
        }
    )

    def render(self, ctx: RenderContext) -> Frame:
        p = ctx.params
        # Magic Lights picks a first frame's fade: the scene's on a switch.
        transition: float | None = None
        when = ctx.now
        if not ctx.first:
            transition = float(p["transition"])
            # Aim at the end of the glide, so the light is on the curve when it lands.
            when += timedelta(seconds=transition)
        curve = _curve_params(p)
        try:
            brightness, kelvin = adaptive_values(when, ctx.sun, curve)
        except ValueError:
            brightness, kelvin = adaptive_values(
                when, ctx.sun, dataclasses.replace(curve, **_UNSHIFTED)
            )
        state = LightState(
            brightness=brightness / 100,
            color=Color(kelvin=kelvin),
            transition=transition,
        )
        return Frame(
            {light.entity_id: state for light in ctx.lights}, float(p["interval"])
        )

    @classmethod
    def swatches(cls, params: Mapping[str, Any]) -> list[str]:
        return [
            Color(kelvin=round(params["min_kelvin"])).as_hex(),
            Color(kelvin=round(params["max_kelvin"])).as_hex(),
        ]
