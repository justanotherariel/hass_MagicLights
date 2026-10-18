import assert from "node:assert/strict";
import { test } from "node:test";
import {
  advancedSummary,
  controlFor,
  formLayout,
  helperOf,
  minWidth,
  rowMinWidth,
  stepChoices,
} from "../src/panel/editor/form-rules";
import { formatStep, moveHandle } from "../src/panel/components/bar-math";
import type { HaFormSchema } from "../src/shared/types";

const pct = { number: { min: 1, max: 100, step: 1, unit_of_measurement: "%", mode: "slider" } };
const minutes = { number: { min: 5, max: 240, step: 1, unit_of_measurement: "min", mode: "slider" } };
const kelvin = { color_temp: { unit: "kelvin", min: 1000, max: 10000 } };
const steps = [
  { value: 30, label: "Short 30 min" },
  { value: 60, label: "Medium 60 min" },
];

// The adaptive schema as effects/schema.py serialises it, cut down.
const ADAPTIVE: HaFormSchema[] = [
  {
    name: "brightness_mode",
    label: "Brightness curve",
    selector: {
      select: {
        options: [
          { value: "static", label: "None (static)" },
          { value: "default", label: "Dim after sunset" },
          { value: "linear", label: "Linear" },
          { value: "tanh", label: "Smooth" },
        ],
      },
    },
  },
  { name: "brightness", label: "Brightness", selector: pct, visible: { field: "brightness_mode", value: "static" } },
  {
    name: "min_brightness",
    label: "Night",
    selector: pct,
    row: "levels",
    visible: { field: "brightness_mode", operator: "not_eq", value: "static" },
  },
  {
    name: "max_brightness",
    label: "Day",
    selector: pct,
    row: "levels",
    visible: { field: "brightness_mode", operator: "not_eq", value: "static" },
  },
  { name: "ramp_dark", label: "Dark-side ramp", selector: minutes, steps, row: "ramps" },
  { name: "min_kelvin", label: "Warmest white", selector: kelvin, pair: { max: "max_kelvin", label: "Warmest ↔ coolest white" } },
  { name: "max_kelvin", label: "Coolest white", selector: kelvin },
  { name: "sunrise_offset", label: "Sunrise offset", selector: { number: { min: -180, max: 180, unit_of_measurement: "min" } }, advanced: true },
  { name: "min_sunrise", label: "Sunrise not before", selector: { time: {} }, advanced: true },
];

const kinds = (rows: { kind: string; item: HaFormSchema }[][]) => rows.map((row) => row.map((c) => `${c.kind}:${c.item.name}`));

test("controls follow the selector's shape, never the effect", () => {
  const control = (item: HaFormSchema) => controlFor(item, ADAPTIVE);
  assert.deepEqual(
    (({ kind, big, unit }) => ({ kind, big, unit }))(control(ADAPTIVE[1]) as { kind: string; big: boolean; unit: string }),
    { kind: "bar", big: true, unit: "%" },
  );
  const offset = control(ADAPTIVE[7]);
  assert.equal(offset.kind === "bar" && offset.big, false, "a number in another unit is a slim bar");
  assert.equal(offset.kind === "bar" && offset.readout, true);
  const speed = control({ name: "speed", selector: { number: { min: 0.05, max: 1, step: 0.05 } } });
  assert.equal(speed.kind === "bar" && speed.readout, false, "a unitless value prints nothing");
  assert.deepEqual(speed.kind === "bar" && speed.ends, ["0.05", "1"], "so its bar shows its bounds");
  const lively = control({ name: "speed", ends: ["Slow", "Lively"], selector: { number: { min: 0, max: 1 } } });
  assert.deepEqual(lively.kind === "bar" && lively.ends, ["Slow", "Lively"], "or the field's own words");
  assert.deepEqual(offset.kind === "bar" && offset.ends, [], "a value with a unit needs no ends");
  assert.equal(control(ADAPTIVE[0]).kind, "segments");
  assert.equal(control({ name: "k", selector: kelvin }).kind, "kelvin");
  assert.equal(control(ADAPTIVE[4]).kind, "steps");
  const range = control(ADAPTIVE[5]);
  assert.equal(range.kind, "range");
  assert.equal(range.label, "Warmest ↔ coolest white");
  assert.deepEqual(range.kind === "range" && [range.min, range.max, range.kelvin], [1000, 10000, true]);
  assert.deepEqual(control(ADAPTIVE[8]), {
    kind: "selector",
    item: ADAPTIVE[8],
    label: "Sunrise not before",
    selector: { time: { no_second: true } },
  });
  const five = { name: "s", selector: { select: { options: ["a", "b", "c", "d", "e"] } } };
  assert.equal(control(five).kind, "selector", "five choices stay a dropdown");
  const custom = { name: "s", selector: { select: { options: ["a"], custom_value: true } } };
  assert.equal(control(custom).kind, "selector");
  assert.deepEqual(control({ name: "s", selector: { select: { options: ["warm_white"] } } }), {
    kind: "segments",
    item: { name: "s", selector: { select: { options: ["warm_white"] } } },
    label: "S",
    options: [{ value: "warm_white", label: "Warm white" }],
  });
  assert.equal(control({ name: "c", selector: { magic_lights_color: {} } }).kind, "color");
  assert.equal(control({ name: "n", selector: { number: { mode: "box" } } }).kind, "selector", "no range, no bar");
  const lonely = { name: "lo", selector: pct, pair: { max: "gone" } };
  assert.equal(control(lonely).kind, "bar", "a pair without its partner is a plain bar");
});

