"""Commanded baseline, diffing, settle windows and the actual sends."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
import itertools
import logging
import math
from typing import Any

from homeassistant.core import Context

from ..color import close
from ..const import Priority
from ..context import create_context
from ..models import (
    OFF,
    Command,
    Driver,
    HaLight,
    Host,
    LightIndex,
    LightState,
    ha_to_state,
    normalise,
)
from .scheduler import Scheduler

_LOGGER = logging.getLogger(__name__)

# How long after a command's transition its state echoes may still arrive.
SETTLE_TIME = 6.0


@dataclass(slots=True)
class Commanded:
    state: LightState  # normalised
    at: float  # monotonic time of submission or send
    pending: bool  # queued in the scheduler, not sent yet
    settle_until: float
    owner: str | None


class Sender:
    """Turns frames into paced commands and remembers what each light was told."""

    def __init__(
        self, host: Host, lights: LightIndex, drivers: Mapping[str, Driver]
    ) -> None:
        self._host = host
        self._lights = lights
        self._drivers = drivers
        self.scheduler = Scheduler(host, lights, self.drive)
        # Newest intent per light, pending or sent; frames diff against it.
        self.commanded: dict[str, Commanded] = {}
        # What actually went to each light.
        self.sent: dict[str, Commanded] = {}
        self.overridden_at: dict[str, float] = {}
        self._off_until: dict[str, float] = {}
        # The effect of the last ON state per light, kept while it is off:
        # many lights resume their effect when switched back on.
        self._effects: dict[str, str] = {}
        self._settle: dict[str, Callable[[], None]] = {}
        self._seq = itertools.count()
        self._failed: dict[str, str | None] = {}
        self._accept: Callable[[Command], bool] = lambda cmd: True
        self._settled: Callable[[str], None] = lambda entity_id: None

    def bind(
        self, accept: Callable[[Command], bool], settled: Callable[[str], None]
    ) -> None:
        """Set the last-moment ownership check and the settle-end callback."""
        self._accept = accept
        self._settled = settled

    # ---- submitting

    def submit_frame(
        self,
        owner: str,
        frame: Mapping[str, LightState],
        priority: Priority,
        *,
        force: bool = False,
        parent: Context | None = None,
    ) -> None:
        for entity_id, state in frame.items():
            if (light := self._lights.lights.get(entity_id)) is not None:
                self._submit(
                    entity_id,
                    normalise(state, light.caps),
                    priority,
                    owner,
                    force,
                    parent,
                )

    def submit_off(
        self,
        entity_ids: Iterable[str],
        transition: float | None,
        *,
        parent: Context | None = None,
    ) -> None:
        off = OFF.replace(transition=transition)
        for entity_id in entity_ids:
            if (light := self._lights.lights.get(entity_id)) is not None:
                self._submit(
                    entity_id,
                    normalise(off, light.caps),
                    Priority.INTERACTIVE,
                    None,
                    True,
                    parent,
                )

    def _submit(
        self,
        entity_id: str,
        state: LightState,
        priority: Priority,
        owner: str | None,
        force: bool,
        parent: Context | None,
    ) -> None:
        cur = self.commanded.get(entity_id)
        if (
            not force
            and cur is not None
            and close(cur.state, state)
            and (not cur.pending or cur.owner == owner)
        ):
            return
        sent = self.sent.get(entity_id)
        if (
            not force
            and cur is not None
            and cur.pending
            and sent is not None
            and close(sent.state, state)
        ):
            # Back to what the light already has: cancel the queued step.
            self.scheduler.drop({entity_id}, cur.owner)
            self.commanded[entity_id] = sent
            return
        self.commanded[entity_id] = Commanded(
            state, self._host.monotonic(), True, math.inf, owner
        )
        self.scheduler.submit(
            Command(
                entity_id,
                self._lights.lights[entity_id],
                state,
                priority,
                owner,
                force,
                next(self._seq),
                parent,
            )
        )

    def rename(self, old: str, new: str) -> None:
        """Carry a light's records over to its new entity id."""
        records: tuple[dict[str, Any], ...] = (
            self.commanded,
            self.sent,
            self.overridden_at,
            self._off_until,
            self._effects,
            self._failed,
        )
        for record in records:
            if old in record:
                record[new] = record.pop(old)
        if (cancel := self._settle.pop(old, None)) is not None:
            cancel()
            self._arm_settle(new, self.sent[new].settle_until - self._host.monotonic())
        self.scheduler.rename(old, new)

    def drop(self, entity_ids: Iterable[str], owner: str | None) -> None:
        """Forget `owner`'s queued commands for these lights."""
        ids = set(entity_ids)
        self.scheduler.drop(ids, owner)
        for entity_id in ids:
            self._revert(entity_id, owner)

    def _revert(self, entity_id: str, owner: str | None) -> None:
        cur = self.commanded.get(entity_id)
        if cur is None or not cur.pending or cur.owner != owner:
            return
        if (sent := self.sent.get(entity_id)) is not None:
            self.commanded[entity_id] = sent
        else:
            del self.commanded[entity_id]

    # ---- sending

    def drive(self, cmd: Command) -> bool:
        """Send a dequeued command; called synchronously by the scheduler."""
        entity_id = cmd.entity_id
        if not self._accept(cmd):
            self._revert(entity_id, cmd.owner)
            return False
        now = self._host.monotonic()
        prev = self._prev(entity_id)
        settle_until = now + (cmd.state.transition or 0.0) + SETTLE_TIME
        record = Commanded(cmd.state, now, False, settle_until, cmd.owner)
        self.commanded[entity_id] = self.sent[entity_id] = record
        self._remember_effect(entity_id, cmd.state)
        if not cmd.state.on:
            self._off_until[entity_id] = settle_until
        self._arm_settle(entity_id, settle_until - now)
        self._host.spawn(self._send(cmd, prev, create_context(cmd.parent)))
        return True

    def _prev(self, entity_id: str) -> LightState | None:
        """Return what the light last got, with the effect it may resume when off."""
        prev = self.sent[entity_id].state if entity_id in self.sent else None
        effect = self._effects.get(entity_id)
        if effect is not None and prev is not None and not prev.on:
            return prev.replace(effect=effect)
        return prev

    def _remember_effect(self, entity_id: str, state: LightState) -> None:
        if not state.on:
            return
        if state.effect:
            self._effects[entity_id] = state.effect
        else:
            self._effects.pop(entity_id, None)

    async def _send(
        self, cmd: Command, prev: LightState | None, context: Context
    ) -> None:
        driver = self._drivers.get(cmd.light.caps.driver) or self._drivers["ha"]
        try:
            await driver.send(cmd.light, cmd.state, prev, context)
        except Exception as err:
            # A blocking send raises whatever the light's integration raises.
            # Logged once per light and activation until a send succeeds again.
            failed = self._failed
            if cmd.entity_id not in failed or failed[cmd.entity_id] != cmd.owner:
                self._failed[cmd.entity_id] = cmd.owner
                _LOGGER.warning("Sending to %s failed: %s", cmd.entity_id, err)
        else:
            self._failed.pop(cmd.entity_id, None)

    def _arm_settle(self, entity_id: str, delay: float) -> None:
        self._cancel_settle(entity_id)
        self._settle[entity_id] = self._host.call_later(
            delay, partial(self._settle_end, entity_id)
        )

    def _cancel_settle(self, entity_id: str) -> None:
        if (cancel := self._settle.pop(entity_id, None)) is not None:
            cancel()

    def _settle_end(self, entity_id: str) -> None:
        self._settle.pop(entity_id, None)
        self._settled(entity_id)

    # ---- baseline

    def seed(self, entity_id: str, state: LightState) -> None:
        """Take a reported state as the baseline, as if we had sent it."""
        if (light := self._lights.lights.get(entity_id)) is not None:
            state = normalise(state, light.caps)
        self._cancel_settle(entity_id)
        self._off_until.pop(entity_id, None)
        self._remember_effect(entity_id, state)
        record = Commanded(state, self._host.monotonic(), False, 0.0, None)
        self.commanded[entity_id] = self.sent[entity_id] = record

    def rebase(self, entity_id: str, state: LightState) -> None:
        """Take a reported state as the baseline unless a command is queued."""
        cur = self.commanded.get(entity_id)
        if cur is None or not cur.pending:
            self.seed(entity_id, state)

    def observe(self, entity_id: str, reported: HaLight) -> None:
        """Accept a foreign on/off flip as the baseline unless a command is queued."""
        cur = self.commanded.get(entity_id)
        if cur is None or (not cur.pending and cur.state.on != reported.on):
            self.seed(entity_id, ha_to_state(reported))

    def on_ish(self, entity_id: str) -> bool:
        """Whether the light is on, or an ON command is queued or settling."""
        reported = self._host.state(entity_id)
        if reported is not None and reported.available and reported.on:
            return True
        cur = self.commanded.get(entity_id)
        return cur is not None and cur.state.on and self._settling(cur)

    def in_settle(self, entity_id: str) -> bool:
        cur = self.commanded.get(entity_id)
        return cur is not None and self._settling(cur)

    def recent_off(self, entity_id: str) -> bool:
        """Whether an OFF we sent may still echo, though a newer ON replaced it."""
        return self._host.monotonic() < self._off_until.get(entity_id, -math.inf)

    def passed_off(self, entity_id: str) -> None:
        """Note the light reported on after our OFF: a later OFF is no echo of it."""
        self._off_until.pop(entity_id, None)

    def last_cmd_on(self, entity_id: str) -> bool | None:
        cur = self.commanded.get(entity_id)
        return None if cur is None else cur.state.on

    def _settling(self, cur: Commanded) -> bool:
        return cur.pending or self._host.monotonic() < cur.settle_until

    def shutdown(self) -> None:
        for cancel in self._settle.values():
            cancel()
        self._settle.clear()
