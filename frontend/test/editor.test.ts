import assert from "node:assert/strict";
import { test } from "node:test";
import { colorToHex, colorToRgb } from "../src/shared/color";
import type { EffectInfo, PaletteInfo, PreviewFrame, StoredScene, TargetInfo } from "../src/shared/types";
import { MlStore, toStored } from "../src/panel/store";
import { hsToRgb, parseColor, rgbToHs, swatchColors, xyToKelvin } from "../src/panel/editor/colors";
import {
  copyId,
  copyOverlaps,
  expandLights,
  frameAt,
  labelledSelector,
  layerOwners,
  layerSummary,
  layerSwatches,
  nextParams,
  sceneProblems,
  stableJson,
  withDefaults,
} from "../src/panel/editor/editor-model";
import { dropWorkingCopy, openWorkingCopy } from "../src/panel/editor/working-copy";
import { fakeHass } from "./fixtures";

// Schemas as effects/schema.py serialises them (HA 2026.8).
const pct = { number: { min: 1, max: 100, step: 1, unit_of_measurement: "%", mode: "slider" } };
const WHITE: EffectInfo = {
  key: "white",
  name: "White",
  description: "",
  icon: "mdi:white-balance-sunny",
  category: "white",
  dynamic: false,
  source: "builtin",
  schema: [
    { name: "kelvin", required: true, default: 2700, selector: { color_temp: { unit: "kelvin", min: 1500, max: 6500 } } },
    { name: "brightness", required: true, default: 100, selector: pct },
  ],
  defaults: { kelvin: 2700, brightness: 100 },
};
const PALETTE: EffectInfo = {
  key: "palette",
  name: "Colour palette",
  description: "",
  icon: "mdi:palette",
  category: "color",
  dynamic: true,
  source: "builtin",
  schema: [
    { name: "palette", required: true, selector: { magic_lights_palette: { min: 1, max: 9 } } },
    { name: "brightness", required: true, selector: pct },
    { name: "white", required: true, selector: { magic_lights_color: { allow_color: false } } },
    { name: "motion", required: true, selector: { select: { options: ["static", "dynamic"] } } },
    {
      name: "speed",
      required: true,
      selector: { number: { min: 0.05, max: 1, step: 0.05, mode: "slider" } },
      visible: { field: "motion", value: "dynamic" },
    },
  ],
  defaults: {
    palette: [{ xy: [0.5019, 0.4152] }],
    brightness: 60,
    white: { kelvin: 2700 },
    motion: "static",
    speed: 0.6,
  },
};
const LIGHT_EFFECT: EffectInfo = {
  ...WHITE,
  key: "light_effect",
  name: "Light effect",
  schema: [
    { name: "effect", required: true, selector: { select: { options: ["candle", "fireplace"], custom_value: true } } },
    { name: "brightness", required: true, selector: pct },
    { name: "speed", required: true, selector: { number: { min: 0, max: 1, step: 0.05, mode: "slider" } } },
    { name: "color", required: true, selector: { magic_lights_color: {} } },
  ],
  defaults: { effect: "candle", brightness: 35, speed: 0.5, color: { xy: [0.561, 0.4042] } },
};
const MODE = "brightness_mode";
const ADAPTIVE: EffectInfo = {
  ...WHITE,
  key: "adaptive",
  name: "Adaptive",
  category: "adaptive",
  schema: [
    {
      name: MODE,
      required: true,
      selector: {
        select: {
          options: [
            { value: "static", label: "None (static)" },
            { value: "default", label: "Dim after sunset" },
            { value: "tanh", label: "Smooth ramp" },
          ],
        },
      },
    },
    { name: "brightness", required: true, selector: pct, visible: { field: MODE, value: "static" } },
    {
      name: "min_brightness",
      required: true,
      selector: pct,
      visible: { field: MODE, operator: "not_eq", value: "static" },
    },
    {
      name: "ramp_dark",
      required: true,
      selector: { number: { min: 5, max: 240, unit_of_measurement: "min", mode: "slider" } },
      visible: { field: MODE, operator: "in", value: ["linear", "tanh"] },
    },
    { name: "min_kelvin", required: true, selector: { color_temp: { unit: "kelvin", min: 1000, max: 10000 } } },
  ],
  defaults: { [MODE]: "static", brightness: 100, min_brightness: 40, ramp_dark: 60, min_kelvin: 2200 },
};

