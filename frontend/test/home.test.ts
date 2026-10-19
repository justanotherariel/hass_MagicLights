import assert from "node:assert/strict";
import { test } from "node:test";
import { heroText, lightsCount, roomLine } from "../src/panel/room-facts";
import { layerLook, matchesQuery, movedBy, tileLine } from "../src/panel/scene-facts";
import type { EffectInfo, HaFormSchema, Look, StoredScene } from "../src/shared/types";
import { attrs } from "./fixtures";

const pct = (name: string, extra: Partial<HaFormSchema> = {}): HaFormSchema => ({
  name,
  selector: { number: { min: 1, max: 100, unit_of_measurement: "%" } },
  ...extra,
});
const kelvin = (name: string, extra: Partial<HaFormSchema> = {}): HaFormSchema => ({
  name,
  selector: { color_temp: { unit: "kelvin", min: 2000, max: 6500 } },
  ...extra,
});

const ADAPTIVE: EffectInfo = {
  key: "sunny",
  name: "Sunny",
  description: "",
  icon: "",
  category: "adaptive",
  dynamic: false,
  source: "test",
  defaults: {},
  schema: [
    { name: "mode", selector: { select: { options: ["static", "curve"] } } },
    pct("brightness", { visible: { field: "mode", value: "static" } }),
    pct("night", { visible: { field: "mode", value: "curve" } }),
    pct("day", { visible: { field: "mode", value: "curve" } }),
    kelvin("warm", { pair: { max: "cool" } }),
    kelvin("cool"),
    { name: "every", selector: { number: { min: 1, max: 60, unit_of_measurement: "s" } }, advanced: true },
  ],
};

const PALETTE: EffectInfo = {
  ...ADAPTIVE,
  key: "dots",
  name: "Dots",
  schema: [{ name: "colours", selector: { magic_lights_palette: {} } }, pct("level")],
};

const effects = (key: string) => [ADAPTIVE, PALETTE].find((e) => e.key === key);

/** A scene whose layers play the given looks, one part each. */
const sceneOf = (...looks: Look[]): [StoredScene, (id: string) => Look | undefined] => [
  {
    id: "s_00000001",
    name: "Hell",
    icon: null,
    layers: looks.map((_, i) => ({ lights: i ? [`light.${i}`] : "all", part: `p_${i}` })),
    others: "off",
    transition: null,
  },
  (id) => looks[Number(id.slice(2))],
];

test("a look is read from the schema: visible levels, whites and colours", () => {
  const curve = { mode: "curve", night: 40, day: 100, warm: 2200, cool: 5000, brightness: 80, every: 9 };
  assert.deepEqual(layerLook(ADAPTIVE, { effect: "sunny", params: curve }), ["Sunny", "40–100%", "2200–5000 K"]);
  const flat = { effect: "sunny", params: { ...curve, mode: "static" } };
  assert.deepEqual(layerLook(ADAPTIVE, flat), ["Sunny", "80%", "2200–5000 K"]);
  assert.deepEqual(layerLook(PALETTE, { effect: "dots", params: { colours: [{}, {}, {}], level: 50 } }), [
    "Dots",
    "50%",
    "3 colours",
  ]);
  assert.deepEqual(layerLook(undefined, { effect: "gone", params: {} }), ["Gone"]);
});

test("a tile names the first part's look and what the others do", () => {
  const sunny = (params: Record<string, unknown>) => ({
    effect: "sunny",
    params: { mode: "static", warm: 2200, cool: 5000, every: 9, ...params },
  });
  assert.equal(tileLine(...sceneOf(sunny({ brightness: 80 })), effects), "Sunny · 80%");
  const dots = { effect: "dots", params: { colours: [{}, {}], level: 50 } };
  assert.equal(tileLine(...sceneOf(sunny({ brightness: 30 }), dots, dots), effects), "Sunny · 30% + Dots");
  // Parts of one kind: how many there are, after the first fact.
  assert.equal(tileLine(...sceneOf({ effect: "gone", params: {} }, { effect: "gone", params: {} }), effects), "Gone · 2 parts");
  const [missing] = sceneOf(sunny({}));
  assert.equal(tileLine(missing, () => undefined, effects), "", "its part is gone");
});

