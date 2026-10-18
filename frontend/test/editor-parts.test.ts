import assert from "node:assert/strict";
import { test } from "node:test";
import {
  changedParams,
  curvePoints,
  curveRange,
  effectOrder,
} from "../src/panel/editor/editor-model";
import { controlFor } from "../src/panel/editor/form-rules";
import { lookLine } from "../src/panel/scene-facts";
import {
  addPart,
  assignLight,
  lightsInNoPart,
  partLabel,
  partsIn,
  setUpHere,
  switcherParts,
  type RoomContext,
} from "../src/panel/editor/parts";
import type { EffectInfo, Layer, PreviewFrame, TargetInfo } from "../src/shared/types";

const room = (area: string, lights: string[], name: string): TargetInfo => ({
  id: `room:${area}`,
  kind: "room",
  name,
  icon: null,
  area_id: area,
  rooms: [area],
  lights,
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
  select_entity_id: `select.${area}_scene`,
});

const LIVING = room("living", ["light.ceiling", "light.floor", "light.sofa"], "Living room");
const BEDROOM = room("bedroom", ["light.bed"], "Bedroom");
const HOUSE = [LIVING, BEDROOM];
const roomLights = (id: string) => HOUSE.find((t) => t.id === id)?.lights;
const ctx = (target: TargetInfo): RoomContext => ({ target, roomLights, everyRoom: HOUSE.map((t) => t.id) });
const names = { light: (e: string) => e.slice(6), room: (ref: string) => ref.slice(5) };

const layer = (lights: Layer["lights"], params: Record<string, unknown> = {}): Layer => ({
  lights,
  effect: "adaptive",
  params,
});

// Hell as the demo has it: shared by both rooms, the sofa lamp a part of its own.
const HELL = [layer(["light.ceiling", "light.floor", "room:bedroom"]), layer(["light.sofa"], { max: 30 })];

test("parts are named by what they are for", () => {
  assert.equal(partLabel(HELL[0], 0, names), "Main");
  assert.equal(partLabel(HELL[1], 1, names), "sofa");
  assert.equal(partLabel(layer(["room:bedroom"]), 2, names), "bedroom only");
  assert.equal(partLabel(layer(["light.a", "light.b", "light.c"]), 1, names), "a +2");
  assert.equal(partLabel(layer([]), 3, names), "Part 4");
});

test("a room's switcher offers the first part and its own", () => {
  const layers = [layer(["room:bedroom"]), ...HELL.slice(1), layer(["room:bedroom"])];
  assert.deepEqual(partsIn(layers, LIVING, roomLights), [1]);
  assert.deepEqual(switcherParts(layers, LIVING, roomLights), [0, 1]);
  assert.deepEqual(switcherParts(layers, BEDROOM, roomLights), [0, 2]);
});

test("moving a light changes only this room's entries", () => {
  // Into the later part: the first keeps it, the later one wins.
  const intoSofa = assignLight(HELL, "light.floor", 1, ctx(LIVING));
  assert.deepEqual(intoSofa.layers[0].lights, HELL[0].lights);
  assert.deepEqual(intoSofa.layers[1].lights, ["light.floor", "light.sofa"]);
  // Back to Main: it leaves the later part.
  const back = assignLight(intoSofa.layers, "light.floor", 0, ctx(LIVING));
  assert.deepEqual(back.layers[1].lights, ["light.sofa"]);
  // All of the room's lights in one part: the room as a whole.
  const whole = assignLight(HELL, "light.sofa", 0, ctx(LIVING));
  assert.deepEqual(whole.layers[0].lights, ["room:bedroom", "room:living"]);
  assert.equal(whole.layers.length, 1, "the emptied part is dropped");
  assert.deepEqual(whole.moved, [0, -1]);
});

test("a light leaves a whole-room entry without touching other rooms", () => {
  const layers = [layer(["room:living", "room:bedroom"])];
  const { layers: next } = assignLight(layers, "light.sofa", null, ctx(LIVING));
  assert.deepEqual(next[0].lights, ["room:bedroom", "light.ceiling", "light.floor"]);
  const all = assignLight([layer("all")], "light.bed", null, ctx(BEDROOM));
  assert.deepEqual(all.layers[0].lights, ["room:living"]);
});

