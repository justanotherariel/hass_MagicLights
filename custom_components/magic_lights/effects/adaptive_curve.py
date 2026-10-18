"""Sun-driven brightness and color temperature, as a pure function.

The curve follows basnijholt/adaptive-lighting: a parabolic "sun position"
in [-1, 1] between (virtual) sunrise, noon, sunset and midnight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import math
from typing import Literal

import astral
import astral.sun

from ..models import SunLocation

# Synthetic 1 h day/night on polar days, as adaptive-lighting does (#1485).
_POLAR_HALF_SPAN = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class AdaptiveParams:
    """Curve parameters. Brightness in percent, color temperature in kelvin."""

    brightness: float = 100  # static mode only
    min_brightness: float = 40
    max_brightness: float = 100
    min_kelvin: int = 2200
    max_kelvin: int = 5000
    brightness_mode: Literal["static", "default", "linear", "tanh"] = "static"
    # Ramp before/after sunset and sunrise (linear/tanh only).
    ramp_dark: timedelta = timedelta(minutes=60)
    ramp_light: timedelta = timedelta(minutes=90)
    sunrise_offset: timedelta = timedelta()
    sunset_offset: timedelta = timedelta()
    # Local-time clamps on the virtual sunrise/sunset keep the indoor day
    # within a human schedule across seasons and on polar days.
    min_sunrise: time | None = None
    max_sunrise: time | None = None
    min_sunset: time | None = None
    max_sunset: time | None = None


def adaptive_values(
    now: datetime, location: SunLocation, params: AdaptiveParams
) -> tuple[int, int]:
    """Return (brightness_pct, kelvin) for the aware datetime `now`."""
    events = _events_around(now, location, params)
    t = now.timestamp()
    (prev_kind, prev_ts), (next_kind, next_ts) = _bracket(events, t)
    pos = _sun_position(t, prev_kind, prev_ts, next_kind, next_ts)

    kelvin = params.min_kelvin + (params.max_kelvin - params.min_kelvin) * max(pos, 0)

    lo, hi = params.min_brightness, params.max_brightness
    if params.brightness_mode == "static":
        bri = params.brightness
    elif params.brightness_mode == "default":
        bri = hi if pos > 0 else lo + (hi - lo) * (1 + pos)
    else:
        # Each bracket holds exactly one of sunrise/sunset.
        rising = "sunrise" in (prev_kind, next_kind)
        ev_ts = prev_ts if prev_kind in ("sunrise", "sunset") else next_ts
        x = t - ev_ts
        dark = params.ramp_dark.total_seconds()
        light = params.ramp_light.total_seconds()
        x1, x2, y1, y2 = (-dark, light, lo, hi) if rising else (-light, dark, hi, lo)
        if params.brightness_mode == "linear":
            bri = y1 + (x - x1) * (y2 - y1) / (x2 - x1)
        else:
            bri = _scaled_tanh(x, x1, x2, rising, lo, hi)
        bri = min(max(bri, lo), hi)

    return max(1, min(100, round(bri))), 10 * round(kelvin / 10)


def _sun_position(
    t: float, prev_kind: str, prev_ts: float, next_kind: str, next_ts: float
) -> float:
    """Parabola: 0 at sunrise/sunset, 1 at noon, -1 at midnight."""
    peak, edge = (
        (prev_ts, next_ts) if next_kind in ("sunrise", "sunset") else (next_ts, prev_ts)
    )
    sign = 1 if next_kind in ("noon", "sunset") else -1
    return sign * (1 - ((t - peak) / (peak - edge)) ** 2)


def _scaled_tanh(
    x: float, x1: float, x2: float, rising: bool, lo: float, hi: float
) -> float:
    """Scaled tanh: 5 % of the range at the dark end and 95 % at the light end."""
    y1, y2 = (0.05, 0.95) if rising else (0.95, 0.05)
    a = (math.atanh(2 * y2 - 1) - math.atanh(2 * y1 - 1)) / (x2 - x1)
    b = x1 - math.atanh(2 * y1 - 1) / a
    return lo + (hi - lo) * 0.5 * (math.tanh(a * (x - b)) + 1)


def _bracket(
    events: list[tuple[float, str]], t: float
) -> tuple[tuple[str, float], tuple[str, float]]:
    for (ts_a, kind_a), (ts_b, kind_b) in zip(events, events[1:]):
        if ts_a <= t < ts_b:
            return (kind_a, ts_a), (kind_b, ts_b)
    raise ValueError("now is outside the computed sun events")


def _events_around(
    now: datetime, location: SunLocation, params: AdaptiveParams
) -> list[tuple[float, str]]:
    """Sunrise/noon/sunset/midnight from yesterday to tomorrow, sorted."""
    today = now.astimezone(location.tz).date()
    days = [today + timedelta(days=d) for d in (-1, 0, 1, 2)]
    rise_set = [_virtual_rise_set(d, location, params) for d in days]
    events: list[tuple[float, str]] = []
    for i, (rise, sset) in enumerate(rise_set):
        events += [(rise, "sunrise"), ((rise + sset) / 2, "noon"), (sset, "sunset")]
        if i + 1 < len(rise_set):
            events.append(((sset + rise_set[i + 1][0]) / 2, "midnight"))
    events.sort()
    kinds = [k for _, k in events]
    order = ("sunrise", "noon", "sunset", "midnight")
    if any(order[(order.index(a) + 1) % 4] != b for a, b in zip(kinds, kinds[1:])):
        raise ValueError("sun events out of order; check offsets and clamp times")
    return events


def _virtual_rise_set(
    day: date, location: SunLocation, params: AdaptiveParams
) -> tuple[float, float]:
    rise = _astral_or_polar(day, location.observer, rising=True) + params.sunrise_offset
    sset = _astral_or_polar(day, location.observer, rising=False) + params.sunset_offset
    rise = _clamp_local(rise, day, location.tz, params.min_sunrise, params.max_sunrise)
    sset = _clamp_local(sset, day, location.tz, params.min_sunset, params.max_sunset)
    return rise.timestamp(), sset.timestamp()


def _astral_or_polar(day: date, observer: astral.Observer, rising: bool) -> datetime:
    try:
        fn = astral.sun.sunrise if rising else astral.sun.sunset
        return fn(observer, day)
    except ValueError:
        noon = astral.sun.noon(observer, day)
        midnight = astral.sun.midnight(observer, day)
        if (
            astral.sun.elevation(observer, noon)
            + astral.sun.elevation(observer, midnight)
            > 0
        ):  # midnight sun
            next_midnight = astral.sun.midnight(observer, day + timedelta(days=1))
            return (
                midnight + _POLAR_HALF_SPAN
                if rising
                else next_midnight - _POLAR_HALF_SPAN
            )
        return noon - _POLAR_HALF_SPAN if rising else noon + _POLAR_HALF_SPAN


def _clamp_local(
    value: datetime, day: date, tz: tzinfo, earliest: time | None, latest: time | None
) -> datetime:
    if earliest is not None:
        value = max(value, datetime.combine(day, earliest, tz).astimezone(UTC))
    if latest is not None:
        value = min(value, datetime.combine(day, latest, tz).astimezone(UTC))
    return value
