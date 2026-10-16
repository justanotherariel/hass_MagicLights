import assert from "node:assert/strict";
import { test } from "node:test";
import { MlStore, menuId } from "../src/panel/store";
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

test("a preset resolves to its copy only inside the copy's scope", async () => {
  const { store } = await load();
  assert.equal(store.resolveScene("room:living", "savanna_sunset")?.id, "s_11111111");
  assert.equal(store.resolveScene("room:kitchen", "savanna_sunset")?.id, "savanna_sunset");
  assert.equal(store.resolveScene("room:living", "s_22222222")?.name, "Movie");
  assert.equal(store.resolveScene("room:living", "nope"), undefined);
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

test("scenes group by category, a customised copy with its preset", async () => {
  const { store } = await load();
  const living = store.scenesFor("room:living");
  assert.deepEqual(
    store.groupScenes(living).map((g) => [g.title, g.scenes.map(menuId)]),
    [
      ["Yours", ["s_22222222"]],
      ["Whites", ["relax"]],
      ["Colours", ["savanna_sunset"]],
    ],
  );
  assert.deepEqual(store.sceneOptions(living.slice(0, 2), menuId), [
    { value: "__group_white", label: "Whites", disabled: true },
    { value: "relax", label: "Relax" },
    { value: "__group_color", label: "Colours", disabled: true },
    { value: "savanna_sunset", label: "Savanna sunset" },
  ]);
});

test("the editor opens a copy of an uncustomised preset, the stored copy otherwise", async () => {
  const { store } = await load();
  const fresh = store.editableScene("room:kitchen", "savanna_sunset");
  assert.match(fresh!.id, /^s_[0-9a-f]{8}$/);
  assert.equal(fresh!.preset, "savanna_sunset");
  assert.deepEqual(fresh!.scope, ["room:kitchen"]);
  assert.equal(store.editableScene("room:living", "savanna_sunset")!.id, "s_11111111");
  assert.equal(store.editableScene("room:living", "unknown"), undefined);
  assert.equal("builtin" in store.editableScene("room:living", "s_22222222")!, false);
});

test("new scenes get unique names and the blank layer", async () => {
  const { store } = await load();
  const blank = store.newScene("room:living");
  assert.equal(blank.name, "New scene");
  assert.deepEqual(blank.layers, [{ lights: "all", effect: "white", params: { kelvin: 2700, brightness: 100 } }]);
  const copy = store.newScene("room:living", store.scene("s_22222222"));
  assert.equal(copy.name, "Movie copy");
  assert.equal(store.uniqueName("Savanna sunset"), "Savanna sunset 2");
});

test("refresh hands the host a new immutable store", async () => {
  const { store, latest } = await load();
  const next = await store.refresh();
  assert.notEqual(next, store);
  assert.equal(latest(), next);
});

/** The kitchen runs the uncustomised Savanna sunset preset, which has a motion switch. */
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
    store.setSceneVisible("room:living", "relax", false),
    store.setSceneVisible("room:living", "s_22222222", false),
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

test("a failed write does not block the next one", async () => {
  const { config, hass } = slowBackend();
  const store = await MlStore.load({ hass, storeChanged: () => undefined });
  const failed = store.saveTargetOptions("room:nowhere", () => ({ detect_changes: false }));
  const next = store.moveScene("room:living", "s_22222222", 0);
  assert.equal(await failed.then(() => "saved", () => "failed"), "failed");
  await next;
  assert.deepEqual(config.targets.find((t) => t.id === "room:living")!.scenes, [
    "s_22222222",
    "relax",
    "savanna_sunset",
  ]);
});
