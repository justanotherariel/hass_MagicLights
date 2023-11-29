import asyncio
from custom_components.magic_lights.plugin_effects import Effect
from collections.abc import Callable
import numpy as np
import re

from colorspacious import cspace_convert

CONFIG_SCHEMA = None
PLUGIN_DOMAIN = "transitions"
PLUGIN_NAME = "Transitions"
PLUGIN_DESCRIPTION = "Smooth and relaxing color transitions."

"""
Input is either a preset
Or Color Coordinates in sRGB, XYZ, LAB, LUV
With colorspace to make transition in
--> Convert to compatible colorspace of lamp
"""

presets = {
    "pink-glow": {
        "colors": ["#ffe4ed", "#f0839a", "#ce505c", "#01555f", "#0d2c2f"],
        "color_space": "HEX-sRGB255",
    },
    "saturated-sunset": {
        "colors": ["#240002", "#6f0100", "#a53005", "#d97d0c", "#fec135"],
        "color_space": "HEX-sRGB255",
    },
    "tokyo": {"colors": [[0.1562, 0.1648, 1], [0.49, 0.316, 1]], "color_space": "xyY1"},
}


class Transition(Effect):
    def __init__(
        self,
        async_call_service: Callable[[str, str, dict], bool],
        entities: list[str],
        conf: any,
    ):
        super().__init__(async_call_service, entities, conf)

        if "preset" in conf:
            if conf["preset"] not in presets:
                self.log.error(
                    "Preset %s not found. Failed to set up effect.", conf["preset"]
                )

            if presets[conf["preset"]]["color_space"] == "HEX-sRGB255":
                self.colors = convert_colors(
                    presets[conf["preset"]]["colors"], "HEX-sRGB255", "sRGB255"
                )
                self.color_space = "sRGB255"
            else:
                self.colors = presets[conf["preset"]]["colors"]
                self.color_space = presets[conf["preset"]]["color_space"]
        else:
            if "colors" not in conf or "color_space" not in conf:
                self.log.error(
                    "Inssuficient information about the transition. Either provide a preset or colors and their color space. Failed to set up effect."
                )

            self.colors = conf["colors"]
            self.color_space = conf["color_space"]

        if "transition_color_space" in conf:
            self.transition_color_space = conf["transition_color_space"]
        else:
            self.transition_color_space = "XYZ100"

        if "transition_time" in conf:
            self.transition_time = conf["transition_time"]
        else:
            self.transition_time = 60

        if "update_rate_s" in conf:
            self.update_rate_s = conf["update_rate_s"]
        else:
            self.update_rate_s = 10

        fpt = self.transition_time / self.update_rate_s
        color_loop_tcs = []
        for color_idx in range(len(self.colors)):
            color1 = self.colors[color_idx]

            if color_idx + 1 >= len(self.colors):
                color2 = self.colors[0]
            else:
                color2 = self.colors[color_idx + 1]

            color_loop_tcs.extend(
                generate_transition(
                    color1=color1,
                    color2=color2,
                    color_space=self.color_space,
                    transition_color_space=self.transition_color_space,
                    frames_per_transtion=fpt,
                )
            )

        self.color_loop = convert_colors(
            color_loop_tcs, self.transition_color_space, "xyY100"
        )

    async def async_start(self):
        while True:
            for color in self.color_loop:
                for entity in self.entities:
                    service_data = {
                        "entity_id": entity,
                        "xy_color": [color[0], color[1]],
                        "brightness": min(255, color[2] * 255),
                        "transition": self.update_rate_s,
                    }
                    await self.async_call_service("light", "turn_on", service_data)

                await asyncio.sleep(self.update_rate_s)


def generate_transition(
    color1: list[int],
    color2: list[int],
    color_space: str,
    transition_color_space: str,
    frames_per_transtion: int,
) -> list[list[int]]:
    # Convert Colors to Transition Color Space (tcs)
    color1_tcs = cspace_convert(color1, color_space, transition_color_space)
    color2_tcs = cspace_convert(color2, color_space, transition_color_space)

    color1_tcs = np.asarray(color1_tcs)
    color2_tcs = np.asarray(color2_tcs)

    cache = []
    for frame_cnt in range(0, int(frames_per_transtion)):
        frame = (
            color1_tcs + (color2_tcs - color1_tcs) * frame_cnt / frames_per_transtion
        )
        cache.append(frame.tolist())

    return cache


def convert_colors(colors, color_space: str, target_color_space: str):
    cache = []
    for color in colors:
        temp_color_space = color_space
        if color_space == "HEX-sRGB255":
            color = hex_to_rgb(color)
            temp_color_space = "sRGB255"
        cache.append(cspace_convert(color, temp_color_space, target_color_space))
    return cache


def hex_to_rgb(hex_color: str) -> list[int]:
    if hex_color[0] != "#" or len(hex_color) != 7:
        return None
    hex_color = hex_color[1:7]
    hex_color = re.findall("..", hex_color)

    rgb = []
    for hex in hex_color:
        rgb.append(int(hex, 16))

    return rgb
