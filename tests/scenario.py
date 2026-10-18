"""A small demo home shared by screenshots and integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .harness import Harness

HUE_EFFECTS = [
    "blink", "breathe", "okay", "channel_change", "candle", "fireplace", "colorloop",
    "sunset", "sunrise", "sparkle", "opal", "glisten", "underwater", "cosmos",
    "sunbeam", "enchant", "finish_effect", "stop_effect", "stop_hue_effect", "none",
]  # fmt: skip

DEMO_LIGHTS = [
    {"name": "Living Ceiling", "modes": ["color_temp", "xy"], "on": True},
    {
        "name": "Living Floor Lamp",
        "modes": ["color_temp"],
        "min_kelvin": 2200,
        "max_kelvin": 6500,
    },
    {"name": "Living TV Strip", "modes": ["color_temp", "xy"], "effects": HUE_EFFECTS},
    {"name": "Living Sofa Lamp", "modes": ["brightness"]},
    {"name": "Kitchen Spot 1", "modes": ["color_temp"], "quantize": True},
    {"name": "Kitchen Spot 2", "modes": ["color_temp"], "quantize": True},
    {"name": "Kitchen Island", "modes": ["color_temp", "xy"], "effects": HUE_EFFECTS},
    {"name": "Bedroom Ceiling", "modes": ["color_temp", "xy"]},
    {"name": "Bedroom Nightstand", "modes": ["hs"], "transition": False},
    {"name": "Hallway", "modes": ["onoff"]},
    {"name": "Office Desk", "modes": ["color_temp"], "jitter": 2, "delay": 0.1},
]

DEMO_AREAS = {
    "Living Room": [
        "light.living_ceiling", "light.living_floor_lamp", "light.living_tv_strip", "light.living_sofa_lamp",
    ],
    "Kitchen": ["light.kitchen_spot_1", "light.kitchen_spot_2", "light.kitchen_island"],
    "Bedroom": ["light.bedroom_ceiling", "light.bedroom_nightstand"],
    "Hallway": ["light.hallway"],
    "Office": ["light.office_desk"],
}  # fmt: skip

# Kitchen and living room share one Zigbee network in the bus tests.
DEMO_SETTINGS = {
    "light_buses": {
        "light.kitchen_spot_1": "zigbee:test",
        "light.kitchen_spot_2": "zigbee:test",
        "light.kitchen_island": "zigbee:test",
    }
}


async def demo_setup(h: Harness) -> None:
    """Install Magic Lights on the demo home."""
    await h.setup_integration()


async def demo_scenes(h: Harness) -> None:
    """Make the home look lived in: a zone, a user scene, live scenes."""
    await h.ws(
        {
            "type": "magic_lights/zone/save",
            "zone": {
                "id": "",
                "name": "Downstairs",
                "icon": "mdi:home-floor-0",
                "rooms": ["living_room", "kitchen"],
                "lights": [],
            },
        }
    )
    await h.ws(
        {
            "type": "magic_lights/scene/save",
            "scene": {
                "id": "s_0a1b2c3d",
                "name": "Movie night",
                "icon": "mdi:movie-open",
                "preset": None,
                "layers": [
                    {
                        "lights": ["room:living_room"],
                        "effect": "white",
                        "params": {"kelvin": 2200, "brightness": 30},
                    },
                    {
                        "lights": ["light.living_tv_strip"],
                        "effect": "palette",
                        "params": {
                            "palette": [{"xy": [0.17, 0.05]}, {"xy": [0.45, 0.2]}],
                            "brightness": 50,
                        },
                    },
                ],
                "others": "off",
                "brightness": 100,
                "transition": None,
            },
        }
    )
    await h.call("activate_scene", "select.living_room_scene", scene="savanna_sunset")
    await h.call("activate_scene", "select.bedroom_scene", scene="relax")
    await h.call("activate_scene", "select.office_scene", scene="concentrate")
    # A lamp dimmed by hand: the bedroom shows one manual light.
    await h.hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.bedroom_nightstand", "brightness": 40},
        blocking=True,
    )
    await h.settle(0.3)
