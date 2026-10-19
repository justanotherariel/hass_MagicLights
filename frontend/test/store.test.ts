import assert from "node:assert/strict";
import { test } from "node:test";
import { groupParts, isWholeRoomOf, MlStore, partOptions, SCENE_ICON, toStoredPart, toStoredScene } from "../src/panel/store";
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

test("a room's scenes are its own, in menu order, lightless ones included", async () => {
  const { store } = await load();
  assert.deepEqual(store.scenesOf("room:living").map((s) => s.name), ["Relax", "Movie", "Nachtlicht"]);
  assert.deepEqual(store.scenesOf("room:kitchen").map((s) => s.id), ["s_00000004", "s_00000005"]);
  assert.deepEqual(store.scenesOf("room:nowhere"), []);
  assert.equal(store.scene("s_00000003")?.fits, false);
});

test("a part's uses are the scenes of existing rooms, rooms by name, then zones", async () => {
  const { store } = await load();
  assert.deepEqual(store.usesOf("relax").map((s) => store.sceneLabel(s)), ["Kitchen · Relax", "Living room · Relax"]);
  assert.deepEqual(store.usesOf("savanna_sunset").map((s) => store.sceneLabel(s)), [
    "Kitchen · Savanna sunset",
    "Downstairs · Savanna sunset",
  ]);
  assert.deepEqual(store.usesOf("p_00000002").map((s) => s.id), ["s_00000003"], "not the dormant one");
  assert.deepEqual(store.dormantUsesOf("p_00000002").map((s) => s.id), ["s_0000000a"]);
  assert.deepEqual(store.dormantUsesOf("relax"), []);
  assert.equal(store.sceneLabel(store.scene("s_0000000a")!), "Unknown room · Nachtlicht");
  assert.deepEqual(store.usesOf("nope"), []);
});

test("a status id names the owner's scene, else a part run on its own", async () => {
  const { store } = await load();
  assert.equal(store.running("room:kitchen", "s_00000005")?.name, "Savanna sunset");
  assert.equal(store.running("room:living", "relax")?.name, "Relax", "a part run as itself");
  assert.equal(store.running("room:living", "s_00000005"), undefined, "another room's scene id");
  assert.equal(store.statusNames.scene("s_00000006", "zone:downstairs"), "Savanna sunset");
  assert.deepEqual(store.swatchesOf("s_00000005", "room:kitchen"), ["#ff5c2f"], "where its layers reach");
  assert.deepEqual(store.swatchesOf("savanna_sunset", "room:kitchen"), ["#4000ff", "#ff5c2f"]);
  assert.deepEqual(store.looksOf(store.scene("s_00000002")!).map((p) => p.id), ["p_00000001"]);
  assert.equal(isPlayable(store.running("room:kitchen", "s_00000005")), false, "one colour here cannot move");
  assert.equal(isPlayable(store.part("savanna_sunset")), true);
});

test("parts group as yours, then the built-ins by kind", async () => {
  const { store } = await load();
  assert.deepEqual(
    groupParts(store.parts()).map((g) => [g.title, g.parts.map((p) => p.id)]),
    [
      ["Your parts", ["p_00000001", "p_00000002"]],
      ["Whites", ["relax"]],
      ["Colour palettes", ["savanna_sunset"]],
    ],
  );
  assert.deepEqual(partOptions(store.parts().slice(0, 2)), [
    { value: "__group_white", label: "Whites", disabled: true },
    { value: "relax", label: "Relax" },
    { value: "__group_color", label: "Colour palettes", disabled: true },
    { value: "savanna_sunset", label: "Savanna sunset" },
  ]);
});

test("a whole-room scene is one layer of all lights, as a part runs by itself", async () => {
  const { store } = await load();
  assert.equal(store.wholeRoomScene("room:living", "relax")?.id, "s_00000001");
  assert.equal(store.wholeRoomScene("room:living", "p_00000002"), undefined, "some lights only");
  assert.equal(store.wholeRoomScene("room:kitchen", "p_00000001"), undefined);
  const relax = store.scene("s_00000001")!;
  assert.equal(isWholeRoomOf({ ...relax, others: "keep" }, "relax"), false);
  assert.equal(isWholeRoomOf({ ...relax, transition: 2 }, "relax"), false);
});

test("scene names are free in their room and never Off or Manual", async () => {
  const { store } = await load();
  assert.equal(store.uniqueSceneName("room:living", "Relax"), "Relax 2");
  assert.equal(store.uniqueSceneName("room:living", "Savanna sunset"), "Savanna sunset", "the kitchen's is elsewhere");
  assert.equal(store.uniqueSceneName("room:living", "relax", ["Relax 2"]), "relax 3");
  assert.equal(store.uniqueSceneName("room:kitchen", "Off"), "Off 2");
  assert.equal(store.uniqueSceneName("room:kitchen", "manual"), "manual 2");
  assert.equal(store.uniquePartName("movie"), "movie 2", "part names are unique over all parts");
  assert.equal(store.uniquePartName("Relax copy", ["Relax copy"]), "Relax copy 2");
});

