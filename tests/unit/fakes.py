"""In-memory stand-ins for the engine's seams (Host, LightIndex, Driver, RuntimeStore)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import heapq
import itertools
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from astral import Observer
import voluptuous as vol

from custom_components.magic_lights.effects.base import Effect, RenderContext
from custom_components.magic_lights.effects.schema import (
    KELVIN,
    PCT,
    ColorSelector,
    PaletteSelector,
)
from custom_components.magic_lights.models import (
    OFF,
    Bus,
    BusLimits,
    Caps,
    Color,
    HaLight,
    Light,
    LightsChange,
    LightState,
    RuntimeV1,
    SunLocation,
    Target,
    TargetId,
    TargetOptions,
)
from homeassistant.core import CoreState
from homeassistant.helpers import selector

START = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)
BERLIN = SunLocation(
    Observer(latitude=52.52, longitude=13.405), ZoneInfo("Europe/Berlin")
)
ZIGBEE = Bus("zigbee:test", "zigbee", "Test Zigbee", BusLimits(10.0, 20, 0.5))


def make_caps(
    *, brightness: bool = True, color: bool = True, color_temp: bool = True, **kw: Any
) -> Caps:
    return Caps(brightness=brightness, color=color, color_temp=color_temp, **kw)


def make_light(
    entity_id: str,
    *,
    caps: Caps | None = None,
    area_id: str | None = None,
    bus: Bus = ZIGBEE,
    name: str | None = None,
    device_id: str | None = None,
) -> Light:
    return Light(
        entity_id=entity_id,
        name=name or entity_id.split(".", 1)[1].replace("_", " ").title(),
        area_id=area_id,
        device_id=device_id,
        bus=bus,
        caps=caps or make_caps(),
    )


def make_room(
    area_id: str,
    lights: Iterable[str],
    *,
    name: str | None = None,
    options: TargetOptions | None = None,
) -> Target:
    return Target(
        id=f"room:{area_id}",
        kind="room",
        name=name or area_id.replace("_", " ").title(),
        icon=None,
        area_id=area_id,
        rooms=(area_id,),
        lights=tuple(sorted(lights)),
        options=options or TargetOptions(),
    )


def make_zone(
    zone_id: str,
    rooms: Iterable[Target],
    extra: Iterable[str] = (),
    *,
    name: str | None = None,
    options: TargetOptions | None = None,
) -> Target:
    rooms = tuple(rooms)
    lights = {e for room in rooms for e in room.lights} | set(extra)
    return Target(
        id=f"zone:{zone_id}",
        kind="zone",
        name=name or zone_id.replace("_", " ").title(),
        icon=None,
        area_id=None,
        rooms=tuple(room.area_id for room in rooms),
        lights=tuple(sorted(lights)),
        options=options or TargetOptions(),
    )


def ha_light(
    on: bool = True,
    *,
    brightness: float | None = None,
    kelvin: int | None = None,
    xy: tuple[float, float] | None = None,
    effect: str | None = None,
    color_mode: str | None = None,
    available: bool = True,
) -> HaLight:
    """Build an HA light state; brightness is 0..255 as HA reports it."""
    if color_mode is None and on:
        color_mode = "color_temp" if kelvin is not None else "xy" if xy else None
    return HaLight(
        available, on and available, brightness, kelvin, xy, effect, color_mode
    )


@dataclass(order=True)
class _Timer:
    when: float
    seq: int
    cb: Callable[[], None] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class FakeHost:
    """Host with a manual clock; timers run only from advance()."""

    def __init__(self, start: datetime = START, sun: SunLocation = BERLIN) -> None:
        self.start = start
        self.clock = 0.0
        self.states: dict[str, HaLight] = {}
        self.events: list[dict[str, Any]] = []
        self.status_calls: list[frozenset[TargetId]] = []
        self.targets_calls: list[tuple[frozenset[TargetId], frozenset[TargetId]]] = []
        self.task_errors: list[BaseException] = []
        self._sun = sun
        self._timers: list[_Timer] = []
        self._seq = itertools.count()

    # Host protocol
    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.clock)

    def monotonic(self) -> float:
        return self.clock

    def call_later(self, delay: float, cb: Callable[[], None]) -> Callable[[], None]:
        timer = _Timer(self.clock + max(0.0, delay), next(self._seq), cb)
        heapq.heappush(self._timers, timer)

        def cancel() -> None:
            timer.cancelled = True

        return cancel

    def state(self, entity_id: str) -> HaLight | None:
        return self.states.get(entity_id)

    def sun(self) -> SunLocation:
        return self._sun

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def status_changed(self, target_ids: Iterable[TargetId]) -> None:
        self.status_calls.append(frozenset(target_ids))

    def targets_changed(
        self, added: Iterable[TargetId], removed: Iterable[TargetId]
    ) -> None:
        self.targets_calls.append((frozenset(added), frozenset(removed)))

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a coroutine at once, without a loop (stands in for async_create_task).

        Fakes never really await, so one step finishes the coroutine; anything
        else lands in `task_errors`.
        """
        try:
            coro.send(None)
        except StopIteration:
            return
        except Exception as err:
            self.task_errors.append(err)
            return
        coro.close()
        self.task_errors.append(RuntimeError(f"{coro!r} suspended"))

    # test controls
    def advance(self, seconds: float = 0.0) -> None:
        """Move the clock, firing due timers in order (also ones they schedule)."""
        until = self.clock + seconds
        while self._timers and self._timers[0].when <= until:
            timer = heapq.heappop(self._timers)
            if timer.cancelled:
                continue
            self.clock = max(self.clock, timer.when)
            timer.cancelled = True
            timer.cb()
        self.clock = until

    def set_state(self, entity_id: str, state: HaLight | None) -> None:
        if state is None:
            self.states.pop(entity_id, None)
        else:
            self.states[entity_id] = state

    @property
    def pending_timers(self) -> list[float]:
        """Due times of the live timers, soonest first."""
        return sorted(t.when for t in self._timers if not t.cancelled)

    def events_of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == kind]


