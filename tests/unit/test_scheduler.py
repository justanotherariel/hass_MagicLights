"""Per-bus pacing: token bucket, min interval, coalescing, priority, min_period."""

from __future__ import annotations

import itertools
import unittest

from custom_components.magic_lights.const import Priority
from custom_components.magic_lights.engine.scheduler import Scheduler
from custom_components.magic_lights.models import (
    OFF,
    Bus,
    BusLimits,
    Color,
    Command,
    Light,
    LightState,
)

from .fakes import ZIGBEE, FakeHost, FakeLightIndex, make_light

FULL = LightState(brightness=0.5, color=Color(kelvin=2700))
LEVEL = LightState(brightness=0.5)


def bus(kind: str, rate: float, burst: int, min_interval: float) -> Bus:
    return Bus(f"{kind}:x", kind, kind, BusLimits(rate, burst, min_interval))


class Rig:
    def __init__(self, lights: list[Light], *, accept: bool = True) -> None:
        self.host = FakeHost()
        self.index = FakeLightIndex(lights)
        self.driven: list[tuple[float, Command]] = []
        self.accept = accept
        self.scheduler = Scheduler(self.host, self.index, self._drive)
        self._seq = itertools.count()

    def _drive(self, cmd: Command) -> bool:
        if self.accept:
            self.driven.append((self.host.clock, cmd))
        return self.accept

    def submit(
        self,
        entity_id: str,
        state: LightState = LEVEL,
        priority: Priority = Priority.INTERACTIVE,
        *,
        owner: str | None = "a",
        force: bool = False,
    ) -> Command:
        cmd = Command(
            entity_id,
            self.index.lights[entity_id],
            state,
            priority,
            owner,
            force,
            next(self._seq),
        )
        self.scheduler.submit(cmd)
        return cmd

    def order(self) -> list[tuple[float, str]]:
        return [(round(t, 3), cmd.entity_id) for t, cmd in self.driven]


def lights_on(b: Bus, n: int) -> list[Light]:
    return [make_light(f"light.l{i}", bus=b) for i in range(n)]


class TokenBucketTest(unittest.TestCase):
    def test_burst_then_rate(self) -> None:
        rig = Rig(lights_on(bus("ip", 2, 3, 0), 6))
        for i in range(6):
            rig.submit(f"light.l{i}")
        self.assertEqual([t for t, _ in rig.order()], [0, 0, 0])
        rig.host.advance(2)
        self.assertEqual([t for t, _ in rig.order()], [0, 0, 0, 0.5, 1.0, 1.5])

    def test_zigbee_colour_and_level_cost_two(self) -> None:
        rig = Rig(lights_on(bus("zigbee", 1, 4, 0), 3))
        for i in range(3):
            rig.submit(f"light.l{i}", FULL)
        self.assertEqual(len(rig.driven), 2)
        rig.host.advance(1.9)
        self.assertEqual(len(rig.driven), 2)
        rig.host.advance(0.1)
        self.assertEqual(rig.order()[-1], (2.0, "light.l2"))

    def test_off_and_level_cost_one_on_zigbee(self) -> None:
        b = bus("zigbee", 1, 2, 0)
        self.assertEqual(Scheduler.cost(b, OFF), 1)
        self.assertEqual(Scheduler.cost(b, LEVEL), 1)
        self.assertEqual(Scheduler.cost(b, FULL), 2)
        self.assertEqual(Scheduler.cost(bus("ip", 1, 2, 0), FULL), 1)

    def test_cost_never_exceeds_burst(self) -> None:
        rig = Rig(lights_on(bus("zigbee", 1, 1, 0), 2))
        rig.submit("light.l0", FULL)
        rig.submit("light.l1", FULL)
        rig.host.advance(1)
        self.assertEqual(rig.order(), [(0, "light.l0"), (1.0, "light.l1")])

    def test_rejected_drive_costs_nothing(self) -> None:
        rig = Rig(lights_on(bus("ip", 1, 1, 10), 2), accept=False)
        rig.submit("light.l0")
        rig.accept = True
        rig.submit("light.l0")
        rig.submit("light.l1")
        # No token spent and no min interval started by the rejected command.
        self.assertEqual(rig.order(), [(0, "light.l0")])


