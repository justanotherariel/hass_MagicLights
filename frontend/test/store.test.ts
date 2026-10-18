import assert from "node:assert/strict";
import { test } from "node:test";
import { MlStore, availabilityIn, menuId, menuIds, withScene } from "../src/panel/store";
import { isPlayable } from "../src/shared/scene-actions";
import type { ConfigDto, HomeAssistant, SelectAttributes, Settings, TargetOptions } from "../src/shared/types";
import { attrs, CONFIG, fakeHass } from "./fixtures";

const load = async () => {
  let latest: MlStore | undefined;
  const host = { hass: fakeHass(), storeChanged: (s: MlStore) => (latest = s) };
  const store = await MlStore.load(host);
  return { store, latest: () => latest };
};

test("targets list rooms by name, then zones", async () => {
  const { store } = await load();
  assert.deepEqual(store.targets.map((t) => t.id), ["room:kitchen", "room:living", "zone:downstairs"]);
});

test("a preset resolves to its edit, the same in every room", async () => {
  const { store } = await load();
  assert.equal(store.resolveScene("savanna_sunset")?.id, "s_11111111");
  assert.equal(store.resolveScene("relax")?.id, "relax");
  assert.equal(store.resolveScene("s_22222222")?.name, "Movie");
  assert.equal(store.resolveScene("nope"), undefined);
  assert.deepEqual(
    store.scenesFor("room:kitchen").map((s) => s.id),
    ["relax", "s_11111111"],
  );
});

test("swatches are those of the layers that reach the target", async () => {
  const { store } = await load();
  assert.deepEqual(store.swatchesOf("savanna_sunset", "room:kitchen"), ["#ff5c2f"]);
  assert.deepEqual(store.swatchesOf("savanna_sunset", "room:living"), ["#4000ff", "#ff5c2f"]);
  // A target without the scene: the scene's own.
  assert.deepEqual(store.swatchesOf("s_22222222", "room:kitchen"), ["#ffa757"]);
});

test("menu ids, origins and visible scenes", async () => {
  const { store } = await load();
  const living = store.scenesFor("room:living");
  assert.deepEqual(living.map(menuId), ["relax", "savanna_sunset", "s_22222222"]);
  assert.equal(store.origin(living[1]).category, "color");
  assert.equal(isPlayable(store.scene("savanna_sunset")), true);
  assert.equal(isPlayable(living[0]), false);
  assert.equal(isPlayable({ ...living[1], dynamic: true, swatches: ["#ff942b"] }), false, "one colour cannot move");
});

test("scenes group by category, an edited preset with its preset", async () => {
  const { store } = await load();
  const living = store.scenesFor("room:living");
  assert.deepEqual(
    store.groupScenes(living).map((g) => [g.title, g.scenes.map(menuId)]),
    [
      ["Yours", ["s_22222222"]],
      ["Whites", ["relax"]],
      ["Colour palettes", ["savanna_sunset"]],
    ],
  );
  assert.deepEqual(store.sceneOptions(living.slice(0, 2), menuId), [
    { value: "__group_white", label: "Whites", disabled: true },
    { value: "relax", label: "Relax" },
    { value: "__group_color", label: "Colour palettes", disabled: true },
    { value: "savanna_sunset", label: "Savanna sunset" },
  ]);
});

test("the editor opens a new edit of a preset, its one stored edit otherwise", async () => {
  const { store } = await load();
  const fresh = store.editableScene("relax")!;
  assert.match(fresh.id, /^s_[0-9a-f]{8}$/);
  assert.equal(fresh.preset, "relax");
  assert.equal("scope" in fresh, false);
  assert.equal(store.editableScene("savanna_sunset")!.id, "s_11111111");
  assert.equal(store.editableScene("unknown"), undefined);
  assert.equal("builtin" in store.editableScene("s_22222222")!, false);
});

test("new scenes are set up where they are made, with a name no scene has", async () => {
  const { store } = await load();
  const blank = store.newScene("room:living");
  assert.equal(blank.name, "New scene");
  assert.deepEqual(blank.layers, [
    { lights: ["room:living"], effect: "white", params: { kelvin: 2700, brightness: 100 } },
  ]);
  assert.deepEqual(store.newScene("zone:downstairs").layers[0].lights, ["room:living", "room:kitchen"]);
  assert.deepEqual(store.newScene().layers[0].lights, [], "made outside a room: lights picked later");
  assert.equal(store.uniqueName("Savanna sunset"), "Savanna sunset 2");
  assert.equal(store.uniqueName("movie"), "movie 2", "a scene can be set up in any room later");
});

