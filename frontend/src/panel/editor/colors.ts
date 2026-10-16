// Colour conversions the colour fields need beyond shared/color.ts.

import { colorToHex, colorToRgb, hexToRgb, rgbToXy, type Rgb } from "../../shared/color";
import type { Color, PaletteInfo } from "../../shared/types";

const SWATCHES = 16;

const isXy = (v: unknown): v is [number, number] =>
  Array.isArray(v) && v.length === 2 && v.every((n) => typeof n === "number");

const isRgb = (v: unknown): v is Rgb =>
  Array.isArray(v) && v.length === 3 && v.every((n) => typeof n === "number");

// A light's colour is a chromaticity, which black does not have.
const fromRgb = (rgb: Rgb): Color | undefined => (rgb.some((v) => v > 0) ? { xy: rgbToXy(rgb) } : undefined);

/** Any accepted colour input ({kelvin}, {xy}, {rgb}, "#rrggbb") as {kelvin} or {xy} (models.Color.parse). */
export const parseColor = (value: unknown): Color | undefined => {
  if (typeof value === "string") {
    return /^#[0-9a-f]{6}$/i.test(value) ? fromRgb(hexToRgb(value)) : undefined;
  }
  if (!value || typeof value !== "object") return undefined;
  const { kelvin, xy, rgb } = value as { kelvin?: unknown; xy?: unknown; rgb?: unknown };
  if (typeof kelvin === "number") return { kelvin };
  if (isXy(xy)) return { xy };
  if (isRgb(rgb)) return fromRgb(rgb);
  return undefined;
};

// HA's colour wheel speaks hue (degrees) and saturation (0..1) at full value.
export const rgbToHs = ([r, g, b]: Rgb): [number, number] => {
  const v = Math.max(r, g, b);
  const c = v - Math.min(r, g, b);
  const h = c === 0 ? 0 : v === r ? (g - b) / c : v === g ? 2 + (b - r) / c : 4 + (r - g) / c;
  return [60 * (h < 0 ? h + 6 : h), v === 0 ? 0 : c / v];
};

export const hsToRgb = ([h, s]: [number, number]): Rgb => {
  const f = (n: number) => {
    const k = (n + h / 60) % 6;
    return Math.round(255 - 255 * s * Math.max(Math.min(k, 4 - k, 1), 0));
  };
  return [f(5), f(3), f(1)];
};

/** McCamy's approximation, as homeassistant.util.color.color_xy_to_temperature. */
export const xyToKelvin = ([x, y]: [number, number]): number => {
  const n = (x - 0.332) / (0.1858 - y);
  return Math.trunc(437 * n ** 3 + 3601 * n ** 2 + 6861 * n + 5517);
};

/** The curated palettes' colours, one per hex, spread over the hue circle. */
export const swatchColors = (palettes: readonly PaletteInfo[], count = SWATCHES): Color[] => {
  const unique = new Map<string, Color>();
  for (const palette of palettes) {
    for (const color of palette.colors) unique.set(colorToHex(color), color);
  }
  const sorted = [...unique.values()]
    .map((color) => ({ color, hue: rgbToHs(colorToRgb(color))[0] }))
    .sort((a, b) => a.hue - b.hue)
    .map((entry) => entry.color);
  if (sorted.length <= count) return sorted;
  return Array.from({ length: count }, (_, i) => sorted[Math.floor((i * sorted.length) / count)]);
};
