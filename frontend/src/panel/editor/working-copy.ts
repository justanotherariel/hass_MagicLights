// The editor's working copy: a draft in sessionStorage under the scene id, kept next to
// its base, the scene as it was first opened. After a reload the edits are measured
// against that, so a scene that was never saved still knows it has unsaved changes, and
// a draft whose scene was saved, deleted or reset elsewhere since says so.

import type { StoredScene } from "../../shared/types";
import { toStored, type MlStore } from "../store";
import { stableJson } from "./editor-model";

/** What happened to the stored scene since its draft began. */
export type DraftStale = "saved" | "deleted" | "reset";

export interface DraftBase {
  scene: StoredScene;
  /** The scene was stored (a user scene or a preset's edit) when the draft began. */
  stored: boolean;
}

export interface WorkingCopy {
  scene: StoredScene;
  /** What the edits are measured against: the stored copy of the scene, else its base. */
  baseline: StoredScene;
  /** The draft is from an earlier visit. */
  restored: boolean;
  /** Saving replaces an edit made elsewhere ("saved") or brings the scene back ("deleted", "reset"). */
  stale?: DraftStale;
}

/** `current`: the stored copy now. Ids are not compared: a preset's edit may be stored under another one. */
export const draftStale = (base: DraftBase, current: StoredScene | undefined): DraftStale | undefined => {
  if (!current) return base.stored ? (base.scene.preset ? "reset" : "deleted") : undefined;
  if (!base.stored) return "saved";
  return stableJson(current) === stableJson({ ...base.scene, id: current.id }) ? undefined : "saved";
};

// Scene ids have no dots, so this never names another scene's draft.
const baseKey = (sceneId: string): string => `${sceneId}.base`;

const readBase = (store: MlStore, sceneId: string): DraftBase | undefined => {
  const base = store.draft<DraftBase>(baseKey(sceneId));
  return base?.scene ? base : undefined;
};

export const openWorkingCopy = (store: MlStore, sceneId: string): WorkingCopy | undefined => {
  const restored = store.draft(sceneId);
  const scene = store.editableScene(sceneId);
  if (!scene) return undefined;
  const stored = store.scene(scene.id);
  const current = stored && !stored.builtin ? toStored(stored) : undefined;
  let base = restored && readBase(store, sceneId);
  if (!base) {
    base = { scene, stored: !!current };
    // Also pins a freshly generated id (a new edit of a preset) across reloads.
    store.setDraft(sceneId, scene);
    store.setDraft(baseKey(sceneId), base);
  }
  return {
    scene,
    baseline: current ?? base.scene,
    restored: !!restored,
    stale: restored ? draftStale(base, current) : undefined,
  };
};

export const dropWorkingCopy = (store: MlStore, sceneId: string): void => {
  store.setDraft(sceneId, undefined);
  store.setDraft(baseKey(sceneId), undefined);
};
