"""What the select attributes and the websocket API say about scenes and targets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import dataclasses
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .buses import BUS_DEFAULTS, BusKind
from .const import DOMAIN, MOTION_PARAM
from .engine import Coordinator
from .models import (
    EffectLookup,
    Layer,
    Light,
    Part,
    Scene,
    StoredScene,
    Target,
    TargetId,
    resolve,
)

_LOGGER = logging.getLogger(__name__)

MAX_SWATCHES = 5


def suggested_entity_id(target: Target) -> str:
    """Name a new select after its target alone, without area or device prefix."""
    return f"{Platform.SELECT}.{slugify(target.name)}_scene"


def select_entity_id(hass: HomeAssistant, target: Target) -> str:
    """Return the target's select, registered or as it will be named."""
    return er.async_get(hass).async_get_entity_id(
        Platform.SELECT, DOMAIN, target.id
    ) or suggested_entity_id(target)


def swatches(looks: Iterable[Layer | Part], effects: EffectLookup) -> list[str]:
    """Return up to five #rrggbb colours over the looks (layers or parts)."""
    out: list[str] = []
    for look in looks:
        if (effect := effects.get(look.effect)) is None:
            continue
        try:
            colors = effect.swatches(effect.validate(look.params))
        except vol.Invalid:
            # Stored params can predate the effect's schema; rendering reports it.
            continue
        except Exception:
            # A third-party effect must not break status or config replies.
            _LOGGER.exception("Swatches of effect %s failed", look.effect)
            continue
        out.extend(c for c in colors if c not in out)
    return out[:MAX_SWATCHES]


def target_swatches(
    coordinator: Coordinator, effects: EffectLookup, scene: Scene, target: Target
) -> list[str]:
    """Swatches of the layers that reach the target."""
    return swatches(
        [scene.layers[i] for i in coordinator.layers_in(scene, target)], effects
    )


def playable(looks: Iterable[Layer | Part], effects: EffectLookup) -> bool:
    """Whether activation can switch the looks' motion (adjust.dynamic)."""
    for look in looks:
        if (effect := effects.get(look.effect)) is not None and any(
            getattr(key, "schema", None) == MOTION_PARAM for key in effect.schema.schema
        ):
            return True
    return False


def scene_summary(
    coordinator: Coordinator, effects: EffectLookup, scene: Scene, target: Target
) -> dict[str, Any]:
    """Describe a scene for a target's select `scenes` attribute."""
    return {
        "id": scene.id,
        "name": scene.name,
        "swatches": target_swatches(coordinator, effects, scene, target),
        "dynamic": playable(scene.layers, effects),
    }


def scene_info(
    coordinator: Coordinator,
    effects: EffectLookup,
    target_id: TargetId,
    stored: StoredScene,
) -> dict[str, Any]:
    """SceneInfo: a target's stored scene, and how it plays there."""
    scene = resolve(stored, coordinator.parts)
    target = coordinator.targets.get(target_id)
    layers = (
        scene.layers
        if target is None
        else [scene.layers[i] for i in coordinator.layers_in(scene, target)]
    )
    return {
        **stored.to_dict(),
        "target": target_id,
        "swatches": swatches(layers, effects),
        "dynamic": playable(scene.layers, effects),
        "fits": target is not None and bool(layers),
        "dormant": target is None,
    }


def part_info(
    coordinator: Coordinator, effects: EffectLookup, part: Part
) -> dict[str, Any]:
    """PartInfo: a part, whether it is built in or edited, and its scenes."""
    used_by: list[str] = []
    dormant: list[str] = []
    for target_id, scenes in coordinator.config.scenes.items():
        live = target_id in coordinator.targets
        for scene in scenes:
            if any(layer.part == part.id for layer in scene.layers):
                (used_by if live else dormant).append(scene.id)
    builtin = part.id in coordinator.builtins
    return {
        **part.to_dict(),
        "icon": part.icon,
        "category": part.category,
        "builtin": builtin,
        "edited": builtin and part.id in coordinator.config.parts,
        "swatches": swatches([part], effects),
        "dynamic": playable([part], effects),
        "used_by": used_by,
        "dormant": dormant,
    }


def target_info(
    hass: HomeAssistant, coordinator: Coordinator, target: Target
) -> dict[str, Any]:
    """TargetInfo: the target with its scenes in menu order and its select."""
    return {
        "id": target.id,
        "kind": target.kind,
        "name": target.name,
        "icon": target.icon,
        "area_id": target.area_id,
        "rooms": list(target.rooms),
        "lights": list(target.lights),
        "options": target.options.to_dict(),
        "scenes": [s.id for s in coordinator.config.scenes.get(target.id, ())],
        "select_entity_id": select_entity_id(hass, target),
    }


def light_info(light: Light) -> dict[str, Any]:
    return {
        "entity_id": light.entity_id,
        "name": light.name,
        "area_id": light.area_id,
        "bus_id": light.bus.id,
        "caps": light.caps.to_dict(),
    }


def bus_infos(lights: Mapping[str, Light]) -> list[dict[str, Any]]:
    """BusInfo for every bus that carries a tracked light."""
    buses: dict[str, dict[str, Any]] = {}
    for entity_id, light in sorted(lights.items()):
        bus = light.bus
        if (info := buses.get(bus.id)) is None:
            defaults = BUS_DEFAULTS.get(bus.kind, BUS_DEFAULTS[BusKind.UNKNOWN])
            info = buses[bus.id] = {
                "id": bus.id,
                "kind": bus.kind,
                "label": bus.label,
                "limits": dataclasses.asdict(bus.limits),
                "defaults": dataclasses.asdict(defaults),
                "lights": [],
            }
        info["lights"].append(entity_id)
    return sorted(buses.values(), key=lambda info: (info["label"], info["id"]))
