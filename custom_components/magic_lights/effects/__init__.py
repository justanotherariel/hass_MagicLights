"""Effect registry: the built-in effects plus those other integrations register."""

from __future__ import annotations

from collections.abc import Iterable
import dataclasses
from dataclasses import dataclass
import re
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util.hass_dict import HassKey

from ..const import DOMAIN
from .adaptive import Adaptive
from .base import Effect
from .light_effect import LightEffect
from .off import Off
from .palette import Palette
from .schema import defaults_of, serialize_schema
from .static import Static
from .white import White

BUILTIN: list[type[Effect]] = [Off, White, Static, Palette, LightEffect, Adaptive]

DATA_REGISTRY: HassKey[Registry] = HassKey(f"{DOMAIN}_registry")

_KEY = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class EffectInfo:
    """What the panel needs to offer an effect and render its form."""

    key: str
    name: str
    description: str
    icon: str
    category: str
    dynamic: bool
    source: str
    schema: list[dict[str, Any]] | None  # None: edit params as a JSON object
    defaults: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class Registry:
    """Effect singletons by key, in registration order."""

    def __init__(self, effects: Iterable[type[Effect]] = ()) -> None:
        self._effects: dict[str, Effect] = {}
        self._infos: dict[str, EffectInfo] = {}
        for cls in effects:
            self.add(cls)

    def add(self, cls: type[Effect], *, source: str = "builtin") -> None:
        """Register an effect class; ValueError on a bad key or schema."""
        if not (isinstance(cls, type) and issubclass(cls, Effect)):
            raise TypeError(f"{cls!r} is not an Effect subclass")
        key = getattr(cls, "key", None)
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError(f"{cls.__name__}: key must match [a-z0-9_]+")
        if key in self._effects:
            raise ValueError(f"effect '{key}' is already registered")
        if not isinstance(getattr(cls, "name", None), str) or not cls.name:
            raise ValueError(f"{key}: an effect needs a name")
        if not _selector_params(cls.schema):
            raise ValueError(
                f"{key}: params must be vol.Required/vol.Optional names"
                " mapped to HA selectors"
            )
        effect = cls()
        self._effects[key] = effect
        self._infos[key] = EffectInfo(
            key=key,
            name=cls.name,
            description=cls.description,
            icon=cls.icon,
            category=cls.category,
            dynamic=cls.dynamic,
            source=source,
            schema=serialize_schema(cls.schema),
            defaults=defaults_of(cls.schema),
        )

    def get(self, key: str) -> Effect | None:
        return self._effects.get(key)

    def catalogue(self) -> list[EffectInfo]:
        return list(self._infos.values())


def _selector_params(schema: Any) -> bool:
    """Whether every param is a plain name mapped to an HA selector.

    The panel renders each param with ha-selector, so nothing else may appear.
    """
    return (
        isinstance(schema, vol.Schema)
        and isinstance(schema.schema, dict)
        and all(
            isinstance(marker, vol.Required | vol.Optional)
            and isinstance(marker.schema, str)
            and isinstance(value, selector.Selector)
            for marker, value in schema.schema.items()
        )
    )


@callback
def async_get_registry(hass: HomeAssistant) -> Registry:
    """Return the registry, created with the built-ins on first use.

    It lives as long as hass, so effects registered by other integrations
    survive reloads of the Magic Lights entry.
    """
    if (registry := hass.data.get(DATA_REGISTRY)) is None:
        registry = hass.data[DATA_REGISTRY] = Registry(BUILTIN)
    return registry
