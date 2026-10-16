"""Sender: commanded baseline, diffing, settle windows, ownership check, on_ish."""

from __future__ import annotations

import unittest

from custom_components.magic_lights.const import Priority
from custom_components.magic_lights.context import create_context, is_ours
from custom_components.magic_lights.engine.sender import SETTLE_TIME, Sender
from custom_components.magic_lights.models import (
    OFF,
    Bus,
    BusLimits,
    Color,
    Light,
    LightState,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from .fakes import FakeDriver, FakeHost, FakeLightIndex, ha_light, make_caps, make_light

FAST = Bus("ip:fast", "ip", "fast", BusLimits(100, 100, 0))
WARM = LightState(brightness=0.5, color=Color(kelvin=2700), transition=1.0)
COOL = LightState(brightness=0.5, color=Color(kelvin=5000), transition=1.0)


def slow_pair() -> list[Light]:
    slow = Bus("cloud:slow", "cloud", "slow", BusLimits(0.01, 1, 0))
    return [make_light("light.b", bus=slow), make_light("light.c", bus=slow)]


class Rig:
    def __init__(self, lights: list[Light] | None = None) -> None:
        self.host = FakeHost()
        self.index = FakeLightIndex(
            lights
            or [
                make_light("light.a"),
                make_light("light.fast", bus=FAST),
                make_light("light.ct", caps=make_caps(color=False, min_kelvin=2200)),
            ]
        )
        self.driver = FakeDriver()
        self.hue = FakeDriver("z2m_hue")
        self.sender = Sender(
            self.host, self.index, {"ha": self.driver, "z2m_hue": self.hue}
        )
        self.accepted = True
        self.settled: list[tuple[float, str]] = []
        self.sender.bind(
            lambda cmd: self.accepted,
            lambda e: self.settled.append((self.host.clock, e)),
        )

    def frame(
        self,
        entity_id: str,
        state: LightState,
        *,
        owner: str = "a",
        force: bool = False,
    ) -> None:
        self.sender.submit_frame(
            owner, {entity_id: state}, Priority.INTERACTIVE, force=force
        )

    def sent(self, entity_id: str) -> list[LightState]:
        return self.driver.sent_to(entity_id)


class SubmitTest(unittest.TestCase):
    def test_states_are_normalised_and_sent_with_our_context(self) -> None:
        rig = Rig()
        rig.frame("light.ct", LightState(brightness=0.5, color=Color(xy=(0.5, 0.41))))
        (sent,) = rig.driver.sent
        self.assertIsNotNone(sent.state.color.kelvin)
        self.assertIsNone(sent.state.color.xy)
        self.assertTrue(is_ours(sent.context))
        self.assertIsNone(sent.prev)
        self.assertEqual(rig.host.task_errors, [])

    def test_commands_are_children_of_the_action(self) -> None:
        rig = Rig()
        action = Context(user_id="u1")
        rig.sender.submit_frame(
            "a", {"light.fast": WARM}, Priority.INTERACTIVE, parent=action
        )
        rig.sender.submit_off(["light.a"], None, parent=action)
        rig.frame("light.fast", COOL)
        first, off, last = (s.context for s in rig.driver.sent)
        for context in (first, off):
            self.assertTrue(is_ours(context))
            self.assertEqual((context.parent_id, context.user_id), (action.id, "u1"))
        self.assertEqual((last.parent_id, last.user_id), (None, None))
        self.assertTrue(is_ours(create_context(action)))

    def test_diff_against_commanded(self) -> None:
        rig = Rig()
        rig.frame("light.fast", WARM)
        rig.frame("light.fast", WARM.replace(transition=5.0))  # transition ignored
        rig.frame("light.fast", WARM.replace(brightness=0.5 + 0.5 / 255))
        self.assertEqual(len(rig.sent("light.fast")), 1)
        rig.frame("light.fast", WARM, force=True)
        self.assertEqual(len(rig.sent("light.fast")), 2)
        rig.frame("light.fast", COOL)
        self.assertEqual(rig.sent("light.fast")[-1].color.kelvin, 5000)
        self.assertEqual(rig.driver.sent[-1].prev.color.kelvin, 2700)

    def test_diff_includes_the_pending_command(self) -> None:
        rig = Rig()
        rig.frame("light.a", WARM)
        rig.frame("light.a", COOL)  # waits for the light's min interval
        self.assertTrue(rig.sender.commanded["light.a"].pending)
        rig.frame("light.a", COOL)
        rig.host.advance(1)
        self.assertEqual([s.color.kelvin for s in rig.sent("light.a")], [2700, 5000])

    def test_going_back_cancels_the_pending_step(self) -> None:
        rig = Rig()
        rig.frame("light.a", WARM)
        rig.frame("light.a", COOL)
        rig.frame("light.a", WARM)
        self.assertIsNone(rig.sender.scheduler.pending("light.a"))
        self.assertIs(rig.sender.commanded["light.a"], rig.sender.sent["light.a"])
        rig.host.advance(1)
        self.assertEqual(len(rig.sent("light.a")), 1)

    def test_close_pending_of_another_owner_is_taken_over(self) -> None:
        rig = Rig()
        rig.frame("light.a", WARM, owner="a")
        rig.frame("light.a", COOL, owner="a")
        rig.frame("light.a", COOL, owner="b")
        self.assertEqual(rig.sender.scheduler.pending("light.a").owner, "b")
        self.assertEqual(rig.sender.commanded["light.a"].owner, "b")

    def test_submit_off_is_ownerless_interactive_and_forced(self) -> None:
        rig = Rig([make_light("light.a", caps=make_caps(transition=False), bus=FAST)])
        rig.sender.submit_off(["light.a", "light.unknown"], 2.0)
        rig.sender.submit_off(["light.a"], 2.0)
        self.assertEqual(rig.sent("light.a"), [OFF, OFF])
        self.assertIsNone(rig.sender.commanded["light.a"].owner)

    def test_drop_reverts_to_what_was_sent(self) -> None:
        rig = Rig()
        rig.frame("light.a", WARM)
        rig.frame("light.a", COOL)
        rig.sender.drop({"light.a"}, "b")
        self.assertTrue(rig.sender.commanded["light.a"].pending)
        rig.sender.drop({"light.a"}, "a")
        self.assertFalse(rig.sender.commanded["light.a"].pending)
        self.assertEqual(rig.sender.commanded["light.a"].state.color.kelvin, 2700)
        rig.host.advance(1)
        self.assertEqual(len(rig.sent("light.a")), 1)

    def test_drop_of_a_never_sent_light_forgets_it(self) -> None:
        rig = Rig(slow_pair())
        rig.frame("light.b", WARM)
        rig.frame("light.c", WARM)  # no token left
        rig.sender.drop({"light.c"}, "a")
        self.assertIsNone(rig.sender.last_cmd_on("light.c"))
        self.assertIsNone(rig.sender.scheduler.pending("light.c"))


class DriveTest(unittest.TestCase):
    def test_last_moment_ownership_check(self) -> None:
        rig = Rig()
        rig.frame("light.a", WARM)
        rig.frame("light.a", COOL)
        rig.accepted = False
        rig.host.advance(1)
        self.assertEqual(len(rig.sent("light.a")), 1)
        self.assertFalse(rig.sender.commanded["light.a"].pending)
        self.assertEqual(rig.sender.commanded["light.a"].state.color.kelvin, 2700)

    def test_prev_keeps_the_effect_while_the_light_is_off(self) -> None:
        caps = make_caps(effects=frozenset({"candle", "off"}))
        for switched_off in ("by us", "by hand"):
            with self.subTest(switched_off=switched_off):
                rig = Rig([make_light("light.fx", caps=caps, bus=FAST)])
                rig.frame("light.fx", WARM.replace(effect="candle"))
                if switched_off == "by us":
                    rig.sender.submit_off(["light.fx"], None)
                else:
                    rig.sender.observe("light.fx", ha_light(False))
                rig.frame("light.fx", WARM)
                prev = rig.driver.sent[-1].prev
                self.assertEqual((prev.on, prev.effect), (False, "candle"))
                rig.frame("light.fx", COOL)  # the stop went with WARM
                self.assertIsNone(rig.driver.sent[-1].prev.effect)

    def test_driver_chosen_by_caps(self) -> None:
        rig = Rig([make_light("light.hue", caps=make_caps(driver="z2m_hue"))])
        rig.frame("light.hue", WARM)
        self.assertEqual(len(rig.hue.sent), 1)
        self.assertEqual(rig.driver.sent, [])

    def test_send_errors_are_logged_once_per_owner(self) -> None:
        rig = Rig()
        rig.driver.error = HomeAssistantError("boom")
        with self.assertLogs("custom_components.magic_lights.engine.sender") as logs:
            rig.frame("light.fast", WARM)
        self.assertEqual(len(logs.records), 1)
        with self.assertNoLogs("custom_components.magic_lights.engine.sender"):
            rig.frame("light.fast", COOL)
        with self.assertLogs("custom_components.magic_lights.engine.sender"):
            rig.frame("light.fast", WARM, owner="b")
        self.assertEqual(rig.host.task_errors, [])

    def test_any_integration_error_is_logged_once(self) -> None:
        rig = Rig()
        rig.driver.error = TimeoutError("bridge did not answer")
        with self.assertLogs("custom_components.magic_lights.engine.sender") as logs:
            rig.frame("light.fast", WARM)
        self.assertEqual(len(logs.records), 1)
        with self.assertNoLogs("custom_components.magic_lights.engine.sender"):
            rig.frame("light.fast", COOL)
        self.assertEqual(rig.host.task_errors, [])


class SettleTest(unittest.TestCase):
    def test_settle_window_and_callback(self) -> None:
        rig = Rig()
        rig.frame("light.fast", WARM)
        until = 1.0 + SETTLE_TIME
        self.assertEqual(rig.sender.commanded["light.fast"].settle_until, until)
        self.assertTrue(rig.sender.in_settle("light.fast"))
        rig.host.advance(until - 0.5)
        self.assertEqual(rig.settled, [])
        rig.host.advance(0.5)
        self.assertEqual(rig.settled, [(until, "light.fast")])
        self.assertFalse(rig.sender.in_settle("light.fast"))

    def test_new_send_rearms_the_settle_timer(self) -> None:
        rig = Rig()
        rig.frame("light.fast", WARM)
        rig.host.advance(3)
        rig.frame("light.fast", COOL.replace(transition=0.0))
        rig.host.advance(20)
        self.assertEqual(rig.settled, [(3 + SETTLE_TIME, "light.fast")])

    def test_pending_counts_as_settling(self) -> None:
        rig = Rig(slow_pair())
        rig.frame("light.b", WARM)
        rig.frame("light.c", WARM)
        self.assertNotIn("light.c", rig.sender.sent)
        self.assertTrue(rig.sender.in_settle("light.c"))
        self.assertTrue(rig.sender.on_ish("light.c"))

    def test_recent_off(self) -> None:
        rig = Rig()
        rig.sender.submit_off(["light.fast"], 1.0)
        rig.frame("light.fast", WARM)
        self.assertTrue(rig.sender.recent_off("light.fast"))
        rig.host.advance(1.0 + SETTLE_TIME)
        self.assertFalse(rig.sender.recent_off("light.fast"))
        rig.sender.submit_off(["light.fast"], 1.0)
        rig.sender.seed("light.fast", WARM)
        self.assertFalse(rig.sender.recent_off("light.fast"))
        rig.sender.submit_off(["light.fast"], 1.0)
        rig.frame("light.fast", WARM)
        rig.sender.passed_off("light.fast")
        self.assertFalse(rig.sender.recent_off("light.fast"))


class BaselineTest(unittest.TestCase):
    def test_on_ish(self) -> None:
        rig = Rig()
        sender = rig.sender
        self.assertFalse(sender.on_ish("light.a"))
        rig.host.set_state("light.a", ha_light(True))
        self.assertTrue(sender.on_ish("light.a"))
        rig.host.set_state("light.a", ha_light(False))
        rig.frame("light.a", WARM)
        self.assertTrue(sender.on_ish("light.a"))  # sent, settling
        rig.frame("light.a", COOL)
        self.assertTrue(sender.on_ish("light.a"))  # pending
        rig.host.advance(20)
        self.assertFalse(sender.on_ish("light.a"))
        rig.sender.submit_off(["light.a"], None)
        self.assertFalse(sender.on_ish("light.a"))
        rig.host.set_state("light.a", ha_light(True, available=False))
        self.assertFalse(sender.on_ish("light.a"))

    def test_seed_is_normalised_and_ownerless(self) -> None:
        rig = Rig()
        rig.sender.seed(
            "light.ct", LightState(brightness=0.4, color=Color(kelvin=1800))
        )
        cur = rig.sender.commanded["light.ct"]
        self.assertEqual(cur.state.color.kelvin, 2200)
        self.assertIs(rig.sender.sent["light.ct"], cur)
        self.assertIsNone(cur.owner)
        self.assertFalse(rig.sender.in_settle("light.ct"))
        self.assertTrue(rig.sender.last_cmd_on("light.ct"))
        self.assertIsNone(rig.sender.last_cmd_on("light.a"))

    def test_observe_takes_contradicting_flips_unless_a_command_is_queued(self) -> None:
        rig = Rig()
        rig.frame("light.a", WARM)
        rig.sender.observe("light.a", ha_light(True, brightness=10))
        self.assertEqual(rig.sender.commanded["light.a"].state.color.kelvin, 2700)
        rig.sender.observe("light.a", ha_light(False))
        self.assertIs(rig.sender.last_cmd_on("light.a"), False)
        rig.frame("light.a", COOL)  # queued behind the min interval
        rig.sender.observe("light.a", ha_light(False))
        self.assertTrue(rig.sender.commanded["light.a"].pending)
        rig.sender.rebase("light.a", OFF)
        self.assertTrue(rig.sender.commanded["light.a"].pending)

    def test_shutdown_cancels_settle_timers(self) -> None:
        rig = Rig()
        rig.frame("light.fast", WARM)
        rig.sender.shutdown()
        rig.host.advance(20)
        self.assertEqual(rig.settled, [])


if __name__ == "__main__":
    unittest.main()
