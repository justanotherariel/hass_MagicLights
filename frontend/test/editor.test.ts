import assert from "node:assert/strict";
import { test } from "node:test";
import { colorToHex, colorToRgb } from "../src/shared/color";
import type { EffectInfo, HomeAssistant, PaletteInfo, PreviewFrame, StoredScene, TargetInfo } from "../src/shared/types";
import { MlStore, toStored } from "../src/panel/store";
import { hsToRgb, parseColor, rgbToHs, swatchColors, xyToKelvin } from "../src/panel/editor/colors";
import {
  canSave,
  expandLights,
  isDirty,
  isSharedLayer,
  frameAt,
  labelledSelector,
  layerCaption,
  layerOwners,
  layerSwatches,
  nextParams,
  roomSlice,
  sceneProblems,
  setRoomSlice,
  stableJson,
  variantsFollow,
  wholeTarget,
  withDefaults,
} from "../src/panel/editor/editor-model";
import { draftStale, dropWorkingCopy, openWorkingCopy } from "../src/panel/editor/working-copy";
import { CONFIG, fakeHass } from "./fixtures";

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
    visible_scenes: null,
    default_scene: null,
    reclaim_on_turn_on: true,
    detect_changes: true,
  },
  scenes: [],
  hidden: [],
  swatches: {},
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
  id: "s_00000001",
  name: "Movie",
  icon: null,
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

const room = (area: string, lights: string[], name = area): TargetInfo => ({
  ...ZONE,
  id: `room:${area}`,
  kind: "room",
  name,
  area_id: area,
  rooms: [area],
  lights,
});
const LIVING = room("living", ROOMS["room:living"], "Living room");
const KITCHEN = room("kitchen", ROOMS["room:kitchen"], "Kitchen");
const CLOSET = room("closet", ["light.closet"], "Closet");
const HOUSE = [LIVING, KITCHEN, CLOSET];
const houseLights = (id: string) => HOUSE.find((t) => t.id === id)?.lights;
const EVERY_ROOM = HOUSE.map((t) => t.id);

test("a room edits only its own part of a layer", () => {
  // One layer shared by three rooms: the living room by its lights, the others whole.
  const shared = ["light.ceiling", "light.lamp", "room:kitchen", "room:closet"];
  assert.deepEqual(roomSlice(shared, LIVING, houseLights), ["light.ceiling", "light.lamp"]);
  assert.deepEqual(roomSlice(shared, KITCHEN, houseLights), ["room:kitchen"]);
  assert.deepEqual(roomSlice("all", LIVING, houseLights), ["room:living"]);

  // "All lights" on: the room as a whole instead of its lights.
  assert.deepEqual(setRoomSlice(shared, LIVING, wholeTarget(LIVING, houseLights), EVERY_ROOM), [
    "room:kitchen",
    "room:closet",
    "room:living",
  ]);
  // Off: only this room's entries go.
  assert.deepEqual(setRoomSlice(shared, KITCHEN, [], EVERY_ROOM), ["light.ceiling", "light.lamp", "room:closet"]);
  // The picker swaps this room's lights and keeps the other rooms'.
  assert.deepEqual(setRoomSlice(shared, LIVING, ["light.lamp"], EVERY_ROOM), [
    "room:kitchen",
    "room:closet",
    "light.lamp",
  ]);
  // From "all", the other rooms keep their place by name.
  assert.deepEqual(setRoomSlice("all", CLOSET, [], EVERY_ROOM), ["room:living", "room:kitchen"]);
  assert.deepEqual(setRoomSlice(["room:closet"], LIVING, [], EVERY_ROOM), ["room:closet"]);
});

