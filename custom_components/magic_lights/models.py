"""Shared domain types and their storage round trips."""

from __future__ import annotations

from collections.abc import (
    Callable,
    Collection,
    Coroutine,
    Iterable,
    Mapping,
    Sequence,
)
import copy
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Any, Literal, Protocol

from astral import Observer

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import slugify

from . import color as color_util
from .const import DEFAULT_TRANSITION, Priority

if TYPE_CHECKING:
    from homeassistant.core import Context, HomeAssistant, State

TargetId = str
SceneCategory = Literal["white", "color", "dynamic", "adaptive", "custom"]
LightStatus = Literal["scene", "manual", "elsewhere", "off", "on", "unavailable"]


def _present(data: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    """Return those of `keys` that `data` has; absent ones keep the field default."""
    return {key: data[key] for key in keys if key in data}


def _number(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"expected a number, got {value!r}")


@dataclass(frozen=True, slots=True)
class Color:
    """Exactly one of a colour temperature or a CIE xy chromaticity."""

    kelvin: int | None = None
    xy: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if (self.kelvin is None) == (self.xy is None):
            raise ValueError("a colour has exactly one of kelvin or xy")

    @classmethod
    def parse(cls, value: Mapping[str, Any] | str | Color) -> Color:
        """Parse {"kelvin"}, {"xy"}, {"rgb"} or "#rrggbb"; raise ValueError."""
        if isinstance(value, Color):
            return value
        if isinstance(value, str):
            return cls._from_rgb(color_util.rgb_from_hex(value))
        if not isinstance(value, Mapping) or len(value) != 1:
            raise ValueError(
                f"expected {{kelvin}}, {{xy}}, {{rgb}} or '#rrggbb', got {value!r}"
            )
        ((key, raw),) = value.items()
        if key == "kelvin":
            kelvin = round(_number(raw))
            if kelvin <= 0:
                raise ValueError(f"kelvin must be positive, got {raw!r}")
            return cls(kelvin=kelvin)
        if key == "xy":
            if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 2:
                raise ValueError(f"xy must be [x, y], got {raw!r}")
            x, y = (_number(v) for v in raw)
            if not (0 <= x <= 1 and 0 < y <= 1):
                raise ValueError(f"xy out of range: {raw!r}")
            return cls(xy=(round(x, 4), round(y, 4)))
        if key == "rgb":
            if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 3:
                raise ValueError(f"rgb must be [r, g, b], got {raw!r}")
            rgb = tuple(round(_number(v)) for v in raw)
            if not all(0 <= v <= 255 for v in rgb):
                raise ValueError(f"rgb out of range: {raw!r}")
            return cls._from_rgb(rgb)
        raise ValueError(f"unknown colour key {key!r}")

    @classmethod
    def _from_rgb(cls, rgb: Sequence[int]) -> Color:
        if not any(rgb):
            raise ValueError("black is not a colour; use the off effect")
        x, y = color_util.xy_from_rgb(rgb)
        return cls(xy=(round(x, 4), round(y, 4)))

    def to_dict(self) -> dict[str, Any]:
        if self.kelvin is not None:
            return {"kelvin": self.kelvin}
        return {"xy": list(self.xy)}

    def as_xy(self) -> tuple[float, float]:
        if self.xy is not None:
            return self.xy
        return color_util.xy_from_kelvin(self.kelvin)

    def as_kelvin(self) -> int:
        if self.kelvin is not None:
            return self.kelvin
        return color_util.kelvin_from_xy(self.xy)

    def as_hex(self) -> str:
        if self.kelvin is not None:
            return color_util.hex_from_rgb(color_util.rgb_from_kelvin(self.kelvin))
        return color_util.hex_from_rgb(color_util.rgb_from_xy(self.xy))


@dataclass(frozen=True, slots=True)
class BusLimits:
    rate: float  # messages per second
    burst: int
    min_interval: float  # seconds between two messages to one light


@dataclass(frozen=True, slots=True)
class Bus:
    id: str
    kind: str  # buses.BusKind value
    label: str
    limits: BusLimits


@dataclass(frozen=True, slots=True)
class Caps:
    brightness: bool
    color: bool
    color_temp: bool
    min_kelvin: int = 2000
    max_kelvin: int = 6535
    transition: bool = True
    effects: frozenset[str] = frozenset()  # HA effect_list
    gradient_points: int = 0
    driver: str = "ha"

    def to_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "effects": sorted(self.effects)}


@dataclass(frozen=True, slots=True)
class Light:
    entity_id: str
    name: str
    area_id: str | None
    device_id: str | None
    bus: Bus
    caps: Caps

    def supports_effect(self, name: str) -> bool:
        return name in self.caps.effects


