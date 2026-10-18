// Typed wrappers for the magic_lights websocket commands and services.

import type {
  Adjust,
  ConfigDto,
  HomeAssistant,
  PreviewFrame,
  PreviewRequest,
  SceneInfo,
  Settings,
  StoredScene,
  SubscribeEvent,
  TargetId,
  TargetInfo,
  TargetOptions,
  Unsubscribe,
  Zone,
} from "./types";

export const DOMAIN = "magic_lights";

const ws = <T>(hass: HomeAssistant, command: string, payload: Record<string, unknown> = {}) =>
  hass.callWS<T>({ type: `${DOMAIN}/${command}`, ...payload });

// ---- websocket (admin)

export const fetchConfig = (hass: HomeAssistant): Promise<ConfigDto> => ws<ConfigDto>(hass, "config");

export const saveScene = async (hass: HomeAssistant, scene: StoredScene): Promise<SceneInfo> =>
  (await ws<{ scene: SceneInfo }>(hass, "scene/save", { scene })).scene;

/** Deleting the edit of a preset resets it in every room. */
export const deleteScene = (hass: HomeAssistant, sceneId: string) =>
  ws<Record<string, never>>(hass, "scene/delete", { scene_id: sceneId });

export const saveZone = async (hass: HomeAssistant, zone: Zone): Promise<Zone> =>
  (await ws<{ zone: Zone }>(hass, "zone/save", { zone })).zone;

export const deleteZone = (hass: HomeAssistant, zoneId: string) =>
  ws<Record<string, never>>(hass, "zone/delete", { zone_id: zoneId });

export const saveTargetOptions = async (
  hass: HomeAssistant,
  targetId: TargetId,
  options: Partial<TargetOptions>,
): Promise<TargetInfo> =>
  (await ws<{ target: TargetInfo }>(hass, "target/options", { target_id: targetId, options }))
    .target;

export const saveSettings = (hass: HomeAssistant, settings: Partial<Settings>) =>
  ws<Record<string, never>>(hass, "settings/save", { settings });

export const fetchPreview = async (
  hass: HomeAssistant,
  request: PreviewRequest,
): Promise<PreviewFrame[]> =>
  (await ws<{ frames: PreviewFrame[] }>(hass, "preview", { ...request })).frames;

export const tryScene = (hass: HomeAssistant, targetId: TargetId, scene: StoredScene, adjust?: Adjust) =>
  ws<Record<string, never>>(hass, "try", { target_id: targetId, scene, ...(adjust ? { adjust } : {}) });

export const stopTry = (hass: HomeAssistant, targetId: TargetId) =>
  ws<Record<string, never>>(hass, "try/stop", { target_id: targetId });

/** Manual lights of the target (or one of them) back into their activation. */
export const reclaimLights = (hass: HomeAssistant, targetId: TargetId, entityId?: string) =>
  ws<Record<string, never>>(hass, "reclaim", {
    target_id: targetId,
    ...(entityId ? { entity_id: entityId } : {}),
  });

export const reshuffle = (hass: HomeAssistant, targetId: TargetId) =>
  ws<Record<string, never>>(hass, "reshuffle", { target_id: targetId });

/** Announces config/targets/effects changes; not admin-only. */
export const subscribeChanges = (
  hass: HomeAssistant,
  callback: (event: SubscribeEvent) => void,
): Promise<Unsubscribe> =>
  hass.connection.subscribeMessage<SubscribeEvent>(callback, { type: `${DOMAIN}/subscribe` });

// ---- services, targeted at a target's select entity

const service = (
  hass: HomeAssistant,
  selectEntityId: string,
  name: string,
  data: Record<string, unknown> = {},
) => hass.callService(DOMAIN, name, data, { entity_id: selectEntityId });

export interface ActivateOptions {
  brightness?: number;
  dynamic?: boolean;
  transition?: number;
}

/** Scene id or name; activating the running scene reclaims and re-sends it. */
export const activateScene = (
  hass: HomeAssistant,
  selectEntityId: string,
  scene: string,
  options: ActivateOptions = {},
) => service(hass, selectEntityId, "activate_scene", { scene, ...options });

export const turnOff = (hass: HomeAssistant, selectEntityId: string, transition?: number) =>
  service(hass, selectEntityId, "turn_off", transition === undefined ? {} : { transition });

export const adjustBrightness = (
  hass: HomeAssistant,
  selectEntityId: string,
  change: { step: number } | { brightness: number },
) => service(hass, selectEntityId, "adjust_brightness", change);

/** Starts or holds the colour motion of the running scene, own or via, in place. */
export const setDynamic = (hass: HomeAssistant, selectEntityId: string, dynamic: boolean) =>
  service(hass, selectEntityId, "set_dynamic", { dynamic });

/** "Stop scene": the target's own activation ends without sending; its status becomes Manual. */
export const release = (hass: HomeAssistant, selectEntityId: string) =>
  service(hass, selectEntityId, "release");

/** Service variant of reclaimLights for non-admin users (whole target). */
export const reclaim = (hass: HomeAssistant, selectEntityId: string) =>
  service(hass, selectEntityId, "reclaim");
