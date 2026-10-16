// What tapping a scene and its ▶ do, for the panel and the card alike.

import { type ActivateOptions, activateScene, setDynamic } from "./api";
import { activeScene, isMoving, runningScene } from "./status";
import type { HomeAssistant, SelectAttributes } from "./types";

/** `dynamic`: the backend found a motion switch in the scene's effects. */
export interface MotionInfo {
  swatches: readonly string[];
  dynamic: boolean;
}

/** Its colour motion can be switched (▶); one colour cannot move. */
export const isPlayable = (scene: MotionInfo | undefined): boolean =>
  !!scene && scene.dynamic && scene.swatches.length > 1;

/**
 * Activating the active scene again reclaims its lights and re-sends it; adjustments do not
 * carry over, so its brightness and motion are passed on.
 */
export const activationOptions = (
  attrs: SelectAttributes | undefined,
  sceneId: string,
  scene: MotionInfo | undefined,
): ActivateOptions => {
  if (!attrs || activeScene(attrs) !== sceneId) return {};
  return {
    ...(attrs.brightness == null ? {} : { brightness: attrs.brightness }),
    ...(isPlayable(scene) ? { dynamic: isMoving(attrs) } : {}),
  };
};

/** ▶/❚❚: the running scene (own or via) starts or holds its motion in place; another one starts moving. */
export const toggleMotion = (
  hass: HomeAssistant,
  selectEntityId: string,
  attrs: SelectAttributes | undefined,
  sceneId: string,
): Promise<unknown> =>
  runningScene(attrs) === sceneId
    ? setDynamic(hass, selectEntityId, !isMoving(attrs))
    : activateScene(hass, selectEntityId, sceneId, { dynamic: true });