const ZONE: TargetInfo = {
  id: "zone:downstairs",
  kind: "zone",
  name: "Downstairs",
  icon: null,
  area_id: null,
  rooms: ["living", "kitchen"],
  lights: ["light.ceiling", "light.island", "light.lamp", "light.spot"],
  options: {
    excluded_lights: [],
    extra_lights: [],
    visible_scenes: null,
    default_scene: null,
    reclaim_on_turn_on: true,
    detect_changes: true,
  },
  scenes: [],
  hidden: [],
  select_entity_id: "select.downstairs_scene",
};
// The kitchen room also holds an area-less light the zone does not include.
const ROOMS: Record<string, string[]> = {
  "room:living": ["light.ceiling", "light.lamp"],
  "room:kitchen": ["light.island", "light.pantry", "light.spot"],
};
const roomLights = (id: string) => ROOMS[id];

test("layer lights expand to the target's lights in target order", () => {
  assert.deepEqual(expandLights("all", ZONE, roomLights), ZONE.lights);
  assert.deepEqual(expandLights(["light.lamp", "light.ceiling"], ZONE, roomLights), ["light.ceiling", "light.lamp"]);
  assert.deepEqual(expandLights(["room:kitchen"], ZONE, roomLights), ["light.island", "light.spot"]);
  assert.deepEqual(expandLights(["room:kitchen", "light.island", "light.other"], ZONE, roomLights), [
    "light.island",
    "light.spot",
  ]);
  assert.deepEqual(expandLights([], ZONE, roomLights), []);
  assert.deepEqual(expandLights(["room:attic"], ZONE, roomLights), []);
});

test("later layers win", () => {
  const owners = layerOwners([{ lights: "all" }, { lights: ["room:kitchen"] }, { lights: ["light.spot"] }], ZONE, roomLights);
  assert.deepEqual(Object.fromEntries(owners), {
    "light.ceiling": 0,
    "light.lamp": 0,
    "light.island": 1,
    "light.spot": 2,
  });
});

test("defaults fill missing keys but not cleared ones", () => {
  const values = withDefaults({ kelvin: 2700, brightness: 100 }, { kelvin: 3000, brightness: undefined });
  assert.deepEqual(values, { kelvin: 3000, brightness: undefined });
  assert.deepEqual(withDefaults({ palette: [{ kelvin: 2000 }] }, {}), { palette: [{ kelvin: 2000 }] });
  const defaults = { palette: [{ kelvin: 2000 }] };
  assert.notEqual(withDefaults(defaults, {}).palette, defaults.palette);
});

test("layer summaries rebuild describe() from the schema", () => {
  assert.equal(layerSummary(WHITE, { kelvin: 2700, brightness: 60 }), "2700 K · 60%");
  assert.equal(layerSummary(PALETTE, {}), "60% · Static");
  assert.equal(layerSummary(PALETTE, { motion: "dynamic" }), "60% · Dynamic");
  assert.equal(layerSummary(LIGHT_EFFECT, { effect: "fireplace", brightness: 45 }), "Fireplace · 45%");
  assert.equal(layerSummary(undefined, { kelvin: 2700 }), "");
});

test("layer summaries show option labels and skip hidden fields", () => {
  assert.equal(layerSummary(ADAPTIVE, {}), "None (static) · 100% · 2200 K");
  // Values of hidden fields are kept, but not summarised.
  const params = { [MODE]: "default", brightness: 70, min_brightness: 20, ramp_dark: 30 };
  assert.equal(layerSummary(ADAPTIVE, params), "Dim after sunset · 20% · 2200 K");
  assert.equal(layerSummary(ADAPTIVE, { ...params, [MODE]: "tanh" }), "Smooth ramp · 20% · 30 min");
});

