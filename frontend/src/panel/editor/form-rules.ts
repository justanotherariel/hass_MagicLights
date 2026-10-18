// Which control draws an effect parameter: generic rules keyed on the selector's shape
// and on the layout hints any effect can put in a key's description (effects/schema.py
// lifts them), never on the effect. Pure, so the rules are tested without a DOM.

import { getHiddenFields } from "../../shared/conditions";
import type { HaFormSchema } from "../../shared/types";
import { humanise } from "../../shared/util";

/** Up to this many choices read better as a segmented control than as a dropdown. */
export const MAX_SEGMENTS = 4;

export interface Choice {
  value: string;
  label: string;
}

export interface Step {
  value: number;
  label: string;
}

interface Base {
  item: HaFormSchema;
  label: string;
}

export interface NumberBounds {
  min: number;
  max: number;
  step: number;
  unit: string;
}

export type Control =
  | (Base & { kind: "color" | "palette" })
  /**
   * A control slider; `big` for a percentage (a brightness). The value is printed in the
   * bar only with a unit (`readout`): a bare "0.6" tells nothing, so such a scale gets
   * `ends` under it instead, the field's own or its bounds.
   */
  | (Base & { kind: "bar"; big: boolean; readout: boolean; ends: string[] } & NumberBounds)
  /** A kelvin gradient with a cursor and the value printed; `wrapped`: stored as a colour, {kelvin}. */
  | (Base & { kind: "kelvin"; min: number; max: number; wrapped: boolean })
  /** One bar for a min/max pair: `item` is the lower key, `high` the upper. */
  | (Base & { kind: "range"; high: HaFormSchema; kelvin: boolean } & NumberBounds)
  | (Base & { kind: "steps"; steps: Step[]; unit: string })
  | (Base & { kind: "segments"; options: Choice[] })
  /** Anything else: HA's own selector, as given or adjusted (`selector`). */
  | (Base & { kind: "selector"; selector: Record<string, unknown> });

export interface FormLayout {
  /** Rows of controls; fields sharing a `row` hint sit side by side. */
  main: Control[][];
  advanced: Control[][];
}

const selectorType = (item: HaFormSchema): string => Object.keys(item.selector ?? {})[0] ?? "";

const config = (item: HaFormSchema): Record<string, unknown> =>
  (item.selector?.[selectorType(item)] ?? {}) as Record<string, unknown>;

const num = (value: unknown): number | undefined => (typeof value === "number" ? value : undefined);

const labelOf = (item: HaFormSchema): string => item.label ?? humanise(item.name);

const numberBounds = (item: HaFormSchema): NumberBounds | undefined => {
  if (selectorType(item) !== "number") return undefined;
  const c = config(item);
  const min = num(c.min);
  const max = num(c.max);
  if (min === undefined || max === undefined || max <= min) return undefined;
  return { min, max, step: num(c.step) ?? 1, unit: typeof c.unit_of_measurement === "string" ? c.unit_of_measurement : "" };
};

/** Kelvin bounds of a color_temp selector; mired selectors are left to HA. */
const kelvinBounds = (item: HaFormSchema): { min: number; max: number } | undefined => {
  if (selectorType(item) !== "color_temp") return undefined;
  const c = config(item);
  if (c.unit !== "kelvin") return undefined;
  return { min: num(c.min) ?? 2000, max: num(c.max) ?? 6500 };
};

const choices = (item: HaFormSchema): Choice[] | undefined => {
  if (selectorType(item) !== "select") return undefined;
  const c = config(item) as { options?: unknown[]; multiple?: boolean; custom_value?: boolean; mode?: string };
  const options = c.options ?? [];
  if (c.multiple || c.custom_value || !options.length || options.length > MAX_SEGMENTS) return undefined;
  return options.map((o) =>
    typeof o === "string"
      ? { value: o, label: humanise(o) }
      : { value: String((o as Choice).value), label: (o as Choice).label ?? humanise(String((o as Choice).value)) },
  );
};

const validEnds = (item: HaFormSchema): string[] | undefined => {
  const ends = item.ends;
  return Array.isArray(ends) && ends.length >= 2 && ends.every((e) => typeof e === "string") ? ends : undefined;
};

/** What the ends of a bar without a readout say: the field's words, else its bounds. */
const endsOf = (item: HaFormSchema, bounds: NumberBounds): string[] =>
  bounds.unit ? [] : validEnds(item) ?? [String(bounds.min), String(bounds.max)];

const validSteps = (item: HaFormSchema): Step[] | undefined => {
  const steps = item.steps;
  if (!Array.isArray(steps) || !steps.length) return undefined;
  return steps.every((s) => typeof s?.value === "number" && typeof s.label === "string") ? steps : undefined;
};

