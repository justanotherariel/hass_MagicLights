"""MagicLights Configuration Schema."""
# pylint: disable=dangerous-default-value
import voluptuous as vol

# ZONE
ZONE_NAME = "zone_name"
ENTITY_LIST = "entities"
ENTITY_GROUPS = "groups"
SCENE_LIST = "scenes"

GROUP_NAME = "group_name"

# SCENES
SCENE_NAME = "scene_name"
PIPE_LIST = "pipes"

# PIPES
EFFECT_CONF = "effect"
MODIFIER_LIST = "modifiers"
SCENE_DELAY = "delay"
SCENE_IMMERSIVE = "immerive_scene"

# EFFECT
EFFECT_NAME = "name"
EFFECT_DATA = "conf"

# MODIFIER
MODIFIER_NAME = "name"
MODIFIER_DATA = "conf"
MODIFIER_UPDATE_INTERVAL = "min_update_interval"


def magic_lights_config_schema() -> dict:
    """Return a schema configuration dict for MagicLights."""

    effect_schema = {
        vol.Required(EFFECT_NAME): str,
        vol.Optional(EFFECT_DATA): vol.Any(dict, list),
    }

    modifier_schema = {
        vol.Required(MODIFIER_NAME): str,
        vol.Optional(MODIFIER_DATA): vol.Any(dict, list),
        vol.Optional(MODIFIER_UPDATE_INTERVAL): int,
    }

    pipe_schema = {
        vol.Required(ENTITY_LIST): [str],
        vol.Required(EFFECT_CONF): effect_schema,
        vol.Optional(MODIFIER_LIST): [modifier_schema],
    }

    scene_schema = {
        vol.Required(str): [pipe_schema],
    }

    group_schema = {vol.Optional(str): [str]}

    zone_schema = {
        vol.Required(ENTITY_LIST): [str],
        vol.Optional(ENTITY_GROUPS): group_schema,
        vol.Required(SCENE_LIST): scene_schema,
    }

    return {vol.Optional(str): zone_schema}
