// What a scene is, in a line, and where it stands across rooms: the text and filters of
// the Scenes tab, the room page's tiles and the Add scenes picker. The look is read from
// the effect's schema (form-rules), never from the effect itself. Pure, tested without a DOM.

import type { EffectInfo, SceneCategory, SceneInfo, TargetInfo } from "../shared/types";
import { humanise, plural, withUnit } from "../shared/util";
import { withDefaults } from "./editor/editor-model";
import { formLayout } from "./editor/form-rules";
import { type Availability, menuId, type MlStore, SCENE_GROUPS } from "./store";

const span = (values: number[], unit: string): string => {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  return lo === hi ? withUnit(lo, unit) : withUnit(`${lo}–${hi}`, unit);
};

/** "Adaptive · 40–100% · 2200–5000 K": a layer's effect, its visible levels, whites and colours. */
export const layerLook = (
  effect: EffectInfo | undefined,
  layer: { effect: string; params: Record<string, unknown> },
): string[] => {
  const parts = [effect?.name ?? humanise(layer.effect)];
  const percent: number[] = [];
  const whites: string[] = [];
  let colours: number | undefined;
  const p = withDefaults(effect?.defaults, layer.params);
  for (const control of effect?.schema ? formLayout(effect.schema, p).main.flat() : []) {
    const value = p[control.item.name];
    if (control.kind === "bar" && control.big && typeof value === "number") percent.push(value);
    if (control.kind === "kelvin" && typeof value === "number") whites.push(`${value} K`);
    if (control.kind === "palette" && Array.isArray(value)) colours = value.length;
    if (control.kind === "range") {
      const high = p[control.high.name];
      if (typeof value !== "number" || typeof high !== "number") continue;
      if (control.kelvin) whites.push(span([value, high], "K"));
      else if (control.unit === "%") percent.push(value, high);
    }
  }
  if (percent.length) parts.push(span(percent, "%"));
  parts.push(...whites);
  if (colours !== undefined) parts.push(plural(colours, "colour"));
  return parts;
};

export const lookLine = (
  effect: EffectInfo | undefined,
  layer: { effect: string; params: Record<string, unknown> },
): string => layerLook(effect, layer).join(" · ");

type EffectOf = (key: string) => EffectInfo | undefined;

/** The effects of later parts unlike the first's: a "White" scene with a palette on the TV. */
const otherKinds = (scene: SceneInfo, effectOf: EffectOf): string[] => {
  const kind = (layer: { effect: string }) => effectOf(layer.effect)?.name ?? humanise(layer.effect);
  const first = kind(scene.layers[0]);
  return [...new Set(scene.layers.slice(1).map(kind))].filter((name) => name !== first);
};

const withOthers = (line: string, scene: SceneInfo, effectOf: EffectOf): string =>
  scene.layers.length ? [line, ...otherKinds(scene, effectOf)].join(" + ") : line;

/** A scene's look: its first layer's, and how many parts it has when they all do the same. */
export const lookParts = (scene: SceneInfo, effectOf: EffectOf): string[] => {
  const layer = scene.layers[0];
  if (!layer) return [];
  const parts = layerLook(effectOf(layer.effect), layer);
  if (scene.layers.length > 1 && !otherKinds(scene, effectOf).length) {
    parts.push(plural(scene.layers.length, "part"));
  }
  return parts;
};

/** "White · 30% · 2700 K + Colour palette". */
export const lookSummary = (scene: SceneInfo, effectOf: EffectOf): string =>
  withOthers(lookParts(scene, effectOf).join(" · "), scene, effectOf);

/** The tile's "what it is": the effect, its first fact and what other parts do. */
export const tileLine = (scene: SceneInfo, effectOf: EffectOf): string =>
  withOthers(lookParts(scene, effectOf).slice(0, 2).join(" · "), scene, effectOf);

/** A scene of the user's, not a preset or its edit. */
export const isOwn = (scene: SceneInfo): boolean => !scene.builtin && !scene.preset;

// ---- the home Scenes tab

export type SceneFilter = "all" | SceneCategory | "edited" | "setup";

