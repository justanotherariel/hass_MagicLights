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
from .models import EffectLookup, Layer, Light, Scene, Target

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


def swatches(layers: Iterable[Layer], effects: EffectLookup) -> list[str]:
    """Return up to five #rrggbb colours over the layers."""
    out: list[str] = []
    for layer in layers:
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


def target_swatches(
    coordinator: Coordinator, effects: EffectLookup, scene: Scene, target: Target
) -> list[str]:
    """Swatches of the layers that reach the target: a room-only layer stays there."""
    return swatches(
        [scene.layers[i] for i in coordinator.layers_in(scene, target)], effects
    )


def playable(scene: Scene, effects: EffectLookup) -> bool:
    """Whether activation can switch the scene's motion (adjust.dynamic)."""
    for layer in scene.layers:
        if (effect := effects.get(layer.effect)) is not None and any(
            getattr(key, "schema", None) == MOTION_PARAM for key in effect.schema.schema
        ):
            return True
    return False


def scene_summary(
    coordinator: Coordinator, effects: EffectLookup, scene: Scene, target: Target
) -> dict[str, Any]:
    """Describe a scene for a target's select `scenes` attribute."""
    return {
        "id": scene.public_id,
        "name": scene.name,
        "swatches": target_swatches(coordinator, effects, scene, target),
        "dynamic": playable(scene, effects),
    }


def scene_info(
    coordinator: Coordinator, effects: EffectLookup, scene: Scene
) -> dict[str, Any]:
    """SceneInfo: the stored form plus catalogue fields."""
    origin = coordinator.presets.get(scene.public_id, scene)
    override = None
    if scene.builtin:
        override = next(
            (s.id for s in coordinator.config.scenes.values() if s.preset == scene.id),
            None,
        )
    runs = coordinator.resolve_scene(scene.public_id) is scene
    return {
        **scene.to_dict(),
        "builtin": scene.builtin,
        # An edited preset is filed and shown where its preset is.
        "category": origin.category,
        "swatches": swatches(scene.layers, effects),
        "dynamic": playable(scene, effects),
        "override": override,
        "fits": sorted(
            target.id
            for target in coordinator.targets.values()
            if runs and coordinator.fits(scene, target)
        ),
    }


def target_info(
    hass: HomeAssistant,
    coordinator: Coordinator,
    effects: EffectLookup,
    target: Target,
) -> dict[str, Any]:
    """TargetInfo: the target with its menu, its select and its scenes' swatches."""
    visible = [s.public_id for s in coordinator.scenes_for(target.id)]
    every = coordinator.scenes_for(target.id, include_hidden=True)
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
        "hidden": [s.public_id for s in every if s.public_id not in visible],
        "swatches": {
            s.public_id: target_swatches(coordinator, effects, s, target) for s in every
        },
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
