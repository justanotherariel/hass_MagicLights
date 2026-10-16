"""Service data, Z2M Hue payloads (golden JSON), topic resolution and drivers."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any
import unittest

from custom_components.magic_lights.models import (
    OFF,
    Caps,
    Color,
    Light,
    LightState,
    normalise,
)
from custom_components.magic_lights.native import (
    HaDriver,
    build_drivers,
    stop_effect_value,
    to_service_data,
)
from custom_components.magic_lights.native.z2m_hue import (
    Z2MHueDriver,
    hue_payloads,
    z2m_command_topic,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from .fake_hass import FakeHass
from .fakes import ZIGBEE

COLOR = Caps(brightness=True, color=True, color_temp=True)
CT_ONLY = Caps(brightness=True, color=False, color_temp=True)
Z2M_HUE_EFFECTS = frozenset(
    {
        "candle",
        "fireplace",
        "sunrise",
        "none",
        "finish_effect",
        "stop_effect",
        "stop_hue_effect",
    }
)
HUE = Caps(brightness=True, color=True, color_temp=True, effects=Z2M_HUE_EFFECTS)
IEEE = "0x0017880100000001"

# Light attributes as a Z2M Hue colour light reports them.
HUE_ATTRS = {
    "supported_color_modes": ["color_temp", "xy"],
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 6535,
    "effect_list": sorted(Z2M_HUE_EFFECTS),
    "supported_features": 44,  # EFFECT | FLASH | TRANSITION
}


class StopEffectValueTest(unittest.TestCase):
    def test_vocabularies(self) -> None:
        cases = [
            ({"candle", "off"}, "off"),
            ({"Rainbow", "None"}, "None"),
            (Z2M_HUE_EFFECTS, "none"),
            ({"candle", "stop_hue_effect"}, "stop_hue_effect"),
            ({"Solid", "Blink"}, "Solid"),  # WLED
            ({"Solid", "None"}, "None"),
            ({"Blink"}, None),
            (set(), None),
        ]
        for effects, expected in cases:
            with self.subTest(effects=effects):
                caps = Caps(True, True, True, effects=frozenset(effects))
                self.assertEqual(stop_effect_value(caps), expected)


class ToServiceDataTest(unittest.TestCase):
    def data(
        self,
        state: LightState,
        prev: LightState | None = None,
        caps: Caps = COLOR,
    ) -> tuple[str, dict[str, Any]]:
        return to_service_data(normalise(state, caps), prev, caps)

    def test_off(self) -> None:
        self.assertEqual(self.data(OFF), ("turn_off", {}))
        self.assertEqual(
            self.data(OFF.replace(transition=2.5)), ("turn_off", {"transition": 2.5})
        )
        self.assertEqual(
            self.data(OFF.replace(transition=0)), ("turn_off", {"transition": 0})
        )

    def test_brightness_and_colour(self) -> None:
        self.assertEqual(
            self.data(LightState(brightness=0.5, color=Color(kelvin=2700))),
            ("turn_on", {"brightness": 128, "color_temp_kelvin": 2700}),
        )
        self.assertEqual(
            self.data(LightState(brightness=0.0001, color=Color(xy=(0.5, 0.4)))),
            ("turn_on", {"brightness": 1, "xy_color": [0.5, 0.4]}),
        )
        self.assertEqual(self.data(LightState()), ("turn_on", {}))

    def test_ct_only_gets_kelvin(self) -> None:
        service, data = self.data(LightState(color=Color(xy=(0.5, 0.4))), caps=CT_ONLY)
        self.assertEqual(service, "turn_on")
        self.assertEqual(set(data), {"color_temp_kelvin"})

    def test_transition_is_kept_when_zero_and_clamped(self) -> None:
        self.assertEqual(self.data(LightState(transition=0))[1], {"transition": 0})
        self.assertEqual(
            self.data(LightState(transition=1.234))[1], {"transition": 1.23}
        )
        self.assertEqual(
            self.data(LightState(transition=10000))[1], {"transition": 6553}
        )
        no_transition = Caps(True, True, True, transition=False)
        self.assertEqual(self.data(LightState(transition=3), caps=no_transition)[1], {})

    def test_effect(self) -> None:
        caps = Caps(True, True, True, effects=frozenset({"candle", "off"}))
        self.assertEqual(
            self.data(LightState(effect="candle"), caps=caps)[1], {"effect": "candle"}
        )
        self.assertEqual(self.data(LightState(effect="disco"), caps=caps)[1], {})

    def test_leaving_an_effect_stops_it_per_vocabulary(self) -> None:
        prev = LightState(brightness=0.5, effect="candle")
        cases = [
            (frozenset({"candle", "off"}), {"effect": "off"}),
            (Z2M_HUE_EFFECTS, {"effect": "none"}),
            (frozenset({"candle", "None"}), {"effect": "None"}),
            (frozenset({"candle", "stop_hue_effect"}), {"effect": "stop_hue_effect"}),
            (frozenset({"Solid", "Fireworks"}), {"effect": "Solid"}),
            (frozenset({"candle"}), {}),
        ]
        for effects, extra in cases:
            with self.subTest(effects=effects):
                caps = Caps(True, True, True, effects=effects)
                self.assertEqual(
                    self.data(LightState(brightness=0.5), prev, caps)[1],
                    {"brightness": 128, **extra},
                )
        caps = Caps(True, True, True, effects=frozenset({"candle", "off"}))
        self.assertEqual(self.data(LightState(), LightState(), caps)[1], {})
        self.assertEqual(self.data(LightState(), None, caps)[1], {})
        self.assertEqual(self.data(OFF, prev, caps), ("turn_off", {}))

    def test_effect_stopped_after_the_light_was_off(self) -> None:
        # The sender's prev keeps the last effect across an off in between.
        caps = Caps(True, True, True, effects=frozenset({"colorloop", "off"}))
        self.assertEqual(
            self.data(
                LightState(brightness=0.5), OFF.replace(effect="colorloop"), caps
            ),
            ("turn_on", {"brightness": 128, "effect": "off"}),
        )


def _wire(payloads: list[dict[str, Any]]) -> list[str]:
    return [json.dumps(p) for p in payloads]


class HuePayloadsGoldenTest(unittest.TestCase):
    """Exact wire JSON, in send order."""

    def payloads(
        self,
        state: LightState,
        prev: LightState | None = None,
        caps: Caps = HUE,
        running: str | None = None,
    ) -> list[str]:
        return _wire(hue_payloads(normalise(state, caps), prev, caps, running))

    def test_candle(self) -> None:
        state = LightState(
            brightness=0.35,
            color=Color(xy=(0.561, 0.4042)),
            effect="candle",
            effect_speed=0.5,
        )
        self.assertEqual(
            self.payloads(state),
            [
                '{"state": "ON", "effect": "candle", "effect_speed": 0.5, "color": {"x": 0.561, "y": 0.4042}}',
                '{"brightness": 89}',
            ],
        )

    def test_fireplace_with_xy(self) -> None:
        state = LightState(
            color=Color(xy=(0.605, 0.38)), effect="fireplace", effect_speed=0.6
        )
        self.assertEqual(
            self.payloads(state),
            [
                '{"state": "ON", "effect": "fireplace", "effect_speed": 0.6, "color": {"x": 0.605, "y": 0.38}}'
            ],
        )

    def test_brightness_comes_after_the_effect_and_never_below_two(self) -> None:
        state = LightState(brightness=0.001, effect="sunrise")
        self.assertEqual(
            self.payloads(state),
            ['{"state": "ON", "effect": "sunrise"}', '{"brightness": 2}'],
        )

    def test_ct_only_effect_has_no_colour(self) -> None:
        caps = Caps(True, False, True, effects=Z2M_HUE_EFFECTS)
        state = LightState(color=Color(xy=(0.561, 0.4042)), effect="candle")
        self.assertEqual(
            self.payloads(state, caps=caps), ['{"state": "ON", "effect": "candle"}']
        )

    def test_gradient_after_an_effect(self) -> None:
        caps = Caps(True, True, True, effects=Z2M_HUE_EFFECTS, gradient_points=9)
        state = LightState(
            brightness=0.6,
            gradient=(
                Color(xy=(0.6441, 0.3283)),
                Color(xy=(0.4806, 0.4220)),
                Color(kelvin=2700),
            ),
            transition=1.25,
        )
        prev = LightState(effect="candle")
        self.assertEqual(
            self.payloads(state, prev, caps),
            [
                '{"effect": "none"}',
                '{"state": "ON", "gradient": ["#ff592d", "#ffc960", "#ffa757"], '
                '"gradient_style": "linear", "brightness": 153, "transition": 1.2}',
            ],
        )

    def test_gradient_without_transition(self) -> None:
        caps = Caps(True, True, True, gradient_points=9)
        state = LightState(gradient=(Color(xy=(0.6441, 0.3283)),), transition=0)
        self.assertEqual(
            self.payloads(state, None, caps),
            ['{"state": "ON", "gradient": ["#ff592d"], "gradient_style": "linear"}'],
        )

    def test_leaving_native_mode_sends_only_the_stop(self) -> None:
        prev = LightState(effect="candle")
        self.assertEqual(
            self.payloads(LightState(brightness=1.0, color=Color(kelvin=2700)), prev),
            ['{"effect": "none"}'],
        )
        self.assertEqual(self.payloads(LightState(brightness=1.0)), [])

    def test_running_effect_only_gets_the_brightness(self) -> None:
        candle = LightState(
            brightness=0.35,
            color=Color(xy=(0.561, 0.4042)),
            effect="candle",
            effect_speed=0.5,
        )
        prev = normalise(candle, HUE)
        dimmer = candle.replace(brightness=0.3)
        brightness = '{"brightness": 76}'
        self.assertEqual(self.payloads(dimmer, prev, running="candle"), [brightness])
        self.assertEqual(
            self.payloads(candle.replace(brightness=None), prev, running="candle"), []
        )
        self.assertEqual(
            self.payloads(dimmer.replace(effect_speed=0.6), prev, running="candle"),
            [
                '{"state": "ON", "effect": "candle", "effect_speed": 0.6, "color": {"x": 0.561, "y": 0.4042}}',
                brightness,
            ],
        )
        # Restarted unless the light reports it and we left it on with it.
        for before, running in (
            (prev, "none"),
            (prev, None),
            (prev.replace(on=False), "candle"),
            (None, "candle"),
        ):
            with self.subTest(prev=before, running=running):
                self.assertEqual(
                    self.payloads(dimmer, before, running=running),
                    [
                        '{"state": "ON", "effect": "candle", "effect_speed": 0.5, "color": {"x": 0.561, "y": 0.4042}}',
                        brightness,
                    ],
                )

    def test_off(self) -> None:
        self.assertEqual(
            self.payloads(OFF, LightState(effect="candle")), ['{"effect": "none"}']
        )
        self.assertEqual(self.payloads(OFF, LightState()), [])
        self.assertEqual(self.payloads(OFF), [])


class _Mqtt:
    def __init__(self, entities: dict[str, Any]) -> None:
        self.debug_info_entities = entities


def _discovery(topic: str, origin: str = "Zigbee2MQTT") -> dict[str, Any]:
    return {
        "subscriptions": {},
        "transmitted": {},
        "discovery_data": {
            "discovery_payload": {"command_topic": topic, "origin": {"name": origin}}
        },
    }


class Z2MCommandTopicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hass = h = FakeHass()
        mqtt = h.add_config_entry("mqtt")
        device = h.add_device(mqtt, identifiers={("mqtt", f"zigbee2mqtt_{IEEE}")})
        h.add_entity(
            "light.strip",
            "mqtt",
            f"{IEEE}_light_zigbee2mqtt",
            entry=mqtt,
            device=device,
        )

    def test_discovery_payload_wins(self) -> None:
        self.hass.data["mqtt"] = _Mqtt(
            {"light.strip": _discovery("z2m/Kitchen Strip/set")}
        )
        self.assertEqual(
            z2m_command_topic(self.hass, "light.strip"), "z2m/Kitchen Strip/set"
        )

    def test_registry_fallback(self) -> None:
        expected = f"zigbee2mqtt/{IEEE}/set"
        self.assertEqual(z2m_command_topic(self.hass, "light.strip"), expected)
        self.hass.data["mqtt"] = _Mqtt(
            {"light.strip": _discovery("x/set", origin="Tasmota")}
        )
        self.assertEqual(z2m_command_topic(self.hass, "light.strip"), expected)
        self.hass.data["mqtt"] = SimpleNamespace()  # internals changed shape
        self.assertEqual(z2m_command_topic(self.hass, "light.strip"), expected)

    def test_not_z2m(self) -> None:
        h = self.hass
        mqtt = h.add_config_entry("mqtt")
        other = h.add_device(mqtt, identifiers={("mqtt", "esp_kitchen")})
        h.add_entity("light.esp", "mqtt", "esp_light", entry=mqtt, device=other)
        h.add_entity("light.nodev", "mqtt", f"{IEEE}_light_zigbee2mqtt_2", entry=mqtt)
        h.add_entity("light.zha", "zha", f"{IEEE}_light_zigbee2mqtt")
        for entity_id in ("light.esp", "light.nodev", "light.zha", "light.missing"):
            with self.subTest(entity_id=entity_id):
                self.assertIsNone(z2m_command_topic(h, entity_id))


class _Recorder:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def __call__(self, hass: Any, topic: str, payload: str) -> None:
        self.published.append((topic, payload))


def _light(entity_id: str, caps: Caps) -> Light:
    return Light(entity_id, entity_id, None, None, ZIGBEE, caps)


class HaDriverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hass = FakeHass()
        self.driver = HaDriver(self.hass)

    def test_detect_from_state(self) -> None:
        self.hass.set_state(
            "light.a",
            "on",
            {
                "supported_color_modes": ["color_temp", "hs"],
                "min_color_temp_kelvin": 2202,
                "max_color_temp_kelvin": 6500,
                "effect_list": ["colorloop", "off"],
                "supported_features": 36,
            },
        )
        self.assertEqual(
            self.driver.detect(self.hass, "light.a"),
            Caps(
                brightness=True,
                color=True,
                color_temp=True,
                min_kelvin=2202,
                max_kelvin=6500,
                transition=True,
                effects=frozenset({"colorloop", "off"}),
            ),
        )

    def test_detect_modes(self) -> None:
        cases = [
            (["onoff"], Caps(False, False, False, transition=False)),
            (["brightness"], Caps(True, False, False, transition=False)),
            (["color_temp"], Caps(True, False, True, transition=False)),
            (["xy"], Caps(True, True, False, transition=False)),
        ]
        for modes, expected in cases:
            with self.subTest(modes=modes):
                self.hass.set_state("light.m", "off", {"supported_color_modes": modes})
                self.assertEqual(self.driver.detect(self.hass, "light.m"), expected)

    def test_detect_from_registry_before_the_state_exists(self) -> None:
        self.hass.add_entity(
            "light.r",
            "hue",
            capabilities={
                "supported_color_modes": ["color_temp"],
                "max_color_temp_kelvin": 5000,
            },
            supported_features=32,
        )
        expected = Caps(True, False, True, max_kelvin=5000, transition=True)
        self.assertEqual(self.driver.detect(self.hass, "light.r"), expected)
        self.hass.set_state("light.r", "unavailable", {"friendly_name": "R"})
        self.assertEqual(self.driver.detect(self.hass, "light.r"), expected)

    def test_detect_unknown_light(self) -> None:
        self.assertEqual(
            self.driver.detect(self.hass, "light.none"),
            Caps(False, False, False, transition=False),
        )

    async def test_send(self) -> None:
        context = Context()
        light = _light("light.a", COLOR)
        await self.driver.send(
            light, normalise(LightState(brightness=1.0), COLOR), None, context
        )
        await self.driver.send(light, OFF.replace(transition=1.0), None, context)
        self.assertEqual(
            self.hass.services.calls,
            [
                (
                    "light",
                    "turn_on",
                    {"entity_id": "light.a", "brightness": 255},
                    True,
                    context,
                ),
                (
                    "light",
                    "turn_off",
                    {"entity_id": "light.a", "transition": 1.0},
                    True,
                    context,
                ),
            ],
        )

    async def test_send_raises_the_integration_error(self) -> None:
        self.hass.services.errors["light.a"] = HomeAssistantError("unreachable")
        with self.assertRaisesRegex(HomeAssistantError, "unreachable"):
            await self.driver.send(
                _light("light.a", COLOR), LightState(), None, Context()
            )


class Z2MHueDriverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hass = h = FakeHass()
        self.recorder = _Recorder()
        self.ha = HaDriver(h)
        self.driver = Z2MHueDriver(h, z2m_command_topic, self.recorder, self.ha)
        mqtt = h.add_config_entry("mqtt")
        self.device = h.add_device(mqtt, identifiers={("mqtt", f"zigbee2mqtt_{IEEE}")})
        h.add_entity(
            "light.hue",
            "mqtt",
            f"{IEEE}_light_zigbee2mqtt",
            entry=mqtt,
            device=self.device,
        )
        h.set_state("light.hue", "off", HUE_ATTRS)

    def test_detect(self) -> None:
        caps = self.driver.detect(self.hass, "light.hue")
        self.assertEqual(
            caps,
            dataclasses.replace(
                self.ha.detect(self.hass, "light.hue"), driver="z2m_hue"
            ),
        )
        self.assertEqual(caps.gradient_points, 0)
        self.assertEqual(self.driver.topics, {"light.hue": f"zigbee2mqtt/{IEEE}/set"})

    def test_detect_gradient(self) -> None:
        self.hass.add_entity(
            "select.hue_gradient_scene",
            "mqtt",
            f"{IEEE}_gradient_scene_zigbee2mqtt",
            device=self.device,
            disabled_by="integration",
        )
        caps = self.driver.detect(self.hass, "light.hue")
        self.assertEqual((caps.driver, caps.gradient_points), ("z2m_hue", 9))

    def test_detect_declines(self) -> None:
        h = self.hass
        h.set_state(
            "light.other", "on", {**HUE_ATTRS, "effect_list": ["candle", "none"]}
        )
        self.assertIsNone(self.driver.detect(h, "light.other"))  # not a Hue definition
        h.set_state("light.yaml", "on", HUE_ATTRS)
        self.assertIsNone(self.driver.detect(h, "light.yaml"))  # no topic

    async def _send(
        self, state: LightState, prev: LightState | None
    ) -> list[tuple[str, str]]:
        caps = self.driver.detect(self.hass, "light.hue")
        await self.driver.send(
            _light("light.hue", caps), normalise(state, caps), prev, Context()
        )
        return [(c[1], c[2]) for c in self.hass.services.calls]

    async def test_effect_goes_over_mqtt_only(self) -> None:
        calls = await self._send(LightState(brightness=0.5, effect="candle"), None)
        self.assertEqual(calls, [])
        self.assertEqual(
            self.recorder.published,
            [
                (f"zigbee2mqtt/{IEEE}/set", '{"state": "ON", "effect": "candle"}'),
                (f"zigbee2mqtt/{IEEE}/set", '{"brightness": 128}'),
            ],
        )

    async def test_leaving_an_effect_stops_then_calls_ha_once(self) -> None:
        calls = await self._send(
            LightState(brightness=1.0), LightState(effect="candle")
        )
        self.assertEqual(
            self.recorder.published, [(f"zigbee2mqtt/{IEEE}/set", '{"effect": "none"}')]
        )
        self.assertEqual(
            calls, [("turn_on", {"entity_id": "light.hue", "brightness": 255})]
        )

    async def test_running_effect_gets_only_the_brightness(self) -> None:
        candle = LightState(brightness=0.5, effect="candle")
        caps = self.driver.detect(self.hass, "light.hue")
        self.hass.set_state("light.hue", "on", {**HUE_ATTRS, "effect": "candle"})
        calls = await self._send(
            candle.replace(brightness=0.4), normalise(candle, caps)
        )
        self.assertEqual(calls, [])
        self.assertEqual(
            self.recorder.published,
            [(f"zigbee2mqtt/{IEEE}/set", '{"brightness": 102}')],
        )

    async def test_effect_stopped_after_the_light_was_off(self) -> None:
        calls = await self._send(
            LightState(brightness=1.0), OFF.replace(effect="candle")
        )
        self.assertEqual(
            self.recorder.published, [(f"zigbee2mqtt/{IEEE}/set", '{"effect": "none"}')]
        )
        self.assertEqual(
            calls, [("turn_on", {"entity_id": "light.hue", "brightness": 255})]
        )

    async def test_off_after_effect(self) -> None:
        calls = await self._send(OFF, LightState(effect="candle"))
        self.assertEqual(
            self.recorder.published, [(f"zigbee2mqtt/{IEEE}/set", '{"effect": "none"}')]
        )
        self.assertEqual(calls, [("turn_off", {"entity_id": "light.hue"})])

    async def test_plain_state_goes_through_ha(self) -> None:
        calls = await self._send(LightState(brightness=1.0), LightState())
        self.assertEqual(self.recorder.published, [])
        self.assertEqual(
            calls, [("turn_on", {"entity_id": "light.hue", "brightness": 255})]
        )

    async def test_unknown_topic_falls_back(self) -> None:
        light = _light("light.unknown", HUE)
        await self.driver.send(
            light, normalise(LightState(effect="candle"), HUE), None, Context()
        )
        self.assertEqual(self.recorder.published, [])
        self.assertEqual(
            [(c[1], c[2]) for c in self.hass.services.calls],
            [("turn_on", {"entity_id": "light.unknown", "effect": "candle"})],
        )


class BuildDriversTest(unittest.TestCase):
    def test_keys(self) -> None:
        drivers = build_drivers(FakeHass())
        self.assertEqual(list(drivers), ["ha", "z2m_hue"])
        self.assertEqual({d.key for d in drivers.values()}, {"ha", "z2m_hue"})


if __name__ == "__main__":
    unittest.main()
