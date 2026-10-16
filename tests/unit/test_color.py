"""Colour conversions and the close()/diverges() tolerance matrices."""

from __future__ import annotations

import unittest

from custom_components.magic_lights import color as color_util
from custom_components.magic_lights.color import close, diverges, hue_angle
from custom_components.magic_lights.models import (
    OFF,
    Color,
    HaLight,
    LightState,
    ha_to_state,
    normalise,
)
from homeassistant.util import color as ha_color

from .fakes import make_caps

CAPS = make_caps(min_kelvin=2202, max_kelvin=6535, effects=frozenset({"candle"}))


def kelvin_for_mired(mired: float) -> int:
    return round(1_000_000 / mired)


class ConversionTest(unittest.TestCase):
    def test_uses_ha_paths(self) -> None:
        self.assertEqual(
            color_util.xy_from_kelvin(2700),
            ha_color.color_hs_to_xy(*ha_color.color_temperature_to_hs(2700)),
        )
        self.assertEqual(
            color_util.kelvin_from_xy((0.4, 0.4)),
            ha_color.color_xy_to_temperature(0.4, 0.4),
        )
        self.assertEqual(color_util.xy_from_rgb((255, 0, 0)), (0.701, 0.299))
        self.assertEqual(
            color_util.rgb_from_xy((0.701, 0.299)),
            ha_color.color_xy_to_RGB(0.701, 0.299),
        )

    def test_kelvin_from_xy_is_bounded(self) -> None:
        grid = [(x / 20, y / 20) for x in range(1, 16) for y in range(1, 18)]
        for xy in [*grid, (0.3, 0.1858)]:  # includes McCamy's pole
            with self.subTest(xy=xy):
                self.assertTrue(1000 <= color_util.kelvin_from_xy(xy) <= 40000)

    def test_nearest_white(self) -> None:
        # Points of the Planckian locus land on their own temperature.
        for xy, kelvin in (
            ((0.4599, 0.4106), 2700),
            ((0.3805, 0.3768), 4000),
            ((0.3135, 0.3236), 6500),
        ):
            with self.subTest(kelvin=kelvin):
                found = color_util.nearest_white(xy, 2000, 6535)
                self.assertLessEqual(
                    abs(color_util.mired(found) - color_util.mired(kelvin)), 3
                )
        self.assertEqual(color_util.nearest_white((0.701, 0.299), 2202, 6535), 2202)
        self.assertEqual(color_util.nearest_white((0.136, 0.04), 2202, 6535), 6535)
        self.assertEqual(color_util.nearest_white((0.3, 0.3), 3000, 3000), 3000)

    def test_hex(self) -> None:
        self.assertEqual(color_util.rgb_from_hex("#ff8000"), (255, 128, 0))
        self.assertEqual(color_util.hex_from_rgb((255, 128, 0)), "#ff8000")
        self.assertEqual(color_util.rgb_from_kelvin(6500), (255, 254, 250))
        for bad in ("ff8000", "#ff80", "#gg0000", 12):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                color_util.rgb_from_hex(bad)

    def test_hue_angle(self) -> None:
        self.assertAlmostEqual(hue_angle(color_util.D65), 0.0)
        red = hue_angle((0.701, 0.299))
        green = hue_angle((0.17, 0.7))
        blue = hue_angle((0.153, 0.048))
        self.assertTrue(350 < red < 360, red)
        self.assertTrue(100 < green < 120, green)
        self.assertTrue(230 < blue < 250, blue)
        for xy in ((0.701, 0.299), (0.17, 0.7), (0.153, 0.048), (0.5, 0.4)):
            self.assertTrue(0 <= hue_angle(xy) < 360)


class CloseTest(unittest.TestCase):
    base = LightState(brightness=128 / 255, color=Color(kelvin=2700), transition=1.0)

    def test_equal_cases(self) -> None:
        cases = {
            "identical": self.base,
            "transition ignored": self.base.replace(transition=5.0),
            "brightness +1/255": self.base.replace(brightness=129 / 255),
            "1 mired": self.base.replace(
                color=Color(kelvin=kelvin_for_mired(1_000_000 / 2700 + 0.9))
            ),
        }
        for name, other in cases.items():
            with self.subTest(name):
                self.assertTrue(close(self.base, other))
                self.assertTrue(close(other, self.base))

    def test_different_cases(self) -> None:
        cases = {
            "off": OFF,
            "brightness +2/255": self.base.replace(brightness=130 / 255),
            "brightness missing": self.base.replace(brightness=None),
            "2 mired": self.base.replace(
                color=Color(kelvin=kelvin_for_mired(1_000_000 / 2700 + 2))
            ),
            "kelvin vs xy": self.base.replace(color=Color(xy=self.base.color.as_xy())),
            "colour missing": self.base.replace(color=None),
            "effect": self.base.replace(effect="candle"),
            "gradient": self.base.replace(gradient=(Color(kelvin=2700),)),
        }
        for name, other in cases.items():
            with self.subTest(name):
                self.assertFalse(close(self.base, other))
                self.assertFalse(close(other, self.base))

    def test_xy_and_effects(self) -> None:
        a = LightState(color=Color(xy=(0.5, 0.4)), effect="candle", effect_speed=0.5)
        self.assertTrue(close(a, a.replace(color=Color(xy=(0.5015, 0.4)))))
        self.assertFalse(close(a, a.replace(color=Color(xy=(0.503, 0.4)))))
        self.assertFalse(close(a, a.replace(effect_speed=0.6)))
        self.assertFalse(close(a, a.replace(effect="fireplace")))
        self.assertTrue(close(a.replace(effect=None), a.replace(effect="off")))

    def test_gradients(self) -> None:
        g = (Color(xy=(0.6, 0.35)), Color(xy=(0.2, 0.2)))
        a = LightState(color=g[0], gradient=g)
        self.assertTrue(close(a, a.replace(gradient=(Color(xy=(0.601, 0.35)), g[1]))))
        self.assertFalse(close(a, a.replace(gradient=g[::-1])))
        self.assertFalse(close(a, a.replace(gradient=g[:1])))

    def test_off_states(self) -> None:
        self.assertTrue(close(OFF, OFF.replace(transition=3)))
        self.assertTrue(close(OFF, LightState(on=False, brightness=0.5)))


