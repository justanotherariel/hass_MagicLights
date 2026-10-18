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


def _adaptive(**params: object) -> dict[str, object]:
    return {
        "brightness_mode": "tanh",
        "min_brightness": 40,
        "max_brightness": 100,
        "min_kelvin": 2200,
        "max_kelvin": 5000,
        **params,
    }


def _scene(scene_id: str, name: str, icon: str, layers: list[dict]) -> dict:
    return {
        "id": scene_id,
        "name": name,
        "icon": icon,
        "preset": None,
        "layers": layers,
        "others": "off",
        "brightness": 100,
        "transition": None,
    }


# One scene shared by several rooms: a part for the sofa lamp in the living room only
# and a warmer part for the bedroom only.
HELL = _scene(
    "s_12186051",
    "Hell",
    "mdi:white-balance-sunny",
    [
        {
            "lights": [
                "light.living_ceiling",
                "light.living_floor_lamp",
                "light.living_tv_strip",
                "room:kitchen",
                "room:hallway",
                "room:bedroom",
            ],
            "effect": "adaptive",
            "params": _adaptive(),
        },
        {
            "lights": ["light.living_sofa_lamp"],
            "effect": "adaptive",
            "params": _adaptive(min_brightness=12, max_brightness=30),
        },
        {
            "lights": ["room:bedroom"],
            "effect": "adaptive",
            "params": _adaptive(max_kelvin=2200),
        },
    ],
)

GEDIMMT = _scene(
    "s_254d2fcd",
    "Gedimmt",
    "mdi:weather-night",
    [
        {
            "lights": ["room:living_room", "room:kitchen", "room:bedroom"],
            "effect": "white",
            "params": {"kelvin": 2725, "brightness": 30},
        }
    ],
)

# Picked into the living room's menu, but its lights are elsewhere: it needs setting up there.
NACHTLICHT = _scene(
    "s_0a915dea",
    "Nachtlicht",
    "mdi:moon-waning-crescent",
    [
        {
            "lights": ["room:bedroom", "room:hallway"],
            "effect": "palette",
            "params": {"palette": [{"xy": [0.6, 0.38]}], "brightness": 1},
        }
    ],
)

MOVIE = _scene(
    "s_0a1b2c3d",
    "Movie night",
    "mdi:movie-open",
    [
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
)

# The edit of a preset, for every room.
SAVANNA_EDIT = {
    **_scene(
        "s_5a7a0001",
        "Savanna sunset",
        "mdi:weather-sunset",
        [
            {
                "lights": "all",
                "effect": "palette",
                "params": {
                    "palette": [
                        {"xy": [0.45, 0.45]},
                        {"xy": [0.55, 0.4]},
                        {"xy": [0.65, 0.33]},
                        {"xy": [0.5, 0.41]},
                    ],
                    "brightness": 58,
                    "motion": "dynamic",
                },
            }
        ],
    ),
    "preset": "savanna_sunset",
}

# The office has no scenes; the hallway keeps its default menu (its scenes that fit).
MENUS = {
    "room:living_room": [
        "s_12186051",
        "s_254d2fcd",
        "s_0a1b2c3d",
        "savanna_sunset",
        "cozy_candle",
        "s_0a915dea",
    ],
    "room:kitchen": ["s_12186051", "s_254d2fcd", "savanna_sunset"],
    "room:bedroom": ["s_12186051", "s_254d2fcd", "s_0a915dea", "relax"],
    "room:office": [],
}


async def demo_scenes(h: Harness) -> None:
    """Make the home look lived in: a zone, shared and edited scenes, live scenes."""
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
    for scene in (HELL, GEDIMMT, NACHTLICHT, MOVIE, SAVANNA_EDIT):
        await h.ws({"type": "magic_lights/scene/save", "scene": scene})
    await h.ws({"type": "magic_lights/menus/save", "menus": MENUS})
    # The user's fade, and an edited preset as the switch-on scene.
    await h.ws(
        {
            "type": "magic_lights/settings/save",
            "settings": {"transition": 1.0, "default_scene": "adaptive"},
        }
    )
    await h.call("activate_scene", "select.living_room_scene", scene="Hell")
    await h.call("activate_scene", "select.kitchen_scene", scene="savanna_sunset")
    await h.call("activate_scene", "select.bedroom_scene", scene="Gedimmt")
    # A lamp dimmed by hand: the bedroom shows one manual light.
    await h.hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.bedroom_nightstand", "brightness": 40},
        blocking=True,
    )
    await h.settle(0.3)
