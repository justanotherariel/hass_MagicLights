// Reading a target's status from its select entity and phrasing it.

import type {
  HassEntity,
  HomeAssistant,
  SelectAttributes,
  StatusOwner,
  StatusScene,
  TargetId,
} from "./types";
import { plural } from "./util";

/** HA drops the attributes of unavailable entities. */
export type TargetStatusView =
  | { available: true; attrs: SelectAttributes }
  | { available: false; attrs?: undefined };

/** Undefined when the entity is missing or an available entity that is not a target's select. */
export const statusOf = (
  hass: HomeAssistant | undefined,
  selectEntityId: string | undefined,
): TargetStatusView | undefined => {
  const entity: HassEntity | undefined = selectEntityId ? hass?.states[selectEntityId] : undefined;
  if (!entity) return undefined;
  if (entity.state === "unavailable" || entity.state === "unknown") return { available: false };
  const attrs = entity.attributes as Partial<SelectAttributes>;
  return attrs.status ? { attrs: attrs as SelectAttributes, available: true } : undefined;
};

/** Lookups the phrasing needs; the card resolves them from hass.states, the panel from its config. */
export interface StatusNames {
  target(id: TargetId): string;
  scene(id: string, owner: TargetId): string | undefined;
}

/**
 * "Relax · 2 lights manual", "Savanna sunset · via Downstairs",
 * "Living room: Relax · Kitchen: Tokyo", "Off".
 */
export const statusLine = (view: TargetStatusView | undefined, names: StatusNames): string => {
  if (!view) return "Not set up";
  const a = view.attrs;
  if (!a) return "Unavailable";
  const parts: string[] = [];
  switch (a.status) {
    case "off":
      return "Off";
    case "manual": {
      const on = Object.values(a.lights).filter((status) => status === "on").length;
      return on ? `Manual · ${plural(on, "light")} on` : "Manual";
    }
    case "mixed":
      parts.push(...a.parts.map((part) => `${names.target(part.target_id)}: ${part.scene_name}`));
      break;
    case "scene":
      // Drafts (the editor's Try) report no scene id.
      parts.push(a.scene_id === null ? "Trying a draft" : (a.scene_name ?? a.scene_id));
      if (a.via) parts.push(`via ${names.target(a.via)}`);
      for (const owner of elsewhereOwners(a)) {
        parts.push(`${names.target(owner.target_id)}: ${ownerScene(owner, names)}`);
      }
      break;
  }
  if (a.manual.length) parts.push(`${plural(a.manual.length, "light")} manual`);
  return parts.join(" · ");
};

/** The name of the scene another target runs on some of this target's lights. */
export const ownerScene = (owner: StatusOwner, names: StatusNames): string =>
  owner.scene_id === null ? "Draft" : (names.scene(owner.scene_id, owner.target_id) ?? owner.scene_id);

/** Distinct foreign activations that own some of this target's lights. */
export const elsewhereOwners = (a: SelectAttributes): StatusOwner[] => {
  const seen = new Map<string, StatusOwner>();
  for (const owner of Object.values(a.elsewhere ?? {})) {
    seen.set(`${owner.target_id}|${owner.scene_id}`, owner);
  }
  return [...seen.values()];
};

export const isOn = (view: TargetStatusView | undefined): boolean =>
  !!view?.attrs && view.attrs.status !== "off";

/** The scene running on the target: its own activation's, or the one it shows via another target. */
export const runningScene = (attrs: SelectAttributes | undefined): string | null =>
  attrs?.status === "scene" ? attrs.scene_id : null;

/**
 * The running scene when it stands for the whole target, so tapping it re-applies it here:
 * not when another target's activation covers only part of this one (a zone with one room
 * running a scene), as activating it would take over the other lights.
 */
export const activeScene = (attrs: SelectAttributes | undefined): string | null =>
  attrs?.via && attrs.partial ? null : runningScene(attrs);

/** The running scene's colours move. */
export const isMoving = (attrs: SelectAttributes | undefined): boolean => !!attrs?.motion;

/** Swatches of what runs: the active scene's, or one per part when mixed. */
export const activeSwatches = (
  view: TargetStatusView | undefined,
  swatchesOf: (sceneId: string, owner: TargetId) => readonly string[] | undefined,
): string[] => {
  const a = view?.attrs;
  if (!a) return [];
  if (a.status === "scene" && a.scene_id) {
    const listed = a.scenes.find((s: StatusScene) => s.id === a.scene_id);
    return [...(listed?.swatches ?? swatchesOf(a.scene_id, a.via ?? a.target_id) ?? [])];
  }
  if (a.status === "mixed") {
    return a.parts.flatMap((part) => swatchesOf(part.scene_id, part.target_id)?.slice(0, 2) ?? []);
  }
  return [];
};
