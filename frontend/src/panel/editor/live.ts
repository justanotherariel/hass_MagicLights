// What the scene and part editors share to preview and try a draft: the span a preview
// covers, the pace of a moving one, and the queue that keeps try / try/stop in order.

import type { HomeAssistant } from "../../shared/types";
import type { MlStore } from "../store";

/** How often a moving preview advances, in ms. */
export const PLAY_STEP = 500;

/** A day from midnight for an adaptive look, else a minute of motion. */
export const previewSpan = (day: boolean): { duration: number; samples: number; start?: string } => {
  if (!day) return { duration: 60, samples: 120 };
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  return { duration: 86400, samples: 96, start: start.toISOString() };
};

export const reducedMotion = (): boolean => matchMedia("(prefers-reduced-motion: reduce)").matches;

export const secondsToday = (): number => {
  const now = new Date();
  return now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
};

/** A draft of an editor's Try runs in the target: its status has no scene id (engine/status.py). */
export const draftRunning = (store: MlStore, hass: HomeAssistant, targetId: string): boolean => {
  const attrs = store.status(hass, targetId)?.attrs;
  return attrs?.status === "scene" && attrs.scene_id === null && attrs.via === null;
};

/** try / try/stop calls in order, so a late Try never outlives Stop. */
export class TryQueue {
  private _last: Promise<unknown> = Promise.resolve();

  run(action: () => Promise<void>): Promise<void> {
    const run = this._last.then(action);
    this._last = run.catch(() => undefined);
    return run;
  }
}