test("form edits keep the values of fields a mode hides", () => {
  const custom = { [MODE]: "tanh", min_brightness: 20, ramp_dark: 30, min_sunrise: "06:30:00" };
  const hidden = nextParams(ADAPTIVE.defaults, custom, MODE, "static");
  assert.deepEqual(hidden, { ...custom, [MODE]: "static", brightness: 100, min_kelvin: 2200 });
  const shown = nextParams(ADAPTIVE.defaults, hidden, MODE, "tanh");
  assert.deepEqual(shown, { ...custom, brightness: 100, min_kelvin: 2200 });
  assert.equal(nextParams(ADAPTIVE.defaults, shown, "min_sunrise", "").min_sunrise, undefined);
});

test("layer swatches: a palette wins over single colours", () => {
  const palette = [{ xy: [0.6, 0.35] as [number, number] }, { kelvin: 2200 }];
  assert.deepEqual(layerSwatches(PALETTE, { palette }), palette.map(colorToHex));
  assert.deepEqual(layerSwatches(WHITE, { kelvin: 2700 }), [colorToHex({ kelvin: 2700 })]);
  assert.deepEqual(layerSwatches(LIGHT_EFFECT, {}), [colorToHex({ xy: [0.561, 0.4042] })]);
  const many = Array.from({ length: 7 }, (_, i) => ({ kelvin: 2000 + i * 500 }));
  assert.equal(layerSwatches(PALETTE, { palette: many }).length, 5);
});

test("plain select options get labels, labelled ones stay", () => {
  const plain = { select: { options: ["static", "dynamic"], mode: "list" } };
  const labelled = labelledSelector(plain);
  assert.deepEqual(labelled, {
    select: {
      options: [
        { value: "static", label: "Static" },
        { value: "dynamic", label: "Dynamic" },
      ],
      mode: "list",
    },
  });
  assert.equal(labelledSelector(plain), labelled);
  const given = { select: { options: [{ value: "a", label: "A" }] } };
  assert.equal(labelledSelector(given), given);
  const number = { number: { min: 1 } };
  assert.equal(labelledSelector(number), number);
});

test("stable JSON ignores key order and undefined", () => {
  assert.equal(stableJson({ b: 1, a: { d: [1, { y: 1, x: 2 }], c: undefined } }), '{"a":{"d":[1,{"x":2,"y":1}]},"b":1}');
  assert.equal(stableJson({ a: 1, b: 2 }), stableJson({ b: 2, a: 1 }));
});

const scene = (s: Partial<StoredScene>): StoredScene => ({
  id: "s_00000001",
  name: "Movie",
  icon: null,
  scope: [],
  preset: null,
  layers: [{ lights: "all", effect: "white", params: {} }],
  others: "off",
  brightness: 100,
  transition: null,
  ...s,
});

test("scene problems gate Try and Save", () => {
  const effectOf = (key: string) => (key === "white" ? WHITE : undefined);
  assert.deepEqual(sceneProblems(scene({}), effectOf), []);
  assert.deepEqual(
    sceneProblems(scene({ name: "  " }), effectOf).map((p) => p.layer),
    [undefined],
  );
  assert.equal(sceneProblems(scene({ name: "manual" }), effectOf).length, 1);
  assert.equal(sceneProblems(scene({ name: "x".repeat(65) }), effectOf).length, 1);
  const layers = [
    { lights: "all" as const, effect: "white", params: {} },
    { lights: [], effect: "white", params: {} },
    { lights: ["light.lamp"], effect: "strobe", params: {} },
  ];
  assert.deepEqual(
    sceneProblems(scene({ layers }), effectOf).map((p) => p.layer),
    [1, 2],
  );
});