@dataclass(frozen=True, slots=True)
class LightState:
    on: bool = True
    brightness: float | None = None  # 0..1
    color: Color | None = None
    effect: str | None = None  # HA effect string
    effect_speed: float | None = None  # 0..1
    gradient: tuple[Color, ...] | None = None
    transition: float | None = None  # seconds; None = engine default

    def replace(self, **changes: Any) -> LightState:
        return dataclasses.replace(self, **changes)


OFF = LightState(on=False)


def _fit_color(color: Color, caps: Caps) -> Color | None:
    if caps.color_temp and color.kelvin is not None:
        return Color(kelvin=min(caps.max_kelvin, max(caps.min_kelvin, color.kelvin)))
    if caps.color:
        if color.xy is not None:
            return color
        x, y = color.as_xy()
        return Color(xy=(round(x, 4), round(y, 4)))
    if caps.color_temp:
        kelvin = color_util.nearest_white(color.xy, caps.min_kelvin, caps.max_kelvin)
        return Color(kelvin=kelvin)
    return None


def normalise(state: LightState, caps: Caps) -> LightState:
    """Return what the light will actually be asked for."""
    transition = state.transition if caps.transition else None
    if not state.on:
        return LightState(on=False, transition=transition)
    brightness = None
    if caps.brightness and state.brightness is not None:
        brightness = max(1, min(255, round(state.brightness * 255))) / 255
    color = state.color
    if color is None and state.gradient:
        color = state.gradient[len(state.gradient) // 2]
    if color is not None:
        color = _fit_color(color, caps)
    effect = state.effect if state.effect in caps.effects else None
    gradient = None
    if state.gradient and caps.gradient_points:
        gradient = tuple(state.gradient[: caps.gradient_points])
    return LightState(
        on=True,
        brightness=brightness,
        color=color,
        effect=effect,
        effect_speed=state.effect_speed if effect is not None else None,
        gradient=gradient,
        transition=transition,
    )


@dataclass(frozen=True, slots=True)
class HaLight:
    """The parts of an HA light state the engine reads; brightness is 0..255."""

    available: bool
    on: bool
    brightness: float | None
    kelvin: int | None
    xy: tuple[float, float] | None
    effect: str | None
    color_mode: str | None

    @classmethod
    def from_state(cls, state: State) -> HaLight:
        attrs = state.attributes
        xy = attrs.get("xy_color")
        return cls(
            available=state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
            on=state.state == STATE_ON,
            brightness=attrs.get("brightness"),
            kelvin=attrs.get("color_temp_kelvin"),
            xy=(float(xy[0]), float(xy[1])) if xy else None,
            effect=attrs.get("effect"),
            color_mode=attrs.get("color_mode"),
        )


def ha_to_state(light: HaLight) -> LightState:
    if not light.on:
        return OFF
    color = None
    if light.kelvin and (light.color_mode == "color_temp" or not light.xy):
        color = Color(kelvin=round(light.kelvin))
    elif light.xy:
        color = Color(xy=light.xy)
    effect = light.effect
    return LightState(
        brightness=light.brightness / 255 if light.brightness is not None else None,
        color=color,
        effect=None if effect is None or effect in color_util.NO_EFFECT else effect,
    )


@dataclass(slots=True)
class Frame:
    states: dict[str, LightState]
    next_update: float | None = None  # seconds until the effect wants to render again


@dataclass(frozen=True, slots=True)
class Layer:
    lights: Literal["all"] | tuple[str, ...]  # "light.x" or "room:<area_id>"
    effect: str
    params: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Layer:
        lights = data["lights"]
        return cls(
            lights="all" if lights == "all" else tuple(lights),
            effect=data["effect"],
            params=copy.deepcopy(dict(data.get("params") or {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lights": "all" if self.lights == "all" else list(self.lights),
            "effect": self.effect,
            "params": copy.deepcopy(dict(self.params)),
        }


@dataclass(frozen=True, slots=True)
class Scene:
    id: str
    name: str
    icon: str | None
    layers: tuple[Layer, ...]
    preset: str | None = None  # set: the edit of that preset, for every target
    others: Literal["off", "keep"] = "off"
    brightness: int = 100
    transition: float | None = None
    category: SceneCategory = "custom"
    builtin: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Scene:
        return cls(
            id=data["id"],
            name=data["name"],
            icon=data.get("icon"),
            layers=tuple(Layer.from_dict(layer) for layer in data["layers"]),
            **_present(
                data,
                "preset",
                "others",
                "brightness",
                "transition",
                "category",
            ),
        )

    @property
    def public_id(self) -> str:
        """The id targets know it by: an edited preset's is the preset's."""
        return self.preset or self.id

    def to_dict(self) -> dict[str, Any]:
        """Return the stored form (StoredScene)."""
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "preset": self.preset,
            "layers": [layer.to_dict() for layer in self.layers],
            "others": self.others,
            "brightness": self.brightness,
            "transition": self.transition,
        }


@dataclass(frozen=True, slots=True)
class Zone:
    id: str
    name: str
    icon: str | None
    rooms: tuple[str, ...]  # area ids
    lights: tuple[str, ...]  # extra entity ids

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Zone:
        return cls(
            id=data["id"],
            name=data["name"],
            icon=data.get("icon"),
            rooms=tuple(data.get("rooms") or ()),
            lights=tuple(data.get("lights") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "rooms": list(self.rooms),
            "lights": list(self.lights),
        }


def new_zone_id(name: str, taken: Collection[str]) -> str:
    """Return an id for a zone called `name` that is not in `taken`."""
    base = slugify(name)[:60] or "zone"
    zone_id, n = base, 2
    while zone_id in taken:
        zone_id, n = f"{base}_{n}", n + 1
    return zone_id


@dataclass(frozen=True, slots=True)
class TargetOptions:
    excluded_lights: tuple[str, ...] = ()
    extra_lights: tuple[str, ...] = ()
    visible_scenes: tuple[str, ...] | None = None  # None = defaults
    default_scene: str | None = None  # None = inherit, "none" = no default
    reclaim_on_turn_on: bool = True
    detect_changes: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TargetOptions:
        visible = data.get("visible_scenes")
        return cls(
            excluded_lights=tuple(data.get("excluded_lights") or ()),
            extra_lights=tuple(data.get("extra_lights") or ()),
            visible_scenes=None if visible is None else tuple(visible),
            **_present(data, "default_scene", "reclaim_on_turn_on", "detect_changes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "excluded_lights": list(self.excluded_lights),
            "extra_lights": list(self.extra_lights),
            "visible_scenes": (
                None if self.visible_scenes is None else list(self.visible_scenes)
            ),
            "default_scene": self.default_scene,
            "reclaim_on_turn_on": self.reclaim_on_turn_on,
            "detect_changes": self.detect_changes,
        }


@dataclass(frozen=True, slots=True)
class Settings:
    transition: float = DEFAULT_TRANSITION
    default_scene: str | None = None
    native_effects: bool = True
    # Partial BusLimits overrides per bus id, merged over the bus kind's defaults.
    buses: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    light_buses: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Settings:
        return cls(
            buses={k: dict(v) for k, v in (data.get("buses") or {}).items()},
            light_buses=dict(data.get("light_buses") or {}),
            **_present(data, "transition", "default_scene", "native_effects"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition": self.transition,
            "default_scene": self.default_scene,
            "native_effects": self.native_effects,
            "buses": {k: dict(v) for k, v in self.buses.items()},
            "light_buses": dict(self.light_buses),
        }


@dataclass(frozen=True, slots=True)
class Config:
    zones: Mapping[str, Zone] = field(default_factory=dict)
    targets: Mapping[TargetId, TargetOptions] = field(default_factory=dict)
    scenes: Mapping[str, Scene] = field(default_factory=dict)
    settings: Settings = field(default_factory=Settings)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Config:
        """Build from the stored form without validating effects."""
        return cls(
            zones={k: Zone.from_dict(v) for k, v in (data.get("zones") or {}).items()},
            targets={
                k: TargetOptions.from_dict(v)
                for k, v in (data.get("targets") or {}).items()
            },
            scenes={
                k: Scene.from_dict(v) for k, v in (data.get("scenes") or {}).items()
            },
            settings=Settings.from_dict(data.get("settings") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        default = TargetOptions()
        return {
            "zones": {k: v.to_dict() for k, v in self.zones.items()},
            "targets": {
                k: v.to_dict() for k, v in self.targets.items() if v != default
            },
            "scenes": {k: v.to_dict() for k, v in self.scenes.items()},
            "settings": self.settings.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Target:
    id: TargetId  # "room:<area_id>" | "zone:<zone_id>"
    kind: Literal["room", "zone"]
    name: str
    icon: str | None
    area_id: str | None
    rooms: tuple[str, ...]
    lights: tuple[str, ...]
    options: TargetOptions


@dataclass(frozen=True, slots=True)
class SunLocation:
    """Where the sun is computed and which zone clamp times are in."""

    observer: Observer
    tz: tzinfo


@dataclass(slots=True)
class Command:
    entity_id: str
    light: Light
    state: LightState  # normalised
    priority: Priority
    owner: str | None
    force: bool
    seq: int
    parent: Context | None = None  # of the action that caused it


@dataclass(frozen=True, slots=True)
class TargetStatus:
    target_id: TargetId
    state: Literal["off", "scene", "manual", "mixed"]
    scene_id: str | None = None
    scene_name: str | None = None
    via: TargetId | None = None
    partial: bool = False
    dynamic: bool = False
    motion: bool = False
    brightness: int | None = None
    manual: tuple[str, ...] = ()
    lights: Mapping[str, LightStatus] = field(default_factory=dict)
    elsewhere: Mapping[str, tuple[TargetId, str | None]] = field(default_factory=dict)
    parts: tuple[tuple[TargetId, str, str, tuple[str, ...]], ...] = ()
    since: datetime | None = None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the select's status attributes."""
        return {
            "status": self.state,
            "scene_id": self.scene_id,
            "scene_name": self.scene_name,
            "via": self.via,
            "partial": self.partial,
            "dynamic": self.dynamic,
            "motion": self.motion,
            "brightness": self.brightness,
            "manual": list(self.manual),
            "lights": dict(self.lights),
            "elsewhere": {
                e: {"target_id": t, "scene_id": s}
                for e, (t, s) in self.elsewhere.items()
            },
            "parts": [
                {
                    "target_id": t,
                    "scene_id": s,
                    "scene_name": n,
                    "lights": list(lights),
                }
                for t, s, n, lights in self.parts
            ],
            "since": None if self.since is None else self.since.isoformat(),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class ActivationSnapshot:
    id: str
    target: TargetId
    scene_id: str
    started: datetime
    lights: tuple[str, ...]
    manual: tuple[str, ...]
    adjust: Mapping[str, Any]
    only_on: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ActivationSnapshot:
        return cls(
            id=data["id"],
            target=data["target"],
            scene_id=data["scene_id"],
            started=datetime.fromisoformat(data["started"]),
            lights=tuple(data.get("lights") or ()),
            manual=tuple(data.get("manual") or ()),
            adjust=dict(data.get("adjust") or {}),
            only_on=data.get("only_on", False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "scene_id": self.scene_id,
            "started": self.started.isoformat(),
            "lights": list(self.lights),
            "manual": list(self.manual),
            "adjust": dict(self.adjust),
            "only_on": self.only_on,
        }


@dataclass(frozen=True, slots=True)
class RuntimeV1:
    activations: tuple[ActivationSnapshot, ...] = ()
    salts: Mapping[str, int] = field(default_factory=dict)  # "<scene_id>|<target_id>"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuntimeV1:
        return cls(
            activations=tuple(
                ActivationSnapshot.from_dict(a) for a in data.get("activations") or ()
            ),
            salts={k: int(v) for k, v in (data.get("salts") or {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "activations": [a.to_dict() for a in self.activations],
            "salts": dict(self.salts),
        }


# ---- protocols (synchronous @callback methods unless marked async)


class Host(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def call_later(
        self, delay: float, cb: Callable[[], None]
    ) -> Callable[[], None]: ...
    def state(self, entity_id: str) -> HaLight | None: ...
    def sun(self) -> SunLocation: ...
    def emit(self, event: dict[str, Any]) -> None: ...
    def status_changed(self, target_ids: Iterable[TargetId]) -> None: ...
    def targets_changed(
        self, added: Iterable[TargetId], removed: Iterable[TargetId]
    ) -> None: ...
    def spawn(self, coro: Coroutine[Any, Any, None]) -> None: ...


@dataclass(frozen=True, slots=True)
class LightsChange:
    added_targets: frozenset[TargetId]
    removed_targets: frozenset[TargetId]
    changed_targets: frozenset[TargetId]
    changed_lights: frozenset[str]


class LightIndex(Protocol):
    lights: Mapping[str, Light]
    targets: Mapping[TargetId, Target]

    def targets_of(self, entity_id: str) -> set[TargetId]: ...
    def expand(
        self, items: Literal["all"] | Sequence[str], target: Target
    ) -> set[str]: ...
    def subscribe(self, cb: Callable[[LightsChange], None]) -> Callable[[], None]: ...


class RuntimeStore(Protocol):
    async def load(self) -> RuntimeV1 | None: ...
    def schedule_save(self, snapshot: RuntimeV1) -> None: ...
    async def flush(self) -> None: ...


class Driver(Protocol):
    key: str

    def detect(self, hass: HomeAssistant, entity_id: str) -> Caps | None: ...
    async def send(
        self,
        light: Light,
        state: LightState,
        prev: LightState | None,
        context: Context,
    ) -> None: ...


class EffectLookup(Protocol):
    """The effect registry as scenes use it: the effect for a key, or None."""

    def get(self, key: str) -> Any: ...


def available_scenes(
    scenes: Mapping[str, Scene], presets: Mapping[str, Scene]
) -> dict[str, Scene]:
    """Return every scene by public id: presets, edited ones in place, then user scenes.

    Presets keep catalogue order and user scenes stored order. A stored scene
    whose preset left the catalogue is a user scene.
    """
    overrides = {s.preset: s for s in scenes.values() if s.preset in presets}
    out = {pid: overrides.get(pid, preset) for pid, preset in presets.items()}
    out.update((s.id, s) for s in scenes.values() if s.preset not in presets)
    return out
