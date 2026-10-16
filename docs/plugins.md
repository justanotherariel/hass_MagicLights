# Writing effects for Magic Lights

A scene is a list of layers; each layer runs one **effect** on some lights. The built-in effects (`off`, `white`, `static`, `palette`, `light_effect`, `adaptive`) use the same API that other integrations can use to add their own. A registered effect shows up in the scene editor with a form generated from its parameter schema; no frontend code is needed.

Everything you need is in `custom_components/magic_lights/api.py`.

## How an effect works

An effect is a class. Magic Lights keeps **one instance** and calls `render(ctx)` whenever the layer needs a frame, and `render` returns the state each light should have now:

- With `ctx.first` set: when the scene starts, when a light rejoins, when the scene is edited and when the user plays or holds its motion. Give every light its current state at once.
- With `ctx.first` not set: when the time your previous frame asked for has come, but also at other times, such as a reshuffle or Magic Lights taking a running scene back over after a restart. Derive an animation's phase from `ctx.t`, not from how often `render` was called.

A brightness change does not call `render`: Magic Lights rescales your last frame.

Magic Lights does the rest: it scales brightness by the scene's and the user's brightness, fits each state to what the light can do, skips states the light already has, and paces commands so the Zigbee (or other) network is not flooded. Lights the user changed by hand are not sent to.

## Registering an effect

In your integration's `manifest.json`:

```json
"after_dependencies": ["magic_lights"]
```

In its `__init__.py`:

```python
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    try:
        from custom_components.magic_lights.api import async_register_effect
    except ImportError:  # Magic Lights is not installed
        return True
    from .pulse import Pulse

    async_register_effect(hass, Pulse)
    return True
```

- Register once, from `async_setup` (not `async_setup_entry`): the registry lives as long as Home Assistant and a second registration of the same key raises `ValueError`.
- Registering before or after Magic Lights has started both work. Scenes that use your key render nothing (and show an error) until it is registered, then pick it up.
- `async_register_effect` raises `ValueError` for a duplicate key, a bad key or a parameter that is not a selector.

## The class

| Attribute | Meaning |
|---|---|
| `key` | `[a-z0-9_]+`, stored in scenes. Prefix it with your domain: `fancy_fx_pulse` |
| `name`, `description`, `icon` | Shown in the editor (`icon` is an `mdi:` name) |
| `category` | `"white"`, `"color"`, `"dynamic"`, `"adaptive"` or `"other"` |
| `dynamic` | `True` if the effect keeps changing the lights; such lights get a share of the bus |
| `schema` | `vol.Schema` of the parameters (below) |
| `render(ctx)` | Required. Returns `Frame(states, next_update)` or just a `{entity_id: LightState}` mapping |
| `swatches(params)` | Optional classmethod: up to 5 `#rrggbb` colours for scene cards |

`swatches` gets validated parameters (defaults filled in).

## Parameters

The schema maps `vol.Required` / `vol.Optional` names to Home Assistant **selectors**, and nothing else; anything else is rejected at registration, because the editor renders every field with `ha-selector`.

```python
vol.Required("speed", default=0.5, description={
    "label": "Speed",
    "helper": "1 is fastest",
    "visible": {"field": "pattern", "value": "wave"},
}): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=1, step=0.05, mode="slider"))
```

- `description` carries the field's `label`, `helper` text and `visible` condition (ha-form's format: `{"field", "value"}` or `{"condition": "and" | "or" | "not", "conditions": [...]}`). Without a label the key is humanised.
- `vol.Optional` without a default means the key may be missing from `ctx.params`.
- `api.py` adds:
  - `PCT`: a 1–100 % slider. Divide by 100 for `LightState.brightness`.
  - `KELVIN(lo=1500, hi=6500)`: a colour-temperature selector in kelvin.
  - `ColorSelector({"allow_white": True, "allow_color": True, "min_kelvin": 1000, "max_kelvin": 10000})`: one colour, stored as `{"kelvin": 2700}` or `{"xy": [x, y]}`. Read it with `Color.parse(value)`.
  - `PaletteSelector({"min": 1, "max": 9})`: a list of such colours.
- Numbers from `NumberSelector`, `PCT` and `KELVIN` arrive as `float`.

## RenderContext

| Field | Meaning |
|---|---|
| `lights` | The lights to render now (`Light`: `entity_id`, `name`, `area_id`, `caps`, `bus`), sorted by entity id. Manual and unavailable lights are left out |
| `members` | Entity ids of **all** of the layer's lights, sorted, including manual and unavailable ones. Deal colours over these |
| `params` | Validated parameters, defaults filled in |
| `now` | Current time, aware UTC (a fake clock in previews) |
| `t` | Seconds since the scene started |
| `sun` | `SunLocation(observer, tz)` for `astral` |
| `rng` | `random.Random` seeded from scene, target, layer and the user's "Reshuffle" count |
| `memory` | A dict for your scratch data, `{}` at the start; kept across frames and parameter edits, never saved |
| `prev` | The states this layer returned last, by entity id |
| `min_period` | Seconds between two commands one light can get on its bus right now |
| `adjust` | `"dynamic"` (bool, may be missing): the user's play/pause. Other keys are Magic Lights' own; brightness is already applied for you |
| `first` | `True` for the first frame of a start, a rejoin, an edit or a play/pause |

