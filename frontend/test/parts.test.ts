import assert from "node:assert/strict";
import { test } from "node:test";
import { partProblems, sceneProblems } from "../src/panel/editor/editor-model";
import { previewSpan, TryQueue } from "../src/panel/editor/live";
import {
  dropPartCopy,
  isPartDirty,
  keepPartCopy,
  openPartCopy,
  partStale,
  startPartCopy,
} from "../src/panel/editor/working-copy";
import { partEntries, partHolders, partSections } from "../src/panel/scene-facts";
import { MlStore, toStoredPart } from "../src/panel/store";
import type { ConfigDto, HomeAssistant, Part, PartInfo } from "../src/shared/types";
import { CONFIG, draftStorage, fakeHass } from "./fixtures";

const builtin = (id: string, name: string, category: PartInfo["category"], extra: Partial<PartInfo> = {}): PartInfo => ({
  id,
  name,
  effect: "white",
  params: { kelvin: 4000, brightness: 100 },
  icon: "mdi:lightbulb",
  category,
  builtin: true,
  edited: false,
  original: { effect: "white", params: { kelvin: 4000, brightness: 100 } },
  swatches: ["#ffffff"],
  dynamic: false,
  used_by: [],
  dormant: [],
  ...extra,
});

const storeOf = async (config: ConfigDto): Promise<MlStore> => {
  const hass = { ...fakeHass(), callWS: async <T>() => structuredClone(config) as T } as HomeAssistant;
  return MlStore.load({ hass, storeChanged: () => undefined });
};

/** The fixture with built-ins no scene uses: plain ones, an edited one and the switch-on default. */
const catalogue = (): ConfigDto => {
  const config = structuredClone(CONFIG);
  const [relax, savanna, ...yours] = config.parts;
  config.parts = [
    relax,
    builtin("bright", "Bright", "white"),
    builtin("concentrate", "Concentrate", "white", { edited: true }),
    savanna,
    builtin("tokyo", "Tokyo", "color"),
    builtin("adaptive", "Adaptive", "adaptive"),
    ...yours,
  ];
  config.settings.default_scene = "adaptive";
  return config;
};

const effectOf = (key: string) => CONFIG.effects.find((e) => e.key === key);

test("the Parts tab: yours, then built-ins in use, edited or the default, the rest folded by kind", async () => {
  const store = await storeOf(catalogue());
  const entries = partEntries(store);
  const byId = Object.fromEntries(entries.map((e) => [e.part.id, e]));
  assert.deepEqual(byId.relax.uses.map((s) => s.id), ["s_00000004", "s_00000001"], "rooms by name");
  assert.equal(byId.adaptive.isDefault, true);
  assert.equal(byId.relax.isDefault, false);

  const { yours, builtins, more } = partSections(entries);
  assert.deepEqual(yours.map((e) => e.part.id), ["p_00000001", "p_00000002"]);
  assert.deepEqual(builtins.map((e) => e.part.id), ["relax", "concentrate", "savanna_sunset", "adaptive"]);
  assert.deepEqual(
    more.map((fold) => [fold.title, fold.entries.map((e) => e.part.id)]),
    [
      ["Whites", ["bright"]],
      ["Colour palettes", ["tokyo"]],
    ],
  );
});

test("a part of yours no scene uses stays in your parts", async () => {
  const config = catalogue();
  config.parts = config.parts.map((p) => (p.id === "p_00000001" ? { ...p, used_by: [] } : p));
  const { yours } = partSections(partEntries(await storeOf(config)));
  const unused = yours.find((e) => e.part.id === "p_00000001");
  assert.deepEqual(unused?.uses, []);
});

test("what keeps a part from being deleted: its scenes and the switch-on default", async () => {
  const store = await storeOf(catalogue());
  assert.deepEqual(partHolders(store, "relax"), ["Kitchen · Relax", "Living room · Relax"]);
  assert.deepEqual(partHolders(store, "adaptive"), ["the switch-on default"]);
  assert.deepEqual(partHolders(store, "bright"), []);
  // A scene of a room that is gone goes with the part.
  assert.deepEqual(partHolders(store, "p_00000002"), ["Living room · Nachtlicht"]);
});

test("a scene with no lights in its room says it needs set-up where it plays a part", async () => {
  const store = await storeOf(CONFIG);
  assert.deepEqual(store.usesOf("p_00000002").map((s) => store.useLabel(s)), ["Living room · Nachtlicht · needs set-up"]);
  assert.deepEqual(store.usesOf("p_00000001").map((s) => store.useLabel(s)), ["Living room · Movie"]);
});

