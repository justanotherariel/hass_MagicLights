import assert from "node:assert/strict";
import { test } from "node:test";
import { colorToHex, colorToRgb } from "../src/shared/color";
import type { EffectInfo, HomeAssistant, PaletteInfo, Part, PreviewFrame, StoredScene, TargetInfo } from "../src/shared/types";
import { type Draft, MlStore } from "../src/panel/store";
import { hsToRgb, parseColor, rgbToHs, swatchColors, xyToKelvin } from "../src/panel/editor/colors";
import {
  canSave,
  changedParts,
  expandLights,
  forkPart,
  frameAt,
  isDirty,
  labelledSelector,
  layerOwners,
  layerSwatches,
  nextParams,
  partUsers,
  sceneProblems,
  stableJson,
  withDefaults,
  withoutUnusedParts,
  withPart,
} from "../src/panel/editor/editor-model";
import { draftStale, dropWorkingCopy, keepWorkingCopy, openWorkingCopy } from "../src/panel/editor/working-copy";
import { CONFIG, draftStorage, fakeHass } from "./fixtures";

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
            { value: "tanh", label: "Smooth" },
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
    default_scene: null,
    reclaim_on_turn_on: true,
    detect_changes: true,
  },
  scenes: [],
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
  id: "s_00000002",
  name: "Movie",
  icon: null,
  layers: [{ lights: "all", part: "p_00000001" }],
  others: "off",
  transition: null,
  ...s,
});
const MOVIE: Part = { id: "p_00000001", name: "Movie", effect: "white", params: { kelvin: 2700, brightness: 100 } };
const SAVED: Record<string, Part> = { p_00000001: MOVIE };
const saved = (id: string): Part | undefined => SAVED[id];

test("scene problems gate Try and Save", () => {
  const effectOf = (key: string) => (key === "white" ? WHITE : undefined);
  const problems = (draft: Partial<Draft>) =>
    sceneProblems({ scene: scene({}), parts: [], ...draft }, saved, effectOf);
  assert.deepEqual(problems({}), []);
  assert.deepEqual(
    problems({ scene: scene({ name: "  " }) }).map((p) => p.layer),
    [undefined],
  );
  assert.equal(problems({ scene: scene({ name: "manual" }) }).length, 1);
  assert.equal(problems({ scene: scene({ name: "x".repeat(65) }) }).length, 1);
  const strobe: Part = { ...MOVIE, id: "p_00000009", effect: "strobe" };
  const layers: StoredScene["layers"] = [
    { lights: "all", part: "p_00000001" },
    { lights: [], part: "p_00000001" },
    { lights: ["light.lamp"], part: strobe.id },
    { lights: ["light.lamp"], part: "p_gone0000" },
  ];
  assert.deepEqual(
    problems({ scene: scene({ layers }), parts: [strobe] }).map((p) => [p.layer, p.message]),
    [
      [1, "Pick at least one light."],
      [2, 'The effect "strobe" is not installed.'],
      [3, 'The part "p_gone0000" no longer exists.'],
    ],
  );
  const unnamed = { ...MOVIE, name: " " };
  assert.deepEqual(
    problems({ parts: [unnamed] }).map((p) => [p.part, p.message]),
    [["p_00000001", "Give the part a name."]],
  );
});

test("a part edit is pending for every scene that uses it; Copy forks it for this scene", () => {
  const two = scene({
    layers: [
      { lights: "all", part: "p_00000001" },
      { lights: ["light.lamp"], part: "relax" },
    ],
  });
  const dimmer = { ...MOVIE, params: { kelvin: 2700, brightness: 30 } };
  const edited = withPart({ scene: two, parts: [] }, dimmer);
  assert.deepEqual(edited.parts, [dimmer]);
  assert.deepEqual(withPart(edited, MOVIE).parts, [MOVIE], "edited again in place");
  // Copy takes the pending edit along; the shared part is left as saved.
  const copy: Part = { ...dimmer, id: "p_0000c0de", name: "Movie copy" };
  const forked = forkPart(edited, "p_00000001", copy);
  assert.deepEqual(forked.scene.layers, [
    { lights: "all", part: "p_0000c0de" },
    { lights: ["light.lamp"], part: "relax" },
  ]);
  assert.deepEqual(forked.parts, [copy]);
  assert.deepEqual(changedParts(forked.parts, saved), [copy], "only the copy is saved");
  // A part no layer plays any more is not saved either.
  const dropped = withoutUnusedParts({ scene: scene({ layers: [{ lights: "all", part: "relax" }] }), parts: [dimmer] });
  assert.deepEqual(dropped.parts, []);
});