test("a new scene plays one part on the whole room, or a blank part made with it", async () => {
  const { store } = await load();
  const { scene, parts } = store.newScene("room:living", "savanna_sunset");
  assert.match(scene.id, /^s_[0-9a-f]{8}$/);
  assert.deepEqual(
    { ...scene, id: "" },
    {
      id: "",
      name: "Savanna sunset",
      icon: "mdi:weather-sunset",
      layers: [{ lights: "all", part: "savanna_sunset" }],
      others: "off",
      transition: null,
    },
  );
  assert.deepEqual(parts, []);
  assert.equal(store.newScene("room:living", "relax").scene.name, "Relax 2");
  assert.equal(store.newScene("room:living", "p_00000001").scene.icon, SCENE_ICON, "your parts carry no icon");
  const blank = store.newScene("room:kitchen", null);
  assert.equal(blank.scene.name, "New scene");
  assert.equal(blank.scene.icon, SCENE_ICON);
  assert.equal(blank.parts.length, 1);
  assert.match(blank.parts[0].id, /^p_[0-9a-f]{8}$/);
  assert.deepEqual(
    { ...blank.parts[0], id: "" },
    { id: "", name: "New part", effect: "white", params: { kelvin: 2700, brightness: 100 } },
  );
  assert.deepEqual(blank.scene.layers, [{ lights: "all", part: blank.parts[0].id }]);
});

test("Copy is the part's look under a new id and a free name", async () => {
  const { store } = await load();
  const relax = store.part("relax")!;
  const copy = store.copyPart({ ...relax, params: { kelvin: 2200 } });
  assert.match(copy.id, /^p_[0-9a-f]{8}$/);
  assert.deepEqual({ ...copy, id: "" }, { id: "", name: "Relax copy", effect: "white", params: { kelvin: 2200 } });
  assert.equal(store.copyPart(relax, ["Relax copy"]).name, "Relax copy 2");
});

test("stored shapes leave the DTO fields out", async () => {
  const { store } = await load();
  assert.deepEqual(Object.keys(toStoredScene(store.scene("s_00000002")!)).sort(), [
    "icon",
    "id",
    "layers",
    "name",
    "others",
    "transition",
  ]);
  assert.deepEqual(toStoredPart(store.part("relax")!), {
    id: "relax",
    name: "Relax",
    effect: "white",
    params: { kelvin: 2700, brightness: 100 },
  });
});

test("refresh hands the host a new immutable store", async () => {
  const { store, latest } = await load();
  const next = await store.refresh();
  assert.notEqual(next, store);
  assert.equal(latest(), next);
});

/** The kitchen runs its Savanna sunset scene. */
const recording = async (kitchen: Partial<SelectAttributes>) => {
  const calls: [string, Record<string, unknown>][] = [];
  const hass = fakeHass({ "select.kitchen_scene": attrs({ status: "scene", target_id: "room:kitchen", ...kitchen }) });
  hass.callService = (async (_domain: string, service: string, data: Record<string, unknown>) => {
    calls.push([service, data]);
  }) as HomeAssistant["callService"];
  return { calls, store: await MlStore.load({ hass, storeChanged: () => undefined }) };
};

test("activating the active scene again keeps its brightness", async () => {
  const { calls, store } = await recording({ scene_id: "s_00000005", brightness: 40, motion: true });
  await store.activate("room:kitchen", "s_00000005");
  await store.activate("room:kitchen", "s_00000004");
  assert.deepEqual(calls, [
    // One colour in the kitchen: nothing to keep moving.
    ["activate_scene", { scene: "s_00000005", brightness: 40 }],
    ["activate_scene", { scene: "s_00000004" }],
  ]);
});

test("re-activating a held part keeps it held", async () => {
  const { calls, store } = await recording({ scene_id: "savanna_sunset", brightness: 40, motion: false });
  await store.activate("room:kitchen", "savanna_sunset");
  assert.deepEqual(calls, [["activate_scene", { scene: "savanna_sunset", brightness: 40, dynamic: false }]]);
});

test("▶ of the running scene changes its motion in place", async () => {
  const { calls, store } = await recording({ scene_id: "s_00000006", via: "zone:downstairs", motion: true });
  await store.toggleMotion("room:kitchen", "s_00000006");
  await store.toggleMotion("room:kitchen", "s_00000004");
  await store.setBrightness("room:kitchen", 57);
  assert.deepEqual(calls, [
    ["set_dynamic", { dynamic: false }],
    ["activate_scene", { scene: "s_00000004", dynamic: true }],
    ["adjust_brightness", { brightness: 57 }],
  ]);
});

