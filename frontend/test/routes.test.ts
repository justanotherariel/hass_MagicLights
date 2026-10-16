import assert from "node:assert/strict";
import { test } from "node:test";
import { editorPath, parseRoute, settingsPath, targetPath } from "../src/panel/routes";

test("routes parse", () => {
  assert.deepEqual(parseRoute(""), { name: "home" });
  assert.deepEqual(parseRoute("/"), { name: "home" });
  assert.deepEqual(parseRoute("/settings"), { name: "settings" });
  assert.deepEqual(parseRoute("/t/room:living_room"), { name: "target", targetId: "room:living_room", tab: "scenes" });
  assert.deepEqual(parseRoute("/t/zone:downstairs/lights"), { name: "target", targetId: "zone:downstairs", tab: "lights" });
  assert.deepEqual(parseRoute("/t/room%3Akitchen/settings"), { name: "target", targetId: "room:kitchen", tab: "settings" });
  assert.deepEqual(parseRoute("/t/room:kitchen/scene/s_0a1b2c3d"), {
    name: "editor",
    targetId: "room:kitchen",
    sceneId: "s_0a1b2c3d",
  });
  assert.deepEqual(parseRoute("/t/room:kitchen/bogus"), { name: "not_found" });
  assert.deepEqual(parseRoute("/zones"), { name: "not_found" });
  assert.deepEqual(parseRoute("/t/room%E0%A4%A/settings"), { name: "not_found" }, "malformed escape");
});

test("paths round-trip", () => {
  const strip = (p: string) => p.replace("/magic_lights", "");
  assert.deepEqual(parseRoute(strip(targetPath("room:a"))), { name: "target", targetId: "room:a", tab: "scenes" });
  assert.deepEqual(parseRoute(strip(targetPath("room:a", "lights"))), { name: "target", targetId: "room:a", tab: "lights" });
  assert.deepEqual(parseRoute(strip(editorPath("zone:z", "relax"))), { name: "editor", targetId: "zone:z", sceneId: "relax" });
  assert.equal(settingsPath(), "/magic_lights/settings");
});