class MinIntervalTest(unittest.TestCase):
    def test_one_light_waits_for_its_interval(self) -> None:
        rig = Rig([make_light("light.a"), make_light("light.b")])
        rig.submit("light.a")
        rig.host.advance(0.1)
        rig.submit("light.a")
        rig.submit("light.b")
        self.assertEqual(rig.order(), [(0, "light.a"), (0.1, "light.b")])
        rig.host.advance(1)
        self.assertEqual(rig.order()[-1], (0.5, "light.a"))

    def test_latest_state_wins_and_keeps_priority_and_force(self) -> None:
        rig = Rig([make_light("light.a")])
        rig.submit("light.a")
        rig.submit("light.a", OFF, Priority.INTERACTIVE, force=True)
        rig.submit("light.a", FULL, Priority.DYNAMIC)
        self.assertIs(rig.scheduler.pending("light.a").state, FULL)
        rig.host.advance(1)
        self.assertEqual(len(rig.driven), 2)
        last = rig.driven[-1][1]
        self.assertIs(last.state, FULL)
        self.assertEqual(last.priority, Priority.INTERACTIVE)
        self.assertTrue(last.force)
        self.assertIsNone(rig.scheduler.pending("light.a"))


class PriorityTest(unittest.TestCase):
    def _blocked(self) -> Rig:
        rig = Rig(lights_on(bus("ip", 1, 1, 0), 4))
        rig.submit("light.l0")  # spends the only token
        return rig

    def test_strict_priority(self) -> None:
        rig = self._blocked()
        rig.submit("light.l1", priority=Priority.BACKGROUND)
        rig.submit("light.l2", priority=Priority.DYNAMIC)
        rig.submit("light.l3", priority=Priority.INTERACTIVE)
        rig.host.advance(3)
        self.assertEqual(
            [e for _, e in rig.order()],
            ["light.l0", "light.l3", "light.l2", "light.l1"],
        )

    def test_oldest_first_and_replacement_keeps_its_place(self) -> None:
        rig = self._blocked()
        rig.submit("light.l1", priority=Priority.DYNAMIC)
        rig.submit("light.l2", priority=Priority.DYNAMIC)
        rig.submit("light.l1", FULL, Priority.DYNAMIC)
        rig.host.advance(2)
        self.assertEqual(
            [e for _, e in rig.order()], ["light.l0", "light.l1", "light.l2"]
        )
        self.assertIs(rig.driven[1][1].state, FULL)

    def test_interactive_does_not_wait_behind_dynamic_lights(self) -> None:
        rig = Rig(lights_on(bus("ip", 1, 1, 0), 3))
        rig.submit("light.l0", priority=Priority.DYNAMIC)
        rig.submit("light.l1", priority=Priority.DYNAMIC)
        rig.submit("light.l2", priority=Priority.INTERACTIVE)
        rig.host.advance(1)
        self.assertEqual(rig.order()[1], (1.0, "light.l2"))


class TimerAndDropTest(unittest.TestCase):
    def test_redrain_rearms_a_single_timer(self) -> None:
        rig = Rig(lights_on(bus("ip", 1, 1, 0), 3))
        rig.submit("light.l0")
        rig.submit("light.l1")
        rig.submit("light.l2")
        self.assertEqual(rig.host.pending_timers, [1.0])
        rig.host.advance(0.5)
        rig.submit("light.l1", FULL)
        self.assertEqual(rig.host.pending_timers, [1.0])

    def test_no_timer_when_queue_is_empty(self) -> None:
        rig = Rig([make_light("light.a")])
        rig.submit("light.a")
        self.assertEqual(rig.host.pending_timers, [])

    def test_drop_matches_owner_exactly(self) -> None:
        rig = Rig(lights_on(bus("ip", 1, 1, 0), 3))
        rig.submit("light.l0")
        rig.submit("light.l1", owner="a")
        rig.submit("light.l2", owner=None)
        rig.scheduler.drop({"light.l1", "light.l2"}, "b")
        self.assertIsNotNone(rig.scheduler.pending("light.l1"))
        rig.scheduler.drop({"light.l1", "light.l2"}, None)
        self.assertIsNone(rig.scheduler.pending("light.l2"))
        self.assertIsNotNone(rig.scheduler.pending("light.l1"))
        rig.scheduler.drop({"light.l1"}, "a")
        self.assertIsNone(rig.scheduler.pending("light.l1"))
        rig.host.advance(5)
        self.assertEqual(len(rig.driven), 1)

    def test_shutdown_cancels_timers_and_pending(self) -> None:
        rig = Rig(lights_on(bus("ip", 1, 1, 0), 2))
        rig.submit("light.l0")
        rig.submit("light.l1")
        rig.scheduler.shutdown()
        self.assertEqual(rig.host.pending_timers, [])
        self.assertIsNone(rig.scheduler.pending("light.l1"))


