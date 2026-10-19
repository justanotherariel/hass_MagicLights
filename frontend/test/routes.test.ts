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
  assert.deepEqual(parseRoute("/t/room:kitchen/scene/new"), { name: "new_scene", targetId: "room:kitchen" });
  assert.deepEqual(parseRoute("/t/zone:z/scene/new", "?part=savanna_sunset"), {
    name: "new_scene",
    targetId: "zone:z",
    partId: "savanna_sunset",
  });
  assert.deepEqual(parseRoute("/t/room:kitchen/bogus"), { name: "not_found" });
  assert.deepEqual(parseRoute("/t/room:kitchen/scene"), { name: "not_found" });
  assert.deepEqual(parseRoute("/zones"), { name: "not_found" });
  assert.deepEqual(parseRoute("/t/room%E0%A4%A/settings"), { name: "not_found" }, "malformed escape");
});

test("links from when scenes were shared by rooms lead to the room or home", () => {
  assert.deepEqual(parseRoute("/scene/relax/in/room:kitchen"), { name: "redirect", path: "/magic_lights/t/room:kitchen" });
  assert.deepEqual(parseRoute("/scene/s_0a1b2c3d"), { name: "redirect", path: "/magic_lights" });
  assert.deepEqual(parseRoute("/scenes"), { name: "redirect", path: "/magic_lights" });
  assert.deepEqual(parseRoute("/scene"), { name: "not_found" });
  assert.deepEqual(parseRoute("/scene/relax/at/room:kitchen"), { name: "not_found" });
});

test("paths round-trip", () => {
  assert.deepEqual(parseRoute(strip(homePath())), { name: "home" });
  assert.deepEqual(parseRoute(strip(targetPath("room:a"))), { name: "target", targetId: "room:a", tab: "scenes" });
  assert.deepEqual(parseRoute(strip(targetPath("room:a", "lights"))), { name: "target", targetId: "room:a", tab: "lights" });
  assert.deepEqual(parseRoute(strip(editorPath("zone:z", "s_00000001"))), {
    name: "editor",
    targetId: "zone:z",
    sceneId: "s_00000001",
  });
  assert.deepEqual(parseRoute(strip(newScenePath("room:a"))), { name: "new_scene", targetId: "room:a" });
  const [path, query] = newScenePath("room:a", "relax").split("?");
  assert.deepEqual(parseRoute(strip(path), `?${query}`), { name: "new_scene", targetId: "room:a", partId: "relax" });
  assert.equal(settingsPath(), "/magic_lights/settings");
});

test("back from the editor leads to its room", () => {
  assert.equal(parentPath({ name: "home" }), undefined);
  assert.equal(parentPath({ name: "settings" }), "/magic_lights");
  assert.equal(parentPath({ name: "target", targetId: "room:a", tab: "lights" }), "/magic_lights");
  assert.equal(parentPath({ name: "editor", targetId: "room:a", sceneId: "s_00000001" }), "/magic_lights/t/room:a");
  assert.equal(parentPath({ name: "new_scene", targetId: "room:a" }), "/magic_lights/t/room:a");
  assert.equal(parentPath({ name: "not_found" }), "/magic_lights");
});
