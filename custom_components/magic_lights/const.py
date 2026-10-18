"""Constants for Magic Lights."""

from enum import IntEnum
from typing import Final

DOMAIN: Final = "magic_lights"
EVENT_MAGIC_LIGHTS: Final = "magic_lights_event"

SIGNAL_STATUS: Final = f"{DOMAIN}_status"
SIGNAL_CONFIG: Final = f"{DOMAIN}_config"
SIGNAL_TARGETS: Final = f"{DOMAIN}_targets"
SIGNAL_EFFECTS: Final = f"{DOMAIN}_effects"

# Select options that are not scenes; scene names may not take them.
OPTION_OFF: Final = "Off"
OPTION_MANUAL: Final = "Manual"

DEFAULT_TRANSITION: Final = 0.4
RUNTIME_SAVE_DELAY: Final = 2.0

# The effect param that switches a layer between static and moving colours
# ("static" / "dynamic"); activate's `dynamic` overrides it.
MOTION_PARAM: Final = "motion"


class Priority(IntEnum):
    """Send priority; lower values are sent first."""

    INTERACTIVE = 0
    DYNAMIC = 1
    BACKGROUND = 2
