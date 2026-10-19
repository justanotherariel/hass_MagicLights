"""Render a scene on a target over time without sending anything."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import math
from typing import Any

from ..models import (
    Color,
    EffectLookup,
    LightIndex,
    LightState,
    Scene,
    SunLocation,
    Target,
    normalise,
)
from .activation import Activation, build_runs, percent, plan_runs, render

MAX_FRAMES = 240
_ADAPTIVE_DEFAULTS = (86400.0, 48)  # one day
_DEFAULTS = (20.0, 40)
_PREVIEW_PERIOD = 0.1
_PLAIN_WHITE = Color(kelvin=2700).as_hex()  # for lights without colour


def _light_dto(state: LightState) -> dict[str, Any]:
    if not state.on:
        return {"on": False, "brightness": 0.0, "rgb": "#000000"}
    dto: dict[str, Any] = {
        "on": True,
        "brightness": round(
            state.brightness if state.brightness is not None else 1.0, 3
        ),
        "rgb": state.color.as_hex() if state.color is not None else _PLAIN_WHITE,
    }
    if state.gradient:
        dto["gradient"] = [c.as_hex() for c in state.gradient]
    if state.effect:
        dto["effect"] = state.effect
    return dto


def preview(
    scene: Scene,
    target: Target,
    lights: LightIndex,
    effects: EffectLookup,
    *,
    sun: SunLocation,
    start: datetime,
    salt: int = 0,
    duration: float | None = None,
    samples: int | None = None,
    adjust: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return PreviewFrame dicts; a scene that never re-renders gives one frame."""
    adaptive = any(layer.effect == "adaptive" for layer in scene.layers)
    default_duration, default_samples = _ADAPTIVE_DEFAULTS if adaptive else _DEFAULTS
    duration = default_duration if duration is None else max(0.0, duration)
    samples = min(MAX_FRAMES, max(1, default_samples if samples is None else samples))

    light_ids = set(target.lights) & lights.lights.keys()
    plans, uncovered = plan_runs(scene, target, light_ids, lights)
    if scene.others == "keep":
        light_ids -= uncovered
    adjust = dict(adjust or {})
    level = adjust.pop("brightness", None)
    activation = Activation(
        id="preview",
        target_id=target.id,
        scene_id=scene.id,
        scene=scene,
        started=start,
        transition=0.0,
        adjust=adjust,
        level=None if level is None else percent(level),
        salt=salt,
        lights=light_ids,
    )
    build_runs(activation, plans, uncovered & light_ids, effects)

    current: dict[str, LightState] = {}
    frames: list[dict[str, Any]] = []
    for k in range(samples):
        t = duration * k / (samples - 1) if samples > 1 else 0.0
        frame = render(
            activation,
            "start" if k == 0 else "periodic",
            lights=lights.lights,
            available=lambda entity_id: True,
            now=start + timedelta(seconds=t),
            monotonic=t,
            sun=sun,
            min_period=lambda members: _PREVIEW_PERIOD,
            due_only=k > 0,
        )
        for entity_id, state in frame.items():
            current[entity_id] = normalise(state, lights.lights[entity_id].caps)
        frames.append(
            {
                "t": round(t, 3),
                "lights": {e: _light_dto(s) for e, s in sorted(current.items())},
            }
        )
        if all(run.due == math.inf for run in activation.runs):
            break
    return frames