test("the editor previews a scene in the first room it fits", async () => {
  const { store } = await load();
  assert.equal(store.previewTarget("s_22222222")?.id, "room:living");
  assert.equal(store.previewTarget("relax")?.id, "room:kitchen");
  assert.equal(store.previewTarget("nope")?.id, "room:kitchen", "nothing fits: the first room");
});

test("menu scenes list every scene once, a preset as its edit", async () => {
  const { store } = await load();
  assert.deepEqual(store.menuScenes().map((s) => s.id), ["relax", "s_11111111", "s_22222222"]);
  assert.equal(store.edited("savanna_sunset"), true);
  assert.equal(store.edited("s_11111111"), true);
  assert.equal(store.edited("relax"), false);
  assert.equal(store.edited("s_22222222"), false);
});

test("availability: shown, fits, needs set-up and the switch-on scene per target", async () => {
  const { store } = await load();
  const living = store.target("room:living")!;
  const kitchen = store.target("room:kitchen")!;
  const movie = store.scene("s_22222222")!;
  // Picked into the kitchen, where Movie has no lights.
  const picked = { ...kitchen, options: { ...kitchen.options, visible_scenes: ["relax", "s_22222222"] } };
  assert.deepEqual(menuIds(picked), ["relax", "s_22222222"]);
  assert.deepEqual(menuIds(kitchen), ["relax", "savanna_sunset"], "a default menu is the scenes it runs");
  const here = availabilityIn(movie, living, null);
  assert.deepEqual([here.shown, here.fits, here.needsSetUp, here.isDefault], [true, true, false, false]);
  const there = availabilityIn(movie, picked, "s_22222222");
  assert.deepEqual([there.shown, there.fits, there.needsSetUp, there.isDefault], [true, false, true, true]);
  const own = { ...living, options: { ...living.options, default_scene: "relax" } };
  assert.equal(availabilityIn(movie, own, "s_22222222").isDefault, false, "a room's own choice wins");
  const zone = store.target("zone:downstairs")!;
  assert.equal(availabilityIn(movie, zone, "s_22222222").isDefault, false, "zones have no switch-on scene");
  assert.deepEqual(
    store.availability("savanna_sunset").map((a) => [a.target.id, a.shown]),
    [
      ["room:kitchen", true],
      ["room:living", true],
      ["zone:downstairs", true],
    ],
  );
});

test("menu edits append and remove ids", () => {
  assert.deepEqual(withScene(["a", "b"], "c", true), ["a", "b", "c"]);
  assert.deepEqual(withScene(["a", "b"], "a", true), ["a", "b"], "kept in place");
  assert.deepEqual(withScene(["a", "b"], "a", false), ["b"]);
});

test("refresh hands the host a new immutable store", async () => {
  const { store, latest } = await load();
  const next = await store.refresh();
  assert.notEqual(next, store);
  assert.equal(latest(), next);
});

/** The kitchen runs the unedited Savanna sunset preset, which has a motion switch. */
const recording = async (kitchen: Partial<SelectAttributes>) => {
  const calls: [string, Record<string, unknown>][] = [];
  const hass = fakeHass({ "select.kitchen_scene": attrs({ status: "scene", target_id: "room:kitchen", ...kitchen }) });
  hass.callService = (async (_domain: string, service: string, data: Record<string, unknown>) => {
    calls.push([service, data]);
  }) as HomeAssistant["callService"];
  return { calls, store: await MlStore.load({ hass, storeChanged: () => undefined }) };
};

test("activating the active scene again keeps its brightness and motion", async () => {
  const { calls, store } = await recording({ scene_id: "savanna_sunset", brightness: 40, motion: true });
  await store.activate("room:kitchen", "savanna_sunset");
  await store.activate("room:kitchen", "relax");
  assert.deepEqual(calls, [
    ["activate_scene", { scene: "savanna_sunset", brightness: 40, dynamic: true }],
    ["activate_scene", { scene: "relax" }],
  ]);
});

test("re-activating a held scene keeps it held", async () => {
  // Activation without `dynamic` falls back to the scene's motion param, which may be "dynamic".
  const { calls, store } = await recording({ scene_id: "savanna_sunset", brightness: 40, motion: false });
  await store.activate("room:kitchen", "savanna_sunset");
  assert.deepEqual(calls, [["activate_scene", { scene: "savanna_sunset", brightness: 40, dynamic: false }]]);
});

test("▶ of the running scene changes its motion in place", async () => {
  const { calls, store } = await recording({ scene_id: "savanna_sunset", via: "zone:downstairs", motion: true });
  await store.toggleMotion("room:kitchen", "savanna_sunset");
  await store.toggleMotion("room:kitchen", "relax");
  await store.setBrightness("room:kitchen", 57);
  assert.deepEqual(calls, [
    ["set_dynamic", { dynamic: false }],
    ["activate_scene", { scene: "relax", dynamic: true }],
    ["adjust_brightness", { brightness: 57 }],
  ]);
});