test("a new part takes the look of its source and the picked lights", () => {
  const { layers, index } = addPart(HELL, 0, ["light.ceiling"], ctx(LIVING));
  assert.equal(index, 1);
  assert.deepEqual(layers[1], { ...HELL[0], lights: ["light.ceiling"] });
  assert.deepEqual(layers[2], HELL[1]);
  // Taking the last light of a later part drops that part.
  const taken = addPart(HELL, 0, ["light.sofa"], ctx(LIVING));
  assert.equal(taken.layers.length, 2);
  assert.deepEqual(taken.layers[1].lights, ["light.sofa"]);
});

test("setting a scene up in a room adds the room to the first part", () => {
  const layers = [layer(["room:bedroom"])];
  assert.deepEqual(setUpHere(layers, ctx(LIVING))[0].lights, ["room:bedroom", "room:living"]);
  const owners = new Map([["light.ceiling", 0]]);
  assert.deepEqual(lightsInNoPart(owners, LIVING), ["light.floor", "light.sofa"]);
});

const ADAPTIVE: EffectInfo = {
  key: "adaptive",
  name: "Adaptive",
  description: "",
  icon: "mdi:theme-light-dark",
  category: "adaptive",
  dynamic: false,
  source: "builtin",
  schema: [
    { name: "brightness_mode", selector: { select: { options: ["static", "tanh"] } } },
    { name: "brightness", selector: { number: { min: 1, max: 100, unit_of_measurement: "%" } } },
  ],
  defaults: { brightness_mode: "static", brightness: 100 },
};

test("an edited preset marks the values that differ, defaults filled", () => {
  const preset = { effect: "adaptive", params: { brightness: 100 } };
  assert.deepEqual([...changedParams(ADAPTIVE, { effect: "adaptive", params: {} }, preset)], []);
  assert.deepEqual([...changedParams(ADAPTIVE, { effect: "adaptive", params: { brightness: 60 } }, preset)], [
    "brightness",
  ]);
  assert.equal(changedParams(ADAPTIVE, { effect: "adaptive", params: {} }, undefined).size, 0);
  const other = changedParams(ADAPTIVE, { effect: "adaptive", params: {} }, { effect: "white", params: {} });
  assert.deepEqual([...other].sort(), ["brightness", "brightness_mode"]);
});

test("effects without settings come last; a look reads as one line", () => {
  const off = { ...ADAPTIVE, key: "off", schema: [] };
  const raw = { ...ADAPTIVE, key: "raw", schema: null };
  assert.deepEqual(
    effectOrder([off, ADAPTIVE, raw]).map((e) => e.key),
    ["adaptive", "raw", "off"],
  );
  assert.equal(lookLine(ADAPTIVE, { effect: "adaptive", params: { brightness: 40 } }), "Adaptive · 40%");
  assert.equal(lookLine(ADAPTIVE, { effect: "adaptive", params: {} }), "Adaptive · 100%", "defaults filled");
  assert.equal(lookLine(undefined, { effect: "gone", params: {} }), "Gone");
});

test("the day curve follows the part's lights, off as zero", () => {
  const frames: PreviewFrame[] = [
    { t: 0, lights: { a: { on: true, brightness: 0.4, rgb: "#ff8800" }, b: { on: false, brightness: 0, rgb: "#000000" } } },
    { t: 43200, lights: { a: { on: true, brightness: 1, rgb: "#ffffff" }, b: { on: true, brightness: 1, rgb: "#ffeedd" } } },
  ];
  assert.deepEqual(curvePoints(frames, ["a"]), [
    { t: 0, level: 0.4, color: "#ff8800" },
    { t: 43200, level: 1, color: "#ffffff" },
  ]);
  assert.deepEqual(
    curvePoints(frames, []).map((p) => p.level),
    [0.2, 1],
  );
  assert.deepEqual(curvePoints(frames, ["gone"]), curvePoints(frames, []), "lights not in the room: all of them");
  assert.deepEqual(curveRange(curvePoints(frames, ["a"])), [40, 100]);
});

test("a colour that may only be white is drawn as a kelvin bar", () => {
  const white = controlFor({
    name: "white",
    selector: { magic_lights_color: { allow_color: false, min_kelvin: 2000, max_kelvin: 6500 } },
  });
  assert.deepEqual(white.kind === "kelvin" && [white.min, white.max, white.wrapped], [2000, 6500, true]);
  assert.equal(controlFor({ name: "c", selector: { magic_lights_color: {} } }).kind, "color");
});
