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
import { addPart, assignLights, lightsInNoPart, partsIn, type RoomContext } from "../src/panel/editor/parts";
import type { EffectInfo, PartLayer, PreviewFrame, TargetInfo } from "../src/shared/types";

const target = (id: TargetInfo["id"], lights: string[], rooms: string[], name: string): TargetInfo => ({
  id,
  kind: id.startsWith("room:") ? "room" : "zone",
  name,
  icon: null,
  area_id: id.startsWith("room:") ? id.slice(5) : null,
  rooms,
  lights,
  options: {
    excluded_lights: [],
    extra_lights: [],
    default_scene: null,
    reclaim_on_turn_on: true,
    detect_changes: true,
  },
  scenes: [],
  select_entity_id: `select.${name.toLowerCase()}_scene`,
});

const LIVING = target("room:living", ["light.ceiling", "light.floor", "light.sofa"], ["living"], "Living");
const BEDROOM = target("room:bedroom", ["light.bed", "light.desk"], ["bedroom"], "Bedroom");
const UPSTAIRS = target("zone:upstairs", [...LIVING.lights, ...BEDROOM.lights, "light.stairs"], ["living", "bedroom"], "Upstairs");
const HOUSE = [LIVING, BEDROOM];
const roomLights = (id: string) => HOUSE.find((t) => t.id === id)?.lights;
const ctx = (t: TargetInfo): RoomContext => ({ target: t, roomLights });

// Hell as the live home has it: the room on Hell, the sofa lamp on a part of its own.
const HELL: PartLayer[] = [
  { lights: "all", part: "p_he11" },
  { lights: ["light.sofa"], part: "p_50fa" },
];

test("the layers that reach a target", () => {
  const stale = [...HELL, { lights: ["light.hall"], part: "p_0001" }];
  assert.deepEqual(partsIn(stale, LIVING, roomLights), [0, 1]);
  assert.deepEqual(partsIn([{ lights: ["room:bedroom"], part: "x" }], UPSTAIRS, roomLights), [0]);
});

test("moving a light: the later part wins, the earlier keeps it", () => {
  const intoSofa = assignLights(HELL, ["light.floor"], 1, ctx(LIVING));
  assert.equal(intoSofa.layers[0].lights, "all");
  assert.deepEqual(intoSofa.layers[1].lights, ["light.floor", "light.sofa"]);
  // Back to the first part: it leaves the later one.
  const back = assignLights(intoSofa.layers, ["light.floor"], 0, ctx(LIVING));
  assert.deepEqual(back.layers[1].lights, ["light.sofa"]);
  // The last light of a part leaves it: the part goes.
  const whole = assignLights(HELL, ["light.sofa"], 0, ctx(LIVING));
  assert.deepEqual(whole.layers, [HELL[0]]);
  assert.deepEqual(whole.moved, [0, -1]);
});

test("a part of every light reads all, so it follows the room", () => {
  const some: PartLayer[] = [{ lights: ["light.ceiling", "light.floor"], part: "p_1" }];
  assert.equal(assignLights(some, ["light.sofa"], 0, ctx(LIVING)).layers[0].lights, "all");
  const out = assignLights([{ lights: "all", part: "p_1" }], ["light.sofa"], null, ctx(LIVING));
  assert.deepEqual(out.layers[0].lights, ["light.ceiling", "light.floor"]);
});

test("a part whose lights all moved to a later part goes", () => {
  const two: PartLayer[] = [
    { lights: ["light.ceiling", "light.floor"], part: "p_a" },
    { lights: ["light.sofa"], part: "p_b" },
  ];
  const first = assignLights(two, ["light.ceiling"], 1, ctx(LIVING));
  assert.equal(first.layers.length, 2, "p_a still plays on the floor lamp");
  const both = assignLights(first.layers, ["light.floor"], 1, ctx(LIVING));
  assert.deepEqual(both.layers, [{ lights: "all", part: "p_b" }]);
  assert.deepEqual(both.moved, [-1, 0]);
  // An "all" layer under a later one that holds every light plays nowhere either.
  const over = assignLights(HELL, ["light.ceiling", "light.floor"], 1, ctx(LIVING));
  assert.deepEqual(over.layers, [{ lights: "all", part: "p_50fa" }]);
});

test("a zone keeps its whole rooms as refs", () => {
  const out = assignLights([{ lights: "all", part: "p_1" }], ["light.stairs"], null, ctx(UPSTAIRS));
  assert.deepEqual(out.layers[0].lights, ["room:living", "room:bedroom"]);
  const desk = assignLights(out.layers, ["light.desk"], null, ctx(UPSTAIRS));
  assert.deepEqual(desk.layers[0].lights, ["room:living", "light.bed"]);
});

test("the last light out keeps the only part, so the scene keeps a part to fill", () => {
  const one: PartLayer[] = [{ lights: ["light.bed"], part: "p_1" }];
  const out = assignLights(one, ["light.bed"], null, ctx(BEDROOM));
  assert.deepEqual(out.layers, [{ lights: [], part: "p_1" }]);
  assert.deepEqual(out.moved, [0]);
});

test("a picked part plays on the picked lights, last so it wins there", () => {
  const { layers, index } = addPart(HELL, "relax", ["light.ceiling"], ctx(LIVING));
  assert.equal(index, 2);
  assert.deepEqual(layers, [...HELL, { lights: ["light.ceiling"], part: "relax" }]);
  assert.deepEqual(addPart([], "relax", LIVING.lights, ctx(LIVING)).layers, [{ lights: "all", part: "relax" }]);
  const every = addPart(HELL, "relax", LIVING.lights, ctx(LIVING));
  assert.deepEqual(every, { layers: [{ lights: "all", part: "relax" }], index: 0 }, "the parts it covers go");
  const some = addPart(HELL, "relax", ["light.sofa"], ctx(LIVING));
  assert.deepEqual(some, { layers: [HELL[0], { lights: ["light.sofa"], part: "relax" }], index: 1 });
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

test("an edited built-in marks the values that differ, defaults filled", () => {
  const builtin = { effect: "adaptive", params: { brightness: 100 } };
  assert.deepEqual([...changedParams(ADAPTIVE, { effect: "adaptive", params: {} }, builtin)], []);
  assert.deepEqual([...changedParams(ADAPTIVE, { effect: "adaptive", params: { brightness: 60 } }, builtin)], [
    "brightness",
  ]);
  assert.equal(changedParams(ADAPTIVE, { effect: "adaptive", params: {} }, null).size, 0, "a part of yours");
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
