"""White: one colour temperature and brightness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from ..models import Color, LightState
from .base import Effect, RenderContext
from .schema import KELVIN, PCT


class White(Effect):
    key = "white"
    name = "White"
    description = "One colour temperature and brightness for every light."
    icon = "mdi:white-balance-sunny"
    category = "white"
    schema = vol.Schema(
        {
            vol.Required(
                "kelvin", default=2700, description={"label": "Colour temperature"}
            ): KELVIN(1500, 6500),
            vol.Required(
                "brightness", default=100, description={"label": "Brightness"}
            ): PCT,
        }
    )

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        p = ctx.params
        state = LightState(
            brightness=p["brightness"] / 100, color=Color(kelvin=round(p["kelvin"]))
        )
        return {light.entity_id: state for light in ctx.lights}

    @classmethod
    def swatches(cls, params: Mapping[str, Any]) -> list[str]:
        return [Color(kelvin=round(params["kelvin"])).as_hex()]
