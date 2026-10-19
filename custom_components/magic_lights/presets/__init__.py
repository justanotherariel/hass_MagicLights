"""Built-in parts (presets/parts.json)."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from ..models import EffectLookup, Part
from ..validation import validate_builtin_part

_LOGGER = logging.getLogger(__name__)

PARTS_FILE = Path(__file__).with_name("parts.json")


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


def palettes_from_parts(builtins: Mapping[str, Part]) -> list[dict[str, Any]]:
    """Return the colour built-ins' palettes for "Load palette…" (PaletteInfo)."""
    return [
        {"id": part.id, "name": part.name, "colors": list(part.params["palette"])}
        for part in builtins.values()
        if part.category == "color" and part.effect == "palette"
    ]
