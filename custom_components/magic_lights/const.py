DOMAIN = "light_scene"

PREDEFINED_SCENES = {
    "custom": {
        "config": {"update_interval_s": 0, "immersive_scene": True},
        "pipes": [{"pipe_type": "no_op", "entities": ["all"], "effects": []}],
    },
    "off": {
        "config": {"update_interval_s": 0, "immersive_scene": True},
        "pipes": [
            {
                "pipe_type": "light.color",
                "entities": ["all.lights"],
                "effects": [{"name": "static", "state": "off"}],
            },
            {
                "pipe_type": "switch",
                "entities": ["all.switches"],
                "effects": [{"name": "static", "state": "off"}],
            },
        ],
    },
}


PIPE_TYPES = {
    "light.color": {
        "required": [],
        "optional": ["brightness", "rgb_color"],
    },
    "light.kelvin": {
        "required": [],
        "optional": ["brightness", "kelvin"],
    },
    "light.dumb": {
        "required": [],
        "optional": [],
    },
}