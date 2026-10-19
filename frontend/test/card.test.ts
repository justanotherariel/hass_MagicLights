import assert from "node:assert/strict";
import { test } from "node:test";
import {
  cardScenes,
  cardSize,
  firstMagicSelect,
  headerLine,
  isMagicSelect,
  lookup,
  parseConfig,
  reclaimable,
} from "../src/card/model";
import { readableTint } from "../src/shared/color";
import { activationOptions, isPlayable, toggleMotion } from "../src/shared/scene-actions";
import { statusLine, statusOf } from "../src/shared/status";
import type { HomeAssistant, SelectAttributes, StatusScene } from "../src/shared/types";
import { attrs } from "./fixtures";

const scene = (id: string, name: string, swatches = ["#ffa757"], dynamic = false): StatusScene => ({
  id,
  name,
  swatches,
  dynamic,
});

const SCENES = [
  scene("relax", "Relax"),
  scene("savanna_sunset", "Savanna sunset", ["#ff5c2f", "#ffbd56"], true),
  scene("s_22222222", "Movie"),
];

const hassWith = (selects: Record<string, Partial<SelectAttributes>>, extra: Partial<HomeAssistant> = {}) =>
  ({
    states: {
      "light.lamp": { entity_id: "light.lamp", state: "on", attributes: {} },
      "select.other": { entity_id: "select.other", state: "a", attributes: { options: ["a"] } },
      ...Object.fromEntries(
        Object.entries(selects).map(([entity_id, a]) => [
          entity_id,
          { entity_id, state: "Relax", attributes: { ...attrs({ scenes: SCENES, ...a }), friendly_name: "X Scene" } },
        ]),
      ),
    },
    entities: {},
    devices: {},
    areas: {},
    themes: { darkMode: false },
    ...extra,
  }) as unknown as HomeAssistant;

test("config defaults and validation", () => {
  assert.deepEqual(parseConfig({ type: "custom:magic-lights-card", entity: "select.living_room_scene" }), {
    type: "custom:magic-lights-card",
    entity: "select.living_room_scene",
    name: undefined,
    show_scenes: true,
    scenes: [],
    columns: 3,
    show_lights: false,
    show_controls: true,
  });
  const full = parseConfig({
    type: "custom:magic-lights-card",
    entity: "select.a_scene",
    name: "Lounge",
    show_scenes: false,
    scenes: ["relax"],
    columns: 4,
    show_lights: true,
    show_controls: false,
  });
  assert.equal(full.name, "Lounge");
  assert.equal(full.columns, 4);
  assert.deepEqual(full.scenes, ["relax"]);
  assert.equal(full.show_lights, true);
  assert.equal(full.show_controls, false);
  for (const bad of [
    {},
    { entity: "" },
    { entity: "light.lamp" },
    { entity: "select.a", columns: 0 },
    { entity: "select.a", columns: 2.5 },
    { entity: "select.a", scenes: "relax" },
    { entity: "select.a", show_lights: "yes" },
  ]) {
    assert.equal(
      (() => {
        try {
          parseConfig(bad);
          return "accepted";
        } catch {
          return "rejected";
        }
      })(),
      "rejected",
      JSON.stringify(bad),
    );
  }
});

test("stub config and entity suggestion find magic_lights selects", () => {
  const hass = hassWith({ "select.living_room_scene": {}, "select.downstairs_scene": { kind: "zone" } });
  assert.equal(firstMagicSelect(hass), "select.downstairs_scene");
  assert.equal(isMagicSelect(hass, "select.other"), false);
  assert.equal(isMagicSelect(hass, "light.lamp"), false);
  const registry = hassWith(
    {},
    { entities: { "select.hall_scene": { entity_id: "select.hall_scene", platform: "magic_lights", labels: [] } } },
  );
  assert.equal(isMagicSelect(registry, "select.hall_scene"), true);
  assert.equal(firstMagicSelect(hassWith({})), undefined);
});

