// Colour maths for swatches, ports of homeassistant/util/color.py (same results as the backend's as_hex).

import type { Color } from "./types";

export type Rgb = [number, number, number];

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export const isKelvin = (c: Color): c is { kelvin: number } => "kelvin" in c;

/** color_temperature_to_rgb (Tanner Helland). */
export const kelvinToRgb = (kelvin: number): Rgb => {
  const t = clamp(kelvin, 1000, 40000) / 100;
  const red = t <= 66 ? 255 : clamp(329.698727446 * (t - 60) ** -0.1332047592, 0, 255);
  const green =
    t <= 66
      ? clamp(99.4708025861 * Math.log(t) - 161.1195681661, 0, 255)
      : clamp(288.1221695283 * (t - 60) ** -0.0755148492, 0, 255);
  const blue =
    t >= 66 ? 255 : t <= 19 ? 0 : clamp(138.5177312231 * Math.log(t - 10) - 305.0447927307, 0, 255);
  return [red, green, blue];
};

/** color_xy_brightness_to_RGB at full brightness (Wide RGB D65). */
export const xyToRgb = (x: number, y: number): Rgb => {
  const Y = 1;
  const vy = y === 0 ? 1e-11 : y;
  const X = (Y / vy) * x;
  const Z = (Y / vy) * (1 - x - vy);
  const linear = [
    X * 1.656492 - Y * 0.354851 - Z * 0.255038,
    -X * 0.707196 + Y * 1.655397 + Z * 0.036152,
    X * 0.051713 - Y * 0.121364 + Z * 1.01153,
  ];
  const gamma = linear.map((v) =>
    Math.max(0, v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055),
  );
  const max = Math.max(...gamma);
  const scaled = max > 1 ? gamma.map((v) => v / max) : gamma;
  return scaled.map((v) => Math.trunc(v * 255)) as Rgb;
};

/** color_RGB_to_xy (Wide RGB D65); 4 decimals keep a round trip through xyToRgb stable. */
export const rgbToXy = ([r, g, b]: Rgb): [number, number] => {
  if (r + g + b === 0) return [0, 0];
  const [R, G, B] = [r, g, b].map((v) => {
    const c = v / 255;
    return c > 0.04045 ? ((c + 0.055) / 1.055) ** 2.4 : c / 12.92;
  });
  const X = R * 0.664511 + G * 0.154324 + B * 0.162028;
  const Y = R * 0.283881 + G * 0.668433 + B * 0.047685;
  const Z = R * 0.000088 + G * 0.07231 + B * 0.986039;
  const sum = X + Y + Z;
  const round4 = (v: number) => Math.round(v * 10000) / 10000;
  return [round4(X / sum), round4(Y / sum)];
};

export const rgbToHex = (rgb: Rgb): string =>
  `#${rgb.map((v) => Math.round(clamp(v, 0, 255)).toString(16).padStart(2, "0")).join("")}`;

export const hexToRgb = (hex: string): Rgb => {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  const n = Number.parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

export const colorToRgb = (c: Color): Rgb => (isKelvin(c) ? kelvinToRgb(c.kelvin) : xyToRgb(...c.xy));

export const colorToHex = (c: Color): string => rgbToHex(colorToRgb(c));

/** WCAG relative luminance, 0 (black) .. 1 (white). */
export const luminance = (hex: string): number => {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

/** `rgba()` of a hex colour, for tinted backgrounds. */
/** The first colour that stays visible as an icon tint on the theme's cards. */
export const readableTint = (colors: readonly string[], dark = false): string | undefined =>
  colors.find((hex) => (dark ? luminance(hex) > 0.1 : luminance(hex) < 0.6));

export const hexAlpha = (hex: string, alpha: number): string =>
  `rgba(${hexToRgb(hex).join(", ")}, ${alpha})`;

/** CSS background for a swatch strip: one colour, or a soft left-to-right blend. */
export const swatchBackground = (swatches: readonly string[]): string => {
  if (swatches.length === 0) return "var(--ha-color-fill-neutral-normal-resting, var(--divider-color))";
  if (swatches.length === 1) return swatches[0];
  return `linear-gradient(90deg, ${swatches.join(", ")})`;
};

/** A left-to-right CSS gradient over a kelvin range, warm to cool. */
export const kelvinGradient = (min: number, max: number, stops = 10): string => {
  const colors = Array.from({ length: stops + 1 }, (_, i) =>
    rgbToHex(kelvinToRgb(min + ((max - min) * i) / stops)),
  );
  return `linear-gradient(90deg, ${colors.join(", ")})`;
};
