"""Shared MagicLights elements."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.magic_lights.data_structures.magic import Magic

SHARE = {"magic": None}


def get_magic() -> Magic:
    if SHARE["magic"] is None:
        from custom_components.magic_lights.data_structures.magic import Magic

        SHARE["magic"] = Magic()

    return SHARE["magic"]
