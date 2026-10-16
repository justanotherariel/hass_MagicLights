"""A target's status from the activations owning its lights (pure)."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..models import HaLight, LightStatus, Target, TargetId, TargetStatus
from .activation import Activation

DRAFT_NAME = "Draft"


def _scene(activation: Activation) -> tuple[str | None, str]:
    if activation.draft:
        return None, DRAFT_NAME
    return activation.scene_id, activation.scene.name


def compute_status(
    target: Target,
    activations: Mapping[str, Activation],
    by_target: Mapping[TargetId, str],
    owner: Mapping[str, str],
    state_of: Callable[[str], HaLight | None],
) -> TargetStatus:
    own = activations.get(by_target.get(target.id, ""))
    owners = {e: activations.get(owner.get(e, "")) for e in target.lights}
    lights: dict[str, LightStatus] = {}
    for entity_id, activation in owners.items():
        reported = state_of(entity_id)
        if reported is None or not reported.available:
            lights[entity_id] = "unavailable"
        elif activation is None:
            lights[entity_id] = "on" if reported.on else "off"
        elif entity_id in activation.manual:
            lights[entity_id] = "manual"
        elif own is None or activation is own:
            lights[entity_id] = "scene"
        else:
            lights[entity_id] = "elsewhere"
    manual = tuple(e for e, status in lights.items() if status == "manual")
    partial = any(activation is None for activation in owners.values())
    foreign = {a.id: a for a in owners.values() if a is not None}

    lead = own
    if lead is None and len(foreign) == 1:
        (lead,) = foreign.values()
    if lead is not None:
        return TargetStatus(
            target.id,
            "scene",
            *_scene(lead),
            via=None if lead is own else lead.target_id,
            partial=partial,
            dynamic=lead.dynamic,
            motion=lead.motion,
            brightness=lead.brightness,
            manual=manual,
            lights=lights,
            elsewhere={
                e: (a.target_id, _scene(a)[0])
                for e, a in owners.items()
                if a is not None and a is not lead
            },
            since=lead.started,
            errors=tuple(lead.errors),
        )
    if foreign:
        return TargetStatus(
            target.id,
            "mixed",
            partial=partial,
            manual=manual,
            lights=lights,
            parts=tuple(
                (
                    a.target_id,
                    a.scene_id,
                    _scene(a)[1],
                    tuple(e for e, o in owners.items() if o is a),
                )
                for a in foreign.values()
            ),
        )
    on = "on" in lights.values()
    return TargetStatus(target.id, "manual" if on else "off", lights=lights)
