// What a target looks like right now: shared by the home tile and the room page's hero.

import { html, type TemplateResult } from "lit";
import { styleMap } from "lit/directives/style-map.js";
import { swatchBackground } from "../shared/color";
import { isPlayable } from "../shared/scene-actions";
import { activeScene, activeSwatches, isMoving, isOn, runningScene, type TargetStatusView } from "../shared/status";
import type { HomeAssistant, SceneInfo, TargetInfo } from "../shared/types";
import "./components/ml-swatch";
import type { SwatchSize } from "./components/ml-swatch";
import { roomLine } from "./room-facts";
import type { MlStore } from "./store";

export interface TargetVisuals {
  view: TargetStatusView | undefined;
  available: boolean;
  /** "Hell · 80% · 4 lights". */
  line: string;
  swatches: string[];
  on: boolean;
  /** Lights an activation gave up; Reclaim brings them back. */
  manual: string[];
  /** The scene running here, own or via another target, resolved. */
  running: SceneInfo | undefined;
  /** Menu id of the running scene when it stands for the whole target (activeScene). */
  active: string | null;
  /** Hold / Play switches the running scene's colours. */
  playable: boolean;
  moving: boolean;
  /** Some layer animates. */
  dynamic: boolean;
}

export const targetVisuals = (store: MlStore, hass: HomeAssistant, target: TargetInfo): TargetVisuals => {
  const view = store.status(hass, target.id);
  const attrs = view?.attrs;
  const sceneId = runningScene(attrs);
  const running = sceneId ? store.resolveScene(sceneId) : undefined;
  return {
    view,
    available: !!view?.available,
    line: roomLine(view, store.statusNames, target.lights.length),
    swatches: activeSwatches(view, store.swatchesOf),
    on: isOn(view),
    manual: attrs?.manual ?? [],
    running,
    active: activeScene(attrs),
    playable: isPlayable(running),
    moving: isMoving(attrs),
    dynamic: !!attrs?.dynamic,
  };
};

/** The zone's or area's own icon, else a bulb (HA's area fallback reads as a blank tile here). */
export const targetIcon = (hass: HomeAssistant, target: TargetInfo, on = false): string => {
  const bulb = target.kind === "zone" ? "mdi:lightbulb-group" : "mdi:lightbulb";
  return (
    target.icon ??
    (target.area_id ? hass.areas?.[target.area_id]?.icon : undefined) ??
    (on ? bulb : `${bulb}-outline`)
  );
};

/** The room's icon on an orb glowing in the running scene's colours; grey while it is off. */
export const renderTargetSwatch = (
  hass: HomeAssistant,
  target: TargetInfo,
  visuals: TargetVisuals,
  size: SwatchSize = 48,
): TemplateResult =>
  html`<ml-swatch
    size=${size}
    .swatches=${visuals.swatches}
    .icon=${targetIcon(hass, target, visuals.on)}
    ?off=${!visuals.on || !visuals.swatches.length}
  ></ml-swatch>`;

export const renderStrip = (visuals: TargetVisuals): TemplateResult => {
  const moving = visuals.dynamic && visuals.swatches.length > 1;
  const style = visuals.on && visuals.swatches.length ? { "--strip": swatchBackground(visuals.swatches) } : {};
  return html`<div class="strip ${moving ? "moving" : ""}" style=${styleMap(style)}></div>`;
};

/**
 * The colour temperature a scene that follows the sun shows now: the first of its lights
 * in white mode. Undefined for other scenes, whose whites do not change.
 */
export const kelvinNow = (store: MlStore, hass: HomeAssistant, visuals: TargetVisuals): number | undefined => {
  const effect = visuals.running?.layers[0] && store.effect(visuals.running.layers[0].effect);
  const lights = visuals.view?.attrs?.lights;
  if (effect?.category !== "adaptive" || !lights) return undefined;
  for (const [entityId, status] of Object.entries(lights)) {
    const attrs = status === "scene" ? hass.states[entityId]?.attributes : undefined;
    if (attrs?.color_mode === "color_temp" && typeof attrs.color_temp_kelvin === "number") {
      return Math.round(attrs.color_temp_kelvin / 50) * 50;
    }
  }
  return undefined;
};
