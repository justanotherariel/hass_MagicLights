import assert from "node:assert/strict";
import { test } from "node:test";
import {
  editorPath,
  homePath,
  newScenePath,
  parentPath,
  parseRoute,
  settingsPath,
  targetPath,
} from "../src/panel/routes";

const strip = (p: string) => p.replace("/magic_lights", "");

test("routes parse", () => {
  assert.deepEqual(parseRoute(""), { name: "home", tab: "rooms" });
  assert.deepEqual(parseRoute("/"), { name: "home", tab: "rooms" });
  assert.deepEqual(parseRoute("/scenes"), { name: "home", tab: "scenes" });
  assert.deepEqual(parseRoute("/settings"), { name: "settings" });
  assert.deepEqual(parseRoute("/t/room:living_room"), { name: "target", targetId: "room:living_room", tab: "scenes" });
  assert.deepEqual(parseRoute("/t/zone:downstairs/lights"), { name: "target", targetId: "zone:downstairs", tab: "lights" });
  assert.deepEqual(parseRoute("/t/room%3Akitchen/settings"), { name: "target", targetId: "room:kitchen", tab: "settings" });
  assert.deepEqual(parseRoute("/scene/s_0a1b2c3d"), { name: "editor", sceneId: "s_0a1b2c3d" });
  assert.deepEqual(parseRoute("/scene/relax/in/room:kitchen"), {
    name: "editor",
    sceneId: "relax",
    targetId: "room:kitchen",
  });
  assert.deepEqual(parseRoute("/scene/new"), { name: "new_scene" });
  assert.deepEqual(parseRoute("/scene/new/in/zone:z"), { name: "new_scene", targetId: "zone:z" });
  assert.deepEqual(parseRoute("/t/room:kitchen/bogus"), { name: "not_found" });
  assert.deepEqual(parseRoute("/scene"), { name: "not_found" });
  assert.deepEqual(parseRoute("/scene/relax/at/room:kitchen"), { name: "not_found" });
  assert.deepEqual(parseRoute("/scene/relax/in"), { name: "not_found" });
  assert.deepEqual(parseRoute("/zones"), { name: "not_found" });
  assert.deepEqual(parseRoute("/t/room%E0%A4%A/settings"), { name: "not_found" }, "malformed escape");
});

test("the old room-bound editor path redirects to the scene in that room", () => {
  assert.deepEqual(parseRoute("/t/room:kitchen/scene/s_0a1b2c3d"), {
    name: "redirect",
    path: "/magic_lights/scene/s_0a1b2c3d/in/room:kitchen",
  });
});

test("paths round-trip", () => {
  assert.deepEqual(parseRoute(strip(homePath())), { name: "home", tab: "rooms" });
  assert.deepEqual(parseRoute(strip(homePath("scenes"))), { name: "home", tab: "scenes" });
  assert.deepEqual(parseRoute(strip(targetPath("room:a"))), { name: "target", targetId: "room:a", tab: "scenes" });
  assert.deepEqual(parseRoute(strip(targetPath("room:a", "lights"))), { name: "target", targetId: "room:a", tab: "lights" });
  assert.deepEqual(parseRoute(strip(editorPath("relax", "zone:z"))), { name: "editor", sceneId: "relax", targetId: "zone:z" });
  assert.deepEqual(parseRoute(strip(editorPath("relax"))), { name: "editor", sceneId: "relax" });
  assert.deepEqual(parseRoute(strip(newScenePath("room:a"))), { name: "new_scene", targetId: "room:a" });
  assert.equal(settingsPath(), "/magic_lights/settings");
});

test("back leads to the room the editor previews in, else to the scenes list", () => {
  assert.equal(parentPath({ name: "home", tab: "scenes" }), undefined);
  assert.equal(parentPath({ name: "settings" }), "/magic_lights");
  assert.equal(parentPath({ name: "target", targetId: "room:a", tab: "lights" }), "/magic_lights");
  assert.equal(parentPath({ name: "editor", sceneId: "relax", targetId: "room:a" }), "/magic_lights/t/room:a");
  assert.equal(parentPath({ name: "editor", sceneId: "relax" }), "/magic_lights/scenes");
  assert.equal(parentPath({ name: "new_scene" }), "/magic_lights/scenes");
  assert.equal(parentPath({ name: "not_found" }), "/magic_lights");
});