test("scene subset by id or name, in config order; the others are reported", () => {
  const a = attrs({ scenes: SCENES });
  assert.deepEqual(cardScenes(a, []), { scenes: SCENES, missing: [] });
  const picked = cardScenes(a, ["movie", "relax", "honolulu", "disco", "Relax"]);
  assert.deepEqual(picked.scenes.map((s) => s.id), ["s_22222222", "relax"]);
  // Only the room's menu: a scene without lights here, or of another room, is missing.
  assert.deepEqual(picked.missing, ["honolulu", "disco"]);
  const names = lookup(hassWith({ "select.kitchen_scene": { target_id: "room:kitchen" } }));
  assert.deepEqual(names.swatchesOf("savanna_sunset", "room:kitchen"), SCENES[1].swatches);
  assert.equal(names.swatchesOf("honolulu", "room:kitchen"), undefined);
});

test("play only on dynamic scenes with several colours", () => {
  assert.equal(isPlayable(SCENES[1]), true);
  assert.equal(isPlayable(SCENES[0]), false);
  assert.equal(isPlayable(scene("one", "One", ["#ff0000"], true)), false);
  assert.equal(isPlayable(undefined), false);
});

test("activating the active scene again keeps its brightness and motion", () => {
  const [relax, savanna] = [SCENES[0], SCENES[1]];
  const options = (a: Partial<SelectAttributes>, scene: StatusScene) =>
    activationOptions(attrs({ status: "scene", ...a }), scene.id, scene);
  const running = { scene_id: "savanna_sunset", brightness: 60, motion: true };
  assert.deepEqual(options(running, savanna), { brightness: 60, dynamic: true });
  assert.deepEqual(options({ ...running, motion: false }, savanna), { brightness: 60, dynamic: false });
  assert.deepEqual(options(running, relax), {});
  assert.deepEqual(options({ scene_id: "relax", brightness: 40, motion: true }, relax), { brightness: 40 });
  // A candle layer animates while the palette holds still.
  assert.deepEqual(options({ ...running, dynamic: true, motion: false }, savanna), { brightness: 60, dynamic: false });
  // A room covered by a zone's activation takes it over with the same look.
  assert.deepEqual(options({ ...running, via: "zone:downstairs" }, savanna), { brightness: 60, dynamic: true });
  // A zone showing one room's scene would take over its other rooms: a fresh activation.
  assert.deepEqual(options({ ...running, via: "room:living", partial: true }, savanna), {});
  assert.deepEqual(activationOptions(attrs({ status: "off" }), savanna.id, savanna), {});
  assert.deepEqual(activationOptions(undefined, savanna.id, savanna), {});
});

test("▶ holds or plays the running scene in place, and starts another one moving", async () => {
  const calls: [string, Record<string, unknown>, unknown][] = [];
  const hass = {
    callService: async (_domain: string, service: string, data: Record<string, unknown>, target: unknown) => {
      calls.push([service, data, target]);
    },
  } as unknown as HomeAssistant;
  const zone = "select.downstairs_scene";
  const viaRoom = attrs({ status: "scene", scene_id: "savanna_sunset", via: "room:living", partial: true, motion: true });
  await toggleMotion(hass, zone, viaRoom, "savanna_sunset");
  await toggleMotion(hass, zone, { ...viaRoom, motion: false }, "savanna_sunset");
  await toggleMotion(hass, zone, { ...viaRoom, dynamic: true, motion: false }, "savanna_sunset");
  await toggleMotion(hass, zone, viaRoom, "tokyo");
  await toggleMotion(hass, zone, attrs({ status: "off" }), "savanna_sunset");
  const target = { entity_id: zone };
  assert.deepEqual(calls, [
    ["set_dynamic", { dynamic: false }, target],
    ["set_dynamic", { dynamic: true }, target],
    ["set_dynamic", { dynamic: true }, target],
    ["activate_scene", { scene: "tokyo", dynamic: true }, target],
    ["activate_scene", { scene: "savanna_sunset", dynamic: true }, target],
  ]);
});

