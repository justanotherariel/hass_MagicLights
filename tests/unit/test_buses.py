"""Bus classification against fake registries."""

from __future__ import annotations

import unittest
from unittest import mock

from custom_components.magic_lights import buses
from custom_components.magic_lights.buses import (
    BUS_DEFAULTS,
    BusKind,
    async_classify,
    bus_kind,
)
from homeassistant.helpers import device_registry as dr

from .fake_hass import FakeHass

Z2M_A = "0x00124b0000000001"
Z2M_B = "0x00124b0000000002"


class ClassifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hass = FakeHass()

    def classify(self, entity_id: str) -> tuple[str, str, str]:
        bus = async_classify(self.hass, entity_id)
        self.assertEqual(bus.limits, BUS_DEFAULTS[BusKind(bus.kind)])
        return bus.id, bus.kind, bus.label

    def test_zha(self) -> None:
        h = self.hass
        zha = h.add_config_entry(
            "zha", "SkyConnect", unique_id="epid=dd:dd:dd:dd:dd:dd:dd:dd"
        )
        coord = h.add_device(zha, connections={(dr.CONNECTION_ZIGBEE, "00:01")})
        bulb = h.add_device(
            zha, connections={(dr.CONNECTION_ZIGBEE, "00:02")}, via=coord
        )
        h.add_entity("light.bulb", "zha", "00:02-11", entry=zha, device=bulb)
        self.assertEqual(
            self.classify("light.bulb"),
            (
                "zigbee:zha:epid=dd:dd:dd:dd:dd:dd:dd:dd",
                "zigbee",
                "Zigbee (ZHA) - SkyConnect",
            ),
        )

    def test_two_z2m_instances_are_separate_buses(self) -> None:
        h = self.hass
        mqtt = h.add_config_entry("mqtt", "Mosquitto")
        for n, ieee in (("a", Z2M_A), ("b", Z2M_B)):
            bridge = h.add_device(
                mqtt,
                identifiers={("mqtt", f"zigbee2mqtt_bridge_{ieee}")},
                name="Zigbee2MQTT Bridge",
            )
            dev = h.add_device(
                mqtt, identifiers={("mqtt", f"zigbee2mqtt_0x{n * 16}")}, via=bridge
            )
            h.add_entity(
                f"light.z2m_{n}",
                "mqtt",
                f"0x{n * 16}_light_zigbee2mqtt",
                entry=mqtt,
                device=dev,
            )
        self.assertEqual(
            self.classify("light.z2m_a"),
            (f"zigbee:z2m:{Z2M_A}", "zigbee", "Zigbee2MQTT - Zigbee2MQTT Bridge"),
        )
        self.assertEqual(self.classify("light.z2m_b")[0], f"zigbee:z2m:{Z2M_B}")

    def test_z2m_without_linked_bridge(self) -> None:
        h = self.hass
        mqtt = h.add_config_entry("mqtt")
        dev = h.add_device(mqtt, identifiers={("mqtt", "zigbee2mqtt_0xabc")})
        h.add_entity("light.z", "mqtt", entry=mqtt, device=dev)
        self.assertEqual(
            self.classify("light.z"), ("zigbee:z2m:unknown", "zigbee", "Zigbee2MQTT")
        )

    def test_plain_mqtt(self) -> None:
        h = self.hass
        mqtt = h.add_config_entry("mqtt")
        esp = h.add_device(mqtt, identifiers={("mqtt", "esp_kitchen")}, name="ESP")
        h.add_entity("light.esp", "mqtt", entry=mqtt, device=esp)
        h.add_entity("light.nodev", "mqtt", entry=mqtt)
        hub = h.add_device(mqtt, identifiers={("mqtt", "hub")}, name="Hub")
        child = h.add_device(mqtt, identifiers={("mqtt", "child")}, via=hub)
        h.add_entity("light.child", "mqtt", entry=mqtt, device=child)
        self.assertEqual(self.classify("light.esp"), (f"ip:mqtt:{esp.id}", "ip", "ESP"))
        self.assertEqual(
            self.classify("light.nodev"), ("ip:mqtt:light.nodev", "ip", "light.nodev")
        )
        self.assertEqual(
            self.classify("light.child"), (f"bridge:mqtt:{hub.id}", "bridge", "Hub")
        )

    def test_zwave_js_ui_and_native_share_a_bus(self) -> None:
        h = self.hass
        mqtt = h.add_config_entry("mqtt")
        zui = h.add_device(
            mqtt, identifiers={("mqtt", "zwavejs2mqtt_0xe1a2b3c4_node12")}
        )
        h.add_entity("light.zui", "mqtt", entry=mqtt, device=zui)
        zw = h.add_config_entry("zwave_js", "Z-Wave JS", unique_id="3785536452")
        zd = h.add_device(zw, identifiers={("zwave_js", "3785536452-5")})
        h.add_entity("light.zw", "zwave_js", entry=zw, device=zd)
        self.assertEqual(self.classify("light.zui")[:2], ("zwave:3785536452", "zwave"))
        self.assertEqual(
            self.classify("light.zw"),
            ("zwave:3785536452", "zwave", "Z-Wave - Z-Wave JS"),
        )

    def test_hue_and_deconz(self) -> None:
        h = self.hass
        hue = h.add_config_entry("hue", "Hue Bridge", unique_id="001788fffe000001")
        h.add_entity("light.hue", "hue", entry=hue)
        deconz = h.add_config_entry("deconz", "Phoscon", unique_id="00212EFFFF")
        h.add_entity("light.deconz", "deconz", entry=deconz)
        self.assertEqual(
            self.classify("light.hue"),
            ("hue:001788fffe000001", "hue", "Hue bridge - Hue Bridge"),
        )
        self.assertEqual(
            self.classify("light.deconz"),
            ("zigbee:deconz:00212EFFFF", "zigbee", "Zigbee (deCONZ) - Phoscon"),
        )

    def test_homekit_controller_transports(self) -> None:
        h = self.hass
        coap = h.add_config_entry(
            "homekit_controller", "Nanoleaf", data={"Connection": "CoAP"}
        )
        ble = h.add_config_entry(
            "homekit_controller", "Eve", data={"Connection": "BLE"}
        )
        ip = h.add_config_entry("homekit_controller", "Lamp", data={"Connection": "IP"})
        bridge = h.add_config_entry(
            "homekit_controller", "Bridge", data={"Connection": "IP"}
        )
        root = h.add_device(bridge)
        acc = h.add_device(bridge, via=root)
        h.add_entity("light.coap", "homekit_controller", entry=coap)
        h.add_entity("light.ble", "homekit_controller", entry=ble)
        h.add_entity("light.ip", "homekit_controller", entry=ip)
        h.add_entity("light.acc", "homekit_controller", entry=bridge, device=acc)
        self.assertEqual(
            self.classify("light.coap"), ("thread:default", "thread", "Thread default")
        )
        self.assertEqual(
            self.classify("light.ble"),
            (f"ble:homekit_controller:{ble.entry_id}", "ble", "Eve"),
        )
        self.assertEqual(
            self.classify("light.ip"),
            (f"ip:homekit_controller:{ip.entry_id}", "ip", "Lamp"),
        )
        self.assertEqual(
            self.classify("light.acc"),
            (
                f"bridge:homekit_controller:{bridge.entry_id}",
                "bridge",
                "HomeKit bridge - Bridge",
            ),
        )

    def test_yeelight_quota_unless_music_mode(self) -> None:
        h = self.hass
        plain = h.add_config_entry("yeelight", "Desk")
        music = h.add_config_entry(
            "yeelight", "Strip", options={"use_music_mode": True}
        )
        h.add_entity("light.desk", "yeelight", entry=plain)
        h.add_entity("light.strip", "yeelight", entry=music)
        self.assertEqual(
            self.classify("light.desk"),
            (f"yeelight:{plain.entry_id}", "yeelight", "Desk"),
        )
        self.assertEqual(
            self.classify("light.strip"),
            (f"ip:yeelight:{music.entry_id}", "ip", "Strip"),
        )

    def test_switch_as_x_follows_the_wrapped_switch(self) -> None:
        h = self.hass
        zha = h.add_config_entry("zha", "ZHA", unique_id="epid=1")
        plug = h.add_device(zha)
        switch = h.add_entity("switch.plug", "zha", entry=zha, device=plug)
        by_options = h.add_config_entry("switch_as_x", "Lamp")
        h.add_entity(
            "light.lamp",
            "switch_as_x",
            entry=by_options,
            options={"switch_as_x": {"entity_id": "switch.plug"}},
        )
        by_entry = h.add_config_entry(
            "switch_as_x", "Lamp 2", options={"entity_id": switch.id}
        )
        h.add_entity("light.lamp2", "switch_as_x", entry=by_entry)
        self.assertEqual(self.classify("light.lamp")[0], "zigbee:zha:epid=1")
        self.assertEqual(self.classify("light.lamp2")[0], "zigbee:zha:epid=1")

    def test_matter(self) -> None:
        h = self.hass
        matter = h.add_config_entry("matter", "Matter")
        hub = h.add_device(matter, name="Aqara Hub")
        bridged = h.add_device(matter, via=hub)
        thread = h.add_device(matter, name="Nanoleaf")
        wifi = h.add_device(matter, name="Wiz")
        bare = h.add_device(matter, name="Bare")
        for name, device in (
            ("bridged", bridged),
            ("thread", thread),
            ("wifi", wifi),
            ("bare", bare),
        ):
            h.add_entity(f"light.{name}", "matter", entry=matter, device=device)
        h.add_entity(
            "sensor.thread_ch",
            "matter",
            entry=matter,
            device=thread,
            translation_key="thread_channel",
            disabled_by="integration",
        )
        h.add_entity(
            "sensor.rssi",
            "matter",
            entry=matter,
            device=wifi,
            translation_key="wifi_rssi",
        )
        self.assertEqual(
            self.classify("light.bridged"),
            (f"bridge:matter:{hub.id}", "bridge", "Matter bridge - Aqara Hub"),
        )
        # No Matter server here: the probe fails and the registry decides.
        self.assertEqual(
            self.classify("light.thread")[:2], ("thread:default", "thread")
        )
        self.assertEqual(
            self.classify("light.wifi"), (f"ip:matter:{wifi.id}", "ip", "Wiz")
        )
        self.assertEqual(
            self.classify("light.bare"),
            (f"unknown:matter:{bare.id}", "unknown", "Bare"),
        )
        with mock.patch.object(
            buses, "_matter_probe", return_value=("thread", "0011aabb")
        ):
            self.assertEqual(self.classify("light.bare")[0], "thread:0011aabb")

    def test_generic_rules(self) -> None:
        h = self.hass
        h.add_integration("tuya", iot_class="cloud_push")
        h.add_integration("esphome", iot_class="local_push", integration_type="device")
        h.add_integration("acme", iot_class="local_polling", integration_type="hub")
        tuya = h.add_config_entry("tuya", "Tuya")
        h.add_entity("light.tuya", "tuya", entry=tuya, device=h.add_device(tuya))
        esp = h.add_config_entry("esphome", "esp-led")
        h.add_entity("light.esp", "esphome", entry=esp, device=h.add_device(esp))
        acme = h.add_config_entry("acme", "Acme hub")
        gateway = h.add_device(acme, name="Gateway")
        h.add_entity(
            "light.behind", "acme", entry=acme, device=h.add_device(acme, via=gateway)
        )
        h.add_entity("light.hubbed", "acme", entry=acme, device=h.add_device(acme))
        bt = h.add_device(
            acme, connections={(dr.CONNECTION_BLUETOOTH, "aa")}, name="BT"
        )
        h.add_entity("light.bt", "acme", entry=acme, device=bt)
        zb = h.add_config_entry("other", "Other")
        h.add_entity(
            "light.zb",
            "other",
            entry=zb,
            device=h.add_device(zb, connections={(dr.CONNECTION_ZIGBEE, "00:03")}),
        )
        lifx = h.add_config_entry("lifx", "LIFX")  # integration not loaded
        lamp = h.add_device(lifx, name="Lamp")
        h.add_entity("light.lifx", "lifx", entry=lifx, device=lamp)
        h.add_entity("light.lifx_nodev", "lifx", entry=lifx)
        h.add_entity("light.template", "template", "uid")
        self.assertEqual(
            self.classify("light.tuya"),
            (f"cloud:tuya:{tuya.entry_id}", "cloud", "Tuya (cloud)"),
        )
        self.assertEqual(
            self.classify("light.esp"), (f"ip:esphome:{esp.entry_id}", "ip", "esp-led")
        )
        self.assertEqual(
            self.classify("light.behind"),
            (f"bridge:acme:{gateway.id}", "bridge", "Gateway"),
        )
        self.assertEqual(
            self.classify("light.hubbed"),
            (f"bridge:acme:{acme.entry_id}", "bridge", "Acme hub"),
        )
        self.assertEqual(self.classify("light.bt"), (f"ble:acme:{bt.id}", "ble", "BT"))
        self.assertEqual(
            self.classify("light.zb"),
            (f"zigbee:other:{zb.entry_id}", "zigbee", "Other"),
        )
        self.assertEqual(
            self.classify("light.lifx"), (f"ip:lifx:{lamp.id}", "ip", "Lamp")
        )
        self.assertEqual(
            self.classify("light.lifx_nodev"),
            (f"ip:lifx:{lifx.entry_id}", "ip", "LIFX"),
        )
        self.assertEqual(
            self.classify("light.template"),
            ("entity:light.template", "unknown", "light.template"),
        )

    def test_bluetooth_device_integrations(self) -> None:
        h = self.hass
        h.add_integration(
            "led_ble", iot_class="local_polling", integration_type="device"
        )
        h.add_integration("shelly", iot_class="local_push", integration_type="device")
        led = h.add_config_entry("led_ble", "LED strip")
        strip = h.add_device(
            led, connections={(dr.CONNECTION_BLUETOOTH, "aa:bb")}, name="Strip"
        )
        h.add_entity("light.strip", "led_ble", entry=led, device=strip)
        shelly = h.add_config_entry("shelly", "Shelly dimmer")
        dimmer = h.add_device(
            shelly,
            connections={
                (dr.CONNECTION_BLUETOOTH, "cc:dd"),
                (dr.CONNECTION_NETWORK_MAC, "cc:dd"),
            },
        )
        h.add_entity("light.dimmer", "shelly", entry=shelly, device=dimmer)
        self.assertEqual(
            self.classify("light.strip"), (f"ble:led_ble:{strip.id}", "ble", "Strip")
        )
        self.assertEqual(
            self.classify("light.dimmer"),
            (f"ip:shelly:{shelly.entry_id}", "ip", "Shelly dimmer"),
        )

    def test_unregistered_and_orphaned(self) -> None:
        h = self.hass
        self.assertEqual(
            self.classify("light.yaml_only"),
            ("entity:light.yaml_only", "unknown", "light.yaml_only"),
        )
        # A zha entity whose config entry is gone takes the generic path.
        h.add_entity("light.orphan", "zha", config_entry_id="gone")
        self.assertEqual(self.classify("light.orphan")[1], "unknown")


class BusKindTest(unittest.TestCase):
    def test_kind_from_id(self) -> None:
        self.assertEqual(bus_kind("zigbee:z2m:0x1"), BusKind.ZIGBEE)
        self.assertEqual(bus_kind("yeelight:abc"), BusKind.YEELIGHT)
        self.assertEqual(bus_kind("entity:light.x"), BusKind.UNKNOWN)
        self.assertEqual(bus_kind("custom"), BusKind.UNKNOWN)

    def test_every_kind_has_defaults(self) -> None:
        self.assertEqual(set(BUS_DEFAULTS), set(BusKind))
        zigbee = BUS_DEFAULTS[BusKind.ZIGBEE]
        self.assertEqual(
            (zigbee.rate, zigbee.burst, zigbee.min_interval), (10.0, 20, 0.5)
        )
        self.assertEqual(BUS_DEFAULTS[BusKind.YEELIGHT].rate, 1.0)


if __name__ == "__main__":
    unittest.main()
