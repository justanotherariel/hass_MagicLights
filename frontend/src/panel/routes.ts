// Panel routes under /magic_lights. Target ids ("room:<area>", "zone:<id>")
// and scene ids are slugs, so path segments need no escaping.

import { navigate } from "../shared/util";

export const PANEL_PATH = "/magic_lights";

export type HomeTab = "rooms" | "scenes";

export type TargetTab = "scenes" | "lights" | "settings";

/** The editor is target-free; `targetId` only picks the room it previews and tries in. */
export type Page =
  | { name: "home"; tab: HomeTab }
  | { name: "settings" }
  | { name: "target"; targetId: string; tab: TargetTab }
  | { name: "editor"; sceneId: string; targetId?: string }
  | { name: "new_scene"; targetId?: string }
  | { name: "redirect"; path: string }
  | { name: "not_found" };

const TABS: readonly TargetTab[] = ["scenes", "lights", "settings"];

/** Not a scene id (those are slugs or "s_…"), so it never shadows one. */
const NEW = "new";

export const homePath = (tab: HomeTab = "rooms"): string =>
  tab === "rooms" ? PANEL_PATH : `${PANEL_PATH}/scenes`;
export const settingsPath = (): string => `${PANEL_PATH}/settings`;
export const targetPath = (targetId: string, tab: TargetTab = "scenes"): string =>
  `${PANEL_PATH}/t/${targetId}${tab === "scenes" ? "" : `/${tab}`}`;
export const editorPath = (sceneId: string, targetId?: string): string =>
  `${PANEL_PATH}/scene/${sceneId}${targetId ? `/in/${targetId}` : ""}`;
/** Opens a fresh draft, set up in `targetId` when given. */
export const newScenePath = (targetId?: string): string => editorPath(NEW, targetId);

const parseScene = (rest: string[]): Page => {
  const [sceneId, sub, targetId] = rest;
  if (!sceneId || (rest.length > 1 && (rest.length !== 3 || sub !== "in" || !targetId))) {
    return { name: "not_found" };
  }
  if (sceneId === NEW) return targetId ? { name: "new_scene", targetId } : { name: "new_scene" };
  return targetId ? { name: "editor", sceneId, targetId } : { name: "editor", sceneId };
};

const parseTarget = (rest: string[]): Page => {
  const [targetId, sub, sceneId] = rest;
  if (!targetId) return { name: "not_found" };
  if (rest.length === 1) return { name: "target", targetId, tab: "scenes" };
  if (rest.length === 2 && TABS.includes(sub as TargetTab)) {
    return { name: "target", targetId, tab: sub as TargetTab };
  }
  // Links from before the editor was target-free.
  if (rest.length === 3 && sub === "scene" && sceneId) {
    return { name: "redirect", path: editorPath(sceneId, targetId) };
  }
  return { name: "not_found" };
};

export const parseRoute = (path: string | undefined): Page => {
  let parts: string[];
  try {
    parts = (path ?? "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    // A mistyped or truncated deep link.
    return { name: "not_found" };
  }
  const [first, ...rest] = parts;
  if (first === undefined) return { name: "home", tab: "rooms" };
  if (parts.length === 1 && first === "scenes") return { name: "home", tab: "scenes" };
  if (parts.length === 1 && first === "settings") return { name: "settings" };
  if (first === "t") return parseTarget(rest);
  if (first === "scene") return parseScene(rest);
  return { name: "not_found" };
};

/** Where the back arrow leads when there is no in-panel history to pop. */
export const parentPath = (page: Page): string | undefined => {
  switch (page.name) {
    case "home":
      return undefined;
    case "editor":
    case "new_scene":
      return page.targetId ? targetPath(page.targetId) : homePath("scenes");
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