class FakeLightIndex:
    """LightIndex over fixed lights and targets; call change() to notify."""

    def __init__(
        self, lights: Iterable[Light] = (), targets: Iterable[Target] = ()
    ) -> None:
        self.lights: dict[str, Light] = {light.entity_id: light for light in lights}
        self.targets: dict[TargetId, Target] = {t.id: t for t in targets}
        self._subscribers: list[Callable[[LightsChange], None]] = []

    def targets_of(self, entity_id: str) -> set[TargetId]:
        return {t.id for t in self.targets.values() if entity_id in t.lights}

    def expand(self, items: Literal["all"] | Sequence[str], target: Target) -> set[str]:
        if items == "all":
            return set(target.lights)
        out: set[str] = set()
        for item in items:
            if item.startswith("room:"):
                room = self.targets.get(item)
                out.update(room.lights if room else ())
            else:
                out.add(item)
        return out & set(target.lights)

    def subscribe(self, cb: Callable[[LightsChange], None]) -> Callable[[], None]:
        self._subscribers.append(cb)
        return lambda: self._subscribers.remove(cb)

    def change(
        self,
        *,
        added: Iterable[TargetId] = (),
        removed: Iterable[TargetId] = (),
        changed: Iterable[TargetId] = (),
        lights: Iterable[str] = (),
    ) -> LightsChange:
        event = LightsChange(
            frozenset(added), frozenset(removed), frozenset(changed), frozenset(lights)
        )
        for cb in list(self._subscribers):
            cb(event)
        return event


@dataclass(frozen=True, slots=True)
class Sent:
    entity_id: str
    state: LightState
    prev: LightState | None
    context: Any


class FakeDriver:
    """Driver recording the (normalised) states it is asked to send."""

    def __init__(self, key: str = "ha", caps: dict[str, Caps] | None = None) -> None:
        self.key = key
        self.caps = caps or {}
        self.sent: list[Sent] = []
        self.error: Exception | None = None  # raised by send() when set

    def detect(self, hass: Any, entity_id: str) -> Caps | None:
        return self.caps.get(entity_id)

    async def send(
        self, light: Light, state: LightState, prev: LightState | None, context: Any
    ) -> None:
        self.sent.append(Sent(light.entity_id, state, prev, context))
        if self.error is not None:
            raise self.error

    def sent_to(self, entity_id: str) -> list[LightState]:
        return [s.state for s in self.sent if s.entity_id == entity_id]


class FakeRuntimeStore:
    """RuntimeStore keeping every scheduled snapshot."""

    def __init__(self, data: RuntimeV1 | None = None) -> None:
        self.data = data
        self.saved: list[RuntimeV1] = []
        self.flushes = 0

    async def load(self) -> RuntimeV1 | None:
        return self.data

    def schedule_save(self, snapshot: RuntimeV1) -> None:
        self.saved.append(snapshot)
        self.data = snapshot

    async def flush(self) -> None:
        self.flushes += 1

    @property
    def last(self) -> RuntimeV1 | None:
        return self.saved[-1] if self.saved else None


def _number(lo: float, hi: float, step: float = 1) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=lo, max=hi, step=step, mode="slider")
    )


def _select(options: list[Any], **kw: Any) -> selector.SelectSelector:
    return selector.SelectSelector(selector.SelectSelectorConfig(options=options, **kw))


HUE_EFFECTS = (
    "candle",
    "fireplace",
    "colorloop",
    "sunset",
    "sunrise",
    "sparkle",
    "opal",
    "glisten",
    "underwater",
    "cosmos",
    "sunbeam",
    "enchant",
)

