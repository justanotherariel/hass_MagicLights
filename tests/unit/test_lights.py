"""LightIndexImpl over fake registries: discovery, targets and change events."""

from __future__ import annotations

import unittest

from custom_components.magic_lights.buses import BUS_DEFAULTS, BusKind
from custom_components.magic_lights.const import DOMAIN
from custom_components.magic_lights.lights import LightIndexImpl, areas
from custom_components.magic_lights.models import (
    BusLimits,
    Caps,
    Config,
    LightsChange,
    Settings,
    TargetOptions,
    Zone,
)
from custom_components.magic_lights.native import build_drivers

from .fake_hass import FakeHass

COLOR = {
    "supported_color_modes": ["color_temp", "xy"],
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 6535,
}
CT = {
    "supported_color_modes": ["color_temp"],
    "min_color_temp_kelvin": 2202,
    "max_color_temp_kelvin": 4000,
}
IEEE = "0x0017880100000001"
HUE_EFFECTS = ["candle", "fireplace", "none", "stop_hue_effect"]
DOWNSTAIRS = Zone(
    "downstairs",
    "Downstairs",
    "mdi:home",
    ("living", "kitchen", "gone"),
    ("light.yaml", "light.unknown"),
)


class LightIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hass = h = FakeHass()
        h.add_area("living", "Living room", icon="mdi:sofa")
        h.add_area("kitchen", "Kitchen")
        h.add_area("empty", "Empty")
        self.hue = hue = h.add_config_entry("hue", "Hue", unique_id="bridge1")
        self.lamp = h.add_device(hue, name="Floor lamp", area_id="living")
        h.add_entity(
            "light.floor",
            "hue",
            "floor",
            entry=hue,
            device=self.lamp,
            capabilities=COLOR,
            supported_features=40,
            has_entity_name=True,
        )
        h.add_entity(
            "light.ceiling",
            "hue",
            "ceiling",
            entry=hue,
            area_id="living",
            capabilities=CT,
        )
        h.add_entity(
            "light.kitchen",
            "hue",
            "kitchen",
            entry=hue,
            area_id="kitchen",
            capabilities=CT,
        )
        h.add_entity(
            "light.hidden",
            "hue",
            "hidden",
            entry=hue,
            area_id="kitchen",
            hidden_by="user",
        )
        # Excluded: disabled, with an entity category, groups of every kind.
        h.add_entity(
            "light.disabled", "hue", "d", area_id="kitchen", disabled_by="user"
        )
        h.add_entity(
            "light.led", "hue", "led", area_id="kitchen", entity_category="config"
        )
        h.add_entity("light.group", "group", "g", area_id="kitchen")
        h.add_entity("light.hue_room", "hue", "room", entry=hue, area_id="living")
        h.set_state("light.hue_room", "on", {"is_hue_group": True})
        h.add_entity("light.zha_group", "zha", "zg", area_id="kitchen")
        h.set_state("light.zha_group", "on", {"group_entities": ["light.kitchen"]})
        h.set_state("light.ha_group", "on", {"entity_id": ["light.kitchen"]})
        h.set_state("light.deconz_group", "on", {"is_deconz_group": True})
        # YAML light without unique_id: a state, no registry entry.
        h.set_state(
            "light.yaml",
            "off",
            {"supported_color_modes": ["onoff"], "friendly_name": "YAML lamp"},
        )
        h.set_state("switch.plug", "on", {})
        self.index = LightIndexImpl(
            h, Config(zones={"downstairs": DOWNSTAIRS}), build_drivers(h)
        )
        self.index.rebuild()
        self.changes: list[LightsChange] = []
        self.unsubscribe = self.index.subscribe(self.changes.append)
        self.unlisten = self.index.async_listen()

    def change(self) -> LightsChange:
        self.assertEqual(len(self.changes), 1, self.changes)
        return self.changes.pop()

    def lights_of(self, target_id: str) -> tuple[str, ...]:
        return self.index.targets[target_id].lights

    # ---- discovery

    def test_light_set(self) -> None:
        self.assertEqual(
            sorted(self.index.lights),
            [
                "light.ceiling",
                "light.floor",
                "light.hidden",
                "light.kitchen",
                "light.yaml",
            ],
        )

    def test_areas_hold_the_lights_the_index_places(self) -> None:
        self.assertEqual(
            areas(self.hass),
            {
                "living": ("Living room", {"light.ceiling", "light.floor"}),
                "kitchen": ("Kitchen", {"light.hidden", "light.kitchen"}),
                "empty": ("Empty", set()),
            },
        )

    def test_light_fields(self) -> None:
        floor = self.index.lights["light.floor"]
        self.assertEqual(
            (floor.name, floor.area_id, floor.device_id),
            ("Floor lamp", "living", self.lamp.id),
        )
        self.assertEqual((floor.bus.id, floor.bus.kind), ("hue:bridge1", "hue"))
        self.assertEqual(floor.bus.limits, BUS_DEFAULTS[BusKind.HUE])
        self.assertEqual(floor.caps, Caps(True, True, True, transition=True))
        yaml = self.index.lights["light.yaml"]
        self.assertEqual(
            (yaml.name, yaml.area_id, yaml.device_id), ("YAML lamp", None, None)
        )
        self.assertEqual((yaml.bus.id, yaml.bus.kind), ("entity:light.yaml", "unknown"))
        self.assertEqual(yaml.caps, Caps(False, False, False, transition=False))

    def test_caps_from_state_before_registry(self) -> None:
        ceiling = self.index.lights["light.ceiling"]
        self.assertEqual(
            ceiling.caps, Caps(True, False, True, 2202, 4000, transition=False)
        )
        self.hass.set_state(
            "light.ceiling",
            "on",
            {**COLOR, "effect_list": ["colorloop"], "supported_features": 36},
        )
        change = self.change()
        self.assertEqual(change.changed_lights, {"light.ceiling"})
        self.assertEqual(change.changed_targets, set())
        self.assertEqual(
            self.index.lights["light.ceiling"].caps,
            Caps(True, True, True, effects=frozenset({"colorloop"})),
        )

    # ---- targets

    def test_rooms(self) -> None:
        self.assertEqual(
            list(self.index.targets), ["room:kitchen", "room:living", "zone:downstairs"]
        )
        living = self.index.targets["room:living"]
        self.assertEqual(
            (living.kind, living.name, living.icon, living.area_id, living.rooms),
            ("room", "Living room", "mdi:sofa", "living", ("living",)),
        )
        self.assertEqual(living.lights, ("light.ceiling", "light.floor"))
        self.assertEqual(living.options, TargetOptions())
        self.assertEqual(
            self.lights_of("room:kitchen"), ("light.hidden", "light.kitchen")
        )

    def test_zone(self) -> None:
        zone = self.index.targets["zone:downstairs"]
        self.assertEqual(
            (zone.kind, zone.name, zone.icon, zone.area_id, zone.rooms),
            ("zone", "Downstairs", "mdi:home", None, ("living", "kitchen")),
        )
        self.assertEqual(
            zone.lights,
            (
                "light.ceiling",
                "light.floor",
                "light.hidden",
                "light.kitchen",
                "light.yaml",
            ),
        )

    def test_room_options(self) -> None:
        options = TargetOptions(
            excluded_lights=("light.ceiling",),
            extra_lights=("light.yaml", "light.unknown"),
        )
        zone_options = TargetOptions(detect_changes=False)
        self.index.apply_config(
            Config(
                zones={"downstairs": DOWNSTAIRS},
                targets={"room:living": options, "zone:downstairs": zone_options},
            )
        )
        change = self.change()
        self.assertEqual(change.changed_targets, {"room:living", "zone:downstairs"})
        self.assertEqual(change.changed_lights, set())
        self.assertEqual(self.lights_of("room:living"), ("light.floor", "light.yaml"))
        self.assertEqual(self.index.targets["room:living"].options, options)
        # The zone takes the rooms as configured.
        self.assertNotIn("light.ceiling", self.lights_of("zone:downstairs"))
        self.assertEqual(self.index.targets["zone:downstairs"].options, zone_options)

    def test_room_keeps_its_select_when_lights_leave(self) -> None:
        self.hass.update_entity("light.kitchen", area_id="living")
        self.changes.clear()
        self.hass.update_entity("light.hidden", area_id=None)
        self.assertEqual(self.change().removed_targets, {"room:kitchen"})

        self.hass.update_entity("light.hidden", area_id="kitchen")
        self.assertEqual(self.change().added_targets, {"room:kitchen"})
        self.hass.add_entity("select.kitchen_scene", DOMAIN, "room:kitchen")
        self.hass.update_entity("light.hidden", area_id=None)
        change = self.change()
        self.assertEqual(change.removed_targets, set())
        self.assertEqual(change.changed_targets, {"room:kitchen", "zone:downstairs"})
        self.assertEqual(self.lights_of("room:kitchen"), ())

    def test_targets_of(self) -> None:
        self.assertEqual(
            self.index.targets_of("light.floor"), {"room:living", "zone:downstairs"}
        )
        self.assertEqual(self.index.targets_of("light.yaml"), {"zone:downstairs"})
        self.assertEqual(self.index.targets_of("light.unknown"), set())

    def test_expand(self) -> None:
        zone = self.index.targets["zone:downstairs"]
        kitchen = self.index.targets["room:kitchen"]
        self.assertEqual(self.index.expand("all", zone), set(zone.lights))
        self.assertEqual(
            self.index.expand(["room:living"], zone), {"light.ceiling", "light.floor"}
        )
        self.assertEqual(
            self.index.expand(
                ["light.floor", "light.kitchen", "light.missing", "room:gone"], zone
            ),
            {"light.floor", "light.kitchen"},
        )
        self.assertEqual(
            self.index.expand(["room:living", "light.floor"], kitchen), set()
        )

    # ---- change events

    def test_entity_moves_rooms(self) -> None:
        self.hass.update_entity("light.ceiling", area_id="kitchen")
        change = self.change()
        self.assertEqual(change.changed_targets, {"room:living", "room:kitchen"})
        self.assertEqual(change.changed_lights, {"light.ceiling"})
        self.assertEqual(
            self.index.targets_of("light.ceiling"), {"room:kitchen", "zone:downstairs"}
        )

    def test_new_light_in_new_area(self) -> None:
        self.hass.add_area("bedroom", "Bedroom")
        self.assertEqual(self.changes, [])
        self.hass.add_entity(
            "light.bed", "hue", "bed", entry=self.hue, area_id="bedroom"
        )
        change = self.change()
        self.assertEqual(
            (change.added_targets, change.changed_lights),
            ({"room:bedroom"}, {"light.bed"}),
        )
        self.assertEqual(list(self.index.targets)[0], "room:bedroom")

    def test_entity_rename_and_removal(self) -> None:
        self.hass.update_entity("light.floor", entity_id="light.floor_lamp")
        self.assertEqual(
            self.change().changed_lights, {"light.floor", "light.floor_lamp"}
        )
        self.assertEqual(
            self.lights_of("room:living"), ("light.ceiling", "light.floor_lamp")
        )
        self.hass.remove_entity("light.floor_lamp")
        self.assertEqual(self.change().changed_lights, {"light.floor_lamp"})
        self.assertEqual(self.lights_of("room:living"), ("light.ceiling",))

    def test_entity_disabled(self) -> None:
        self.hass.update_entity("light.kitchen", disabled_by="user")
        self.assertEqual(self.change().changed_lights, {"light.kitchen"})
        self.assertNotIn("light.kitchen", self.index.lights)

    def test_device_area_change(self) -> None:
        self.hass.update_device(self.lamp, area_id="kitchen")
        change = self.change()
        self.assertEqual(change.changed_lights, {"light.floor"})
        self.assertIn("light.floor", self.lights_of("room:kitchen"))
        other = self.hass.add_device(self.hue, area_id="living")
        self.hass.update_device(other, area_id="kitchen")
        self.assertEqual(self.changes, [])

    def test_area_rename_and_removal(self) -> None:
        self.hass.update_area("living", name="Lounge")
        self.assertEqual(self.change().changed_targets, {"room:living"})
        self.assertEqual(list(self.index.targets)[:2], ["room:kitchen", "room:living"])
        self.assertEqual(self.index.targets["room:living"].name, "Lounge")
        self.hass.update_area("empty", name="Still empty")
        self.assertEqual(self.changes, [])
        self.hass.remove_area("kitchen")
        change = self.change()
        self.assertEqual(change.removed_targets, {"room:kitchen"})
        self.assertEqual(change.changed_targets, {"zone:downstairs"})
        self.assertEqual(self.index.targets["zone:downstairs"].rooms, ("living",))

    def test_state_changes_without_new_caps_are_ignored(self) -> None:
        attrs = {**CT, "friendly_name": "Kitchen"}
        self.hass.set_state("light.kitchen", "on", attrs)
        self.change()
        self.hass.set_state("light.kitchen", "off", attrs)
        self.hass.set_state(
            "light.kitchen",
            "on",
            {**attrs, "brightness": 20, "color_temp_kelvin": 2700},
        )
        self.hass.set_state("light.kitchen", "unavailable", attrs)
        self.assertEqual(self.changes, [])

    def test_restored_state_replaced_by_the_real_one(self) -> None:
        attrs = {**CT, "friendly_name": "Kitchen"}
        self.hass.set_state("light.kitchen", "unavailable", {**attrs, "restored": True})
        self.change()
        self.hass.set_state("light.kitchen", "on", attrs)
        self.assertEqual(self.changes, [])  # re-read, nothing differs

    def test_group_attribute_appears(self) -> None:
        self.hass.set_state(
            "light.kitchen", "on", {**CT, "group_entities": ["light.hidden"]}
        )
        self.assertEqual(self.change().changed_lights, {"light.kitchen"})
        self.assertNotIn("light.kitchen", self.index.lights)

    def test_group_stays_excluded_while_unavailable(self) -> None:
        restored = {**COLOR, "friendly_name": "Living room", "restored": True}
        self.hass.set_state("light.hue_room", "unavailable", restored)
        self.assertEqual(self.changes, [])
        self.assertNotIn("light.hue_room", self.index.lights)
        self.hass.set_state("light.hue_room", "on", {**COLOR, "is_hue_group": True})
        self.assertEqual(self.changes, [])
        self.hass.set_state("light.hue_room", "on", COLOR)
        self.assertEqual(self.change().changed_lights, {"light.hue_room"})

    def test_group_from_the_registry_before_its_state(self) -> None:
        h = self.hass
        h.add_entity(
            "light.hue_living",
            "hue",
            "grouped-light-1",
            entry=self.hue,
            area_id="living",
            translation_key="hue_grouped_light",
        )
        h.set_state("light.hue_living", "unavailable", {**COLOR, "restored": True})
        h.add_entity(
            "light.deconz_kitchen", "deconz", "00212EFFFF-/groups/1", area_id="kitchen"
        )
        z2m = h.add_config_entry("mqtt", "MQTT")
        group = h.add_device(
            z2m, area_id="kitchen", manufacturer="Zigbee2MQTT", model="Group"
        )
        h.add_entity("light.z2m_kitchen", "mqtt", "1_light_zigbee2mqtt", device=group)
        self.assertEqual(self.changes, [])
        self.assertNotIn("light.hue_living", self.index.lights)
        self.assertNotIn("light.deconz_kitchen", self.index.lights)
        self.assertNotIn("light.z2m_kitchen", self.index.lights)

    def test_state_only_light_comes_and_goes(self) -> None:
        self.hass.set_state(
            "light.new", "on", {"supported_color_modes": ["brightness"]}
        )
        self.assertEqual(self.change().changed_lights, {"light.new"})
        self.assertEqual(
            self.index.lights["light.new"].caps,
            Caps(True, False, False, transition=False),
        )
        self.hass.remove_state("light.new")
        self.assertEqual(self.change().changed_lights, {"light.new"})
        self.assertNotIn("light.new", self.index.lights)
        # A registered light stays, with its registry caps, without a state.
        self.hass.set_state("light.kitchen", "on", CT)
        self.hass.remove_state("light.kitchen")
        self.assertEqual(self.changes, [])
        self.assertIn("light.kitchen", self.index.lights)

    def test_native_effects(self) -> None:
        h = self.hass
        mqtt = h.add_config_entry("mqtt")
        bulb = h.add_device(
            mqtt, identifiers={("mqtt", f"zigbee2mqtt_{IEEE}")}, area_id="living"
        )
        h.add_entity(
            "light.hue_bulb",
            "mqtt",
            f"{IEEE}_light_zigbee2mqtt",
            entry=mqtt,
            device=bulb,
        )
        h.set_state("light.hue_bulb", "on", {**COLOR, "effect_list": HUE_EFFECTS})
        h.add_entity(
            "sensor.hue_bulb_gradient",
            "mqtt",
            f"{IEEE}_gradient_zigbee2mqtt",
            device=bulb,
        )
        self.index.rebuild()
        self.changes.clear()
        caps = self.index.lights["light.hue_bulb"].caps
        self.assertEqual((caps.driver, caps.gradient_points), ("z2m_hue", 9))
        self.index.apply_config(
            Config(
                zones={"downstairs": DOWNSTAIRS},
                settings=Settings(native_effects=False),
            )
        )
        self.assertEqual(self.change().changed_lights, {"light.hue_bulb"})
        caps = self.index.lights["light.hue_bulb"].caps
        self.assertEqual((caps.driver, caps.gradient_points), ("ha", 0))
        self.assertEqual(caps.effects, frozenset())

    def test_bus_settings(self) -> None:
        settings = Settings(
            buses={"hue:bridge1": {"rate": 5.0}, "zigbee:test": {"min_interval": 1.0}},
            light_buses={"light.floor": "zigbee:test", "light.yaml": "hue:bridge1"},
        )
        self.index.apply_config(
            Config(zones={"downstairs": DOWNSTAIRS}, settings=settings)
        )
        self.assertEqual(
            self.change().changed_lights,
            {
                "light.floor",
                "light.yaml",
                "light.ceiling",
                "light.kitchen",
                "light.hidden",
            },
        )
        floor = self.index.lights["light.floor"].bus
        self.assertEqual(
            (floor.id, floor.kind, floor.label, floor.limits),
            ("zigbee:test", "zigbee", "zigbee:test", BusLimits(10.0, 20, 1.0)),
        )
        yaml = self.index.lights["light.yaml"].bus
        self.assertEqual(yaml, self.index.lights["light.kitchen"].bus)
        self.assertEqual(
            (yaml.id, yaml.label, yaml.limits),
            ("hue:bridge1", "Hue bridge - Hue", BusLimits(5.0, 20, 0.4)),
        )
        # Single-light updates keep the settings applied.
        self.hass.update_entity("light.floor", area_id="kitchen")
        self.assertEqual(self.index.lights["light.floor"].bus, floor)

    # ---- plumbing

    def test_mappings_are_updated_in_place(self) -> None:
        lights, targets = self.index.lights, self.index.targets
        self.hass.update_entity("light.ceiling", area_id="kitchen")
        self.assertIs(self.index.lights, lights)
        self.assertIs(self.index.targets, targets)
        self.assertEqual(lights["light.ceiling"].area_id, "kitchen")

    def test_unchanged_rebuild_is_silent(self) -> None:
        self.index.rebuild()
        self.index.apply_config(Config(zones={"downstairs": DOWNSTAIRS}))
        self.assertEqual(self.changes, [])

    def test_unsubscribe_and_unlisten(self) -> None:
        self.unsubscribe()
        self.unsubscribe()
        self.hass.update_entity("light.ceiling", area_id="kitchen")
        self.assertEqual(self.changes, [])
        self.unlisten()
        self.assertEqual(self.hass.bus.listeners, [])


if __name__ == "__main__":
    unittest.main()
