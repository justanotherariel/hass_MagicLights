// What a target looks like right now: shared by the home tile and the target page header.

import { html, type TemplateResult } from "lit";
import { styleMap } from "lit/directives/style-map.js";
import { hexAlpha, readableTint, swatchBackground } from "../shared/color";
import { isPlayable } from "../shared/scene-actions";
import {
  activeScene,
  activeSwatches,
  isMoving,
  isOn,
  runningScene,
  statusLine,
  type TargetStatusView,
} from "../shared/status";
import type { HomeAssistant, SceneInfo, TargetInfo } from "../shared/types";
import type { MlStore } from "./store";

export interface TargetVisuals {
  view: TargetStatusView | undefined;
  available: boolean;
  line: string;
  swatches: string[];
  on: boolean;
  /** Lights an activation gave up; Reclaim brings them back. */
  manual: string[];
  /** The scene running here, own or via another target, resolved. */
  running: SceneInfo | undefined;
  /** Menu id of the running scene when it stands for the whole target (activeScene). */
  active: string | null;
  /** ▶ starts or holds the running scene's colours. */
  playable: boolean;
  moving: boolean;
  /** Some layer animates. */
  dynamic: boolean;
}

export const targetVisuals = (
  store: MlStore,
  hass: HomeAssistant,
  target: TargetInfo,
): TargetVisuals => {
  const view = store.status(hass, target.id);
  const attrs = view?.attrs;
  const sceneId = runningScene(attrs);
  const running = sceneId ? store.resolveScene(attrs?.via ?? target.id, sceneId) : undefined;
  return {
    view,
    available: !!view?.available,
    line: statusLine(view, store.statusNames),
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

/** Round icon tinted with the running scene's first colour that stays visible on the theme's cards. */
export const renderTargetIcon = (
  hass: HomeAssistant,
  target: TargetInfo,
  visuals: TargetVisuals,
): TemplateResult => {
  const tint = visuals.on ? readableTint(visuals.swatches, hass.themes?.darkMode) : undefined;
  const style = tint ? { color: tint, backgroundColor: hexAlpha(tint, 0.2) } : {};
  return html`<span class="target-icon ${visuals.on ? "on" : ""}" style=${styleMap(style)}>
    <ha-icon .icon=${targetIcon(hass, target, visuals.on)}></ha-icon>
  </span>`;
};

export const renderStrip = (visuals: TargetVisuals): TemplateResult => {
  const moving = visuals.dynamic && visuals.swatches.length > 1;
  const style = visuals.on && visuals.swatches.length ? { "--strip": swatchBackground(visuals.swatches) } : {};
  return html`<div class="strip ${moving ? "moving" : ""}" style=${styleMap(style)}></div>`;
};
