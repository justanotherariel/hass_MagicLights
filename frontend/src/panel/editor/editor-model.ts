// Pure logic of the scene editor: which lights a layer sets, the part of a layer one
// room edits, layer summaries, draft comparison and the checks that gate Try and Save.

import { colorToHex } from "../../shared/color";
import { getHiddenFields } from "../../shared/conditions";
import type {
  Color,
  EffectInfo,
  HaFormSchema,
  LayerLights,
  PreviewFrame,
  StoredScene,
  TargetInfo,
} from "../../shared/types";
import { humanise, plural, withUnit } from "../../shared/util";
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

/** The `room:` refs and lights that stand for the whole target. */
export const wholeTarget = (target: TargetInfo, roomLights: RoomLights): string[] => {
  const refs = target.rooms.map((area) => `room:${area}`);
  const covered = new Set(refs.flatMap((ref) => roomLights(ref) ?? []));
  return [...refs, ...target.lights.filter((e) => !covered.has(e))];
};

/** Whether an entry of a layer's lights belongs to the target: one of its rooms or lights. */
const inTarget = (entry: string, target: TargetInfo): boolean =>
  entry.startsWith("room:") ? target.rooms.includes(entry.slice(5)) : target.lights.includes(entry);

/** The entries of a layer the target edits; "all" counts as the whole target. */
export const roomSlice = (lights: LayerLights, target: TargetInfo, roomLights: RoomLights): string[] =>
  lights === "all" ? wholeTarget(target, roomLights) : lights.filter((e) => inTarget(e, target));

/** Whether the target's slice covers all of it. */
export const isWhole = (lights: LayerLights, target: TargetInfo, roomLights: RoomLights): boolean => {
  const slice = roomSlice(lights, target, roomLights);
  return wholeTarget(target, roomLights).every((e) => slice.includes(e));
};

/**
 * A layer's lights with the target's slice replaced and every other room's entries kept.
 * `everyRoom` lists the `room:` refs that "all" stands for elsewhere.
 */
export const setRoomSlice = (
  lights: LayerLights,
  target: TargetInfo,
  slice: readonly string[],
  everyRoom: readonly string[],
): string[] => {
  const others = (lights === "all" ? everyRoom : lights).filter((e) => !inTarget(e, target));
  return [...others, ...slice.filter((e) => !others.includes(e))];
};

/** "Ankleide only", "3 rooms", "also Kitchen": the rooms a layer reaches besides `here`. */
export const layerCaption = (
  lights: LayerLights,
  here: TargetInfo,
  rooms: readonly TargetInfo[],
  roomLights: RoomLights,
): string | undefined => {
  if (lights === "all") return undefined;
  const elsewhere = rooms.filter(
    (room) =>
      room.id !== here.id &&
      !here.rooms.includes(room.area_id ?? "") &&
      expandLights(lights, room, roomLights).length > 0,
  );
  if (!elsewhere.length) return undefined;
  const reach = elsewhere.length === 1 ? elsewhere[0].name : plural(elsewhere.length, "room");
  if (expandLights(lights, here, roomLights).length) return `also ${reach}`;
  return elsewhere.length === 1 ? `${reach} only` : reach;
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

const optionLabel = (item: HaFormSchema, value: string): string => {
  const select = labelledSelector(item.selector ?? {}).select as { options?: { value?: unknown; label?: string }[] };
  return select.options?.find((option) => option.value === value)?.label ?? humanise(value);
};

const formatNumber = (value: number): string =>
  Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);

/** "2700 K · 60%": numbers with a unit and choices, the first three visible params. */
export const layerSummary = (effect: EffectInfo | undefined, params: Record<string, unknown>): string => {
  const values = withDefaults(effect?.defaults, params);
  const parts: string[] = [];
  for (const item of visibleItems(effect, values)) {
    const value = values[item.name];
    const type = selectorType(item);
    const config = (item.selector?.[type] ?? {}) as { unit_of_measurement?: string };
    if (type === "color_temp" && typeof value === "number") parts.push(`${Math.round(value)} K`);
    else if (type === "number" && typeof value === "number" && config.unit_of_measurement) {
      parts.push(withUnit(formatNumber(value), config.unit_of_measurement));
    } else if (type === "select" && typeof value === "string" && value) parts.push(optionLabel(item, value));
  }
  return parts.slice(0, 3).join(" · ");
};

/** JSON with sorted keys and without undefined values, so equal scenes compare equal. */
export const stableJson = (value: unknown): string =>
  JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v,
  );

export interface Problem {
  /** Layer index, or undefined for the scene itself. */
  layer?: number;
  message: string;
}

/** What the backend would reject before it gets the chance; Try and Save wait for none. */
export const sceneProblems = (
  scene: StoredScene,
  effectOf: (key: string) => EffectInfo | undefined,
): Problem[] => {
  const problems: Problem[] = [];
  const name = scene.name.trim();
  if (!name) problems.push({ message: "Give the scene a name." });
  else if (name.length > 64) problems.push({ message: "The name is longer than 64 characters." });
  else if (["off", "manual"].includes(name.toLowerCase())) {
    problems.push({ message: `"${name}" is reserved for the scene menu.` });
  }
  scene.layers.forEach((layer, i) => {
    if (layer.lights !== "all" && layer.lights.length === 0) {
      problems.push({ layer: i, message: "Pick at least one light." });
    }
    if (!effectOf(layer.effect)) {
      problems.push({ layer: i, message: `The effect "${layer.effect}" is not installed.` });
    }
  });
  return problems;
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