test("frameAt picks the last frame at or before t", () => {
  const frames: PreviewFrame[] = [0, 0.5, 1, 1.5].map((t) => ({ t, lights: {} }));
  assert.equal(frameAt([], 1), undefined);
  assert.equal(frameAt(frames, 0)?.t, 0);
  assert.equal(frameAt(frames, 0.49)?.t, 0);
  assert.equal(frameAt(frames, 1)?.t, 1);
  assert.equal(frameAt(frames, 99)?.t, 1.5);
  assert.equal(frameAt(frames.slice(0, 1), 5)?.t, 0);
});

test("hue/saturation round trip and kelvin from xy", () => {
  for (const rgb of [
    [255, 0, 0],
    [0, 128, 255],
    [255, 200, 100],
  ] as const) {
    const back = hsToRgb(rgbToHs([...rgb]));
    const max = Math.max(...rgb);
    rgb.forEach((v, i) => assert(Math.abs(back[i] - (v * 255) / max) <= 1, `${rgb} -> ${back}`));
  }
  assert.deepEqual(rgbToHs([0, 0, 0]), [0, 0]);
  // homeassistant.util.color.color_xy_to_temperature on the same points.
  assert.equal(xyToKelvin([0.42, 0.365]), 2964);
  assert.equal(xyToKelvin([0.326, 0.333]), 5802);
  assert.equal(xyToKelvin([0.598, 0.383]), 1741);
});

test("colour inputs parse like models.Color.parse", () => {
  assert.deepEqual(parseColor({ kelvin: 2700 }), { kelvin: 2700 });
  assert.deepEqual(parseColor({ xy: [0.5, 0.4] }), { xy: [0.5, 0.4] });
  assert.deepEqual(parseColor({ rgb: [255, 0, 0] }), parseColor("#ff0000"));
  assert.deepEqual(parseColor("#FF0000"), { xy: [0.7006, 0.2993] });
  assert.equal(parseColor("red"), undefined);
  assert.equal(parseColor({ xy: [1] }), undefined);
  assert.equal(parseColor(null), undefined);
  // Black has no chromaticity: the backend rejects it.
  assert.equal(parseColor({ rgb: [0, 0, 0] }), undefined);
  assert.equal(parseColor("#000000"), undefined);
});

test("swatch colours are unique and sorted by hue", () => {
  const palette = (id: string, colors: PaletteInfo["colors"]): PaletteInfo => ({ id, name: id, colors });
  const red = { xy: [0.64, 0.33] as [number, number] };
  const blue = { xy: [0.15, 0.06] as [number, number] };
  const green = { xy: [0.3, 0.6] as [number, number] };
  const swatches = swatchColors([palette("a", [blue, red]), palette("b", [red, green])]);
  assert.deepEqual(swatches.map(colorToHex), [red, green, blue].map(colorToHex));
  const hues = swatches.map((c) => rgbToHs(colorToRgb(c))[0]);
  assert.deepEqual([...hues].sort((a, b) => a - b), hues);
  const many = Array.from({ length: 40 }, (_, i) => ({ kelvin: 1500 + i * 100 }));
  assert.equal(swatchColors([palette("c", many)], 16).length, 16);
});

test("narrowing a stored customisation saves a new copy, widening keeps its id", () => {
  const everywhere = scene({ id: "s_0000000e", preset: "relax", scope: [] });
  const fresh = copyId(everywhere, everywhere, ["room:bedroom"]);
  assert.match(fresh, /^s_[0-9a-f]{8}$/);
  assert.notEqual(fresh, everywhere.id);
  const narrowed = { ...everywhere, id: fresh, scope: ["room:bedroom" as const] };
  assert.equal(copyId(everywhere, narrowed, ["room:bedroom"]), fresh, "toggling keeps the new id");
  assert.equal(copyId(everywhere, narrowed, []), everywhere.id);
  const room = scene({ id: "s_0000000b", preset: "relax", scope: ["room:bedroom"] });
  assert.equal(copyId(room, room, []), room.id);
  const two = scene({ id: "s_0000000c", preset: "relax", scope: ["room:bedroom", "room:office"] });
  assert.notEqual(copyId(two, two, ["room:bedroom"]), two.id);
  const unsaved = scene({ id: "s_0000000f", preset: "relax", scope: ["room:bedroom"] });
  assert.equal(copyId(undefined, unsaved, []), unsaved.id);
  const own = scene({ scope: ["room:bedroom"] });
  assert.equal(copyId(own, own, []), own.id, "a user scene's scope is where it is available");
});

