// The editor's working copy: a draft in sessionStorage under the route, kept next to
// the scene as it was first opened. After a reload the edits are measured against that,
// so a scene that was never saved still knows it has unsaved changes.

import type { StoredScene } from "../../shared/types";
import { toStored, type MlStore } from "../store";

// Scene ids have no dots, so this never names another route's draft.
const baseOf = (sceneId: string): string => `${sceneId}.base`;

export interface WorkingCopy {
  scene: StoredScene;
  /** What the edits are measured against: the stored copy of the opened scene, else the scene as opened. */
  baseline: StoredScene;
  /** The draft is from an earlier visit. */
  restored: boolean;
}

export const openWorkingCopy = (
  store: MlStore,
  targetId: string,
  sceneId: string,
): WorkingCopy | undefined => {
  const restored = store.draft(targetId, sceneId);
  const scene = store.editableScene(targetId, sceneId);
  if (!scene) return undefined;
  let base = restored && store.draft(targetId, baseOf(sceneId));
  if (!base) {
    base = scene;
    // Also pins a freshly generated id (preset copies) across reloads.
    store.setDraft(targetId, sceneId, scene);
    store.setDraft(targetId, baseOf(sceneId), scene);
  }
  const stored = store.scene(base.id);
  return {
    scene,
    baseline: stored && !stored.builtin ? toStored(stored) : base,
    restored: !!restored,
  };
};

export const dropWorkingCopy = (store: MlStore, targetId: string, sceneId: string): void => {
  store.setDraft(targetId, sceneId, undefined);
  store.setDraft(targetId, baseOf(sceneId), undefined);
};