export const FILTERS: readonly [SceneFilter, string][] = [
  ["all", "All"],
  ...SCENE_GROUPS,
  ["edited", "Edited"],
  ["setup", "Needs set-up"],
];

export interface SceneEntry {
  /** Menu id: a preset's id for its edit. */
  id: string;
  scene: SceneInfo;
  own: boolean;
  group: SceneCategory;
  edited: boolean;
  /** The scene for a light switched on in an idle room (settings). */
  isDefault: boolean;
  /** The targets showing it in their menu. */
  shown: Availability[];
  /** In some menu without lights there. */
  needsSetUp: boolean;
}

/** Every scene once: the user's (each, even one that fits no room), then the presets. */
export const sceneEntries = (store: MlStore): SceneEntry[] =>
  store.menuScenes().map((scene) => {
    const id = menuId(scene);
    const shown = store.availability(id).filter((a) => a.shown);
    return {
      id,
      scene,
      own: isOwn(scene),
      group: store.groupOf(scene),
      edited: store.edited(id),
      isDefault: store.config.settings.default_scene === id,
      shown,
      needsSetUp: shown.some((a) => a.needsSetUp),
    };
  });

export const matchesFilter = (entry: SceneEntry, filter: SceneFilter): boolean => {
  switch (filter) {
    case "all":
      return true;
    case "edited":
      return entry.edited;
    case "setup":
      return entry.needsSetUp;
    default:
      return entry.group === filter;
  }
};

/** A search by name; an empty query matches every scene. */
export const matchesQuery = (entry: SceneEntry, query: string): boolean => {
  const q = query.trim().toLowerCase();
  return !q || entry.scene.name.toLowerCase().includes(q);
};

export interface SceneSections {
  own: SceneEntry[];
  /** Presets a room shows, edited ones and the switch-on scene. */
  presets: SceneEntry[];
  /** The rest of the library, folded away. */
  more: SceneEntry[];
}

export const sceneSections = (entries: readonly SceneEntry[]): SceneSections => {
  const sections: SceneSections = { own: [], presets: [], more: [] };
  for (const entry of entries) {
    if (entry.own) sections.own.push(entry);
    else if (entry.shown.length || entry.edited || entry.isDefault) sections.presets.push(entry);
    else sections.more.push(entry);
  }
  return sections;
};

// ---- the Add scenes picker

export interface PickLine {
  text: string;
  /** Picked here without lights here: it shows greyed out until it is set up. */
  warn: boolean;
}

/** The picker's second line: where a scene of yours runs, or what a preset is. */
export const pickLine = (
  store: MlStore,
  entry: SceneEntry,
  target: TargetInfo,
  picked: boolean,
): PickLine => {
  const scene = entry.scene;
  const parts = lookParts(scene, (key) => store.effect(key));
  if (!entry.own) {
    const notes = [entry.edited && "Edited", entry.isDefault && "switch-on default"].filter(Boolean);
    return { text: notes.join(" · ") || parts.slice(1, 3).join(" · ") || parts[0] || "", warn: false };
  }
  const fitsHere = scene.fits.includes(target.id);
  const rooms = scene.fits.filter((id) => id !== target.id && store.target(id)?.kind === "room");
  if (fitsHere) {
    const where = rooms.length ? plural(rooms.length + (target.kind === "room" ? 1 : 0), "room") : "here only";
    return { text: [parts[0], where].filter(Boolean).join(" · "), warn: false };
  }
  if (picked) return { text: "Needs set-up here", warn: true };
  if (!rooms.length) return { text: "Not set up anywhere", warn: false };
  const text = rooms.length === 1 ? `Set up for ${store.targetName(rooms[0])}` : `Set up for ${plural(rooms.length, "room")}`;
  return { text, warn: false };
};

/** A menu with `sceneId` moved `by` places, kept inside the list. */
export const movedBy = (ids: readonly string[], sceneId: string, by: number): string[] => {
  const from = ids.indexOf(sceneId);
  if (from < 0) return [...ids];
  const to = Math.max(0, Math.min(ids.length - 1, from + by));
  const next = ids.filter((id) => id !== sceneId);
  next.splice(to, 0, sceneId);
  return next;
};