/** The control of one field; a pair's partner comes from `schema`. */
export const controlFor = (item: HaFormSchema, schema: readonly HaFormSchema[] = []): Control => {
  const label = labelOf(item);
  const type = selectorType(item);
  if (type === "magic_lights_color") {
    // A colour that may only be a white is a colour temperature.
    const c = config(item);
    if (c.allow_color !== false) return { kind: "color", item, label };
    return { kind: "kelvin", item, label, min: num(c.min_kelvin) ?? 1000, max: num(c.max_kelvin) ?? 10000, wrapped: true };
  }
  if (type === "magic_lights_palette") return { kind: "palette", item, label };
  const high = item.pair ? schema.find((other) => other.name === item.pair?.max) : undefined;
  if (high && selectorType(high) === type) {
    const pairLabel = item.pair?.label ?? `${label} – ${labelOf(high)}`;
    const k = [kelvinBounds(item), kelvinBounds(high)];
    if (k[0] && k[1]) {
      const bounds = { min: Math.min(k[0].min, k[1].min), max: Math.max(k[0].max, k[1].max) };
      return { kind: "range", item, high, kelvin: true, label: pairLabel, ...bounds, step: 50, unit: "K" };
    }
    const n = [numberBounds(item), numberBounds(high)];
    if (n[0] && n[1]) {
      const bounds = { min: Math.min(n[0].min, n[1].min), max: Math.max(n[0].max, n[1].max) };
      return { kind: "range", item, high, kelvin: false, label: pairLabel, ...bounds, step: n[0].step, unit: n[0].unit };
    }
  }
  const steps = validSteps(item);
  const bounds = numberBounds(item);
  if (steps && type === "number") return { kind: "steps", item, label, steps, unit: bounds?.unit ?? "" };
  if (bounds) {
    const ends = endsOf(item, bounds);
    return { kind: "bar", item, label, big: bounds.unit === "%", readout: !!bounds.unit, ends, ...bounds };
  }
  const kelvin = kelvinBounds(item);
  if (kelvin) return { kind: "kelvin", item, label, ...kelvin, wrapped: false };
  const options = choices(item);
  if (options) return { kind: "segments", item, label, options };
  if (type === "time") {
    return { kind: "selector", item, label, selector: { time: { ...config(item), no_second: true } } };
  }
  return { kind: "selector", item, label, selector: item.selector ?? {} };
};

/** The visible fields as controls, a pair as one, grouped into rows and the advanced part. */
export const formLayout = (schema: readonly HaFormSchema[], values: Record<string, unknown>): FormLayout => {
  const hidden = getHiddenFields(schema, values);
  const visible = schema.filter((item) => !hidden.has(item.name));
  const controls = visible.map((item) => controlFor(item, schema));
  const partners = new Set(controls.flatMap((c) => (c.kind === "range" ? [c.high.name] : [])));
  const layout: FormLayout = { main: [], advanced: [] };
  for (const control of controls) {
    if (partners.has(control.item.name)) continue;
    const rows = control.item.advanced ? layout.advanced : layout.main;
    const last = rows[rows.length - 1];
    const row = control.item.row;
    if (row && last?.[0].item.row === row) last.push(control);
    else rows.push([control]);
  }
  return layout;
};

/** Rough widths in px, to keep segment labels on one line. */
const FIELD_MIN = 200;
const CHAR = 8;
const SEGMENT_PAD = 16;

/** The width a control needs: its segments' longest label on one line, each, else a plain field. */
export const minWidth = (control: Control): number => {
  const labels =
    control.kind === "steps" ? control.steps.map((s) => s.label)
    : control.kind === "segments" ? control.options.map((o) => o.label)
    : undefined;
  if (!labels) return FIELD_MIN;
  const longest = Math.max(...labels.map((l) => l.length));
  return Math.max(FIELD_MIN, labels.length * (longest * CHAR + SEGMENT_PAD));
};

/** Fields of a row sit side by side only while each gets the width it needs. */
export const rowMinWidth = (row: readonly Control[]): number => Math.max(...row.map(minWidth));

/** A narrow segment wraps its label before the value, never inside it: "Medium" / "60 min". */
const keepWhole = (label: string): string => label.replace(/ (\d[\d.,]*) (\S+)$/, " $1\u00a0$2");

/** Segments of a steps control; a value that is no step shows as one more, selected. */
export const stepChoices = (steps: readonly Step[], value: unknown, unit: string): Choice[] => {
  const options = steps.map((s) => ({ value: String(s.value), label: keepWhole(s.label) }));
  if (typeof value === "number" && !steps.some((s) => s.value === value)) {
    options.push({ value: String(value), label: keepWhole(`Custom ${value}${unit ? ` ${unit}` : ""}`) });
  }
  return options;
};

/** The text under a field: the hint for its current value, else its helper. */
export const helperOf = (item: HaFormSchema, value: unknown): string | undefined =>
  (item.hints && value !== undefined && item.hints[String(value)]) || item.helper;

/** "Sunrise offset · Update every": what a collapsed advanced part holds. */
export const advancedSummary = (rows: readonly Control[][]): string =>
  rows
    .flat()
    .map((c) => c.label)
    .join(" · ");
