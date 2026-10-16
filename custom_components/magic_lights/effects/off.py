"""Off: switch the layer's lights off."""

from __future__ import annotations

from ..models import OFF, LightState
from .base import Effect, RenderContext


class Off(Effect):
    key = "off"
    name = "Off"
    description = "Switches the layer's lights off."
    icon = "mdi:lightbulb-off"

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        return {light.entity_id: OFF for light in ctx.lights}
