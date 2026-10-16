"""Per-bus pacing: token bucket, per-light minimum interval, strict priority."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from functools import partial
import math

from ..models import Bus, Color, Command, Host, Light, LightIndex, LightState

# Float slack so a timer fired exactly at a deadline finds it met.
_EPSILON = 1e-6
_FULL_COMMAND = LightState(brightness=1.0, color=Color(kelvin=2700))


@dataclass(slots=True)
class BusQueue:
    bus: Bus
    tokens: float
    refilled: float
    pending: dict[str, Command] = field(default_factory=dict)  # at most one per light
    last_sent: dict[str, float] = field(default_factory=dict)
    timer: Callable[[], None] | None = None


class Scheduler:
    """Paces commands per bus; `drive` returns whether it sent the command."""

    def __init__(
        self, host: Host, lights: LightIndex, drive: Callable[[Command], bool]
    ) -> None:
        self._host = host
        self._lights = lights
        self._drive = drive
        self._queues: dict[str, BusQueue] = {}
        # Entity ids rendered by dynamic layers, per activation.
        self.dynamic: dict[str, set[str]] = {}

    def set_dynamic(self, owner: str, entity_ids: Iterable[str]) -> None:
        if ids := set(entity_ids):
            self.dynamic[owner] = ids
        else:
            self.dynamic.pop(owner, None)

    def pending(self, entity_id: str) -> Command | None:
        for queue in self._queues.values():
            if (cmd := queue.pending.get(entity_id)) is not None:
                return cmd
        return None

    def submit(self, cmd: Command) -> None:
        """Queue a command, replacing the light's pending one."""
        queue = self._queue(cmd.light.bus)
        if (old := queue.pending.get(cmd.entity_id)) is not None:
            cmd.priority = min(cmd.priority, old.priority)
            cmd.force |= old.force
            # A replaced command keeps its place in line.
            cmd.seq = min(cmd.seq, old.seq)
        queue.pending[cmd.entity_id] = cmd
        self._drain(queue)

    def rename(self, old: str, new: str) -> None:
        """Carry a light's queued command and pacing over to its new entity id."""
        for queue in self._queues.values():
            if (cmd := queue.pending.pop(old, None)) is not None:
                light = replace(cmd.light, entity_id=new)
                queue.pending[new] = replace(cmd, entity_id=new, light=light)
            if old in queue.last_sent:
                queue.last_sent[new] = queue.last_sent.pop(old)
        for entity_ids in self.dynamic.values():
            if old in entity_ids:
                entity_ids.remove(old)
                entity_ids.add(new)

    def drop(self, entity_ids: Iterable[str], owner: str | None) -> None:
        """Remove pending commands of `owner` (None matches only None)."""
        ids = set(entity_ids)
        for queue in self._queues.values():
            for entity_id in ids & queue.pending.keys():
                if queue.pending[entity_id].owner == owner:
                    del queue.pending[entity_id]

    @staticmethod
    def cost(bus: Bus, state: LightState) -> int:
        """Messages a command costs: Zigbee and Z-Wave split colour and level."""
        split = bus.kind in ("zigbee", "zwave")
        full = state.on and state.color is not None and state.brightness is not None
        return min(2 if split and full else 1, max(1, bus.limits.burst))

    def min_period(self, lights: Iterable[Light]) -> float:
        """Achievable per-light send period for these lights, before rendering."""
        buses = {light.bus.id: light.bus for light in lights}
        if not buses:
            return 0.1
        per_bus = Counter(
            light.bus.id
            for entity_id in set().union(*self.dynamic.values())
            if (light := self._lights.lights.get(entity_id)) is not None
        )
        worst = 0.1
        for bus_id, bus in buses.items():
            msgs = per_bus[bus_id] * self.cost(bus, _FULL_COMMAND)
            worst = max(worst, bus.limits.min_interval, 1.2 * msgs / bus.limits.rate)
        return worst

    def update_limits(self) -> None:
        """Take buses and lights from the index; move commands of re-homed lights."""
        buses = {light.bus.id: light.bus for light in self._lights.lights.values()}
        moved: list[Command] = []
        for bus_id, queue in self._queues.items():
            queue.bus = buses.get(bus_id, queue.bus)
            for entity_id, cmd in list(queue.pending.items()):
                if (light := self._lights.lights.get(entity_id)) is None:
                    continue
                if light.bus.id == bus_id:
                    cmd.light = light
                else:
                    del queue.pending[entity_id]
                    moved.append(replace(cmd, light=light))
        for cmd in moved:
            self.submit(cmd)
        for queue in list(self._queues.values()):
            self._drain(queue)

    def shutdown(self) -> None:
        for queue in self._queues.values():
            if queue.timer is not None:
                queue.timer()
                queue.timer = None
            queue.pending.clear()

    def _queue(self, bus: Bus) -> BusQueue:
        if (queue := self._queues.get(bus.id)) is None:
            queue = self._queues[bus.id] = BusQueue(
                bus, float(bus.limits.burst), self._host.monotonic()
            )
        return queue

    def _drain(self, queue: BusQueue) -> None:
        if queue.timer is not None:
            queue.timer()
            queue.timer = None
        limits = queue.bus.limits
        now = self._host.monotonic()
        queue.tokens = min(
            limits.burst, queue.tokens + (now - queue.refilled) * limits.rate
        )
        queue.refilled = now
        wait = math.inf
        while queue.pending:
            ready = [
                cmd
                for cmd in queue.pending.values()
                if now - queue.last_sent.get(cmd.entity_id, -math.inf)
                >= limits.min_interval - _EPSILON
            ]
            if not ready:
                wait = min(
                    queue.last_sent[e] + limits.min_interval for e in queue.pending
                )
                break
            cmd = min(ready, key=lambda c: (c.priority, c.seq))
            cost = self.cost(queue.bus, cmd.state)
            if queue.tokens < cost - _EPSILON:
                wait = now + (cost - queue.tokens) / limits.rate
                break
            del queue.pending[cmd.entity_id]
            if self._drive(cmd):
                queue.tokens -= cost
                queue.last_sent[cmd.entity_id] = now
        if queue.pending:
            queue.timer = self._host.call_later(
                max(0.0, wait - now), partial(self._drain, queue)
            )