test("a saved customisation makes room in the copies it overlaps", () => {
  const everywhere = scene({ id: "s_0000000e", preset: "relax", scope: [] });
  const two = scene({ id: "s_0000000c", preset: "relax", scope: ["room:bedroom", "room:office"] });
  const kitchen = scene({ id: "s_0000000d", preset: "relax", scope: ["room:kitchen"] });
  const other = scene({ id: "s_00000010", preset: "focus", scope: [] });
  const stored = [everywhere, two, kitchen, other, scene({})];
  const widened = { ...kitchen, scope: [] };
  assert.deepEqual(copyOverlaps(widened, stored), [{ copy: everywhere }]);
  const bedroom = scene({ id: "s_00000011", preset: "relax", scope: ["room:bedroom"] });
  assert.deepEqual(copyOverlaps(bedroom, stored), [{ copy: two, rest: ["room:office"] }]);
  assert.deepEqual(copyOverlaps({ ...bedroom, scope: ["room:bedroom", "room:office"] }, stored), [
    { copy: two, rest: undefined },
  ]);
  assert.deepEqual(copyOverlaps(kitchen, stored), [], "a room copy and an everywhere copy coexist");
  assert.deepEqual(copyOverlaps(everywhere, stored), []);
  assert.deepEqual(copyOverlaps(scene({ scope: [] }), stored), [], "user scenes are no copies");
});

test("a reload keeps the edits of a scene that was never saved unsaved", async () => {
  const items = new Map<string, string>();
  const sessionStorage = {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  };
  Object.assign(globalThis, { window: { sessionStorage } });
  const store = await MlStore.load({ hass: fakeHass(), storeChanged: () => undefined });

  // The gallery's New scene writes the draft, then the editor opens it.
  const created = store.newScene("room:living");
  store.setDraft("room:living", created.id, created);
  assert.deepEqual(openWorkingCopy(store, "room:living", created.id)?.baseline, created);
  store.setDraft("room:living", created.id, { ...created, name: "Edited" });
  const reloaded = openWorkingCopy(store, "room:living", created.id);
  assert.equal(reloaded?.restored, true);
  assert.equal(reloaded?.scene.name, "Edited");
  assert.deepEqual(reloaded?.baseline, created);
  dropWorkingCopy(store, "room:living", created.id);
  assert.equal(items.size, 0);

  // A preset copy keeps its generated id and its baseline.
  const copy = openWorkingCopy(store, "room:kitchen", "relax")!;
  store.setDraft("room:kitchen", "relax", { ...copy.scene, brightness: 40 });
  const again = openWorkingCopy(store, "room:kitchen", "relax");
  assert.equal(again?.scene.id, copy.scene.id);
  assert.deepEqual(again?.baseline, copy.scene);

  // Without its draft, an old baseline is not used.
  store.setDraft("room:kitchen", "relax", undefined);
  const next = openWorkingCopy(store, "room:kitchen", "relax")!;
  assert.equal(next.restored, false);
  assert.deepEqual(next.baseline, next.scene);

  // A stored scene is measured against its stored copy, also after its id changed.
  const sunset = openWorkingCopy(store, "room:living", "savanna_sunset")!;
  store.setDraft("room:living", "savanna_sunset", { ...sunset.scene, id: "s_12345678", scope: [] });
  assert.deepEqual(
    openWorkingCopy(store, "room:living", "savanna_sunset")?.baseline,
    toStored(store.scene("s_11111111")!),
  );
});
