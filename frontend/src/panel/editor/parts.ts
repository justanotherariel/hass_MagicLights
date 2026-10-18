// A scene's parts as one room sees them. A part is a layer; the editor shows the parts
// that reach the preview room and moves that room's lights between them. Every change
// edits only the room's own entries of a layer (roomSlice / setRoomSlice), so the other
// rooms keep exactly the lights they had.

import type { Layer, LayerLights, TargetInfo } from "../../shared/types";
import { expandLights, roomSlice, setRoomSlice, wholeTarget, type RoomLights } from "./editor-model";

export interface PartNames {
  light(entityId: string): string;
  /** A "room:<area>" entry. */
  room(ref: string): string;
}

/** Where a room's entries resolve and what "all" stands for elsewhere. */
export interface RoomContext {
  target: TargetInfo;
  roomLights: RoomLights;
  /** Every room target id ("room:<area>"), for a layer of "all". */
  everyRoom: readonly string[];
}

/** "Main" for the first layer; else what it is for: "Bedroom only", "Sofa lamp", "Sofa lamp +2". */
export const partLabel = (layer: { lights: LayerLights }, index: number, names: PartNames): string => {
  if (index === 0 || layer.lights === "all") return "Main";
  const [first, ...rest] = layer.lights;
  if (first === undefined) return `Part ${index + 1}`;
  const name = first.startsWith("room:") ? `${names.room(first)} only` : names.light(first);
  return rest.length ? `${name} +${rest.length}` : name;
};

/** The layers that set at least one light of the target, in order. */
export const partsIn = (layers: readonly Layer[], target: TargetInfo, roomLights: RoomLights): number[] =>
  layers.flatMap((layer, i) => (expandLights(layer.lights, target, roomLights).length ? [i] : []));

/** The parts a room's switcher offers: the shared first part, and every part that reaches the room. */
export const switcherParts = (layers: readonly Layer[], target: TargetInfo, roomLights: RoomLights): number[] => [
  0,
  ...partsIn(layers, target, roomLights).filter((i) => i !== 0),
];

/** A slice of the room's entries as its lights. */
const sliceLights = (slice: readonly string[], ctx: RoomContext): Set<string> =>
  new Set(expandLights([...slice], ctx.target, ctx.roomLights));

/** The room's slice covering exactly `lights`: the whole room as its refs, else the lights. */
const sliceOf = (lights: Set<string>, ctx: RoomContext): string[] => {
  const all = ctx.target.lights;
  if (all.length && all.every((e) => lights.has(e))) return wholeTarget(ctx.target, ctx.roomLights);
  return all.filter((e) => lights.has(e));
};

const withRoomLights = (layer: Layer, lights: Set<string>, ctx: RoomContext): Layer => {
  const slice = roomSlice(layer.lights, ctx.target, ctx.roomLights);
  const current = sliceLights(slice, ctx);
  const same = current.size === lights.size && [...lights].every((e) => current.has(e));
  if (same) return layer;
  return { ...layer, lights: setRoomSlice(layer.lights, ctx.target, sliceOf(lights, ctx), ctx.everyRoom) };
};

/** Drops the parts after the first that have no lights left anywhere. */
const withoutEmptyParts = (layers: readonly Layer[]): { layers: Layer[]; moved: number[] } => {
  const moved: number[] = [];
  const kept: Layer[] = [];
  layers.forEach((layer, i) => {
    if (i > 0 && layer.lights !== "all" && !layer.lights.length) moved.push(-1);
    else moved.push(kept.push(layer) - 1);
  });
  return { layers: kept, moved };
};

/** The light joins `part` (null: none) and leaves every later part, which would win over it. */
const moveInto = (layers: readonly Layer[], entityId: string, part: number | null, ctx: RoomContext): Layer[] =>
  layers.map((layer, i) => {
    const lights = sliceLights(roomSlice(layer.lights, ctx.target, ctx.roomLights), ctx);
    if (part !== null && i === part) lights.add(entityId);
    else if (part === null || i > part) lights.delete(entityId);
    return withRoomLights(layer, lights, ctx);
  });

/**
 * Moves a light of the room into part `part` (null: no part). Earlier parts keep it, as
 * the part wins over them anyway. Parts other than the first that end up with no lights
 * in any room are dropped. Returns the layers and where each old index went (-1: dropped).
 */
export const assignLight = (
  layers: readonly Layer[],
  entityId: string,
  part: number | null,
  ctx: RoomContext,
): { layers: Layer[]; moved: number[] } => withoutEmptyParts(moveInto(layers, entityId, part, ctx));

/**
 * A new part right after `from`, with its look, for `lights` of the room only: they move
 * into it. Returns the layers and the new part's index.
 */
export const addPart = (
  layers: readonly Layer[],
  from: number,
  lights: readonly string[],
  ctx: RoomContext,
): { layers: Layer[]; index: number } => {
  const source = layers[from];
  const part: Layer = { lights: [], effect: source.effect, params: structuredClone(source.params) };
  let next = [...layers.slice(0, from + 1), part, ...layers.slice(from + 1)];
  const index = from + 1;
  for (const e of lights) next = moveInto(next, e, index, ctx);
  const result = withoutEmptyParts(next);
  return { layers: result.layers, index: result.moved[index] };
};

/** "Use all lights here": the whole room joins the first part. */
export const setUpHere = (layers: readonly Layer[], ctx: RoomContext): Layer[] =>
  layers.map((layer, i) =>
    i === 0 ? withRoomLights(layer, new Set(ctx.target.lights), ctx) : layer,
  );

/** The room's lights in no part, in room order. */
export const lightsInNoPart = (owners: ReadonlyMap<string, number>, target: TargetInfo): string[] =>
  target.lights.filter((e) => !owners.has(e));
