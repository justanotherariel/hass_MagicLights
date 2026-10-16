"""Static: one colour (or white) and brightness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from ..models import Color, LightState
from .base import Effect, RenderContext
from .schema import PCT, ColorSelector


class Static(Effect):
    key = "static"
    name = "Static colour"
    description = "One colour or white and one brightness for every light."
    icon = "mdi:lightbulb"
    category = "color"
    schema = vol.Schema(
        {
            vol.Required(
                "color", default={"kelvin": 2700}, description={"label": "Colour"}
            ): ColorSelector(),
            vol.Required(
                "brightness", default=100, description={"label": "Brightness"}
            ): PCT,
        }
    )

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        p = ctx.params
        state = LightState(
            brightness=p["brightness"] / 100, color=Color.parse(p["color"])
        )
        return {light.entity_id: state for light in ctx.lights}

    @classmethod
    def swatches(cls, params: Mapping[str, Any]) -> list[str]:
        return [Color.parse(params["color"]).as_hex()]