/** A backend that applies writes after a delay, like a slow round trip. */
const slowBackend = () => {
  const config: ConfigDto = structuredClone(CONFIG);
  const sent: [string, Record<string, unknown>][] = [];
  const hass = fakeHass();
  hass.callWS = (async (message: { type: string } & Record<string, unknown>) => {
    const { type, ...payload } = message;
    if (type === "magic_lights/config") return structuredClone(config);
    await new Promise((resolve) => setTimeout(resolve, 5));
    sent.push([type.replace("magic_lights/", ""), payload]);
    const target = config.targets.find((t) => t.id === message.target_id);
    switch (type) {
      case "magic_lights/target/options":
        if (!target) throw new Error(`There is no target '${String(message.target_id)}'`);
        target.options = { ...target.options, ...(message.options as Partial<TargetOptions>) };
        return { target };
      case "magic_lights/scenes/order":
        if (!target) throw new Error(`There is no target '${String(message.target_id)}'`);
        target.scenes = message.scene_ids as string[];
        return { target };
      case "magic_lights/settings/save":
        config.settings = { ...config.settings, ...(message.settings as Partial<Settings>) };
        return {};
      case "magic_lights/library/save":
        for (const { target: owner, scene } of message.scenes as { target: string; scene: { id: string } }[]) {
          config.targets.find((t) => t.id === owner)!.scenes.push(scene.id);
          config.scenes.push({ ...config.scenes[1], ...scene, target: owner as "room:living" });
        }
        return { parts: [], scenes: [] };
    }
    throw new Error(`unexpected ${type}`);
  }) as HomeAssistant["callWS"];
  return { config, sent, hass };
};

test("quick successive edits build on each other, not on the snapshot they started from", async () => {
  const { config, sent, hass } = slowBackend();
  let latest: MlStore | undefined;
  const store = await MlStore.load({ hass, storeChanged: (s) => (latest = s) });
  // Every tap comes from the same rendered snapshot.
  await Promise.all([
    store.saveTargetOptions("room:living", (t) => ({ excluded_lights: [...t.options.excluded_lights, "light.a"] })),
    store.saveTargetOptions("room:living", (t) => ({ excluded_lights: [...t.options.excluded_lights, "light.b"] })),
    store.saveSettings((s) => ({ light_buses: { ...s.light_buses, "light.a": "zigbee:1" } })),
    store.saveSettings((s) => ({ light_buses: { ...s.light_buses, "light.b": "zigbee:1" } })),
  ]);
  const living = config.targets.find((t) => t.id === "room:living")!;
  assert.deepEqual(living.options.excluded_lights, ["light.a", "light.b"]);
  assert.deepEqual(config.settings.light_buses, { "light.a": "zigbee:1", "light.b": "zigbee:1" });
  assert.equal(sent.length, 4);
  assert.deepEqual(latest?.target("room:living")?.options.excluded_lights, ["light.a", "light.b"]);
});

test("New scene adds one whole-room scene per picked part in one save, names free in the room", async () => {
  const { sent, hass } = slowBackend();
  const store = await MlStore.load({ hass, storeChanged: () => undefined });
  await Promise.all([
    store.addScenes("room:living", ["relax", "savanna_sunset"]),
    store.addScenes("room:living", ["relax"]),
  ]);
  const scenes = sent.map(([command, payload]) => {
    assert.equal(command, "library/save");
    assert.equal("parts" in payload, false, "parts stay as they are");
    return (payload.scenes as { target: string; scene: { name: string; layers: unknown } }[]).map(
      ({ target, scene }) => [target, scene.name, scene.layers],
    );
  });
  assert.deepEqual(scenes, [
    [
      ["room:living", "Relax 2", [{ lights: "all", part: "relax" }]],
      ["room:living", "Savanna sunset", [{ lights: "all", part: "savanna_sunset" }]],
    ],
    // Built on the first save's result.
    [["room:living", "Relax 3", [{ lights: "all", part: "relax" }]]],
  ]);
});

test("Arrange writes the room's order; a failed write does not block the next one", async () => {
  const { config, sent, hass } = slowBackend();
  const store = await MlStore.load({ hass, storeChanged: () => undefined });
  const failed = store.orderScenes("room:nowhere", []);
  const next = store.orderScenes("room:living", ["s_00000003", "s_00000001", "s_00000002"]);
  assert.equal(await failed.then(() => "saved", () => "failed"), "failed");
  await next;
  assert.deepEqual(sent.at(-1), [
    "scenes/order",
    { target_id: "room:living", scene_ids: ["s_00000003", "s_00000001", "s_00000002"] },
  ]);
  assert.deepEqual(config.targets.find((t) => t.id === "room:living")!.scenes, [
    "s_00000003",
    "s_00000001",
    "s_00000002",
  ]);
});
