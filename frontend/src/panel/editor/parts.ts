// A scene's layers as its target's lights see them. Each layer plays one part on some of
// the target's lights; the lights card moves lights between layers. A layer holding every
// light reads "all", so it follows the room as lights join or leave it.

import type { LayerLights, PartLayer, TargetInfo } from "../../shared/types";
import { expandLights, layerOwners, type RoomLights } from "./editor-model";

/** Where the target's `room:` entries resolve. */
export interface RoomContext {
  target: TargetInfo;
  roomLights: RoomLights;
}

/** The layers that set at least one light of the target, in order. */
export const partsIn = (layers: readonly PartLayer[], target: TargetInfo, roomLights: RoomLights): number[] =>
  layers.flatMap((layer, i) => (expandLights(layer.lights, target, roomLights).length ? [i] : []));

/** The entries covering exactly `lights`: "all", else a zone's whole rooms as refs, then single lights. */
const sliceOf = (lights: ReadonlySet<string>, ctx: RoomContext): LayerLights => {
  const all = ctx.target.lights;
  if (all.length && all.every((e) => lights.has(e))) return "all";
  const refs = ctx.target.kind === "zone" ? ctx.target.rooms.map((area) => `room:${area}`) : [];
  const whole = refs.filter((ref) => {
    const own = ctx.roomLights(ref) ?? [];
    return own.length > 0 && own.every((e) => lights.has(e));
  });
  const covered = new Set(whole.flatMap((ref) => ctx.roomLights(ref) ?? []));
  return [...whole, ...all.filter((e) => lights.has(e) && !covered.has(e))];
};

const withLights = (layer: PartLayer, lights: ReadonlySet<string>, ctx: RoomContext): PartLayer => {
  const current = new Set(expandLights(layer.lights, ctx.target, ctx.roomLights));
  const same = current.size === lights.size && [...lights].every((e) => current.has(e));
  return same ? layer : { ...layer, lights: sliceOf(lights, ctx) };
};

/**
 * Drops layers that play on no light, as later layers took all of theirs, keeping the
 * last while none plays. Returns the layers and where each old index went (-1: dropped).
 */
const withoutIdleParts = (
  layers: readonly PartLayer[],
  ctx: RoomContext,
): { layers: PartLayer[]; moved: number[] } => {
  const playing = new Set(layerOwners(layers, ctx.target, ctx.roomLights).values());
  if (!playing.size) playing.add(layers.length - 1);
  const kept: PartLayer[] = [];
  const moved = layers.map((layer, i) => (playing.has(i) ? kept.push(layer) - 1 : -1));
  return { layers: kept, moved };
};

/** The lights join layer `part` (null: none) and leave every later layer, which would win over it. */
const moveInto = (
  layers: readonly PartLayer[],
  entityIds: readonly string[],
  part: number | null,
  ctx: RoomContext,
): PartLayer[] =>
  layers.map((layer, i) => {
    const lights = new Set(expandLights(layer.lights, ctx.target, ctx.roomLights));
    for (const e of entityIds) {
      if (part !== null && i === part) lights.add(e);
      else if (part === null || i > part) lights.delete(e);
    }
    return withLights(layer, lights, ctx);
  });

/**
 * Moves lights of the target into layer `part` (null: no part). Earlier layers keep them,
 * so an "all" layer still follows the room; a layer left playing on no light is dropped.
 * Returns the layers and where each old index went (-1: dropped).
 */
export const assignLights = (
  layers: readonly PartLayer[],
  entityIds: readonly string[],
  part: number | null,
  ctx: RoomContext,
): { layers: PartLayer[]; moved: number[] } => withoutIdleParts(moveInto(layers, entityIds, part, ctx), ctx);

/** A last layer playing `partId` on `lights`: it wins over the others there. Returns the layers and its index. */
export const addPart = (
  layers: readonly PartLayer[],
  partId: string,
  lights: readonly string[],
  ctx: RoomContext,
): { layers: PartLayer[]; index: number } => {
  const out = assignLights([...layers, { lights: [], part: partId }], lights, layers.length, ctx);
  return { layers: out.layers, index: out.moved[layers.length] };
};

/** The target's lights in no part, in target order. */
export const lightsInNoPart = (owners: ReadonlyMap<string, number>, target: TargetInfo): string[] =>
  target.lights.filter((e) => !owners.has(e));
