// The editor's working copy: a draft in sessionStorage under the scene id, kept next to
// its base, the draft as it was first opened plus each edited part as it was saved when
// its edit began. After a reload the edits are measured against that, so a scene that was
// never saved still knows it has unsaved changes, and a draft whose scene or parts were
// saved or deleted elsewhere since says so.

import type { Part, StoredScene } from "../../shared/types";
import { type Draft, type MlStore, toStoredPart, toStoredScene } from "../store";
import { stableJson } from "./editor-model";

/** What happened to the saved scene or its parts since the draft began. */
export type DraftStale = "saved" | "deleted";

export interface DraftBase {
  draft: Draft;
  /** The scene was saved when the draft began. */
  stored: boolean;
  /** Each part the draft edits, as it was saved when its edit began (null: new). */
  parts: Record<string, Part | null>;
}

export interface WorkingCopy {
  draft: Draft;
  /** What the edits are measured against: the saved scene, else the base. */
  baseline: Draft;
  /** The draft is from an earlier visit. */
  restored: boolean;
  /** Saving replaces what was saved elsewhere ("saved") or brings the scene back ("deleted"). */
  stale?: DraftStale;
}

/** `scene`: the saved scene now; `part`: a saved part now. */
export const draftStale = (
  base: DraftBase,
  scene: StoredScene | undefined,
  part: (id: string) => Part | undefined,
): DraftStale | undefined => {
  if (!scene) return base.stored ? "deleted" : undefined;
  if (!base.stored || stableJson(scene) !== stableJson(base.draft.scene)) return "saved";
  const moved = Object.entries(base.parts).some(([id, was]) => {
    const now = part(id);
    return stableJson(now ? toStoredPart(now) : null) !== stableJson(was);
  });
  return moved ? "saved" : undefined;
};

// Scene ids have no dots, so this never names another scene's draft.
const baseKey = (sceneId: string): string => `${sceneId}.base`;

const readBase = (store: MlStore, sceneId: string): DraftBase | undefined => {
  const base = store.draft<DraftBase>(baseKey(sceneId));
  return base?.draft ? base : undefined;
};

const savedScene = (store: MlStore, sceneId: string): StoredScene | undefined => {
  const scene = store.scene(sceneId);
  return scene ? toStoredScene(scene) : undefined;
};

/**
 * The draft of a target's scene: the stored one (an earlier visit, or a new scene the
 * panel just made), else the saved scene. Undefined for another target's scene or none.
 */
export const openWorkingCopy = (store: MlStore, targetId: string, sceneId: string): WorkingCopy | undefined => {
  const saved = store.scene(sceneId);
  if (saved && saved.target !== targetId) return undefined;
  // A stored value without a scene is no draft of this editor.
  const restored = store.draft(sceneId)?.scene ? store.draft(sceneId) : undefined;
  const current = savedScene(store, sceneId);
  const draft = restored ?? (current && { scene: current, parts: [] });
  if (!draft || draft.scene.id !== sceneId) return undefined;
  let base = restored && readBase(store, sceneId);
  if (!base) {
    base = { draft, stored: !!current, parts: {} };
    store.setDraft(sceneId, draft);
    store.setDraft(baseKey(sceneId), base);
  }
  return {
    draft,
    baseline: current ? { scene: current, parts: [] } : base.draft,
    restored: !!restored,
    stale: restored ? draftStale(base, current, (id) => store.part(id)) : undefined,
  };
};

/** Stores the draft; a part edited for the first time keeps its saved version in the base. */
export const keepWorkingCopy = (store: MlStore, draft: Draft): void => {
  const sceneId = draft.scene.id;
  store.setDraft(sceneId, draft);
  const base = readBase(store, sceneId);
  const fresh = draft.parts.filter((part) => base && !(part.id in base.parts));
  if (!base || !fresh.length) return;
  for (const part of fresh) {
    const saved = store.part(part.id);
    base.parts[part.id] = saved ? toStoredPart(saved) : null;
  }
  store.setDraft(baseKey(sceneId), base);
};

export const dropWorkingCopy = (store: MlStore, sceneId: string): void => {
  store.setDraft(sceneId, undefined);
  store.setDraft(baseKey(sceneId), undefined);
};