test("a search matches part of a name, in any case; a blank one matches all", () => {
  assert.equal(matchesQuery("Savanna sunset", "  SUNS "), true);
  assert.equal(matchesQuery("Relax", " "), true);
  assert.equal(matchesQuery("Relax", "nothing like it"), false);
});

test("arrange moves a scene and keeps it inside the menu", () => {
  assert.deepEqual(movedBy(["a", "b", "c"], "a", 1), ["b", "a", "c"]);
  assert.deepEqual(movedBy(["a", "b", "c"], "c", -5), ["c", "a", "b"]);
  assert.deepEqual(movedBy(["a", "b", "c"], "c", 1), ["a", "b", "c"]);
  assert.deepEqual(movedBy(["a"], "x", 1), ["a"]);
});

const names = { target: (id: string) => (id === "zone:downstairs" ? "Downstairs" : id), scene: () => "Relax" };

test("a room's status line carries the live facts", () => {
  const view = (a: Parameters<typeof attrs>[0]) => ({ available: true as const, attrs: attrs(a) });
  assert.equal(roomLine(view({ status: "off" }), names, 6), "Off · 6 lights");
  assert.equal(
    roomLine(view({ status: "scene", scene_id: "hell", scene_name: "Hell", brightness: 80 }), names, 4),
    "Hell · 80% · 4 lights",
  );
  assert.equal(
    roomLine(view({ status: "scene", scene_id: "dim", scene_name: "Gedimmt", brightness: 30, manual: ["light.a"] }), names, 2),
    "Gedimmt · 30% · 1 light manual",
  );
  assert.equal(
    roomLine(view({ status: "scene", scene_id: "x", scene_name: "Hell", via: "zone:downstairs" }), names, 2),
    "Hell · via Downstairs · 2 lights",
  );
  assert.equal(roomLine(view({ status: "scene", scene_id: null }), names, 2), "Trying a draft · 2 lights");
  assert.equal(roomLine(undefined, names, 2), "Not set up");
  assert.equal(
    lightsCount(attrs({ partial: true, lights: { "light.a": "scene", "light.b": "off" } }), 2),
    "1 of 2 lights",
  );
});

test("the hero names what runs and what is manual, for every status", () => {
  const ctx = { names, lights: 4, lightName: (id: string) => id.replace("light.", ""), time: () => "18:42" };
  const view = (a: Parameters<typeof attrs>[0]) => ({ available: true as const, attrs: attrs(a) });
  assert.deepEqual(
    heroText(view({ status: "scene", scene_id: "hell", scene_name: "Hell", since: "2026-10-18T18:42:00" }), {
      ...ctx,
      kelvinNow: 3100,
    }),
    { title: "Hell", facts: ["Running since 18:42", "now 3100 K", "4 lights, all following"] },
  );
  assert.deepEqual(
    heroText(view({ status: "scene", scene_id: "s", scene_name: "Sunset", motion: true, since: "x", manual: ["light.strip"] }), ctx),
    { title: "Sunset", facts: ["Playing since 18:42", "4 lights", "strip manual"] },
  );
  assert.equal(heroText(view({ status: "scene", scene_id: null }), ctx).title, "Trying a draft");
  assert.deepEqual(heroText(view({ status: "off" }), ctx), { title: "Off", facts: ["4 lights"] });
  assert.deepEqual(heroText(view({ status: "manual", lights: { "light.a": "on" }, manual: ["light.a", "light.b"] }), ctx), {
    title: "Manual",
    facts: ["1 of 4 lights on", "2 lights manual"],
  });
  assert.deepEqual(
    heroText(
      view({
        status: "mixed",
        parts: [{ target_id: "zone:downstairs", scene_id: "r", scene_name: "Relax", lights: ["light.a"] }],
      }),
      ctx,
    ).facts,
    ["Downstairs: Relax", "4 lights"],
  );
  assert.deepEqual(heroText({ available: false }, ctx), { title: "Unavailable", facts: [] });
});
