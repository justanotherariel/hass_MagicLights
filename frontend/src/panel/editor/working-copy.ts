// The editor's working copy: a draft in sessionStorage under the scene, kept next to
// the scene as it was first opened. After a reload the edits are measured against that,
// so a scene that was never saved still knows it has unsaved changes.

import type { StoredScene } from "../../shared/types";
import { toStored, type MlStore } from "../store";
import { stableJson } from "./editor-model";

// Scene ids have no dots, so these never name another route's draft.
const baseOf = (sceneId: string): string => `${sceneId}.base`;
// Kept only while the opened scene was a stored one.
const storedOf = (sceneId: string): string => `${sceneId}.stored`;

export interface WorkingCopy {
  scene: StoredScene;
  /** What the edits are measured against: the stored copy of the opened scene, else the scene as opened. */
  baseline: StoredScene;
  /** The draft is from an earlier visit. */
  restored: boolean;
  /** Since the draft began, the scene was saved elsewhere (saving replaces that) or removed (saving brings it back). */
  stale?: "saved" | "removed";
}

export const openWorkingCopy = (store: MlStore, sceneId: string): WorkingCopy | undefined => {
  const restored = store.draft(sceneId);
  const scene = store.editableScene(sceneId);
  if (!scene) return undefined;
  const stored = store.scene(scene.id);
  const current = stored && !stored.builtin ? toStored(stored) : undefined;
  let base = restored && store.draft(baseOf(sceneId));
  if (!base) {
    base = scene;
    // Also pins a freshly generated id (a new edit of a preset) across reloads.
    store.setDraft(sceneId, scene);
    store.setDraft(baseOf(sceneId), scene);
    store.setDraft(storedOf(sceneId), current);
  }
  let stale: WorkingCopy["stale"];
  if (restored && current) {
    if (stableJson(current) !== stableJson({ ...base, id: current.id })) stale = "saved";
  } else if (restored && store.draft(storedOf(sceneId))) stale = "removed";
  return { scene, baseline: current ?? base, restored: !!restored, stale };
};

export const dropWorkingCopy = (store: MlStore, sceneId: string): void => {
  store.setDraft(sceneId, undefined);
  store.setDraft(baseOf(sceneId), undefined);
  store.setDraft(storedOf(sceneId), undefined);
};
