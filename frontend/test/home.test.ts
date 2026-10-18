import assert from "node:assert/strict";
import { test } from "node:test";
import { heroText, lightsCount, roomLine } from "../src/panel/room-facts";
import {
  lookParts,
  lookSummary,
  matchesFilter,
  matchesQuery,
  movedBy,
  pickLine,
  sceneEntries,
  sceneSections,
  tileLine,
} from "../src/panel/scene-facts";
import { MlStore } from "../src/panel/store";
import type { ConfigDto, EffectInfo, HaFormSchema, SceneInfo } from "../src/shared/types";
import { attrs, CONFIG, fakeHass } from "./fixtures";

const load = async (edit: (config: ConfigDto) => void = () => undefined) => {
  const config = structuredClone(CONFIG);
  edit(config);
  const hass = { ...fakeHass(), callWS: async () => structuredClone(config) } as unknown as ReturnType<typeof fakeHass>;
  return MlStore.load({ hass, storeChanged: () => undefined });
};

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

const sceneOf = (layers: SceneInfo["layers"]): SceneInfo => ({ ...CONFIG.scenes[3], layers });
const effects = (key: string) => [ADAPTIVE, PALETTE].find((e) => e.key === key);

test("a look is read from the schema: visible levels, whites, colours and parts", () => {
  const curve = { mode: "curve", night: 40, day: 100, warm: 2200, cool: 5000, brightness: 80, every: 9 };
  assert.deepEqual(
    lookParts(sceneOf([{ lights: "all", effect: "sunny", params: curve }, { lights: ["light.a"], effect: "sunny", params: curve }]), effects),
    ["Sunny", "40–100%", "2200–5000 K", "2 parts"],
  );
  const flat = sceneOf([{ lights: "all", effect: "sunny", params: { ...curve, mode: "static" } }]);
  assert.deepEqual(lookParts(flat, effects), ["Sunny", "80%", "2200–5000 K"]);
  assert.equal(tileLine(flat, effects), "Sunny · 80%");
  const dots = sceneOf([{ lights: "all", effect: "dots", params: { colours: [{}, {}, {}], level: 50 } }]);
  assert.deepEqual(lookParts(dots, effects), ["Dots", "50%", "3 colours"]);
  assert.deepEqual(lookParts(sceneOf([{ lights: "all", effect: "gone", params: {} }]), effects), ["Gone"]);
});

test("a scene of mixed parts names what the others do", () => {
  const curve = { mode: "static", warm: 2200, cool: 5000, brightness: 30, every: 9 };
  const dots = { lights: ["light.tv"], effect: "dots", params: { colours: [{}, {}], level: 50 } };
  const mixed = sceneOf([{ lights: "all", effect: "sunny", params: curve }, dots, { ...dots, lights: ["light.b"] }]);
  assert.deepEqual(lookParts(mixed, effects), ["Sunny", "30%", "2200–5000 K"]);
  assert.equal(tileLine(mixed, effects), "Sunny · 30% + Dots");
  assert.equal(lookSummary(mixed, effects), "Sunny · 30% · 2200–5000 K + Dots");
});

test("the Scenes tab lists yours, the presets in use, and folds the rest", async () => {
  const store = await load((c) => {
    c.scenes.push({ ...c.scenes[0], id: "read", name: "Read" });
    c.settings.default_scene = "read";
    c.scenes.push({ ...c.scenes[0], id: "tokyo", name: "Tokyo", category: "color" });
    // Movie is picked in the kitchen, which has none of its lights.
    c.targets[2].options = { ...c.targets[2].options, visible_scenes: ["relax", "s_22222222"] };
  });
  const entries = sceneEntries(store);
  const { own, presets, more } = sceneSections(entries);
  assert.deepEqual(own.map((e) => e.id), ["s_22222222"]);
  assert.deepEqual(presets.map((e) => e.id), ["relax", "savanna_sunset", "read"], "shown, edited or switch-on");
  assert.deepEqual(more.map((e) => e.id), ["tokyo"]);
  const ids = (filter: Parameters<typeof matchesFilter>[1]) =>
    entries.filter((e) => matchesFilter(e, filter)).map((e) => e.id);
  assert.deepEqual(ids("edited"), ["savanna_sunset"]);
  assert.deepEqual(ids("setup"), ["s_22222222"]);
  assert.deepEqual(ids("color"), ["savanna_sunset", "tokyo"]);
  assert.deepEqual(ids("custom"), ["s_22222222"]);
  const movie = own[0];
  assert.deepEqual(
    movie.shown.map((a) => [a.target.id, a.needsSetUp]),
    [
      ["room:kitchen", true],
      ["room:living", false],
    ],
  );
});

test("a search matches part of a name, in any case; a blank one matches all", async () => {
  const entries = sceneEntries(await load());
  const names = (query: string) => entries.filter((e) => matchesQuery(e, query)).map((e) => e.scene.name);
  assert.deepEqual(names("  SUNS "), ["Savanna sunset"]);
  assert.equal(names(" ").length, entries.length);
  assert.deepEqual(names("nothing like it"), []);
});

test("a scene of yours that fits no room is still listed", async () => {
  const store = await load((c) => {
    c.scenes[3].fits = [];
  });
  assert.deepEqual(sceneSections(sceneEntries(store)).own.map((e) => e.id), ["s_22222222"]);
});

test("pick tiles say where a scene runs or what a preset is", async () => {
  const store = await load((c) => {
    c.settings.default_scene = "savanna_sunset";
  });
  const entry = (id: string) => sceneEntries(store).find((e) => e.id === id)!;
  const kitchen = store.target("room:kitchen")!;
  const living = store.target("room:living")!;
  assert.deepEqual(pickLine(store, entry("s_22222222"), kitchen, true), { text: "Needs set-up here", warn: true });
  assert.deepEqual(pickLine(store, entry("s_22222222"), kitchen, false), { text: "Set up for Living room", warn: false });
  assert.deepEqual(pickLine(store, entry("s_22222222"), living, true), { text: "White · here only", warn: false });
  assert.equal(pickLine(store, entry("savanna_sunset"), living, true).text, "Edited · switch-on default");
  assert.equal(pickLine(store, entry("relax"), living, false).text, "White");
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
