// Pure logic of the scene editor: which lights a layer sets, draft comparison and the
// checks that gate Try and Save.

import { colorToHex } from "../../shared/color";
import { getHiddenFields } from "../../shared/conditions";
import type {
  Color,
  EffectInfo,
  HaFormSchema,
  LayerLights,
  Look,
  Part,
  PreviewFrame,
  SceneInfo,
  TargetInfo,
} from "../../shared/types";
import { humanise } from "../../shared/util";
import type { Draft } from "../store";
import { parseColor } from "./colors";

/** The lights of a room target ("room:<area_id>"), as the backend's LightIndex.expand reads them. */
export type RoomLights = (roomId: string) => readonly string[] | undefined;

/** Entity ids a layer selects on the target, in target order. */
export const expandLights = (lights: LayerLights, target: TargetInfo, roomLights: RoomLights): string[] => {
  if (lights === "all") return [...target.lights];
  const picked = new Set<string>();
  for (const item of lights) {
    if (item.startsWith("room:")) roomLights(item)?.forEach((e) => picked.add(e));
    else picked.add(item);
  }
  return target.lights.filter((e) => picked.has(e));
};

/** Light -> index of the layer that sets it; later layers win. */
export const layerOwners = (
  layers: readonly { lights: LayerLights }[],
  target: TargetInfo,
  roomLights: RoomLights,
): Map<string, number> => {
  const owners = new Map<string, number>();
  layers.forEach((layer, i) => {
    for (const e of expandLights(layer.lights, target, roomLights)) owners.set(e, i);
  });
  return owners;
};

/** Params as the form shows them: defaults for keys the params lack (a cleared key stays cleared). */
export const withDefaults = (
  defaults: Record<string, unknown> | undefined,
  params: Record<string, unknown>,
): Record<string, unknown> => {
  const out = { ...params };
  for (const [key, value] of Object.entries(defaults ?? {})) {
    if (!Object.hasOwn(out, key)) out[key] = structuredClone(value);
  }
  return out;
};

/** A form's params after one field changed; hidden fields keep their values. */
export const nextParams = (
  defaults: Record<string, unknown> | undefined,
  params: Record<string, unknown>,
  name: string,
  value: unknown,
): Record<string, unknown> => withDefaults(defaults, { ...params, [name]: value === "" ? undefined : value });

const visibleItems = (effect: EffectInfo | undefined, values: Record<string, unknown>): HaFormSchema[] => {
  const schema = effect?.schema ?? [];
  const hidden = getHiddenFields(schema, values);
  return schema.filter((item) => !hidden.has(item.name));
};

const selectorType = (item: HaFormSchema): string => Object.keys(item.selector ?? {})[0] ?? "";

const labelled = new WeakMap<Record<string, unknown>, Record<string, unknown>>();

/** Plain select options read as labels ("static" → "Static"); the values stay as they are. */
export const labelledSelector = (selector: Record<string, unknown>): Record<string, unknown> => {
  const select = selector.select as { options?: unknown[] } | undefined;
  if (!select?.options?.some((o) => typeof o === "string")) return selector;
  let result = labelled.get(selector);
  if (!result) {
    const options = select.options.map((o) => (typeof o === "string" ? { value: o, label: humanise(o) } : o));
    result = { ...selector, select: { ...select, options } };
    labelled.set(selector, result);
  }
  return result;
};

/** Up to five swatches from a layer's colour params; a palette wins over single colours. */
export const layerSwatches = (effect: EffectInfo | undefined, params: Record<string, unknown>): string[] => {
  const values = withDefaults(effect?.defaults, params);
  const items = visibleItems(effect, values);
  const palettes = items.filter((item) => selectorType(item) === "magic_lights_palette");
  const colours: Color[] = [];
  for (const item of palettes.length ? palettes : items) {
    const value = values[item.name];
    switch (selectorType(item)) {
      case "magic_lights_palette":
        if (Array.isArray(value)) colours.push(...value.flatMap((c) => parseColor(c) ?? []));
        break;
      case "magic_lights_color": {
        const color = parseColor(value);
        if (color) colours.push(color);
        break;
      }
      case "color_temp":
        if (typeof value === "number") colours.push({ kelvin: value });
        break;
    }
  }
  return colours.slice(0, 5).map(colorToHex);
};

/** JSON with sorted keys and without undefined values, so equal scenes compare equal. */
export const stableJson = (value: unknown): string =>
  JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v,
  );

/** The draft's parts that differ from the saved ones: edits and new parts, by id. */
export const changedParts = (parts: readonly Part[], saved: SavedPart): Part[] =>
  parts
    .filter((part) => {
      const before = saved(part.id);
      return !before || stableJson(storedPart(before)) !== stableJson(storedPart(part));
    })
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

const storedPart = ({ id, name, effect, params }: Part): Part => ({ id, name, effect, params });

/** A saved part by id. */
export type SavedPart = (id: string) => Part | undefined;

/** Whether the draft differs from what it is measured against; parts equal to the saved ones do not count. */
export const isDirty = (draft: Draft, baseline: Draft, saved: SavedPart): boolean =>
  stableJson(draft.scene) !== stableJson(baseline.scene) ||
  stableJson(changedParts(draft.parts, saved)) !== stableJson(changedParts(baseline.parts, saved));

/** Save needs a change, except for a new scene: an unchanged stored one would store nothing. */
export const canSave = (draft: Draft, baseline: Draft, stored: boolean, saved: SavedPart): boolean =>
  !stored || isDirty(draft, baseline, saved);

