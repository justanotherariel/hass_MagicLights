// The card's config and what it derives from hass.states; no DOM, so it runs under node:test.

import { DOMAIN } from "../shared/api";
import { statusLine, type StatusNames, type TargetStatusView } from "../shared/status";
import type { HassEntity, HomeAssistant, SelectAttributes, StatusScene, TargetId } from "../shared/types";
import { humanise } from "../shared/util";

export const CARD_TYPE = "magic-lights-card";

export interface CardConfig {
  type: string;
  entity: string;
  name?: string;
  show_scenes: boolean;
  /** Ids or names of the target's scenes, in display order; empty = the whole menu. */
  scenes: string[];
  columns: number;
  show_lights: boolean;
  show_controls: boolean;
}

export const MAX_COLUMNS = 6;

const bool = (value: unknown, fallback: boolean, key: string): boolean => {
  if (value === undefined) return fallback;
  if (typeof value !== "boolean") throw new Error(`${key} must be true or false`);
  return value;
};

/** Validates a raw card config; the thrown message is what HA's error card shows. */
export const parseConfig = (raw: Record<string, unknown>): CardConfig => {
  const { entity, name, scenes, columns } = raw;
  if (!entity) throw new Error("Specify an entity");
  if (typeof entity !== "string" || !entity.startsWith("select.")) {
    throw new Error("entity must be a Magic Lights select (select.*)");
  }
  if (name !== undefined && typeof name !== "string") throw new Error("name must be text");
  if (scenes !== undefined && !(Array.isArray(scenes) && scenes.every((s) => typeof s === "string"))) {
    throw new Error("scenes must be a list of scene ids or names");
  }
  if (
    columns !== undefined &&
    !(typeof columns === "number" && Number.isInteger(columns) && columns >= 1 && columns <= MAX_COLUMNS)
  ) {
    throw new Error(`columns must be a whole number from 1 to ${MAX_COLUMNS}`);
  }
  return {
    type: String(raw.type ?? `custom:${CARD_TYPE}`),
    entity,
    name: name || undefined,
    show_scenes: bool(raw.show_scenes, true, "show_scenes"),
    scenes: (scenes as string[] | undefined) ?? [],
    columns: (columns as number | undefined) ?? 3,
    show_lights: bool(raw.show_lights, false, "show_lights"),
    show_controls: bool(raw.show_controls, true, "show_controls"),
  };
};

const isTargetSelect = (state: HassEntity | undefined): boolean =>
  !!state &&
  state.entity_id.startsWith("select.") &&
  typeof state.attributes.target_id === "string" &&
  Array.isArray(state.attributes.scenes);

export const isMagicSelect = (hass: HomeAssistant, entityId: string): boolean =>
  entityId.startsWith("select.") &&
  (hass.entities?.[entityId]?.platform === DOMAIN || isTargetSelect(hass.states[entityId]));

export const firstMagicSelect = (hass: HomeAssistant): string | undefined =>
  Object.keys(hass.states)
    .filter((id) => isMagicSelect(hass, id))
    .sort()[0];

/** The target's name: its service device, as the select's friendly name carries the "Scene" suffix. */
export const targetName = (hass: HomeAssistant, entityId: string): string | undefined => {
  const deviceId = hass.entities?.[entityId]?.device_id;
  const device = deviceId ? hass.devices?.[deviceId] : undefined;
  return device?.name_by_user || device?.name || hass.states[entityId]?.attributes.friendly_name;
};

/** Other targets' names and scenes, read from their own selects. */
export interface Lookup extends StatusNames {
  sceneOf(sceneId: string, owner: TargetId): StatusScene | undefined;
  swatchesOf(sceneId: string, owner: TargetId): readonly string[] | undefined;
}

export const lookup = (hass: HomeAssistant): Lookup => {
  let selects: Map<string, HassEntity> | undefined;
  const selectOf = (targetId: TargetId) => {
    selects ??= new Map(
      Object.values(hass.states)
        .filter(isTargetSelect)
        .map((state) => [state.attributes.target_id as string, state]),
    );
    return selects.get(targetId);
  };
  const sceneOf = (sceneId: string, owner: TargetId) => {
    const attrs = selectOf(owner)?.attributes as SelectAttributes | undefined;
    return attrs?.scenes?.find((s) => s.id === sceneId);
  };
  return {
    target: (id) => {
      const select = selectOf(id);
      return (select && targetName(hass, select.entity_id)) || humanise(id.slice(id.indexOf(":") + 1));
    },
    scene: (sceneId, owner) => {
      const attrs = selectOf(owner)?.attributes as SelectAttributes | undefined;
      return attrs?.scene_id === sceneId && attrs.scene_name ? attrs.scene_name : sceneOf(sceneId, owner)?.name;
    },
    sceneOf,
    swatchesOf: (sceneId, owner) => sceneOf(sceneId, owner)?.swatches,
  };
};

export interface CardScenes {
  scenes: StatusScene[];
  /** Configured scenes the target does not have. */
  missing: string[];
}

/** The scene grid: the target's menu, or the configured subset in its order. */
export const cardScenes = (attrs: SelectAttributes, wanted: readonly string[]): CardScenes => {
  const scenes = attrs.scenes ?? [];
  if (!wanted.length) return { scenes, missing: [] };
  const picked = new Set<StatusScene>();
  const missing: string[] = [];
  for (const key of wanted) {
    const name = key.toLowerCase();
    const scene = scenes.find((s) => s.id === key) ?? scenes.find((s) => s.name.toLowerCase() === name);
    if (scene) picked.add(scene);
    else missing.push(key);
  }
  return { scenes: [...picked], missing };
};

/** Lights an activation gave up; Reclaim brings them back. */
export const reclaimable = (view: TargetStatusView | undefined): string[] => view?.attrs?.manual ?? [];

/** The status line; the manual count moves to the Reclaim strip when that is shown. */
export const headerLine = (view: TargetStatusView | undefined, names: StatusNames): string =>
  statusLine(view?.attrs?.manual.length ? { ...view, attrs: { ...view.attrs, manual: [] } } : view, names);

/** Rows of 50 px for the masonry view. */
export const cardSize = (config: CardConfig, view: TargetStatusView | undefined): number => {
  let size = 1;
  if (reclaimable(view).length) size += 1;
  if (config.show_scenes && view?.attrs) {
    const { scenes, missing } = cardScenes(view.attrs, config.scenes);
    // An empty menu still takes a row for its hint.
    size += Math.ceil(scenes.length / config.columns) + (missing.length ? 1 : 0) || 1;
  }
  if (config.show_lights) size += 1;
  if (config.show_controls) size += 1;
  return size;
};