class DivergesTest(unittest.TestCase):
    def test_brightness(self) -> None:
        cmd = LightState(brightness=100 / 255)
        self.assertFalse(diverges(LightState(brightness=125 / 255), cmd, CAPS))
        self.assertTrue(diverges(LightState(brightness=126 / 255), cmd, CAPS))
        self.assertTrue(diverges(LightState(brightness=74 / 255), cmd, CAPS))
        self.assertFalse(
            diverges(LightState(brightness=200 / 255), cmd, make_caps(brightness=False))
        )
        self.assertFalse(diverges(LightState(), cmd, CAPS))  # not reported
        self.assertFalse(diverges(LightState(brightness=0.9), LightState(), CAPS))

    def test_kelvin(self) -> None:
        cmd = LightState(color=Color(kelvin=4000))  # 250 mired
        self.assertFalse(
            diverges(LightState(color=Color(kelvin=kelvin_for_mired(264.9))), cmd, CAPS)
        )
        self.assertTrue(
            diverges(LightState(color=Color(kelvin=kelvin_for_mired(265.5))), cmd, CAPS)
        )
        self.assertTrue(
            diverges(LightState(color=Color(kelvin=kelvin_for_mired(234.5))), cmd, CAPS)
        )

    def test_xy(self) -> None:
        cmd = LightState(color=Color(xy=(0.5, 0.4)))
        self.assertFalse(diverges(LightState(color=Color(xy=(0.54, 0.4))), cmd, CAPS))
        self.assertTrue(diverges(LightState(color=Color(xy=(0.5, 0.46))), cmd, CAPS))
        self.assertFalse(diverges(LightState(), cmd, CAPS))  # colour not reported

    def test_cross_mode(self) -> None:
        # Reported in the light's mode; the command is converted to it.
        kelvin_cmd = LightState(color=Color(kelvin=2700))
        self.assertFalse(
            diverges(
                LightState(color=Color(xy=Color(kelvin=2700).as_xy())), kelvin_cmd, CAPS
            )
        )
        xy_cmd = LightState(color=Color(xy=(0.38, 0.38)))
        reported = LightState(color=Color(kelvin=Color(xy=(0.38, 0.38)).as_kelvin()))
        self.assertFalse(diverges(reported, xy_cmd, CAPS))
        self.assertTrue(diverges(LightState(color=Color(kelvin=6500)), xy_cmd, CAPS))

    def test_clamped_kelvin_is_not_a_divergence(self) -> None:
        # The scene asks 2000 K; the bulb's floor is 2202 K and it reports 2202 K.
        wanted = LightState(brightness=0.5, color=Color(kelvin=2000))
        reported = ha_to_state(
            HaLight(True, True, 128, 2202, (0.5, 0.4), None, "color_temp")
        )
        self.assertFalse(diverges(reported, normalise(wanted, CAPS), CAPS))
        self.assertTrue(
            diverges(reported, wanted, CAPS)
        )  # why the baseline is normalised

    def test_effect_and_power(self) -> None:
        cmd = LightState(brightness=0.5)
        self.assertTrue(
            diverges(LightState(brightness=0.5, effect="candle"), cmd, CAPS)
        )
        self.assertFalse(diverges(LightState(brightness=0.5, effect="off"), cmd, CAPS))
        # Z2M keeps reporting the last stop command; WLED rests on "Solid".
        for value in ("stop_effect", "stop_hue_effect", "finish_effect", "Solid"):
            with self.subTest(value):
                reported = LightState(brightness=0.5, effect=value)
                self.assertFalse(diverges(reported, cmd, CAPS))
                self.assertTrue(close(reported, cmd))
                self.assertIsNone(
                    ha_to_state(
                        HaLight(True, True, 128, None, (0.5, 0.4), value, "xy")
                    ).effect
                )
        self.assertTrue(
            diverges(LightState(brightness=0.5), cmd.replace(effect="candle"), CAPS)
        )
        self.assertTrue(diverges(OFF, cmd, CAPS))
        self.assertTrue(diverges(cmd, OFF, CAPS))
        self.assertFalse(diverges(OFF, OFF.replace(transition=2), CAPS))


if __name__ == "__main__":
    unittest.main()
