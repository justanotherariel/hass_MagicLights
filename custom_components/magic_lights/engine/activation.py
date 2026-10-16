"""Activations, their layer runs, and rendering one frame."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
import random
from typing import Any, NamedTuple

import voluptuous as vol

from homeassistant.core import Context

from ..const import MOTION_PARAM
from ..effects.base import Effect, RenderContext, as_frame
from ..models import (
    OFF,
    ActivationSnapshot,
    EffectLookup,
    Light,
    LightIndex,
    LightState,
    Scene,
    SunLocation,
    Target,
    TargetId,
)

_LOGGER = logging.getLogger(__name__)

# Reasons whose frame counts as the first of an activation, rejoin or edit.
FIRST_REASONS = frozenset({"start", "reclaim", "turn_on", "config", "available"})
# Reasons with a fixed default transition; others use the activation's.
_QUICK_REASONS = frozenset({"reclaim", "turn_on", "available"})
QUICK_TRANSITION = 0.5


def percent(value: float) -> int:
    """Round a brightness level to a whole percentage, 1 to 100."""
    return round(min(100, max(1, value)))


class Plan(NamedTuple):
    index: int
    effect: str
    params: Mapping[str, Any]
    members: tuple[str, ...]


def plan_runs(
    scene: Scene, target: Target, light_ids: Collection[str], lights: LightIndex
) -> tuple[list[Plan], set[str]]:
    """Assign each light to its last covering layer; return plans and uncovered."""
    layer_of: dict[str, int] = {}
    for index, layer in enumerate(scene.layers):
        for entity_id in lights.expand(layer.lights, target) & set(light_ids):
            layer_of[entity_id] = index
    plans = [
        Plan(
            index,
            layer.effect,
            layer.params,
            tuple(sorted(e for e, i in layer_of.items() if i == index)),
        )
        for index, layer in enumerate(scene.layers)
    ]
    return [p for p in plans if p.members], set(light_ids) - layer_of.keys()


def seeded_rng(
    scene_id: str, target_id: TargetId, index: int, salt: int
) -> random.Random:
    return random.Random(f"{scene_id}|{target_id}|{index}|{salt}")


@dataclass(slots=True, eq=False)
class LayerRun:
    index: int
    effect: Effect
    params: dict[str, Any]
    members: tuple[str, ...]  # every light of the layer in the activation, sorted
    rng: random.Random
    memory: dict[str, Any] = field(default_factory=dict)
    prev: dict[str, LightState] = field(default_factory=dict)
    # As `prev`, without dynamic steps: their jitter must not move the level shown.
    steady: dict[str, LightState] = field(default_factory=dict)
    due: float = math.inf  # monotonic time of the next render

    @property
    def animating(self) -> bool:
        return self.effect.dynamic and self.due < math.inf


@dataclass(slots=True, eq=False)
class Activation:
    id: str
    target_id: TargetId
    scene_id: str  # public id: the preset id for a customised copy
    scene: Scene  # as resolved for the target
    started: datetime
    transition: float  # the effective one: explicit, the scene's or the default
    transition_override: float | None = None  # given at activation
    adjust: dict[str, Any] = field(default_factory=dict)  # "scale", "dynamic"
    level: int | None = None  # wanted brightness until a frame sets the scale
    salt: int = 0
    draft: bool = False
    only_on: bool = False
    lights: set[str] = field(default_factory=set)
    manual: set[str] = field(default_factory=set)
    runs: list[LayerRun] = field(default_factory=list)
    uncovered: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    # What a draft took over: the activation it replaced, and the lights it
    # took from others.
    replaced: tuple[ActivationSnapshot, ...] = ()
    context: Context | None = None  # of the action that started it
    timer: Callable[[], None] | None = None
    adopting: bool = False  # restored, lights not all seen yet

    @property
    def active(self) -> set[str]:
        return self.lights - self.manual

    @property
    def scale(self) -> float:
        """Factor on the effects' brightness: the scene's, or set by an adjust."""
        return self.adjust.get("scale", self.scene.brightness / 100)

    def peak(self) -> float | None:
        """Brightest level the effects gave an active light, unscaled."""
        active = self.active
        return max(
            (
                state.brightness
                for run in self.runs
                for e in run.members
                if e in active
                and (state := run.steady.get(e)) is not None
                and state.on
                and state.brightness is not None
            ),
            default=None,
        )

    @property
    def brightness(self) -> int:
        """Effective level of the brightest active light, in percent."""
        if self.level is not None:
            return self.level
        peak = self.peak()
        return percent(self.scale * (1.0 if peak is None else peak) * 100)

    @property
    def dynamic(self) -> bool:
        """Whether a dynamic layer is animating right now."""
        return any(run.animating for run in self.runs)

    @property
    def motion(self) -> bool:
        """Whether the scene's colours are set to move (its ▶ state)."""
        if (dynamic := self.adjust.get("dynamic")) is not None:
            return dynamic
        return any(run.params.get(MOTION_PARAM) == "dynamic" for run in self.runs)

    def run_of(self, entity_id: str) -> LayerRun | None:
        for run in self.runs:
            if entity_id in run.members:
                return run
        return None

    def wants_on(self, entity_id: str) -> bool:
        if (run := self.run_of(entity_id)) is not None:
            return run.effect.key != "off"
        return not (entity_id in self.uncovered and self.scene.others == "off")

    def snapshot(self) -> ActivationSnapshot:
        return ActivationSnapshot(
            id=self.id,
            target=self.target_id,
            scene_id=self.scene_id,
            started=self.started,
            lights=tuple(sorted(self.lights)),
            manual=tuple(sorted(self.manual)),
            adjust=dict(self.adjust),
            only_on=self.only_on,
        )