test("a zone's part of a layer is its rooms and its own lights", () => {
  const zone = { ...ZONE, lights: [...ZONE.lights, "light.porch"] };
  assert.deepEqual(wholeTarget(zone, roomLights), ["room:living", "room:kitchen", "light.porch"]);
  const layer = ["room:kitchen", "room:closet", "light.lamp"];
  assert.deepEqual(roomSlice(layer, zone, roomLights), ["room:kitchen", "light.lamp"]);
  assert.deepEqual(setRoomSlice(layer, zone, ["room:living"], EVERY_ROOM), ["room:closet", "room:living"]);
});

test("layer captions name the other rooms a layer reaches", () => {
  const closetOnly = ["room:closet"];
  assert.equal(layerCaption(closetOnly, LIVING, HOUSE, houseLights), "Closet only");
  assert.equal(layerCaption(closetOnly, CLOSET, HOUSE, houseLights), undefined);
  const spread = ["light.lamp", "room:kitchen", "room:closet"];
  assert.equal(layerCaption(spread, CLOSET, HOUSE, houseLights), "also 2 rooms");
  assert.equal(layerCaption(["room:kitchen", "room:closet"], LIVING, HOUSE, houseLights), "2 rooms");
  assert.equal(layerCaption("all", LIVING, HOUSE, houseLights), undefined);
  assert.equal(layerCaption(["room:kitchen"], ZONE, HOUSE, houseLights), undefined, "a zone's own rooms");
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
  store.setDraft(created.id, created);
  assert.deepEqual(openWorkingCopy(store, created.id)?.baseline, created);
  store.setDraft(created.id, { ...created, name: "Edited" });
  const reloaded = openWorkingCopy(store, created.id);
  assert.equal(reloaded?.restored, true);
  assert.equal(reloaded?.stale, undefined);
  assert.equal(reloaded?.scene.name, "Edited");
  assert.deepEqual(reloaded?.baseline, created);
  dropWorkingCopy(store, created.id);
  assert.equal(items.size, 0);

  // A preset copy keeps its generated id and its baseline.
  const copy = openWorkingCopy(store, "relax")!;
  store.setDraft("relax", { ...copy.scene, brightness: 40 });
  const again = openWorkingCopy(store, "relax");
  assert.equal(again?.scene.id, copy.scene.id);
  assert.deepEqual(again?.baseline, copy.scene);

  // Without its draft, an old baseline is not used.
  store.setDraft("relax", undefined);
  const next = openWorkingCopy(store, "relax")!;
  assert.equal(next.restored, false);
  assert.deepEqual(next.baseline, next.scene);

  // An edited preset opens its stored edit and is measured against it.
  const sunset = openWorkingCopy(store, "savanna_sunset")!;
  assert.equal(sunset.scene.id, "s_11111111");
  store.setDraft("savanna_sunset", { ...sunset.scene, brightness: 40 });
  const edited = openWorkingCopy(store, "savanna_sunset");
  assert.deepEqual(edited?.baseline, toStored(store.scene("s_11111111")!));
  assert.equal(edited?.stale, undefined);
});

test("a draft of a scene deleted or reset elsewhere says saving brings it back", async () => {
  const items = new Map<string, string>();
  const sessionStorage = {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  };
  Object.assign(globalThis, { window: { sessionStorage } });
  const store = await MlStore.load({ hass: fakeHass(), storeChanged: () => undefined });
  for (const sceneId of ["savanna_sunset", "s_22222222"]) {
    const copy = openWorkingCopy(store, sceneId)!;
    store.setDraft(sceneId, { ...copy.scene, brightness: 40 });
  }
  // A new preset edit was never stored, so nothing was removed.
  const fresh = openWorkingCopy(store, "relax")!;
  store.setDraft("relax", { ...fresh.scene, brightness: 40 });

  // Another tab resets Savanna sunset and deletes Movie.
  const config = structuredClone(CONFIG);
  config.scenes = config.scenes
    .filter((s) => !["s_11111111", "s_22222222"].includes(s.id))
    .map((s) => (s.id === "savanna_sunset" ? { ...s, override: null } : s));
  const hass = { ...fakeHass(), callWS: async <T>() => structuredClone(config) as T } as HomeAssistant;
  const later = await MlStore.load({ hass, storeChanged: () => undefined });

  const sunset = openWorkingCopy(later, "savanna_sunset")!;
  assert.equal(sunset.scene.id, "s_11111111");
  assert.equal(sunset.stale, "reset");
  assert.equal(openWorkingCopy(later, "s_22222222")?.stale, "deleted");
  assert.equal(openWorkingCopy(later, "relax")?.stale, undefined);
  dropWorkingCopy(later, "savanna_sunset");
  assert.equal(items.has("magic_lights.draft.savanna_sunset.base"), false);
});