`Light.caps` (`Caps`): `brightness`, `color`, `color_temp`, `min_kelvin`, `max_kelvin`, `transition`, `effects` (the light's `effect_list`), `gradient_points` (0 = no gradient). `light.supports_effect(name)` checks `effects`.

## LightState and Frame

`LightState(on=True, brightness=None, color=None, effect=None, effect_speed=None, gradient=None, transition=None)`; `OFF` is `LightState(on=False)`.

- `brightness` is 0..1; `color` is a `Color(kelvin=…)` or `Color(xy=(x, y))`; `transition` is seconds, `None` lets Magic Lights pick (the scene's transition on start, a short one on rejoin).
- `effect` is a firmware effect name from `caps.effects`; `effect_speed` is 0..1; `gradient` is a tuple of colours for gradient lights.

`Frame(states, next_update)`: `next_update` is the number of seconds until you want to render again, or `None` if the frame holds until something changes.

## Rules

- `render` is synchronous and pure: no `hass`, no I/O, no sleeping, no global state. It must return within about 5 ms for 50 lights.
- Use `ctx.rng`, never the `random` module: previews, restarts and the "Reshuffle" button rely on it.
- Keep per-light data in `ctx.memory`, keyed by entity id and dealt over `ctx.members`, so a light going manual does not change the others. Memory survives parameter edits, so index lists defensively (`i % len(palette)`).
- Return a state for every light you want set. Omitted lights keep what they were last sent; returning an unchanged state costs nothing (it is not resent).
- Set `effect` or `gradient` only after checking `light.caps`, and always give a `color` beside them for lights or paths that cannot use them. White-only lights get the white nearest to your xy; give them a kelvin to choose it yourself.
- `next_update` is raised to at least `max(ctx.min_period, 0.1)`. Plan your steps with `ctx.min_period` in mind and use it (or your step length) as `transition` for smooth fades.
- If the effect can move or hold still, give it a parameter named `motion` with the values `"static"` and `"dynamic"`, and honour `ctx.adjust.get("dynamic")` over it (`True` plays, `False` holds, `None` means as configured). The panel and the card offer play/pause only for effects with such a `motion` parameter; the `activate_scene` and `set_dynamic` services set `dynamic` for any scene. Play moves a scene saved as `"static"` too, so do not hide the fields motion uses behind `motion`: the editor drops the values of hidden fields.

## A complete example

`custom_components/fancy_fx/pulse.py`:

```python
"""Pulse: brightness swells and fades, each light out of step with the others."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.helpers import selector

from custom_components.magic_lights.api import (
    PCT,
    Color,
    ColorSelector,
    Effect,
    Frame,
    LightState,
    RenderContext,
)


class Pulse(Effect):
    key = "fancy_fx_pulse"
    name = "Pulse"
    description = "Brightness swells and fades; every light has its own rhythm."
    icon = "mdi:heart-pulse"
    category = "dynamic"
    dynamic = True
    schema = vol.Schema(
        {
            vol.Required("color", default={"kelvin": 2700}, description={"label": "Colour"}): ColorSelector(),
            vol.Required("low", default=20, description={"label": "Lowest brightness"}): PCT,
            vol.Required("high", default=80, description={"label": "Highest brightness"}): PCT,
            vol.Required("motion", default="dynamic", description={"label": "Motion"}): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["static", "dynamic"], mode="list")
            ),
            vol.Required(
                "period",
                default=20,
                description={
                    "label": "Seconds per pulse",
                    "helper": "Busy networks stretch it",
                },
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=4, max=120, step=1, unit_of_measurement="s", mode="slider")
            ),
        }
    )

    def render(self, ctx: RenderContext) -> Frame:
        p = ctx.params
        color = Color.parse(p["color"])
        dynamic = ctx.adjust.get("dynamic")
        if dynamic is None:
            dynamic = p["motion"] == "dynamic"
        if not dynamic:  # held: the high level
            state = LightState(brightness=p["high"] / 100, color=color)
            return Frame({light.entity_id: state for light in ctx.lights})
        half = max(p["period"] / 2, ctx.min_period)  # never faster than the bus allows
        phase = ctx.memory.setdefault("phase", {})
        for entity_id in ctx.members:  # over all members: stable when lights come and go
            phase.setdefault(entity_id, ctx.rng.randrange(2))
        states = {}
        for light in ctx.lights:
            e = light.entity_id
            # From the clock, not by counting renders: an extra render (a
            # reshuffle, a restart) must not turn the pulse around.
            up = (round(ctx.t / half) + phase[e]) % 2 == 0
            level = p["high"] if up else p["low"]
            # White-only lights get the colour as the nearest white; on/off-only
            # lights simply stay on.
            states[e] = LightState(
                brightness=level / 100, color=color, transition=None if ctx.first else half
            )
        return Frame(states, next_update=half)

    @classmethod
    def swatches(cls, params: Mapping[str, Any]) -> list[str]:
        return [Color.parse(params["color"]).as_hex()]
```

## Testing without Home Assistant

`render` needs no running Home Assistant, so a plain unit test can build the context:

```python
import random
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from astral import Observer

from custom_components.magic_lights.api import Caps, Light, RenderContext
from custom_components.magic_lights.models import Bus, BusLimits, SunLocation

bus = Bus("test", "zigbee", "Test", BusLimits(rate=10, burst=20, min_interval=0.5))
lamp = Light("light.lamp", "Lamp", None, None, bus, Caps(brightness=True, color=True, color_temp=True))
ctx = RenderContext(
    lights=(lamp,), members=("light.lamp",), params=Pulse.validate({}),
    now=datetime.now(UTC), t=0.0, sun=SunLocation(Observer(52.5, 13.4), ZoneInfo("Europe/Berlin")),
    rng=random.Random("seed"), memory={}, prev={}, min_period=0.5, adjust={}, first=True,
)
frame = Pulse().render(ctx)
```

Render again with the same `memory` and `rng`, a larger `t` and `first=False` to step through time. The editor's preview does the same with a fake clock.