/** A backend that applies option and settings writes after a delay, like a slow round trip. */
const slowBackend = () => {
  const config: ConfigDto = structuredClone(CONFIG);
  const sent: Record<string, unknown>[] = [];
  const hass = fakeHass();
  hass.callWS = (async (message: { type: string } & Record<string, unknown>) => {
    if (message.type === "magic_lights/config") return structuredClone(config);
    await new Promise((resolve) => setTimeout(resolve, 5));
    if (message.type === "magic_lights/target/options") {
      const options = message.options as Partial<TargetOptions>;
      sent.push(options);
      const target = config.targets.find((t) => t.id === message.target_id)!;
      target.options = { ...target.options, ...options };
      if (options.visible_scenes) target.scenes = options.visible_scenes;
      return { target };
    }
    if (message.type === "magic_lights/menus/save") {
      const menus = message.menus as Record<string, string[] | null>;
      sent.push(menus);
      const targets = Object.entries(menus).map(([id, menu]) => {
        const target = config.targets.find((t) => t.id === id);
        if (!target) throw new Error(`There is no target '${id}'`);
        target.options = { ...target.options, visible_scenes: menu };
        if (menu) target.scenes = menu;
        return target;
      });
      return { targets };
    }
    if (message.type === "magic_lights/settings/save") {
      const settings = message.settings as Partial<Settings>;
      sent.push(settings);
      config.settings = { ...config.settings, ...settings };
      return {};
    }
    throw new Error(`unexpected ${message.type}`);
  }) as HomeAssistant["callWS"];
  return { config, sent, hass };
};

test("quick successive edits build on each other, not on the snapshot they started from", async () => {
  const { config, sent, hass } = slowBackend();
  let latest: MlStore | undefined;
  const store = await MlStore.load({ hass, storeChanged: (s) => (latest = s) });
  // Both taps come from the same rendered snapshot.
  await Promise.all([
    store.setInMenus("relax", ["room:living"], false),
    store.setInMenus("s_22222222", ["room:living"], false),
    store.saveTargetOptions("room:living", (t) => ({ excluded_lights: [...t.options.excluded_lights, "light.a"] })),
    store.saveTargetOptions("room:living", (t) => ({ excluded_lights: [...t.options.excluded_lights, "light.b"] })),
    store.saveSettings((s) => ({ light_buses: { ...s.light_buses, "light.a": "zigbee:1" } })),
    store.saveSettings((s) => ({ light_buses: { ...s.light_buses, "light.b": "zigbee:1" } })),
  ]);
  const living = config.targets.find((t) => t.id === "room:living")!;
  assert.deepEqual(living.scenes, ["savanna_sunset"]);
  assert.deepEqual(living.options.excluded_lights, ["light.a", "light.b"]);
  assert.deepEqual(config.settings.light_buses, { "light.a": "zigbee:1", "light.b": "zigbee:1" });
  assert.equal(sent.length, 6);
  assert.deepEqual(latest?.target("room:living")?.scenes, ["savanna_sunset"]);
});

test("one write sets a scene in several menus, built on the latest menus", async () => {
  const { config, sent, hass } = slowBackend();
  const store = await MlStore.load({ hass, storeChanged: () => undefined });
  await Promise.all([
    store.setInMenus("s_22222222", ["room:kitchen", "room:living"], true),
    store.setInMenus("relax", ["room:kitchen"], false),
  ]);
  assert.deepEqual(sent, [
    {
      "room:kitchen": ["relax", "savanna_sunset", "s_22222222"],
      "room:living": ["relax", "savanna_sunset", "s_22222222"],
    },
    { "room:kitchen": ["savanna_sunset", "s_22222222"] },
  ]);
  assert.deepEqual(config.targets.find((t) => t.id === "room:kitchen")!.options.visible_scenes, [
    "savanna_sunset",
    "s_22222222",
  ]);
});

test("a failed write does not block the next one", async () => {
  const { config, hass } = slowBackend();
  const store = await MlStore.load({ hass, storeChanged: () => undefined });
  const failed = store.setMenu("room:nowhere", []);
  const next = store.setMenu("room:living", ["s_22222222", "relax", "savanna_sunset"]);
  assert.equal(await failed.then(() => "saved", () => "failed"), "failed");
  await next;
  assert.deepEqual(config.targets.find((t) => t.id === "room:living")!.scenes, [
    "s_22222222",
    "relax",
    "savanna_sunset",
  ]);
});
