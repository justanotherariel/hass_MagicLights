"""Effect plugin base class and render context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import random
from typing import Any, ClassVar, Literal

import voluptuous as vol

from ..models import Frame, Light, LightState, SunLocation


@dataclass(slots=True)
class RenderContext:
    lights: tuple[Light, ...]  # render now: active, available members, by entity_id
    members: tuple[str, ...]  # every member of the layer, sorted; deal over these
    params: Mapping[str, Any]  # validated, defaults filled
    now: datetime  # aware UTC
    t: float  # seconds since activation start
    sun: SunLocation
    rng: random.Random  # seeded per scene, target, layer and salt
    memory: dict[str, Any]  # per activation and layer, never persisted
    prev: Mapping[str, LightState]  # last states this layer produced
    min_period: float  # achievable per-light send period
    adjust: Mapping[str, Any]  # "dynamic" (play/pause); brightness is applied for you
    first: bool  # first frame of an activation, rejoin, edit or play/pause


class Effect(ABC):
    """A stateless renderer; the registry holds one instance per key."""

    key: ClassVar[str]  # [a-z0-9_]+; third parties prefix with their domain
    name: ClassVar[str]
    description: ClassVar[str] = ""
    icon: ClassVar[str] = "mdi:auto-fix"
    category: ClassVar[Literal["white", "color", "dynamic", "adaptive", "other"]] = (
        "other"
    )
    dynamic: ClassVar[bool] = False  # UI hint and scheduler class
    # Keys: vol.Required/Optional(name, default=..., description={"label",
    # "helper", "visible"}); values: HA Selector instances only.
    schema: ClassVar[vol.Schema] = vol.Schema({})

    @classmethod
    def validate(cls, params: Mapping[str, Any]) -> dict[str, Any]:
        return cls.schema(dict(params))

    @abstractmethod
    def render(self, ctx: RenderContext) -> Frame | Mapping[str, LightState]:
        """Return the states to set now; synchronous and side-effect free."""

    @classmethod
    def swatches(cls, params: Mapping[str, Any]) -> list[str]:
        """Return up to five hex colours."""
        return []


def as_frame(result: Frame | Mapping[str, LightState]) -> Frame:
    return result if isinstance(result, Frame) else Frame(dict(result))
