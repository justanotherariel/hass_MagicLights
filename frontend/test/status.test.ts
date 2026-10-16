import assert from "node:assert/strict";
import { test } from "node:test";
import { MlStore } from "../src/panel/store";
import { activeSwatches, statusLine, statusOf } from "../src/shared/status";
import type { SelectAttributes } from "../src/shared/types";
import { attrs, fakeHass } from "./fixtures";

const ENTITY = "select.living_room_scene";

const line = async (a: Partial<SelectAttributes>, available = true) => {
  const hass = fakeHass({ [ENTITY]: attrs(a) }, available);
  const store = await MlStore.load({ hass, storeChanged: () => undefined });
  const view = statusOf(hass, ENTITY);
  return { text: statusLine(view, store.statusNames), swatches: activeSwatches(view, store.swatchesOf) };
};

test("off, unavailable, not set up", async () => {
  assert.equal((await line({ status: "off" })).text, "Off");
  assert.equal((await line({ status: "scene" }, false)).text, "Unavailable");
  assert.equal(statusLine(undefined, { target: (id) => id, scene: () => undefined }), "Not set up");
});

test("own scene with manual lights", async () => {
  const { text, swatches } = await line({
    status: "scene",
    scene_id: "relax",
    scene_name: "Relax",
    manual: ["light.a", "light.b"],
  });
  assert.equal(text, "Relax · 2 lights manual");
  assert.deepEqual(swatches, ["#ff942b"]);
});

test("via a zone, with the zone's scene swatches", async () => {
  const { text, swatches } = await line({
    status: "scene",
    scene_id: "savanna_sunset",
    scene_name: "Savanna sunset",
    via: "zone:downstairs",
  });
  assert.equal(text, "Savanna sunset · via Downstairs");
  assert.deepEqual(swatches, ["#ff5c2f", "#ffbd56"]);
});

test("lights running elsewhere and mixed targets", async () => {
  const elsewhere = await line({
    status: "scene",
    scene_id: "savanna_sunset",
    scene_name: "Savanna sunset",
    elsewhere: { "light.lamp": { target_id: "room:living", scene_id: "relax" } },
  });
  assert.equal(elsewhere.text, "Savanna sunset · Living room: Relax");
  const draft = await line({
    status: "scene",
    scene_id: "relax",
    scene_name: "Relax",
    elsewhere: { "light.ceiling": { target_id: "room:kitchen", scene_id: null } },
  });
  assert.equal(draft.text, "Relax · Kitchen: Draft");
  const mixed = await line({
    status: "mixed",
    parts: [
      { target_id: "room:living", scene_id: "relax", scene_name: "Relax", lights: ["light.lamp"] },
      {
        target_id: "room:kitchen",
        scene_id: "savanna_sunset",
        scene_name: "Savanna sunset",
        lights: ["light.ceiling"],
      },
    ],
  });
  assert.equal(mixed.text, "Living room: Relax · Kitchen: Savanna sunset");
  assert.deepEqual(mixed.swatches, ["#ff942b", "#ff5c2f", "#ffbd56"]);
});

test("drafts and manual targets", async () => {
  assert.equal((await line({ status: "scene", scene_id: null, scene_name: "Draft" })).text, "Trying a draft");
  assert.equal((await line({ status: "manual", lights: { "light.a": "on", "light.b": "off" } })).text, "Manual · 1 light on");
});