test("one scene has one draft, whichever room opens it", async () => {
  const items = new Map<string, string>();
  const sessionStorage = {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  };
  Object.assign(globalThis, { window: { sessionStorage } });
  const store = await MlStore.load({ hass: fakeHass(), storeChanged: () => undefined });

  const relax = openWorkingCopy(store, "relax")!;
  store.setDraft("relax", { ...relax.scene, brightness: 40 });
  assert.deepEqual([...items.keys()].sort(), ["magic_lights.draft.relax", "magic_lights.draft.relax.base"]);

  // A preset draft begun before the preset was edited elsewhere saves over that edit, and says so.
  const preset = { ...toStored(store.scene("savanna_sunset")!), id: "s_99999999", preset: "savanna_sunset" };
  store.setDraft("savanna_sunset", { ...preset, brightness: 40 });
  store.setDraft("savanna_sunset.base", { scene: preset, stored: false });
  const late = openWorkingCopy(store, "savanna_sunset")!;
  assert.equal(late.scene.id, "s_11111111");
  assert.equal(late.scene.brightness, 40);
  assert.equal(late.stale, "saved");
  assert.deepEqual(late.baseline, toStored(store.scene("s_11111111")!));
});

const adaptiveLayer = (lights: string[], params: Record<string, unknown>) => ({
  lights,
  effect: "adaptive",
  params: { brightness_mode: "tanh", min_brightness: 40, max_brightness: 100, max_kelvin: 5000, ...params },
});

const hell = (layers: StoredScene["layers"]): StoredScene => ({
  id: "s_12186051",
  name: "Hell",
  icon: null,
  preset: null,
  layers,
  others: "off",
  brightness: 100,
  transition: null,
});

test("room-only layers follow shared edits to the values they had in common", () => {
  const before = hell([
    adaptiveLayer(["room:living", "room:kitchen"], {}),
    adaptiveLayer(["light.shelf"], { min_brightness: 12, max_brightness: 30 }),
    adaptiveLayer(["room:closet"], { max_kelvin: 2200 }),
    { lights: ["light.tv"], effect: "white", params: { max_brightness: 100 } },
  ]);
  const edited = structuredClone(before);
  edited.layers[0].params.max_brightness = 80;
  edited.layers[0].params.max_kelvin = 4000;
  const { scene, layers } = variantsFollow(before, edited, (i) => i === 0);
  assert.deepEqual(layers, [1, 2]);
  assert.equal(scene.layers[1].params.max_kelvin, 4000, "the shelf follows the white");
  assert.equal(scene.layers[1].params.max_brightness, 30, "and keeps its own brightness");
  assert.equal(scene.layers[2].params.max_brightness, 80, "the closet follows the brightness");
  assert.equal(scene.layers[2].params.max_kelvin, 2200, "and keeps its own white");
  assert.equal(scene.layers[3].params.max_brightness, 100, "another effect does not follow");
  assert.equal(before.layers[2].params.max_brightness, 100, "inputs stay untouched");
});

