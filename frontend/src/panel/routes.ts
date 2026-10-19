// Panel routes under /magic_lights. Target ids ("room:<area>", "zone:<id>"), scene
// and part ids are slugs, so path segments need no escaping.

import { navigate } from "../shared/util";

export const PANEL_PATH = "/magic_lights";

export type HomeTab = "rooms" | "parts";

export type TargetTab = "scenes" | "lights" | "settings";

/** The editor belongs to its scene's room; a new scene starts from a part, or from a blank one. */
export type Page =
  | { name: "home"; tab: HomeTab }
  | { name: "settings" }
  | { name: "part"; partId: string }
  | { name: "target"; targetId: string; tab: TargetTab }
  | { name: "editor"; targetId: string; sceneId: string }
  | { name: "new_scene"; targetId: string; partId?: string }
  | { name: "redirect"; path: string }
  | { name: "not_found" };

const TABS: readonly TargetTab[] = ["scenes", "lights", "settings"];

/** Not a scene id (those are "s_…"), so it never shadows one. */
const NEW = "new";

export const homePath = (tab: HomeTab = "rooms"): string =>
  tab === "rooms" ? PANEL_PATH : `${PANEL_PATH}/parts`;
export const settingsPath = (): string => `${PANEL_PATH}/settings`;
export const targetPath = (targetId: string, tab: TargetTab = "scenes"): string =>
  `${PANEL_PATH}/t/${targetId}${tab === "scenes" ? "" : `/${tab}`}`;
export const editorPath = (targetId: string, sceneId: string): string =>
  `${targetPath(targetId)}/scene/${sceneId}`;
export const partPath = (partId: string): string => `${PANEL_PATH}/part/${partId}`;
/** A fresh draft in the target: the whole room playing `partId`, else a blank part. */
export const newScenePath = (targetId: string, partId?: string): string =>
  `${editorPath(targetId, NEW)}${partId ? `?part=${encodeURIComponent(partId)}` : ""}`;

const parseTarget = (rest: string[], query: URLSearchParams): Page => {
  const [targetId, sub, sceneId] = rest;
  if (!targetId) return { name: "not_found" };
  if (rest.length === 1) return { name: "target", targetId, tab: "scenes" };
  if (rest.length === 2 && TABS.includes(sub as TargetTab)) {
    return { name: "target", targetId, tab: sub as TargetTab };
  }
  if (rest.length === 3 && sub === "scene" && sceneId) {
    if (sceneId !== NEW) return { name: "editor", targetId, sceneId };
    const partId = query.get("part");
    return partId ? { name: "new_scene", targetId, partId } : { name: "new_scene", targetId };
  }
  return { name: "not_found" };
};

/** Old bookmarks lead to the room, else home. */
const parseOldScene = (rest: string[]): Page => {
  const [, sub, targetId] = rest;
  if (rest.length === 3 && sub === "in" && targetId) return { name: "redirect", path: targetPath(targetId) };
  if (rest.length === 1) return { name: "redirect", path: homePath() };
  return { name: "not_found" };
};

/** `search` is the location's query string ("?part=relax"). */
export const parseRoute = (path: string | undefined, search = ""): Page => {
  let parts: string[];
  try {
    parts = (path ?? "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    // A mistyped or truncated deep link.
    return { name: "not_found" };
  }
  const [first, ...rest] = parts;
  if (first === undefined) return { name: "home", tab: "rooms" };
  if (parts.length === 1 && first === "parts") return { name: "home", tab: "parts" };
  if (parts.length === 1 && first === "scenes") return { name: "redirect", path: homePath("parts") };
  if (parts.length === 1 && first === "settings") return { name: "settings" };
  if (parts.length === 2 && first === "part") return { name: "part", partId: rest[0] };
  if (first === "t") return parseTarget(rest, new URLSearchParams(search));
  if (first === "scene" && rest.length) return parseOldScene(rest);
  return { name: "not_found" };
};

/** Where the back arrow leads when there is no in-panel history to pop. */
export const parentPath = (page: Page): string | undefined => {
  switch (page.name) {
    case "home":
      return undefined;
    case "editor":
    case "new_scene":
      return targetPath(page.targetId);
    case "part":
      return homePath("parts");
    default:
      return homePath();
  }
};

/** In-panel navigation marks its history entries so Back can pop them. */
export const go = (path: string): void => navigate(path, { data: { magicLights: true } });

/** Back to where we came from inside the panel, else replace with the parent page (deep links). */
export const goBack = (parent: string): void => {
  if (history.state?.magicLights) history.back();
  else navigate(parent, { replace: true });
};

/** Same page, other view (tabs, a resolved redirect): replaces the entry and keeps its Back behaviour. */
export const replaceWith = (path: string): void =>
  navigate(path, { replace: true, data: history.state });