test("a part's problems: a name, not Off or Manual, and an installed effect", () => {
  const part: Part = { id: "p_00000001", name: "Movie", effect: "white", params: {} };
  assert.deepEqual(partProblems(part, effectOf), []);
  assert.deepEqual(partProblems({ ...part, name: "  " }, effectOf), [{ part: part.id, message: "Give the part a name." }]);
  assert.deepEqual(partProblems({ ...part, name: " manual " }, effectOf), [
    { part: part.id, message: '"manual" is reserved for the scene menu.' },
  ]);
  assert.deepEqual(partProblems({ ...part, effect: "gone" }, effectOf), [
    { part: part.id, message: 'The effect "gone" is not installed.' },
  ]);
  // The scene editor flags a reserved part name too: it plays in the menu as a default.
  const draft = {
    scene: { id: "s_00000001", name: "Hell", icon: null, layers: [{ lights: "all" as const, part: part.id }], others: "off" as const, transition: null },
    parts: [{ ...part, name: "Off" }],
  };
  assert.deepEqual(sceneProblems(draft, () => undefined, effectOf), [
    { part: part.id, message: '"Off" is reserved for the scene menu.' },
  ]);
});

test("the part editor's working copy: saved, reloaded, made by Copy, stale", async () => {
  const items = draftStorage();
  const store = await storeOf(CONFIG);
  const movie = toStoredPart(store.part("p_00000001")!);

  const opened = openPartCopy(store, "p_00000001")!;
  assert.deepEqual(opened, { draft: movie, saved: movie, restored: false });
  assert.equal(isPartDirty(opened.draft, opened.saved), false);
  assert(items.has("magic_lights.part-draft.p_00000001"));

  const dimmer = { ...movie, params: { kelvin: 2200, brightness: 10 } };
  keepPartCopy(store, dimmer);
  const reloaded = openPartCopy(store, "p_00000001")!;
  assert.equal(reloaded.restored, true);
  assert.equal(reloaded.stale, undefined);
  assert.deepEqual(reloaded.draft, dimmer);
  assert.equal(isPartDirty(reloaded.draft, reloaded.saved), true);
  dropPartCopy(store, "p_00000001");
  assert.equal(items.size, 0);

  // Copy: a part only the draft knows, always worth saving.
  const copy = store.copyPart(dimmer);
  assert.equal(openPartCopy(store, copy.id), undefined, "no draft, not saved");
  startPartCopy(store, copy);
  const fresh = openPartCopy(store, copy.id)!;
  assert.deepEqual(fresh, { draft: copy, saved: null, restored: true, stale: undefined });
  assert.equal(isPartDirty(copy, null), true);
  assert.equal(openPartCopy(store, "p_0000dead"), undefined);

  // Another tab saves Movie, then deletes it.
  keepPartCopy(store, dimmer);
  const config = structuredClone(CONFIG);
  config.parts = config.parts.map((p) => (p.id === movie.id ? { ...p, name: "Film" } : p));
  assert.equal(openPartCopy(await storeOf(config), movie.id)?.stale, "saved");
  config.parts = config.parts.filter((p) => p.id !== movie.id);
  assert.equal(openPartCopy(await storeOf(config), movie.id)?.stale, "deleted");
});

test("a part's base tells what changed elsewhere since", () => {
  const part: Part = { id: "p_00000001", name: "Movie", effect: "white", params: { kelvin: 2200 } };
  assert.equal(partStale(part, { ...part }), undefined);
  assert.equal(partStale(part, { ...part, name: "Film" }), "saved");
  assert.equal(partStale(part, null), "deleted");
  assert.equal(partStale(null, null), undefined, "made by Copy, not saved yet");
  // Reset restarts the copy at the built-in as it ships.
  assert.equal(partStale(part, part), undefined);
});

test("a preview spans a day from midnight for an adaptive look, else a minute", () => {
  assert.deepEqual(previewSpan(false), { duration: 60, samples: 120 });
  const day = previewSpan(true);
  assert.equal(day.duration, 86400);
  const start = new Date(day.start!);
  assert.deepEqual([start.getHours(), start.getMinutes(), start.getSeconds()], [0, 0, 0]);
});

test("try calls run in order, and one that fails does not stop the next", async () => {
  const queue = new TryQueue();
  const done: string[] = [];
  const slow = queue.run(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
    done.push("try");
  });
  const failed = queue.run(async () => {
    throw new Error("nope");
  });
  const stop = queue.run(async () => void done.push("stop"));
  await slow;
  assert.equal(await failed.then(() => "ok", () => "failed"), "failed");
  await stop;
  assert.deepEqual(done, ["try", "stop"]);
});
