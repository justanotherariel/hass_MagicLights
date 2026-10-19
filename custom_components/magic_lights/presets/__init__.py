"""Built-in scenes (presets/scenes.json) and parts (presets/parts.json)."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from ..models import EffectLookup, Part, Scene
from ..validation import validate_builtin_part, validate_preset

_LOGGER = logging.getLogger(__name__)

SCENES_FILE = Path(__file__).with_name("scenes.json")
PARTS_FILE = Path(__file__).with_name("parts.json")


def load_presets(registry: EffectLookup) -> dict[str, Scene]:
    """Load and validate the built-in scenes, in catalogue order.

    Reads a file: call it from the executor.
    """
    presets: dict[str, Scene] = {}
    for data in json.loads(SCENES_FILE.read_text(encoding="utf-8")):
        try:
            scene = validate_preset(data, registry)
        except vol.Invalid as err:
            _LOGGER.error("Skipping built-in scene %s: %s", data.get("id"), err)
            continue
        presets[scene.id] = scene
    return presets


def load_parts(registry: EffectLookup) -> dict[str, Part]:
    """Load and validate the built-in parts, in catalogue order.

    Reads a file: call it from the executor.
    """
    parts: dict[str, Part] = {}
    for data in json.loads(PARTS_FILE.read_text(encoding="utf-8")):
        try:
            part = validate_builtin_part(data, registry)
        except vol.Invalid as err:
            _LOGGER.error("Skipping built-in part %s: %s", data.get("id"), err)
            continue
        parts[part.id] = part
    return parts


def palettes_from_presets(presets: Mapping[str, Scene]) -> list[dict[str, Any]]:
    """Return the colour presets' palettes for "Load palette…" (PaletteInfo)."""
    out = []
    for scene in presets.values():
        if scene.category != "color" or scene.layers[0].effect != "palette":
            continue
        out.append(
            {
                "id": scene.id,
                "name": scene.name,
                "colors": list(scene.layers[0].params["palette"]),
            }
        )
    return out