class LimitsTest(unittest.TestCase):
    def test_update_limits_moves_a_rehomed_light(self) -> None:
        slow = bus("cloud", 1, 1, 0)
        fast = Bus("ip:fast", "ip", "fast", BusLimits(10, 10, 0))
        rig = Rig([make_light("light.a", bus=slow), make_light("light.b", bus=slow)])
        rig.submit("light.a")
        rig.submit("light.b")
        self.assertEqual(len(rig.driven), 1)
        rig.index.lights["light.b"] = make_light("light.b", bus=fast)
        rig.scheduler.update_limits()
        self.assertEqual(rig.order(), [(0, "light.a"), (0, "light.b")])
        self.assertIs(rig.driven[-1][1].light.bus, fast)

    def test_update_limits_takes_new_bus_limits(self) -> None:
        old = bus("cloud", 1, 1, 0)
        rig = Rig([make_light("light.a", bus=old), make_light("light.b", bus=old)])
        rig.submit("light.a")
        rig.submit("light.b")
        new = Bus(old.id, "cloud", "cloud", BusLimits(4, 1, 0.0))
        for e in ("light.a", "light.b"):
            rig.index.lights[e] = make_light(e, bus=new)
        rig.scheduler.update_limits()
        rig.host.advance(0.25)
        self.assertEqual(rig.order()[-1], (0.25, "light.b"))


class MinPeriodTest(unittest.TestCase):
    def test_formula_with_shared_dynamic_lights(self) -> None:
        ip = Bus("ip:x", "ip", "ip", BusLimits(20, 20, 0.1))
        lights = [make_light(f"light.z{i}") for i in range(4)]
        lights.append(make_light("light.ip", bus=ip))
        rig = Rig(lights)
        s = rig.scheduler
        self.assertEqual(s.min_period([]), 0.1)
        self.assertEqual(s.min_period([rig.index.lights["light.z0"]]), 0.5)
        s.set_dynamic("a", {"light.z0", "light.z1", "light.z2"})
        s.set_dynamic("b", {"light.z2", "light.z3"})
        # 4 distinct zigbee lights x 2 messages, 10 msg/s, 20 % headroom.
        self.assertAlmostEqual(s.min_period([rig.index.lights["light.z0"]]), 0.96)
        self.assertEqual(s.min_period([rig.index.lights["light.ip"]]), 0.1)
        mixed = [rig.index.lights["light.ip"], rig.index.lights["light.z1"]]
        self.assertAlmostEqual(s.min_period(mixed), 0.96)
        s.set_dynamic("a", ())
        self.assertNotIn("a", s.dynamic)
        self.assertAlmostEqual(
            s.min_period([rig.index.lights["light.z0"]]), max(0.5, 1.2 * 4 / 10)
        )

    def test_min_period_is_at_least_min_interval(self) -> None:
        rig = Rig([make_light("light.a", bus=ZIGBEE)])
        rig.scheduler.set_dynamic("a", {"light.a"})
        self.assertEqual(rig.scheduler.min_period([rig.index.lights["light.a"]]), 0.5)


if __name__ == "__main__":
    unittest.main()