# The built-in parameter table, without the real renderers.
PARAM_TABLE: dict[str, vol.Schema] = {
    "off": vol.Schema({}),
    "white": vol.Schema(
        {
            vol.Required("kelvin", default=2700): KELVIN(1500, 6500),
            vol.Required("brightness", default=100): PCT,
        }
    ),
    "static": vol.Schema(
        {
            vol.Required("color", default={"kelvin": 2700}): ColorSelector(),
            vol.Required("brightness", default=100): PCT,
        }
    ),
    "palette": vol.Schema(
        {
            vol.Required(
                "palette", default=[{"xy": [0.5019, 0.4152]}]
            ): PaletteSelector({"min": 1, "max": 9}),
            vol.Required("motion", default="static"): _select(
                [
                    {"value": "static", "label": "Still"},
                    {"value": "dynamic", "label": "Animate"},
                ]
            ),
            vol.Required("speed", default=0.6): _number(0.05, 1, 0.05),
            vol.Required("brightness", default=60): PCT,
            vol.Required("white", default={"kelvin": 2700}): ColorSelector(
                {"allow_color": False}
            ),
            vol.Required("gradients", default=True): selector.BooleanSelector(),
        }
    ),
    "light_effect": vol.Schema(
        {
            vol.Required("effect", default="candle"): _select(
                list(HUE_EFFECTS), custom_value=True
            ),
            vol.Required("brightness", default=35): PCT,
            vol.Required("speed", default=0.5): _number(0, 1, 0.05),
            vol.Required("color", default={"xy": [0.561, 0.4042]}): ColorSelector(),
        }
    ),
    "adaptive": vol.Schema(
        {
            vol.Required("brightness_mode", default="static"): _select(
                [
                    {"value": "static", "label": "None (static)"},
                    {"value": "default", "label": "Dim after sunset"},
                    {"value": "linear", "label": "Linear"},
                    {"value": "tanh", "label": "Smooth"},
                ]
            ),
            vol.Required("brightness", default=100): PCT,
            vol.Required("min_brightness", default=40): PCT,
            vol.Required("max_brightness", default=100): PCT,
            vol.Required("ramp_dark", default=60): _number(5, 240),
            vol.Required("ramp_light", default=90): _number(5, 240),
            vol.Required("min_kelvin", default=2200): KELVIN(1000, 10000),
            vol.Required("max_kelvin", default=5000): KELVIN(1000, 10000),
            vol.Required("sunrise_offset", default=0): _number(-180, 180),
            vol.Required("sunset_offset", default=0): _number(-180, 180),
            vol.Optional("min_sunrise"): selector.TimeSelector(),
            vol.Optional("max_sunrise"): selector.TimeSelector(),
            vol.Optional("min_sunset"): selector.TimeSelector(),
            vol.Optional("max_sunset"): selector.TimeSelector(),
            vol.Required("interval", default=90): _number(30, 600),
            vol.Required("transition", default=45): _number(1, 120),
        }
    ),
}


class _TableEffect(Effect):
    """Renders params literally: brightness % and colour/kelvin, or off."""

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        if self.key == "off":
            return {light.entity_id: OFF for light in ctx.lights}
        p = ctx.params
        color = p.get("color") or (p["palette"][0] if "palette" in p else None)
        if "kelvin" in p:
            color = {"kelvin": p["kelvin"]}
        state = LightState(
            brightness=p.get("brightness", 100) / 100,
            color=Color.parse(color) if color else None,
        )
        return {light.entity_id: state for light in ctx.lights}


def _table_effect(key: str, schema: vol.Schema) -> Effect:
    cls = type(
        f"Fake_{key}", (_TableEffect,), {"key": key, "name": key, "schema": schema}
    )
    return cls()


class FakeRegistry:
    """Registry stand-in with the PARAM_TABLE schemas; add() takes extra effects."""

    def __init__(self) -> None:
        self.effects: dict[str, Effect] = {
            key: _table_effect(key, schema) for key, schema in PARAM_TABLE.items()
        }

    def add(self, effect: Effect) -> None:
        self.effects[effect.key] = effect

    def get(self, key: str) -> Effect | None:
        return self.effects.get(key)


class _ShellConfig:
    def __init__(self, config_dir: str) -> None:
        self.config_dir = config_dir

    def path(self, *parts: str) -> str:
        return str(Path(self.config_dir, *parts))


class _ShellBus:
    def async_listen_once(self, event_type: str, listener: Any) -> Any:
        return lambda: None


class ShellHass:
    """Just enough of HomeAssistant for helpers.storage.Store."""

    def __init__(self, config_dir: str) -> None:
        self.data: dict[str, Any] = {}
        self.config = _ShellConfig(config_dir)
        self.loop = asyncio.get_running_loop()
        self.state = CoreState.running
        self.bus = _ShellBus()

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)
