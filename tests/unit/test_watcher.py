"""Override detection, turn-on handling and the HA listeners."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any
import unittest
from unittest.mock import MagicMock, patch

from custom_components.magic_lights.const import SIGNAL_STATUS, SIGNAL_TARGETS
from custom_components.magic_lights.context import create_context, is_ours
from custom_components.magic_lights.engine.host import HassHost
from custom_components.magic_lights.engine.coordinator import LATE_FRAME
from custom_components.magic_lights.engine.sender import SETTLE_TIME
from custom_components.magic_lights.engine.watcher import Watcher
from custom_components.magic_lights.models import (
    OFF,
    Color,
    HaLight,
    Settings,
    TargetOptions,
)
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, Event, State, is_callback
from homeassistant.helpers.target import SelectedEntities

from .fakes import FakeLightIndex, make_caps, make_light
from .test_coordinator import (
    FAST,
    HA_GONE,
    HA_OFF,
    LIVING_LIGHTS,
    RELAX_STATE,
    ROOM,
    ZONE,
    Flicker,
    Rig,
    ha_on,
)

WATCHER = "custom_components.magic_lights.engine.watcher"


def overrides(rig: Rig) -> list[tuple[str, str]]:
    return [(e["entity_id"], e["reason"]) for e in rig.events("override")]


def relaxed(**kw: Any) -> Rig:
    """Relax running on the living room, every light on, settle windows over."""
    rig = Rig(**kw)
    rig.activate(ROOM, "relax")
    rig.echo()
    rig.host.advance(10)
    rig.clear()
    return rig


class CallPathTest(unittest.TestCase):
    def test_bare_turn_on_of_an_active_light_is_a_no_op(self) -> None:
        rig = relaxed()
        rig.call("turn_on", "light.l1")
        rig.call("toggle", "light.l1", bare=True)  # on: toggles off
        self.assertEqual(overrides(rig), [("light.l1", "turn_off")])
        self.assertEqual(rig.driver.sent, [])

    def test_turn_on_with_attributes_is_a_change(self) -> None:
        rig = relaxed()
        rig.call("turn_on", "light.l1", bare=False)
        self.assertEqual(overrides(rig), [("light.l1", "changed")])
        self.assertEqual(rig.own(ROOM).manual, {"light.l1"})
        self.assertEqual(rig.c.status(ROOM).manual, ("light.l1",))
        self.assertEqual(rig.store.last.activations[0].manual, ("light.l1",))

    def test_turn_off_overrides_and_the_last_light_ends_it(self) -> None:
        rig = relaxed()
        rig.call("turn_off", *LIVING_LIGHTS)
        self.assertEqual(len(overrides(rig)), 3)
        self.assertIsNotNone(rig.own(ROOM))  # still on until they report
        rig.report("light.l1", HA_OFF)
        rig.report("light.l2", HA_OFF)
        self.assertIsNotNone(rig.own(ROOM))
        rig.report("light.l3", HA_OFF)
        self.assertIsNone(rig.own(ROOM))
        self.assertEqual(rig.events("ended")[0]["reason"], "all_off")
        self.assertEqual(rig.driver.sent, [])

    def test_bare_turn_on_rejoins_a_manual_light(self) -> None:
        rig = relaxed()
        rig.call("turn_on", "light.l1", bare=False)
        rig.clear()
        rig.call("turn_on", "light.l1")
        self.assertEqual(rig.own(ROOM).manual, set())
        self.assertEqual(rig.last("light.l1").transition, 0.4)
        self.assertEqual(rig.last("light.l1").color.kelvin, 2237)
        self.assertEqual(rig.events("reclaimed")[0]["entity_id"], "light.l1")

    def test_no_rejoin_when_reclaim_on_turn_on_is_off(self) -> None:
        rig = relaxed(living=TargetOptions(reclaim_on_turn_on=False))
        rig.call("turn_on", "light.l1", bare=False)
        rig.call("turn_on", "light.l1")
        rig.report("light.l1", HA_OFF)
        rig.host.advance(LATE_FRAME)
        rig.report("light.l1", ha_on())
        self.assertEqual(rig.own(ROOM).manual, {"light.l1"})
        self.assertEqual(rig.driver.sent, [])

    def test_switched_back_on_by_a_call_is_not_repaired(self) -> None:
        rig = relaxed(living=TargetOptions(reclaim_on_turn_on=False))
        rig.call("turn_off", "light.l1")
        rig.report("light.l1", HA_OFF)
        rig.host.advance(0.5)
        rig.call("turn_on", "light.l1")
        rig.report("light.l1", ha_on())
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.own(ROOM).manual, {"light.l1"})

    def test_bare_turn_on_of_a_light_the_scene_wants_off(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "corner")
        rig.echo()
        rig.call("turn_on", "light.l2")
        self.assertEqual(overrides(rig), [("light.l2", "turned_on")])
        rig.clear()
        rig.report("light.l2", ha_on())  # the call's own effect, not a late frame
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.own(ROOM).manual, {"light.l2"})

    def test_unowned_light_without_default_scene_is_left_alone(self) -> None:
        rig = Rig()
        rig.call("turn_on", "light.l1")
        rig.report("light.l1", ha_on())
        self.assertEqual(rig.c.activations, {})
        self.assertEqual(rig.c.status(ROOM).state, "manual")

    def test_flips_caused_by_a_call_are_not_judged_again(self) -> None:
        rig = relaxed()
        rig.report("light.l1", HA_OFF)  # wall switch: manual
        rig.host.advance(LATE_FRAME)
        rig.clear()
        rig.call("turn_on", "light.l1", bare=False)  # the user picks a colour
        rig.report("light.l1", ha_on(40, xy=(0.2, 0.2)))
        rig.host.advance(20)
        self.assertEqual(rig.own(ROOM).manual, {"light.l1"})
        self.assertEqual(rig.driver.sent, [])


class FlipTest(unittest.TestCase):
    def test_wall_switch_during_an_emulated_candle(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "candle")
        rig.echo()
        rig.host.advance(Flicker.PERIOD)
        rig.echo()
        self.assertTrue(rig.c.status(ROOM).dynamic)
        frames = len(rig.sent("light.l1"))
        rig.host.advance(0.1)
        rig.report("light.l1", HA_OFF)  # inside the frame's settle window
        self.assertEqual(overrides(rig), [("light.l1", "turned_off")])
        rig.host.advance(0.2)
        rig.report("light.l1", ha_on(100, kelvin=2000))  # our in-flight frame lands
        self.assertEqual(rig.last("light.l1"), OFF.replace(transition=0))
        rig.report("light.l1", HA_OFF)
        rig.host.advance(5)
        self.assertEqual(len(rig.sent("light.l1")), frames + 1)
        self.assertEqual(len(overrides(rig)), 1)
        self.assertGreater(len(rig.sent("light.l2")), frames + 5)
        # The others go too: the activation ends, a late frame is still undone.
        rig.report("light.l2", HA_OFF)
        rig.report("light.l3", HA_OFF)
        self.assertIsNone(rig.own(ROOM))
        rig.report("light.l3", ha_on(100, kelvin=2000))
        self.assertFalse(rig.last("light.l3").on)
        rig.report("light.l3", HA_OFF)
        repairs = len(rig.sent("light.l3"))
        rig.host.advance(LATE_FRAME)
        rig.report("light.l3", ha_on())  # a real switch-on later is left alone
        rig.host.advance(SETTLE_TIME)
        self.assertEqual(len(rig.sent("light.l3")), repairs)
        self.assertEqual(rig.c.status(ROOM).lights["light.l3"], "on")

    def test_late_off_after_a_long_transition_is_ours(self) -> None:
        rig = relaxed()
        rig.activate(ROOM, "corner", transition=8)
        rig.host.advance(8.5)
        rig.echo()  # the OFFs land after the 8 s fade
        rig.host.advance(20)
        self.assertEqual(overrides(rig), [])
        self.assertEqual(rig.own(ROOM).manual, set())
        self.assertEqual(rig.c.status(ROOM).lights["light.l2"], "scene")

    def test_off_to_on_inside_settle_is_judged_when_it_ends(self) -> None:
        rig = relaxed()
        rig.activate(ROOM, "corner")
        rig.echo()
        rig.host.advance(3)
        rig.report("light.l2", ha_on())
        rig.report("light.l3", ha_on())
        self.assertEqual(overrides(rig), [])
        rig.host.advance(1)
        rig.report("light.l3", HA_OFF)  # changed its mind before the end
        rig.host.advance(1 + SETTLE_TIME - 4)
        self.assertEqual(overrides(rig), [("light.l2", "turned_on")])

    def test_physical_on_of_a_wanted_off_light(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "corner")
        rig.echo()
        rig.host.advance(10)
        rig.report("light.l2", ha_on())
        self.assertEqual(overrides(rig), [("light.l2", "turned_on")])

    def test_switched_off_then_on_again_rejoins(self) -> None:
        rig = relaxed()
        rig.report("light.l1", HA_OFF)
        self.assertEqual(overrides(rig), [("light.l1", "turned_off")])
        rig.host.advance(60)
        rig.report("light.l1", ha_on())
        self.assertEqual(rig.own(ROOM).manual, set())
        self.assertEqual(rig.last("light.l1").transition, 0.4)
        rig.echo("light.l1")
        rig.host.advance(10)
        self.assertEqual(len(overrides(rig)), 1)

    def test_our_own_echoes_are_never_overrides(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.activate(ROOM, "corner")
        rig.echo()
        rig.activate(ROOM, "read")
        rig.echo()
        rig.host.advance(20)
        self.assertEqual(overrides(rig), [])
        self.assertEqual(rig.own(ROOM).manual, set())

    def test_off_echo_after_a_quick_switch_back_on(self) -> None:
        rig = relaxed()
        rig.c.turn_off(ROOM)
        rig.host.advance(0.3)
        rig.activate(ROOM, "relax")
        rig.host.advance(0.5)
        rig.report("light.l1", HA_OFF)  # the OFF lands late
        rig.report("light.l1", RELAX_STATE)  # then our ON
        rig.report("light.l2", HA_OFF)  # the user switches this one off for real
        rig.host.advance(1)
        self.assertEqual(overrides(rig), [])
        rig.host.advance(SETTLE_TIME)
        self.assertEqual(overrides(rig), [("light.l2", "turned_off")])
        self.assertEqual(rig.own(ROOM).manual, {"light.l2"})

    def test_unavailable_and_back(self) -> None:
        rig = relaxed()
        rig.report("light.l1", HA_GONE)
        rig.report("light.l1", RELAX_STATE)
        self.assertEqual(rig.driver.sent, [])
        rig.report("light.l1", HA_GONE)
        rig.report("light.l1", ha_on(255, kelvin=2700))  # power-on default
        self.assertEqual(rig.last("light.l1").color.kelvin, 2237)
        self.assertEqual(rig.last("light.l1").transition, 0.4)
        self.assertEqual(overrides(rig), [])

    def test_quick_off_and_on_long_after_our_last_send_rejoins(self) -> None:
        rig = relaxed()
        rig.host.advance(3600)
        rig.report("light.l1", HA_OFF)
        rig.host.advance(1)
        rig.report("light.l1", ha_on())  # nothing of ours was in flight
        self.assertTrue(rig.last("light.l1").on)
        self.assertEqual(rig.own(ROOM).manual, set())

    def test_real_off_after_off_then_a_scene(self) -> None:
        for scene_id in ("relax", "candle"):
            with self.subTest(scene=scene_id):
                rig = relaxed()
                rig.c.turn_off(ROOM)
                rig.echo()
                rig.host.advance(1.5)
                rig.activate(ROOM, scene_id)
                rig.echo()
                rig.host.advance(1.5)
                rig.report("light.l1", HA_OFF)
                self.assertEqual(overrides(rig), [("light.l1", "turned_off")])

    def test_real_off_after_a_slow_fade_out_and_a_scene(self) -> None:
        rig = relaxed()
        rig.c.turn_off(ROOM, transition=60)
        rig.echo()
        rig.host.advance(5)
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.host.advance(15)
        rig.report("light.l1", HA_OFF)
        self.assertEqual(overrides(rig), [("light.l1", "turned_off")])
        self.assertEqual(rig.c.status(ROOM).lights["light.l1"], "manual")

    def test_a_command_the_light_never_applied(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo("light.l1", "light.l2")
        rig.host.advance(1 + SETTLE_TIME)
        self.assertEqual(overrides(rig), [("light.l3", "turned_off")])


class DriftTest(unittest.TestCase):
    def test_drift_outside_settle(self) -> None:
        rig = relaxed()
        rig.report("light.l1", ha_on(50, kelvin=2237))
        rig.report("light.l2", ha_on(143, kelvin=3000))
        self.assertEqual(
            overrides(rig), [("light.l1", "changed"), ("light.l2", "changed")]
        )

    def test_small_changes_and_colour_mode_flips_are_not_drift(self) -> None:
        rig = relaxed()
        rig.report("light.l1", ha_on(160, kelvin=2300))
        rig.report("light.l2", ha_on(143, xy=Color(kelvin=2237).as_xy()))
        rig.report("light.l3", HaLight(True, True, None, None, None, None, "onoff"))
        self.assertEqual(overrides(rig), [])

    def test_no_drift_inside_settle(self) -> None:
        rig = Rig()
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.report("light.l1", ha_on(20, kelvin=2700))  # mid-transition
        rig.echo("light.l1")
        rig.host.advance(1 + SETTLE_TIME)
        self.assertEqual(overrides(rig), [])

    def test_adaptive_drift_is_judged_at_settle_end(self) -> None:
        for applied in (True, False):
            with self.subTest(applied=applied):
                rig = Rig()
                rig.activate(ROOM, "adaptive")
                rig.echo()
                rig.host.advance(90)
                step = rig.last("light.l1")
                self.assertEqual(step.transition, 45)
                rig.echo("light.l2", "light.l3")
                rig.report("light.l1", ha_on(204, kelvin=2400))  # fading
                rig.host.advance(45)
                if applied:
                    rig.echo("light.l1")
                self.assertEqual(overrides(rig), [])
                rig.host.advance(SETTLE_TIME)
                expected = [] if applied else [("light.l1", "changed")]
                self.assertEqual(overrides(rig), expected)

    def test_no_drift_while_an_effect_or_gradient_is_commanded(self) -> None:
        caps = {
            "light.l1": make_caps(effects=frozenset({"candle"})),
            "light.l2": make_caps(gradient_points=2),
        }
        rig = Rig(caps=caps)
        rig.activate(ROOM, "glow")
        rig.echo()
        rig.host.advance(10)
        for e in LIVING_LIGHTS:
            rig.report(e, ha_on(255, xy=(0.15, 0.06)))
        self.assertEqual(overrides(rig), [("light.l3", "changed")])

    def test_room_options_apply_under_a_zone_scene(self) -> None:
        rig = Rig(living=TargetOptions(detect_changes=False, reclaim_on_turn_on=False))
        rig.activate(ZONE, "relax")
        rig.echo()
        rig.host.advance(10)
        rig.report("light.l1", ha_on(20, kelvin=5000))
        rig.report("light.k1", ha_on(20, kelvin=5000))
        self.assertEqual(overrides(rig), [("light.k1", "changed")])
        rig.call("turn_on", "light.l2", bare=False)
        rig.call("turn_on", "light.l2")
        rig.call("turn_on", "light.k1")
        self.assertEqual(rig.own(ZONE).manual, {"light.l2"})

    def test_detect_changes_off(self) -> None:
        rig = Rig(living=TargetOptions(detect_changes=False))
        rig.activate(ROOM, "relax")
        rig.echo()
        rig.report("light.l1", ha_on(20, kelvin=5000))
        rig.host.advance(10)
        rig.report("light.l1", ha_on(30, kelvin=5000))
        self.assertEqual(overrides(rig), [])
        rig.report("light.l1", HA_OFF)  # switching still counts
        self.assertEqual(overrides(rig), [("light.l1", "turned_off")])


# ---- the HA side


class GroupToggleTest(unittest.TestCase):
    """HA toggles a group by the group's state, not by each member's."""

    def toggle_group(self, rig: Rig) -> None:
        hass = FakeHass()
        hass.states.set("light.living", "on", {"entity_id": list(LIVING_LIGHTS)})
        with patch(f"{WATCHER}.async_track_state_change_event"):
            Watcher(hass, rig.c, rig.index).start()
        hass.bus.fire(
            {
                "domain": "light",
                "service": "toggle",
                "service_data": {"entity_id": "light.living"},
            },
            Context(),
        )

    def test_a_manual_member_that_is_off_stays_off(self) -> None:
        rig = relaxed()
        rig.report("light.l3", HA_OFF)
        rig.host.advance(10)
        rig.clear()
        self.toggle_group(rig)
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(
            overrides(rig), [("light.l1", "turn_off"), ("light.l2", "turn_off")]
        )

    def test_unowned_members_that_are_off_are_not_adopted(self) -> None:
        rig = Rig(settings=Settings(default_scene="relax"))
        rig.report("light.l1", ha_on())
        rig.echo("light.l1")
        rig.host.advance(10)
        rig.clear()
        self.toggle_group(rig)
        self.assertEqual(rig.driver.sent, [])
        self.assertEqual(rig.own(ROOM).lights, {"light.l1"})


class FakeBus:
    def __init__(self) -> None:
        self.listeners: list[tuple[str, Callable, Callable | None]] = []

    def async_listen(
        self, event_type: str, listener: Callable, event_filter: Callable | None = None
    ) -> Callable[[], None]:
        item = (event_type, listener, event_filter)
        self.listeners.append(item)
        return lambda: self.listeners.remove(item)

    def fire(self, data: Mapping[str, Any], context: Context | None = None) -> None:
        for event_type, listener, event_filter in list(self.listeners):
            if event_type == EVENT_CALL_SERVICE and (
                event_filter is None or event_filter(data)
            ):
                listener(Event(event_type, dict(data), context=context))


class FakeStates(dict):
    def set(self, entity_id: str, state: str, attrs: Mapping[str, Any]) -> None:
        self[entity_id] = State(entity_id, state, attrs)


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()
        self.states = FakeStates()
        self.data: dict[str, Any] = {}


class StubCoordinator:
    def __init__(self) -> None:
        self.calls: list[tuple[set[str], str, bool]] = []
        self.states: list[tuple[str, HaLight | None, HaLight | None]] = []

    def on_foreign_call(self, entity_ids: set[str], service: str, bare: bool) -> None:
        self.calls.append((entity_ids, service, bare))

    def on_light_state(
        self, entity_id: str, old: HaLight | None, new: HaLight | None
    ) -> None:
        self.states.append((entity_id, old, new))


class WatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hass = FakeHass()
        self.index = FakeLightIndex(
            [make_light(e, bus=FAST) for e in ("light.a", "light.b", "light.c")]
        )
        self.coordinator = StubCoordinator()
        self.tracked: list[tuple[frozenset[str], Callable]] = []
        self.untracked = 0
        patcher = patch(f"{WATCHER}.async_track_state_change_event", self._track)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.watcher = Watcher(self.hass, self.coordinator, self.index)
        self.watcher.start()

    def _track(self, hass: Any, entity_ids: Any, action: Callable) -> Callable:
        self.tracked.append((frozenset(entity_ids), action))

        def untrack() -> None:
            self.untracked += 1

        return untrack

    def call(
        self,
        service: str,
        data: Mapping[str, Any],
        *,
        domain: str = "light",
        context: Context | None = None,
    ) -> None:
        self.hass.bus.fire(
            {"domain": domain, "service": service, "service_data": dict(data)},
            context or Context(),
        )

    def test_listeners_are_callbacks_and_filter_light_calls(self) -> None:
        ((event_type, listener, event_filter),) = self.hass.bus.listeners
        self.assertEqual(event_type, EVENT_CALL_SERVICE)
        self.assertTrue(is_callback(listener))
        self.assertTrue(is_callback(event_filter))
        self.assertTrue(event_filter({"domain": "light", "service": "toggle"}))
        self.assertFalse(event_filter({"domain": "light", "service": "reload"}))
        self.assertFalse(event_filter({"domain": "switch", "service": "turn_on"}))
        ((ids, action),) = self.tracked
        self.assertEqual(ids, {"light.a", "light.b", "light.c"})
        self.assertTrue(is_callback(action))

    def test_calls_are_translated(self) -> None:
        self.call("turn_on", {"entity_id": "light.a", "transition": 2})
        self.call("turn_on", {"entity_id": ["light.b", "light.x"], "brightness": 9})
        self.call("turn_off", {"entity_id": "light.x"})
        self.call("turn_on", {"entity_id": "switch.x"}, domain="switch")
        self.call("turn_off", {"entity_id": "light.a"}, context=create_context())
        self.assertEqual(
            self.coordinator.calls,
            [({"light.a"}, "turn_on", True), ({"light.b"}, "turn_on", False)],
        )

    def test_entity_id_all(self) -> None:
        self.call("toggle", {"entity_id": "all"})
        self.call("turn_off", {"entity_id": ["all"]})
        self.assertEqual(
            [c[0] for c in self.coordinator.calls],
            [{"light.a", "light.b", "light.c"}] * 2,
        )

    def test_group_attributes_are_expanded(self) -> None:
        self.hass.states.set(
            "light.group", "on", {"entity_id": ["light.a", "light.hue"]}
        )
        self.hass.states.set(
            "light.hue", "on", {"group_entities": ["light.b"], "entity_id": "x"}
        )
        self.call("turn_off", {"entity_id": "light.group"})
        self.assertEqual(
            self.coordinator.calls, [({"light.a", "light.b"}, "turn_off", True)]
        )

    def test_group_toggles_follow_the_group_state(self) -> None:
        self.hass.states.set("light.on", "on", {"entity_id": ["light.a", "light.b"]})
        self.hass.states.set("light.off", "off", {"entity_id": ["light.c"]})
        self.hass.states.set("light.gone", "unavailable", {"entity_id": ["light.a"]})
        self.call("toggle", {"entity_id": ["light.on", "light.off", "light.gone"]})
        self.call("toggle", {"entity_id": "light.a"})  # a plain light: its own state
        self.assertEqual(
            {(frozenset(ids), service) for ids, service, _ in self.coordinator.calls},
            {
                (frozenset({"light.a", "light.b"}), "turn_off"),
                (frozenset({"light.c"}), "turn_on"),
                (frozenset({"light.a"}), "toggle"),
            },
        )
        self.assertEqual(len(self.coordinator.calls), 3)

    def test_area_device_and_label_targets_use_ha_resolution(self) -> None:
        seen = []

        def extract(
            hass: Any, selection: Any, expand_group: bool = True
        ) -> SelectedEntities:
            seen.append((selection.area_ids, selection.label_ids, expand_group))
            return SelectedEntities(indirectly_referenced={"light.c", "sensor.x"})

        with patch(f"{WATCHER}.async_extract_referenced_entity_ids", extract):
            self.call("turn_on", {"area_id": "living", "label_id": ["evening"]})
        self.assertEqual(seen, [({"living"}, {"evening"}, True)])
        self.assertEqual(self.coordinator.calls, [({"light.c"}, "turn_on", True)])

    def test_state_changes_are_translated(self) -> None:
        ((_, action),) = self.tracked
        old = State("light.a", "off")
        new = State(
            "light.a",
            "on",
            {"brightness": 100, "color_temp_kelvin": 2700, "color_mode": "color_temp"},
        )
        action(
            Event(
                "state_changed",
                {"entity_id": "light.a", "old_state": old, "new_state": new},
            )
        )
        action(
            Event(
                "state_changed",
                {"entity_id": "light.a", "old_state": new, "new_state": None},
            )
        )
        (first, second) = self.coordinator.states
        self.assertEqual(first[0], "light.a")
        self.assertFalse(first[1].on)
        self.assertEqual(
            (first[2].on, first[2].brightness, first[2].kelvin), (True, 100, 2700)
        )
        self.assertIsNone(second[2])

    def test_retracks_when_the_lights_change(self) -> None:
        self.index.change()
        self.assertEqual(len(self.tracked), 1)  # same lights: kept
        self.index.lights["light.d"] = make_light("light.d")
        self.index.change(lights=["light.d"])
        self.assertEqual(self.untracked, 1)
        self.assertIn("light.d", self.tracked[-1][0])

    def test_stop_unsubscribes(self) -> None:
        self.watcher.stop()
        self.assertEqual(self.hass.bus.listeners, [])
        self.assertEqual(self.untracked, 1)
        self.index.change(lights=["light.a"])
        self.assertEqual(len(self.tracked), 1)


class HassHostTest(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        self.hass = FakeHass()
        self.hass.loop = self.loop
        self.hass.config = MagicMock(latitude=52.5, longitude=13.4, elevation=34)
        self.hass.bus = MagicMock()
        self.hass.async_create_task = MagicMock()
        self.sent: list[tuple[Any, ...]] = []
        patcher = patch(
            "custom_components.magic_lights.engine.host.async_dispatcher_send",
            lambda hass, *args: self.sent.append(args),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host = HassHost(self.hass)

    def run_once(self) -> None:
        self.loop.run_until_complete(asyncio.sleep(0))

    def test_status_is_coalesced_per_loop_iteration(self) -> None:
        self.host.status_changed(["room:a"])
        self.host.status_changed({"room:b", "room:a"})
        self.host.status_changed([])
        self.assertEqual(self.sent, [])
        self.run_once()
        self.assertEqual(self.sent, [(SIGNAL_STATUS, frozenset({"room:a", "room:b"}))])
        self.host.status_changed([])
        self.run_once()
        self.assertEqual(len(self.sent), 1)

    def test_targets_changed(self) -> None:
        self.host.targets_changed(["room:a"], [])
        self.assertEqual(
            self.sent, [(SIGNAL_TARGETS, frozenset({"room:a"}), frozenset())]
        )

    def test_timers_run_on_the_loop_and_cancel(self) -> None:
        fired: list[str] = []
        self.host.call_later(0, lambda: fired.append("a"))
        cancel = self.host.call_later(0, lambda: fired.append("b"))
        cancel()
        self.run_once()
        self.assertEqual(fired, ["a"])
        before = self.loop.time()
        self.assertLessEqual(before, self.host.monotonic())

    def test_state_emit_sun_and_spawn(self) -> None:
        self.assertIsNone(self.host.state("light.a"))
        self.hass.states.set("light.a", "unavailable", {})
        self.assertFalse(self.host.state("light.a").available)
        self.host.emit({"type": "off", "target_id": "room:a"})
        ((event_type, data), kwargs) = self.hass.bus.async_fire.call_args
        self.assertEqual((event_type, data["type"]), ("magic_lights_event", "off"))
        self.assertTrue(is_ours(kwargs["context"]))
        sun = self.host.sun()
        self.assertEqual((sun.observer.latitude, sun.observer.longitude), (52.5, 13.4))
        self.assertIsNotNone(self.host.now().tzinfo)
        coro = MagicMock()
        self.host.spawn(coro)
        args, kwargs = self.hass.async_create_task.call_args
        self.assertIs(args[0], coro)
        self.assertFalse(kwargs["eager_start"])


if __name__ == "__main__":
    unittest.main()