test("Used by counts this scene first, saved or new to the part", async () => {
  const store = await MlStore.load({ hass: fakeHass(), storeChanged: () => undefined });
  const ids = (users: ReturnType<typeof partUsers>) => users.map((s) => s?.id);
  // The kitchen's Relax comes first among the users; the living room's is this scene.
  assert.deepEqual(ids(partUsers(store.usesOf("relax"), "s_00000001")), ["s_00000001", "s_00000004"]);
  assert.deepEqual(ids(partUsers(store.usesOf("relax"), "s_0000beef")), [undefined, "s_00000004", "s_00000001"]);
  assert.deepEqual(ids(partUsers(store.usesOf("p_00000001"), "s_00000002")), ["s_00000002"], "only this scene");
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

test("a reload keeps the edits of a scene that was never saved unsaved", async () => {
  const items = draftStorage();
  const store = await MlStore.load({ hass: fakeHass(), storeChanged: () => undefined });

  // The panel writes a new scene's draft, then the editor opens it.
  const created = store.newScene("room:living", null);
  const id = created.scene.id;
  store.setDraft(id, created);
  assert.deepEqual(openWorkingCopy(store, "room:living", id)?.baseline, created);
  const renamed = { ...created, scene: { ...created.scene, name: "Edited" } };
  keepWorkingCopy(store, renamed);
  const reloaded = openWorkingCopy(store, "room:living", id);
  assert.equal(reloaded?.restored, true);
  assert.equal(reloaded?.stale, undefined);
  assert.equal(reloaded?.draft.scene.name, "Edited");
  assert.deepEqual(reloaded?.baseline, created);
  assert.equal(isDirty(reloaded!.draft, reloaded!.baseline, saved), true);
  dropWorkingCopy(store, id);
  assert.equal(items.size, 0);

  // A saved scene opens as saved, measured against it, in its own room only.
  const movie = openWorkingCopy(store, "room:living", "s_00000002")!;
  assert.equal(movie.restored, false);
  assert.deepEqual(movie.draft, movie.baseline);
  assert.equal(movie.draft.scene.name, "Movie");
  assert.equal(openWorkingCopy(store, "room:kitchen", "s_00000002"), undefined);
  assert.equal(openWorkingCopy(store, "room:living", "s_0000dead"), undefined);
});

test("a draft says when its scene or parts were saved or deleted elsewhere", async () => {
  draftStorage();
  const store = await MlStore.load({ hass: fakeHass(), storeChanged: () => undefined });
  for (const sceneId of ["s_00000001", "s_00000002"]) {
    const { draft } = openWorkingCopy(store, "room:living", sceneId)!;
    keepWorkingCopy(store, { ...draft, scene: { ...draft.scene, transition: 3 } });
  }
  // Relax's built-in is edited in the draft; its saved version is kept as the edit's base.
  const relax = openWorkingCopy(store, "room:living", "s_00000001")!.draft;
  const dimmer: Part = { id: "relax", name: "Relax", effect: "white", params: { kelvin: 2700, brightness: 20 } };
  keepWorkingCopy(store, withPart(relax, dimmer));

  // Another tab deletes Movie and edits Relax for everyone.
  const config = structuredClone(CONFIG);
  config.scenes = config.scenes.filter((s) => s.id !== "s_00000002");
  config.parts = config.parts.map((p) => (p.id === "relax" ? { ...p, params: { kelvin: 2200, brightness: 100 } } : p));
  const hass = { ...fakeHass(), callWS: async <T>() => structuredClone(config) as T } as HomeAssistant;
  const later = await MlStore.load({ hass, storeChanged: () => undefined });

  assert.equal(openWorkingCopy(later, "room:living", "s_00000002")?.stale, "deleted");
  const stale = openWorkingCopy(later, "room:living", "s_00000001")!;
  assert.equal(stale.stale, "saved", "the part it edits was saved elsewhere");
  assert.deepEqual(stale.draft.parts, [dimmer]);
});

test("save needs a change, except for a new scene", () => {
  const draft: Draft = { scene: scene({}), parts: [] };
  assert.equal(isDirty(draft, structuredClone(draft), saved), false);
  assert.equal(canSave(draft, draft, true, saved), false, "an unchanged saved scene");
  assert.equal(canSave({ ...draft, scene: scene({ transition: 2 }) }, draft, true, saved), true);
  assert.equal(canSave(draft, draft, false, saved), true, "a new scene saves as it is");
  // A part edit is a change; an edit back to the saved part is none.
  const dimmer = { ...MOVIE, params: { brightness: 30 } };
  assert.equal(canSave(withPart(draft, dimmer), draft, true, saved), true);
  assert.equal(canSave(withPart(draft, MOVIE), draft, true, saved), false);
  // A new scene's blank part is part of its base.
  const blank: Part = { ...MOVIE, id: "p_0000b1a4", name: "New part" };
  const fresh: Draft = { scene: scene({ layers: [{ lights: "all", part: blank.id }] }), parts: [blank] };
  assert.equal(isDirty(fresh, structuredClone(fresh), saved), false);
  assert.equal(isDirty(withPart(fresh, { ...blank, name: "Lamp" }), fresh, saved), true);
});

test("a draft's base tells what changed elsewhere since", () => {
  const draft: Draft = { scene: scene({}), parts: [] };
  const base = { draft, stored: true, parts: {} };
  assert.equal(draftStale(base, draft.scene, saved), undefined);
  assert.equal(draftStale(base, scene({ transition: 2 }), saved), "saved");
  assert.equal(draftStale(base, undefined, saved), "deleted");
  assert.equal(draftStale({ ...base, stored: false }, undefined, saved), undefined);
  assert.equal(draftStale({ ...base, stored: false }, draft.scene, saved), "saved");
  const edit = { ...base, parts: { p_00000001: MOVIE } };
  assert.equal(draftStale(edit, draft.scene, saved), undefined, "the part is as the edit found it");
  assert.equal(draftStale(edit, draft.scene, () => ({ ...MOVIE, name: "Film" })), "saved");
  assert.equal(draftStale(edit, draft.scene, () => undefined), "saved", "deleted since");
  assert.equal(draftStale({ ...base, parts: { p_0000b1a4: null } }, draft.scene, saved), undefined, "a new part");
});
