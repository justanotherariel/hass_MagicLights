"""Colour conversions (via homeassistant.util.color) and light state comparisons."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
import math
import re
from typing import TYPE_CHECKING

from homeassistant.util import color as color_util

if TYPE_CHECKING:
    from .models import Caps, Color, LightState

D65 = (0.3127, 0.3290)
# Effect values that mean "no effect running": stop commands that some
# integrations keep reporting, and WLED's resting effect.
NO_EFFECT = frozenset(
    {"off", "none", "None", "stop_effect", "stop_hue_effect", "finish_effect", "Solid"}
)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def xy_from_kelvin(kelvin: float) -> tuple[float, float]:
    # Same path HA's light service takes for xy-only lights, so the result
    # matches what such a light reports back.
    return color_util.color_hs_to_xy(*color_util.color_temperature_to_hs(kelvin))


def kelvin_from_xy(xy: Sequence[float]) -> int:
    x, y = xy
    if abs(0.1858 - y) < 1e-6:  # McCamy's pole
        y += 1e-6
    return min(40000, max(1000, color_util.color_xy_to_temperature(x, y)))


def _planck_xy(kelvin: float) -> tuple[float, float]:
    """Point of the Planckian locus (Kim et al. cubic spline, 1667-25000 K)."""
    t = min(25000.0, max(1667.0, kelvin))
    if t <= 4000:
        x = -0.2661239e9 / t**3 - 0.2343589e6 / t**2 + 0.8776956e3 / t + 0.17991
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + 0.2226347e3 / t + 0.24039
    if t <= 2222:
        y = -1.1063814 * x**3 - 1.3481102 * x**2 + 2.18555832 * x - 0.20219683
    elif t <= 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.081758 * x**3 - 5.8733867 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


def _uv(xy: Sequence[float]) -> tuple[float, float]:
    """CIE 1960 UCS, the space colour temperature distances are measured in."""
    x, y = xy
    d = -2 * x + 12 * y + 3
    return 4 * x / d, 6 * y / d


@cache
def _locus_uv(mireds: float) -> tuple[float, float]:
    return _uv(_planck_xy(1_000_000 / mireds))


def nearest_white(xy: tuple[float, float], min_kelvin: int, max_kelvin: int) -> int:
    """Return the kelvin within the range whose white is closest to `xy`.

    Unlike McCamy's formula this holds for saturated colours too: red ends at
    the warm end, blue at the cold end.
    """
    uv = _uv(xy)
    coldest, warmest = mired(max_kelvin), mired(min_kelvin)
    candidates = [
        coldest,
        *range(math.ceil(coldest), math.floor(warmest) + 1),
        warmest,
    ]
    best = min(candidates, key=lambda m: math.dist(uv, _locus_uv(m)))
    return min(max_kelvin, max(min_kelvin, round(1_000_000 / best)))


def xy_from_rgb(rgb: Sequence[float]) -> tuple[float, float]:
    return color_util.color_RGB_to_xy(*(round(c) for c in rgb))


def xy_from_hs(hs: Sequence[float]) -> tuple[float, float]:
    return color_util.color_hs_to_xy(*hs)


def rgb_from_xy(xy: Sequence[float]) -> tuple[int, int, int]:
    return color_util.color_xy_to_RGB(*xy)


def rgb_from_kelvin(kelvin: float) -> tuple[int, int, int]:
    r, g, b = color_util.color_temperature_to_rgb(kelvin)
    return round(r), round(g), round(b)


def rgb_from_hex(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _HEX.match(value):
        raise ValueError(f"expected '#rrggbb', got {value!r}")
    r, g, b = color_util.rgb_hex_to_rgb_list(value[1:])
    return r, g, b


def hex_from_rgb(rgb: Sequence[int]) -> str:
    return "#" + color_util.color_rgb_to_hex(*rgb)


def hue_angle(xy: Sequence[float]) -> float:
    """Angle of the colour around the D65 white point, degrees in [0, 360)."""
    return math.degrees(math.atan2(xy[1] - D65[1], xy[0] - D65[0])) % 360


def mired(kelvin: float) -> float:
    return 1_000_000 / kelvin


def xy_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _effect(value: str | None) -> str | None:
    return None if value is None or value in NO_EFFECT else value


def _near(a: float | None, b: float | None, tolerance: float) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tolerance


def _color_close(a: Color | None, b: Color | None) -> bool:
    if a is None or b is None:
        return a is b
    if a.kelvin is not None and b.kelvin is not None:
        return abs(mired(a.kelvin) - mired(b.kelvin)) <= 1
    if a.xy is not None and b.xy is not None:
        return xy_distance(a.xy, b.xy) <= 0.002
    return False


def close(a: LightState, b: LightState) -> bool:
    """Tight equality of two normalised states; decides whether to resend."""
    if a.on != b.on:
        return False
    if not a.on:
        return True
    if a.gradient is None or b.gradient is None:
        gradients = a.gradient is b.gradient
    else:
        gradients = len(a.gradient) == len(b.gradient) and all(
            _color_close(x, y) for x, y in zip(a.gradient, b.gradient, strict=True)
        )
    return (
        gradients
        and _near(a.brightness, b.brightness, 1 / 255 + 1e-9)
        and _color_close(a.color, b.color)
        and _effect(a.effect) == _effect(b.effect)
        and _near(a.effect_speed, b.effect_speed, 1e-3)
    )


def diverges(reported: LightState, commanded: LightState, caps: Caps) -> bool:
    """Coarse check whether a reported state differs from the normalised command.

    Colours are compared in the reported colour mode; attributes the light
    does not report are skipped.
    """
    if reported.on != commanded.on:
        return True
    if not reported.on:
        return False
    if (
        caps.brightness
        and reported.brightness is not None
        and commanded.brightness is not None
        and abs(reported.brightness - commanded.brightness) > 25 / 255
    ):
        return True
    if reported.color is not None and commanded.color is not None:
        if reported.color.kelvin is not None:
            delta = mired(reported.color.kelvin) - mired(commanded.color.as_kelvin())
            if abs(delta) > 15:
                return True
        elif xy_distance(reported.color.as_xy(), commanded.color.as_xy()) > 0.05:
            return True
    return _effect(reported.effect) != _effect(commanded.effect)