def build_runs(
    activation: Activation,
    plans: Iterable[Plan],
    uncovered: set[str],
    effects: EffectLookup,
    keep: Callable[[LayerRun, Plan], bool] | None = None,
) -> None:
    """Create the activation's runs; reuse a run's state where `keep` allows."""
    old = {run.index: run for run in activation.runs}
    activation.runs = []
    activation.errors = []
    for plan in plans:
        label = f"layer {plan.index + 1}"
        if (effect := effects.get(plan.effect)) is None:
            activation.errors.append(f"{label}: unknown effect {plan.effect}")
            continue
        try:
            params = effect.validate(plan.params)
        except vol.Invalid as err:
            activation.errors.append(f"{label}: {err}")
            continue
        run = old.get(plan.index)
        if (
            run is not None
            and keep is not None
            and run.effect.key == effect.key
            and keep(run, plan)
        ):
            run.effect, run.params, run.members = effect, params, plan.members
        else:
            run = LayerRun(
                plan.index,
                effect,
                params,
                plan.members,
                seeded_rng(
                    activation.scene_id,
                    activation.target_id,
                    plan.index,
                    activation.salt,
                ),
            )
        activation.runs.append(run)
    activation.uncovered = uncovered


def render(
    activation: Activation,
    reason: str,
    *,
    lights: Mapping[str, Light],
    available: Callable[[str], bool],
    now: datetime,
    monotonic: float,
    sun: SunLocation,
    min_period: Callable[[list[Light]], float],
    only: Collection[str] | None = None,
    due_only: bool = False,
) -> dict[str, LightState]:
    """Render the activation's due runs; brightness and transitions applied."""
    active = activation.active

    def renderable(entity_id: str) -> bool:
        return (
            entity_id in active
            and entity_id in lights
            and available(entity_id)
            and (only is None or entity_id in only)
        )

    frame: dict[str, LightState] = {}
    t = (now - activation.started).total_seconds()
    for run in activation.runs:
        members = [lights[e] for e in run.members if renderable(e)]
        if not members:
            if only is None:
                run.due = math.inf
            continue
        if reason == "adjust":
            # A brightness change rescales what the effect last rendered.
            frame.update(
                {
                    e: state
                    for e in run.members
                    if renderable(e)
                    and (state := run.prev.get(e)) is not None
                    and state.on
                }
            )
            continue
        if due_only and run.due > monotonic + 0.005:
            continue
        period = min_period(members)
        ctx = RenderContext(
            lights=tuple(members),
            members=run.members,
            params=run.params,
            now=now,
            t=t,
            sun=sun,
            rng=run.rng,
            memory=run.memory,
            prev=run.prev,
            min_period=period,
            adjust=activation.adjust,
            first=reason in FIRST_REASONS,
        )
        try:
            result = as_frame(run.effect.render(ctx))
        except Exception:  # a plugin bug must not stop the other layers
            _LOGGER.exception("Effect %s failed to render", run.effect.key)
            run.due = math.inf
            continue
        rendered = {light.entity_id for light in members}
        states = {e: s for e, s in result.states.items() if e in rendered}
        run.prev = {**run.prev, **states}
        if reason != "periodic" or not run.effect.dynamic:
            run.steady = {**run.steady, **states}
        frame.update(states)
        due = (
            math.inf
            if result.next_update is None
            else monotonic + max(result.next_update, period, 0.1)
        )
        run.due = due if only is None else min(run.due, due)
    if activation.scene.others == "off" and reason != "adjust":
        frame.update({e: OFF for e in activation.uncovered if renderable(e)})

    if activation.level is not None and (peak := activation.peak()) is not None:
        # The brightest light gets the wanted level, the others follow it.
        activation.adjust["scale"] = activation.level / 100 / peak
        activation.level = None
    scale = activation.scale
    if reason == "periodic":
        transition = None
    elif reason in _QUICK_REASONS:
        transition = QUICK_TRANSITION
    else:
        transition = activation.transition
    out: dict[str, LightState] = {}
    for entity_id, state in frame.items():
        if state.on and state.brightness is not None:
            state = state.replace(
                brightness=min(1.0, max(1 / 255, state.brightness * scale))
            )
        # An adjust answers at once, whatever fade the effect last chose; a
        # firmware effect keeps its own (a Hue sunrise's is its duration).
        if transition is not None and (
            state.transition is None or (reason == "adjust" and not state.effect)
        ):
            state = state.replace(transition=transition)
        out[entity_id] = state
    return out
