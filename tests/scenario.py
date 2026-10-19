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


def _part(part_id: str, name: str, effect: str, params: dict) -> dict:
    return {"id": part_id, "name": name, "effect": effect, "params": params}


def _scene(scene_id: str, name: str, icon: str, *layers: tuple) -> dict:
    return {
        "id": scene_id,
        "name": name,
        "icon": icon,
        "layers": [{"lights": lights, "part": part} for lights, part in layers],
        "others": "off",
        "transition": None,
    }


# Looks shared by several rooms, and two a room has to itself.
PARTS = [
    _part("p_12186051", "Hell", "adaptive", _adaptive()),
    _part(
        "p_2c1f0e11",
        "Hell · Living Room",
        "adaptive",
        _adaptive(min_brightness=12, max_brightness=30),
    ),
    _part("p_3b6a1d02", "Hell · Bedroom", "adaptive", _adaptive(max_kelvin=2200)),
    _part("p_254d2fcd", "Gedimmt", "white", {"kelvin": 2725, "brightness": 30}),
    _part(
        "p_0a915dea",
        "Nachtlicht",
        "palette",
        {"palette": [{"xy": [0.6, 0.38]}], "brightness": 1},
    ),
    _part(
        "p_0a1b2c3d",
        "Movie strip",
        "palette",
        {"palette": [{"xy": [0.17, 0.05]}, {"xy": [0.45, 0.2]}], "brightness": 50},
    ),
    _part("p_0a1b2c3e", "Movie", "white", {"kelvin": 2200, "brightness": 30}),
    # The edit of a built-in, for every room.
    _part(
        "savanna_sunset",
        "Savanna sunset",
        "palette",
        {
            "palette": [
                {"xy": [0.45, 0.45]},
                {"xy": [0.55, 0.4]},
                {"xy": [0.65, 0.33]},
                {"xy": [0.5, 0.41]},
            ],
            "brightness": 58,
            "motion": "dynamic",
        },
    ),
]

SUN, NIGHT, MOON = (
    "mdi:white-balance-sunny",
    "mdi:weather-night",
    "mdi:moon-waning-crescent",
)
LIVING_HELL = [
    "light.living_ceiling",
    "light.living_floor_lamp",
    "light.living_tv_strip",
]

# Each room's scenes, in menu order: Hell shared by three rooms, a room-only
# part, built-ins as scenes, the edited Savanna sunset. The office has none;
# the living room's Nachtlicht plays on a light that is no longer there.
SCENES = {
    "room:living_room": [
        _scene("s_12186051", "Hell", SUN, (LIVING_HELL, "p_12186051"),
               (["light.living_sofa_lamp"], "p_2c1f0e11")),
        _scene("s_254d2fcd", "Gedimmt", NIGHT, ("all", "p_254d2fcd")),
        _scene("s_0a1b2c3d", "Movie night", "mdi:movie-open", ("all", "p_0a1b2c3e"),
               (["light.living_tv_strip"], "p_0a1b2c3d")),
        _scene("s_5a7a0001", "Savanna sunset", "mdi:weather-sunset", ("all", "savanna_sunset")),
        _scene("s_c02c0001", "Cozy candle", "mdi:candle", ("all", "cozy_candle")),
        _scene("s_0a915dea", "Nachtlicht", MOON, (["light.hallway"], "p_0a915dea")),
    ],
    "room:kitchen": [
        _scene("s_12186052", "Hell", SUN, ("all", "p_12186051")),
        _scene("s_254d2fce", "Gedimmt", NIGHT, ("all", "p_254d2fcd")),
        _scene("s_5a7a0002", "Savanna sunset", "mdi:weather-sunset", ("all", "savanna_sunset")),
    ],
    "room:bedroom": [
        _scene("s_12186053", "Hell", SUN, ("all", "p_3b6a1d02")),
        _scene("s_254d2fcf", "Gedimmt", NIGHT, ("all", "p_254d2fcd")),
        _scene("s_0a915deb", "Nachtlicht", MOON, ("all", "p_0a915dea")),
        _scene("s_4e1a0001", "Relax", "mdi:sofa", ("all", "relax")),
    ],
    "room:hallway": [
        _scene("s_12186054", "Hell", SUN, ("all", "p_12186051")),
        _scene("s_0a915dec", "Nachtlicht", MOON, ("all", "p_0a915dea")),
    ],
    "zone:downstairs": [
        _scene("s_5a7a0003", "Savanna sunset", "mdi:weather-sunset", ("all", "savanna_sunset")),
        _scene("s_254d2fd0", "Abend", NIGHT, (["room:living_room"], "p_254d2fcd"),
               (["room:kitchen"], "p_12186051")),
    ],
}  # fmt: skip


async def demo_scenes(h: Harness) -> None:
    """Make the home look lived in: a zone, shared parts, an edit, live scenes."""
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
            "type": "magic_lights/library/save",
            "parts": PARTS,
            "scenes": [
                {"target": target_id, "scene": scene}
                for target_id, scenes in SCENES.items()
                for scene in scenes
            ],
        }
    )
    # The user's fade, and a built-in as the switch-on scene.
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