test("the layout hides fields, merges a pair, groups rows and folds advanced fields", () => {
  const smooth = formLayout(ADAPTIVE, { brightness_mode: "tanh" });
  assert.deepEqual(kinds(smooth.main), [
    ["segments:brightness_mode"],
    ["bar:min_brightness", "bar:max_brightness"],
    ["steps:ramp_dark"],
    ["range:min_kelvin"],
  ]);
  assert.deepEqual(kinds(smooth.advanced), [["bar:sunrise_offset"], ["selector:min_sunrise"]]);
  assert.equal(advancedSummary(smooth.advanced), "Sunrise offset · Sunrise not before");
  const still = formLayout(ADAPTIVE, { brightness_mode: "static" });
  assert.deepEqual(kinds(still.main).slice(0, 2), [["segments:brightness_mode"], ["bar:brightness"]]);
});

test("a row stacks once a segment label would wrap", () => {
  const smooth = formLayout(ADAPTIVE, { brightness_mode: "tanh" });
  const [levels] = smooth.main.filter((row) => row.length === 2);
  assert.equal(rowMinWidth(levels), 200, "two bars sit side by side from 2 × 200 px");
  const ramp = controlFor(ADAPTIVE[4], ADAPTIVE);
  assert.equal(minWidth(ramp), 2 * ("Medium 60 min".length * 8 + 16));
  const curve = controlFor(ADAPTIVE[0], ADAPTIVE);
  assert.equal(minWidth(curve), 4 * ("Dim after sunset".length * 8 + 16));
});

test("a field's text follows its value when it has hints", () => {
  const item: HaFormSchema = { name: "mode", helper: "Pick one", hints: { static: "One brightness all day" } };
  assert.equal(helperOf(item, "static"), "One brightness all day");
  assert.equal(helperOf(item, "tanh"), "Pick one");
  assert.equal(helperOf({ name: "plain" }, "x"), undefined);
});

test("a stored value that is no step shows as one more segment", () => {
  assert.deepEqual(stepChoices(steps, 60, "min"), [
    { value: "30", label: "Short 30\u00a0min" },
    { value: "60", label: "Medium 60\u00a0min" },
  ]);
  assert.deepEqual(stepChoices(steps, 75, "min").at(-1), { value: "75", label: "Custom 75\u00a0min" });
});

test("range handles snap to the step and never pass each other", () => {
  const bounds = { min: 1000, max: 10000, step: 50 };
  assert.deepEqual(moveHandle([2200, 5000], 0, 2234, bounds), [2250, 5000]);
  assert.deepEqual(moveHandle([2200, 5000], 0, 9000, bounds), [4950, 5000], "stops a step short of the other");
  assert.deepEqual(moveHandle([2200, 5000], 1, 100, bounds), [2200, 2250]);
  assert.deepEqual(moveHandle([2200, 5000], 1, 20000, bounds), [2200, 10000]);
  assert.deepEqual(moveHandle([2200, 5000], 0, -5, bounds), [1000, 5000]);
  assert.equal(formatStep(0.1 + 0.2, 0.05), "0.3");
  assert.equal(formatStep(40, 1), "40");
});