test("names of other targets come from their selects and devices", () => {
  const hass = hassWith(
    {
      "select.downstairs_scene": {
        target_id: "zone:downstairs",
        status: "scene",
        scene_id: "tokyo",
        scene_name: "Tokyo",
      },
    },
    {
      entities: {
        "select.downstairs_scene": {
          entity_id: "select.downstairs_scene",
          device_id: "d1",
          platform: "magic_lights",
          labels: [],
        },
      },
      devices: { d1: { id: "d1", name: "Downstairs", name_by_user: null, area_id: null } },
    },
  );
  const names = lookup(hass);
  assert.equal(names.target("zone:downstairs"), "Downstairs");
  assert.equal(names.target("room:guest_room"), "Guest room");
  assert.equal(names.scene("tokyo", "zone:downstairs"), "Tokyo");
  assert.equal(names.scene("savanna_sunset", "zone:downstairs"), "Savanna sunset");
  assert.equal(names.scene("relax", "room:nowhere"), undefined);
  assert.deepEqual(names.swatchesOf("savanna_sunset", "zone:downstairs"), ["#ff5c2f", "#ffbd56"]);
});

test("manual lights move from the status line to the Reclaim strip", () => {
  const entity = "select.living_room_scene";
  const view = (a: Partial<SelectAttributes>) => statusOf(hassWith({ [entity]: a }), entity);
  const names = lookup(hassWith({}));
  const owned = view({ status: "scene", scene_id: "relax", scene_name: "Relax", manual: ["light.a", "light.b"] });
  assert.deepEqual(reclaimable(owned), ["light.a", "light.b"]);
  assert.equal(headerLine(owned, names), "Relax");
  // Nothing owns them: they are just on, Reclaim cannot help.
  const loose = view({ status: "manual", lights: { "light.a": "on", "light.b": "off" } });
  assert.deepEqual(reclaimable(loose), []);
  assert.equal(headerLine(loose, names), "Manual · 1 light on");
  assert.equal(headerLine(view({ status: "off" }), names), "Off");
});

test("an unavailable select has no attributes and reads Unavailable", () => {
  const entity = "select.empty_zone_scene";
  const hass = {
    states: { [entity]: { entity_id: entity, state: "unavailable", attributes: { options: [] } } },
  } as unknown as HomeAssistant;
  const view = statusOf(hass, entity);
  assert.deepEqual(view, { available: false });
  assert.equal(statusLine(view, lookup(hass)), "Unavailable");
  assert.deepEqual(reclaimable(view), []);
  assert.equal(statusOf(hassWith({}), "select.other"), undefined, "not a Magic Lights select");
});

test("masonry size follows the shown parts", () => {
  const config = parseConfig({ entity: "select.a" });
  const view = (a: Partial<SelectAttributes>) => ({ attrs: attrs({ scenes: SCENES, ...a }), available: true as const });
  assert.equal(cardSize(config, view({})), 3);
  assert.equal(cardSize(config, view({ status: "scene", scene_id: "relax", manual: ["light.a"] })), 4);
  assert.equal(cardSize({ ...config, columns: 1, show_lights: true }, view({})), 6);
  assert.equal(cardSize({ ...config, scenes: ["relax", "honolulu"] }, view({})), 4, "a row for the warning");
  assert.equal(cardSize({ ...config, show_scenes: false, show_controls: false }, undefined), 1);
  assert.equal(cardSize(config, view({ scenes: [] })), 3, "a row for the empty menu's hint");
});

test("icon tint skips colours that vanish on the theme", () => {
  assert.equal(readableTint(["#fff8f0", "#ff5c2f"], false), "#ff5c2f");
  assert.equal(readableTint(["#2d1ad8", "#e81ec8"], true), "#e81ec8");
  assert.equal(readableTint(["#ffffff"], false), undefined);
  assert.equal(readableTint([], true), undefined);
});
