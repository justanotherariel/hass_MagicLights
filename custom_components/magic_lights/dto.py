"""What the select attributes and the websocket API say about scenes and targets."""

from __future__ import annotations

from collections.abc import Mapping
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
from .models import EffectLookup, Light, Scene, Target

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


def swatches(scene: Scene, effects: EffectLookup) -> list[str]:
    """Return up to five #rrggbb colours over the scene's layers."""
    out: list[str] = []
    for layer in scene.layers:
        if (effect := effects.get(layer.effect)) is None:
            continue
        try:
            colors = effect.swatches(effect.validate(layer.params))
        except vol.Invalid:
            # Stored params can predate the effect's schema; rendering reports it.
            continue
        except Exception:
            # A third-party effect must not break status or config replies.
            _LOGGER.exception("Swatches of effect %s failed", layer.effect)
            continue
        out.extend(c for c in colors if c not in out)
    return out[:MAX_SWATCHES]


def playable(scene: Scene, effects: EffectLookup) -> bool:
    """Whether activation can switch the scene's motion (adjust.dynamic)."""
    for layer in scene.layers:
        if (effect := effects.get(layer.effect)) is not None and any(
            getattr(key, "schema", None) == MOTION_PARAM for key in effect.schema.schema
        ):
            return True
    return False


def scene_summary(scene: Scene, effects: EffectLookup) -> dict[str, Any]:
    """Describe a scene for the select's `scenes` attribute."""
    return {
        "id": scene.public_id,
        "name": scene.name,
        "swatches": swatches(scene, effects),
        "dynamic": playable(scene, effects),
    }


def scene_info(
    coordinator: Coordinator, effects: EffectLookup, scene: Scene
) -> dict[str, Any]:
    """SceneInfo: the stored form plus catalogue fields."""
    origin = coordinator.presets.get(scene.public_id, scene)
    if scene.builtin:
        customised_in = sorted(
            target_id
            for target_id in coordinator.targets
            if coordinator.resolve_scene(target_id, scene.id) is not scene
        )
    else:
        customised_in = []
    return {
        **scene.to_dict(),
        "builtin": scene.builtin,
        # A copy is filed and shown where its preset is.
        "category": origin.category,
        "swatches": swatches(scene, effects),
        "dynamic": playable(scene, effects),
        "customised_in": customised_in,
    }


def target_info(
    hass: HomeAssistant, coordinator: Coordinator, target: Target
) -> dict[str, Any]:
    """TargetInfo: the target with its menu and its select."""
    visible = [s.public_id for s in coordinator.scenes_for(target.id)]
    every = [
        s.public_id for s in coordinator.scenes_for(target.id, include_hidden=True)
    ]
    return {
        "id": target.id,
        "kind": target.kind,
        "name": target.name,
        "icon": target.icon,
        "area_id": target.area_id,
        "rooms": list(target.rooms),
        "lights": list(target.lights),
        "options": target.options.to_dict(),
        "scenes": visible,
        "hidden": [scene_id for scene_id in every if scene_id not in visible],
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