test("only later layers follow, and only a params edit", () => {
  const before = hell([adaptiveLayer(["light.a"], {}), adaptiveLayer(["light.b"], {})]);
  const editLater = structuredClone(before);
  editLater.layers[1].params.min_brightness = 10;
  assert.deepEqual(variantsFollow(before, editLater, () => true).layers, [], "an earlier layer keeps its value");
  const added = { ...before, layers: [...before.layers, adaptiveLayer(["light.c"], { min_brightness: 5 })] };
  assert.deepEqual(variantsFollow(before, added, () => true), { scene: added, layers: [] });
  const palette = hell([
    { lights: ["light.a"], effect: "palette", params: { palette: [{ kelvin: 2000 }] } },
    { lights: ["light.b"], effect: "palette", params: { palette: [{ kelvin: 2000 }] } },
  ]);
  const recoloured = structuredClone(palette);
  recoloured.layers[0].params.palette = [{ xy: [0.5, 0.4] }];
  assert.deepEqual(
    variantsFollow(palette, recoloured, (i) => i === 0).scene.layers[1].params.palette,
    [{ xy: [0.5, 0.4] }],
  );
});

test("a room-only layer's edit stays in its room", () => {
  // Main, the living room's sofa lamp, the closet's own layer: all on the same white.
  const before = hell([
    adaptiveLayer(["room:living", "room:kitchen", "room:closet"], {}),
    adaptiveLayer(["light.lamp"], { min_kelvin: 2200 }),
    adaptiveLayer(["room:closet"], { min_kelvin: 2200 }),
  ]);
  const shared = (i: number) => isSharedLayer(before.layers[i].lights, i, HOUSE, houseLights);
  assert.deepEqual([0, 1, 2].map(shared), [true, false, false]);
  const edited = structuredClone(before);
  edited.layers[1].params.min_kelvin = 2700;
  assert.deepEqual(variantsFollow(before, edited, shared), { scene: edited, layers: [] });

  assert.equal(isSharedLayer("all", 3, HOUSE, houseLights), true);
  assert.equal(isSharedLayer(["light.lamp", "room:closet"], 1, HOUSE, houseLights), true, "two rooms share it");
  const twoRooms = hell([...before.layers.slice(0, 1), adaptiveLayer(["light.lamp", "room:closet"], {}), before.layers[2]]);
  const warmer = structuredClone(twoRooms);
  warmer.layers[1].params.max_kelvin = 4000;
  const sharedTwo = (i: number) => isSharedLayer(twoRooms.layers[i].lights, i, HOUSE, houseLights);
  assert.deepEqual(variantsFollow(twoRooms, warmer, sharedTwo).layers, [2], "a part of two rooms leads later ones");
});

test("save needs a change, except for a new scene of your own", () => {
  const scene = hell([adaptiveLayer(["light.a"], {})]);
  assert.equal(isDirty(scene, structuredClone(scene)), false);
  assert.equal(canSave(scene, scene, true), false, "an unchanged stored scene");
  assert.equal(canSave({ ...scene, brightness: 50 }, scene, true), true);
  const preset = { ...scene, preset: "adaptive" };
  assert.equal(canSave(preset, preset, false), false, "an untouched preset");
  assert.equal(canSave(scene, scene, false), true, "a new scene saves as it is");
});

test("a draft's base tells what changed elsewhere since", () => {
  const scene = hell([adaptiveLayer(["light.a"], {})]);
  assert.equal(draftStale({ scene, stored: true }, scene), undefined);
  assert.equal(draftStale({ scene, stored: true }, { ...scene, brightness: 50 }), "saved");
  assert.equal(draftStale({ scene, stored: true }, undefined), "deleted");
  assert.equal(draftStale({ scene: { ...scene, preset: "adaptive" }, stored: true }, undefined), "reset");
  assert.equal(draftStale({ scene, stored: false }, undefined), undefined);
  assert.equal(draftStale({ scene, stored: false }, scene), "saved");
  assert.equal(draftStale({ scene, stored: true }, { ...scene, id: "s_99999999" }), undefined, "ids are not compared");
});
