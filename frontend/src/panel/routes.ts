// Panel routes under /magic_lights. Target ids ("room:<area>", "zone:<id>")
// and scene ids are slugs, so path segments need no escaping.

import { navigate } from "../shared/util";

export const PANEL_PATH = "/magic_lights";

export type TargetTab = "scenes" | "lights" | "settings";

export type Page =
  | { name: "home" }
  | { name: "settings" }
  | { name: "target"; targetId: string; tab: TargetTab }
  | { name: "editor"; targetId: string; sceneId: string }
  | { name: "not_found" };

const TABS: readonly TargetTab[] = ["scenes", "lights", "settings"];

export const homePath = (): string => PANEL_PATH;
export const settingsPath = (): string => `${PANEL_PATH}/settings`;
export const targetPath = (targetId: string, tab: TargetTab = "scenes"): string =>
  `${PANEL_PATH}/t/${targetId}${tab === "scenes" ? "" : `/${tab}`}`;
export const editorPath = (targetId: string, sceneId: string): string =>
  `${PANEL_PATH}/t/${targetId}/scene/${sceneId}`;

export const parseRoute = (path: string | undefined): Page => {
  let parts: string[];
  try {
    parts = (path ?? "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    // A mistyped or truncated deep link.
    return { name: "not_found" };
  }
  if (parts.length === 0) return { name: "home" };
  if (parts.length === 1 && parts[0] === "settings") return { name: "settings" };
  if (parts[0] !== "t" || !parts[1]) return { name: "not_found" };
  const [, targetId, sub, sceneId] = parts;
  if (parts.length === 2) return { name: "target", targetId, tab: "scenes" };
  if (parts.length === 3 && TABS.includes(sub as TargetTab)) {
    return { name: "target", targetId, tab: sub as TargetTab };
  }
  if (parts.length === 4 && sub === "scene") return { name: "editor", targetId, sceneId };
  return { name: "not_found" };
};

/** In-panel navigation marks its history entries so Back can pop them. */
export const go = (path: string): void => navigate(path, { data: { magicLights: true } });

/** Back to where we came from inside the panel, else replace with the parent page (deep links). */
export const goBack = (parent: string): void => {
  if (history.state?.magicLights) history.back();
  else navigate(parent, { replace: true });
};

/** Same page, other view (tabs): replaces the entry and keeps its Back behaviour. */
export const replaceWith = (path: string): void =>
  navigate(path, { replace: true, data: history.state });
