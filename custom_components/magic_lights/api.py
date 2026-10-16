"""Public API for effect plugins; docs/plugins.md is the guide.

An effect is a stateless class: Magic Lights keeps one instance and calls
`render(ctx)` whenever the layer needs a frame: on start, rejoin, edit and
play/pause (`ctx.first`), when `next_update` is due, and at other times such
as a reshuffle or a restart. Rules:

- `render` is synchronous and side-effect free: no `hass`, no I/O, no
  sleeping; it returns within about 5 ms for 50 lights.
- Randomness only from `ctx.rng` (seeded per scene, target, layer and salt),
  never the `random` module, so previews and restarts deal the same way.
- Per-light scratch goes into `ctx.memory`, keyed by the ids in `ctx.members`
  so deals stay stable when a light is manual or unavailable. Memory survives
  param edits: index palettes with `% len(...)`.
- Return a state for every light you want set this frame; omitted lights keep
  their last commanded state, and an unchanged state is not resent.
- Set `effect`/`gradient` only after checking `light.caps` and always give a
  `color` beside them. White-only lights get the white nearest to your xy;
  give them a kelvin to choose it yourself.
- Derive animation phases from `ctx.t`, not from counting renders.
- `next_update` is clamped to at least `max(ctx.min_period, 0.1)`; use
  `transition = ctx.min_period` or your own step for smooth fades.
- An effect that can move or hold still has a `motion` param ("static" /
  "dynamic"; the UI's play/pause needs it) and honours
  `ctx.adjust.get("dynamic")` over it. Brightness is applied by the engine.
- Schemas map `vol.Required`/`vol.Optional` names to HA selectors only (plus
  `ColorSelector` and `PaletteSelector`); `description={"label", "helper",
  "visible"}` on the key feeds the panel's form.

Register from your integration's `async_setup` (manifest:
`"after_dependencies": ["magic_lights"]`) with `async_register_effect`.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_EFFECTS
from .effects import async_get_registry
from .effects.base import Effect, RenderContext
from .effects.schema import KELVIN, PCT, ColorSelector, PaletteSelector
from .models import OFF, Caps, Color, Frame, Light, LightState

__all__ = [
    "KELVIN",
    "OFF",
    "PCT",
    "Caps",
    "Color",
    "ColorSelector",
    "Effect",
    "Frame",
    "Light",
    "LightState",
    "PaletteSelector",
    "RenderContext",
    "async_register_effect",
]


@callback
def async_register_effect(hass: HomeAssistant, cls: type[Effect]) -> None:
    """Add an effect class; scenes using its key start rendering it.

    Raises ValueError for a duplicate key or a param that is not a selector.
    """
    async_get_registry(hass).add(cls, source=_source(cls))
    async_dispatcher_send(hass, SIGNAL_EFFECTS)


def _source(cls: type[Effect]) -> str:
    """Name the integration that defines `cls`."""
    parts = cls.__module__.split(".")
    if parts[0] == "custom_components" and len(parts) > 1:
        return parts[1]
    if parts[:2] == ["homeassistant", "components"] and len(parts) > 2:
        return parts[2]
    return parts[0]
