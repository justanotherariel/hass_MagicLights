import assert from "node:assert/strict";
import { test } from "node:test";
import { colorToHex, rgbToXy, swatchBackground, xyToRgb } from "../src/shared/color";

// Reference values from homeassistant.util.color (HA 2026.8.3).
test("kelvin to hex matches color_temperature_to_rgb", () => {
  const cases: [number, string][] = [
    [1500, "#ff6c00"],
    [2237, "#ff942b"],
    [2700, "#ffa757"],
    [4292, "#ffd5b3"],
    [6500, "#fffefa"],
  ];
  for (const [kelvin, hex] of cases) assert.equal(colorToHex({ kelvin }), hex, `${kelvin} K`);
});

test("xy to rgb matches color_xy_to_RGB", () => {
  assert.deepEqual(xyToRgb(0.5019, 0.4152), [255, 189, 86]);
  assert.deepEqual(xyToRgb(0.64, 0.33), [255, 92, 47]);
  assert.deepEqual(xyToRgb(0.1532, 0.0475), [64, 0, 255]);
  assert.deepEqual(xyToRgb(0.3127, 0.329), [245, 254, 255]);
});

test("rgb to xy matches color_RGB_to_xy to 3 decimals", () => {
  const round3 = ([x, y]: [number, number]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000];
  assert.deepEqual(round3(rgbToXy([255, 0, 0])), [0.701, 0.299]);
  assert.deepEqual(round3(rgbToXy([0, 0, 255])), [0.136, 0.04]);
  assert.deepEqual(round3(rgbToXy([255, 180, 90])), [0.508, 0.401]);
  assert.deepEqual(rgbToXy([0, 0, 0]), [0, 0]);
});

test("swatch backgrounds", () => {
  assert.equal(swatchBackground(["#ff0000"]), "#ff0000");
  assert.equal(swatchBackground(["#ff0000", "#0000ff"]), "linear-gradient(90deg, #ff0000, #0000ff)");
  assert.match(swatchBackground([]), /^var\(/);
});
