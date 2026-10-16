"""Palette: deal colours over the lights, optionally drifting between them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import random
from typing import Any

import voluptuous as vol

from homeassistant.helpers import selector

from ..color import hue_angle
from ..models import Color, Frame, LightState
from .base import Effect, RenderContext
from .schema import PCT, ColorSelector, PaletteSelector

# Hypfer's rule, in degrees of hue angle around the white point: a fade over
# more than the maximum passes close to white, one under the minimum is not seen.
_MIN_HUE_STEP = 2
_MAX_HUE_STEP = 150


def step_seconds(speed: float) -> float:
    """Seconds per colour step for Hue's speed 0..1 (1 is fastest)."""
    return min(120.0, max(2.0, 120.0 * (1 / 60) ** speed))


class Palette(Effect):
    key = "palette"
    name = "Colour palette"
    description = (
        "Deals the palette's colours over the lights; in dynamic mode every light"
        " drifts through the palette on its own."
    )
    icon = "mdi:palette"
    category = "color"
    dynamic = True
    schema = vol.Schema(
        {
            vol.Required(
                "palette",
                default=[{"xy": [0.5019, 0.4152]}],
                description={"label": "Colours"},
            ): PaletteSelector({"min": 1, "max": 9}),
            vol.Required(
                "brightness", default=60, description={"label": "Brightness"}
            ): PCT,
            vol.Required(
                "white",
                default={"kelvin": 2700},
                description={
                    "label": "White",
                    "helper": "For lights that only do white",
                },
            ): ColorSelector({"allow_color": False}),
            vol.Required(
                "motion", default="static", description={"label": "Motion"}
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["static", "dynamic"], mode="list"
                )
            ),
            vol.Required(
                "speed",
                default=0.6,
                description={
                    "label": "Speed",
                    "helper": "How fast the colours drift when they move, also when ▶ plays a static scene",
                },
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.05, max=1, step=0.05, mode="slider")
            ),
            vol.Required(
                "gradients",
                default=True,
                description={
                    "label": "Gradients",
                    "helper": "Spread the palette over gradient lights",
                },
            ): selector.BooleanSelector(),
        }
    )

    def render(self, ctx: RenderContext) -> Frame:
        p = ctx.params
        colors = [Color.parse(c) for c in p["palette"]]
        n = len(colors)
        base = p["brightness"] / 100
        white = Color.parse(p["white"])
        deal = self._deal(ctx, n)
        dynamic = ctx.adjust.get("dynamic")
        if dynamic is None:
            dynamic = p["motion"] == "dynamic"
        period = max(step_seconds(p["speed"]), ctx.min_period)
        steps: dict[str, dict[str, Any]] = ctx.memory.setdefault("steps", {})
        states: dict[str, LightState] = {}
        due: float | None = None
        for light in ctx.lights:
            e = light.entity_id
            if not light.caps.color:
                states[e] = LightState(brightness=base, color=white)
                continue
            if not dynamic:
                state = LightState(brightness=base, color=colors[deal[e] % n])
                points = light.caps.gradient_points
                if p["gradients"] and points and n > 1:
                    state = state.replace(
                        gradient=tuple(
                            colors[(deal[e] + k) % n] for k in range(min(n, points))
                        )
                    )
                states[e] = state
                continue
            step = steps.get(e)
            if step is None or ctx.first:
                # Start, rejoin or edit: the current colour at once, then a
                # random phase so the lights do not step in unison.
                index = deal[e] % n if step is None else step["color"] % n
                step = steps[e] = {
                    "color": index,
                    "until": ctx.t + ctx.rng.uniform(0.2 * period, period),
                }
                states[e] = LightState(brightness=base, color=colors[index])
            elif ctx.t >= step["until"]:
                step["color"] = _next(step["color"] % n, colors, ctx.rng)
                step["until"] = ctx.t + period * ctx.rng.uniform(0.8, 1.2)
                states[e] = LightState(
                    brightness=min(1.0, base * ctx.rng.uniform(0.9, 1.1)),
                    color=colors[step["color"]],
                    transition=0.8 * period,
                )
            else:
                states[e] = ctx.prev.get(e) or LightState(
                    brightness=base, color=colors[step["color"] % n]
                )
            due = step["until"] if due is None else min(due, step["until"])
        next_update = None if due is None else max(due - ctx.t, ctx.min_period)
        return Frame(states, next_update)

    @staticmethod
    def _deal(ctx: RenderContext, n: int) -> dict[str, int]:
        """Palette index per member: a shuffled deck, without replacement per round.

        Dealt over every member, so a light that is manual or unavailable now
        does not shift the others' colours. White-only lights get no card:
        they never show it, and colour lights would repeat colours instead.
        """
        white = {light.entity_id for light in ctx.lights if not light.caps.color}
        deal: dict[str, int] | None = ctx.memory.get("deal")
        if deal is None:
            cards = [e for e in ctx.members if e not in white]
            indices: list[int] = []
            while len(indices) < len(cards):
                block = list(range(n))
                ctx.rng.shuffle(block)
                indices += block
            deal = ctx.memory["deal"] = dict(zip(cards, indices, strict=False))
        for e in ctx.members:
            if e not in deal and e not in white:
                deal[e] = ctx.rng.randrange(n)
        return deal

    @classmethod
    def swatches(cls, params: Mapping[str, Any]) -> list[str]:
        return [Color.parse(c).as_hex() for c in params["palette"][:5]]


def _next(cur: int, colors: Sequence[Color], rng: random.Random) -> int:
    """Pick the next palette index; never one whose fade would cross white."""
    angle = hue_angle(colors[cur].as_xy())
    others = [i for i in range(len(colors)) if i != cur]
    ok = [
        i
        for i in others
        if _MIN_HUE_STEP
        <= abs((hue_angle(colors[i].as_xy()) - angle + 180) % 360 - 180)
        <= _MAX_HUE_STEP
    ]
    return rng.choice(ok or others or [cur])