/** The draft with `part` edited in place: every scene using it gets the change on Save. */
export const withPart = (draft: Draft, part: Part): Draft => ({
  scene: draft.scene,
  parts: draft.parts.some((p) => p.id === part.id)
    ? draft.parts.map((p) => (p.id === part.id ? part : p))
    : [...draft.parts, part],
});

/**
 * Copy: the scene's layers playing `sourceId` play `copy` instead, which carries the
 * pending edits; the shared part goes back to how it is saved.
 */
export const forkPart = (draft: Draft, sourceId: string, copy: Part): Draft => ({
  scene: {
    ...draft.scene,
    layers: draft.scene.layers.map((l) => (l.part === sourceId ? { ...l, part: copy.id } : l)),
  },
  parts: [...draft.parts.filter((p) => p.id !== sourceId), copy],
});

/** The draft without parts that no layer plays any more: their edits are not saved. */
export const withoutUnusedParts = (draft: Draft): Draft => {
  const used = new Set(draft.scene.layers.map((l) => l.part));
  return { scene: draft.scene, parts: draft.parts.filter((p) => used.has(p.id)) };
};

export interface Problem {
  /** Layer index, or undefined for the scene itself. */
  layer?: number;
  /** A part's own problem (its name). */
  part?: string;
  message: string;
}

const nameProblem = (name: string, what: string): string | undefined => {
  const trimmed = name.trim();
  if (!trimmed) return `Give the ${what} a name.`;
  if (trimmed.length > 64) return "The name is longer than 64 characters.";
  return undefined;
};

/** What the backend would reject before it gets the chance; Try and Save wait for none. */
export const sceneProblems = (
  draft: Draft,
  saved: SavedPart,
  effectOf: (key: string) => EffectInfo | undefined,
): Problem[] => {
  const problems: Problem[] = [];
  const { scene } = draft;
  const named = nameProblem(scene.name, "scene");
  if (named) problems.push({ message: named });
  else if (["off", "manual"].includes(scene.name.trim().toLowerCase())) {
    problems.push({ message: `"${scene.name.trim()}" is reserved for the scene menu.` });
  }
  const partOf = (id: string) => draft.parts.find((p) => p.id === id) ?? saved(id);
  scene.layers.forEach((layer, i) => {
    if (layer.lights !== "all" && layer.lights.length === 0) {
      problems.push({ layer: i, message: "Pick at least one light." });
    }
    const part = partOf(layer.part);
    if (!part) problems.push({ layer: i, message: `The part "${layer.part}" no longer exists.` });
    else if (!effectOf(part.effect)) {
      problems.push({ layer: i, message: `The effect "${part.effect}" is not installed.` });
    }
  });
  for (const part of draft.parts) {
    const problem = nameProblem(part.name, "part");
    if (problem) problems.push({ part: part.id, message: problem });
  }
  return problems;
};

/**
 * Who a part's edits reach: the edited scene first (undefined while it is unsaved or does
 * not use the part yet), then the other saved scenes that use it.
 */
export const partUsers = (saved: readonly SceneInfo[], sceneId: string): (SceneInfo | undefined)[] => {
  const here = saved.find((s) => s.id === sceneId);
  return [here, ...saved.filter((s) => s !== here)];
};

/** The last frame at or before t. */
export const frameAt = (frames: readonly PreviewFrame[], t: number): PreviewFrame | undefined => {
  let lo = 0;
  let hi = frames.length - 1;
  if (hi < 0) return undefined;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (frames[mid].t <= t) lo = mid;
    else hi = mid - 1;
  }
  return frames[lo];
};

/**
 * The params of a look that differ from the original (a built-in as it ships), defaults
 * filled on both sides. Another effect counts as all changed.
 */
export const changedParams = (effect: EffectInfo | undefined, look: Look, original: Look | null | undefined): Set<string> => {
  if (!original) return new Set();
  const now = withDefaults(effect?.defaults, look.params);
  if (original.effect !== look.effect) return new Set(Object.keys(now));
  const before = withDefaults(effect?.defaults, original.params);
  const keys = new Set([...Object.keys(now), ...Object.keys(before)]);
  return new Set([...keys].filter((key) => stableJson(now[key]) !== stableJson(before[key])));
};

/** The effect switch's order: the catalogue's, effects without settings (Off) last. */
export const effectOrder = (effects: readonly EffectInfo[]): EffectInfo[] => [
  ...effects.filter((e) => e.schema === null || e.schema.length > 0),
  ...effects.filter((e) => e.schema !== null && e.schema.length === 0),
];

export interface CurvePoint {
  t: number;
  /** Mean brightness of the lights, 0..1; a light that is off counts as 0. */
  level: number;
  /** The first lit light's colour. */
  color?: string;
}

/** One point per frame for `lights`, or for every light of the frame when none of them is in it. */
export const curvePoints = (frames: readonly PreviewFrame[], lights: readonly string[]): CurvePoint[] =>
  frames.map((frame) => {
    const own = lights.filter((e) => e in frame.lights);
    const ids = own.length ? own : Object.keys(frame.lights);
    const states = ids.map((e) => frame.lights[e]);
    const level = states.length
      ? states.reduce((sum, s) => sum + (s.on ? s.brightness : 0), 0) / states.length
      : 0;
    return { t: frame.t, level, color: states.find((s) => s.on)?.rgb };
  });

/** The lowest and highest level, in percent. */
export const curveRange = (points: readonly CurvePoint[]): [number, number] => {
  const levels = points.map((p) => Math.round(p.level * 100));
  return [Math.min(...levels), Math.max(...levels)];
};
