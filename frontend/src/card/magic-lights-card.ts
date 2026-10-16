// custom:magic-lights-card: status, Reclaim, scene grid and controls of one target, read from
// its select's attributes. Uses only elements dashboards always define (ha-card, ha-icon, ha-state-icon).

import { LitElement, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import * as api from "../shared/api";
import { readableTint, rgbToHex, swatchBackground, type Rgb } from "../shared/color";
import { activationOptions, isPlayable, toggleMotion } from "../shared/scene-actions";
import {
  activeScene,
  activeSwatches,
  isMoving,
  isOn,
  runningScene,
  statusOf,
  type TargetStatusView,
} from "../shared/status";
import type { HomeAssistant, LightStatus, SelectAttributes, StatusScene } from "../shared/types";
import { fire, showMoreInfo } from "../shared/util";
import { CONFIG_FORM } from "./config-form";
import {
  type CardConfig,
  cardScenes,
  cardSize,
  firstMagicSelect,
  headerLine,
  type Lookup,
  lookup,
  parseConfig,
  reclaimable,
  targetName,
} from "./model";
import { cardStyles } from "./styles";

const LIGHT_STATUS: Record<LightStatus, string> = {
  scene: "Scene",
  manual: "Manual",
  elsewhere: "Elsewhere",
  off: "Off",
  on: "On",
  unavailable: "Unavailable",
};

const BRIGHTNESS_STEP = 10;

export class MagicLightsCard extends LitElement {
  @property({ attribute: false }) hass?: HomeAssistant;

  @state() private _config?: CardConfig;

  static getConfigForm() {
    return CONFIG_FORM;
  }

  static getStubConfig(hass: HomeAssistant) {
    return { entity: firstMagicSelect(hass) ?? "" };
  }

  setConfig(config: Record<string, unknown>): void {
    this._config = parseConfig(config);
  }

  getCardSize(): number {
    if (!this._config) return 1;
    return cardSize(this._config, statusOf(this.hass, this._config.entity));
  }

  // Content height depends on the scene count, so the sections view sizes the card to it.
  getGridOptions() {
    return { columns: 12, rows: "auto" as const, min_columns: 6, min_rows: 2 };
  }

  protected shouldUpdate(changed: PropertyValues): boolean {
    const old = changed.get("hass") as HomeAssistant | undefined;
    if (changed.has("_config") || !old || !this.hass || !this._config) return true;
    const hass = this.hass;
    const entity = this._config.entity;
    if (
      old.states[entity] !== hass.states[entity] ||
      old.devices !== hass.devices ||
      old.themes !== hass.themes
    ) {
      return true;
    }
    if (!this._config.show_lights) return false;
    const lights = (hass.states[entity]?.attributes as Partial<SelectAttributes> | undefined)?.lights;
    return Object.keys(lights ?? {}).some((e) => old.states[e] !== hass.states[e]);
  }

  protected render() {
    const config = this._config;
    const hass = this.hass;
    if (!config || !hass) return nothing;
    const stateObj = hass.states[config.entity];
    if (!stateObj) return this._renderWarning(`Entity not available: ${config.entity}`);
    const view = statusOf(hass, config.entity);
    if (!view) return this._renderWarning(`${config.entity} is not a Magic Lights select`);

    const names = lookup(hass);
    const attrs = view.attrs;
    const manual = reclaimable(view);
    const on = isOn(view);
    const tint = on ? readableTint(activeSwatches(view, names.swatchesOf), hass.themes?.darkMode) : undefined;
    const name = config.name ?? targetName(hass, config.entity) ?? config.entity;

    return html`
      <ha-card>
        <button class="header" @click=${this._moreInfo}>
          <span class="icon ${on ? "on" : ""}" style=${styleMap(tint ? { "--tint": tint } : {})}>
            <ha-state-icon .stateObj=${stateObj}></ha-state-icon>
          </span>
          <span class="info">
            <span class="name">${name}</span>
            <span class="status">${headerLine(view, names)}</span>
          </span>
        </button>
        <div class="body">
          ${manual.length ? this._renderManual(manual.length) : nothing}
          ${config.show_scenes && attrs ? this._renderScenes(attrs, config) : nothing}
          ${config.show_lights && attrs ? this._renderLights(attrs) : nothing}
          ${config.show_controls ? this._renderControls(view, names) : nothing}
        </div>
      </ha-card>
    `;
  }

  private _renderWarning(message: string) {
    return html`<ha-card><div class="warning">${message}</div></ha-card>`;
  }

  private _renderManual(count: number) {
    return html`<div class="manual" role="status">
      <ha-icon icon="mdi:hand-back-right-outline"></ha-icon>
      <span class="manual-text">
        ${count} <span class="wide">${count === 1 ? "light" : "lights"}</span> manual
      </span>
      <button class="reclaim" @click=${() => this._call(api.reclaim(this.hass!, this._config!.entity))}>
        Reclaim
      </button>
    </div>`;
  }

  private _renderScenes(attrs: SelectAttributes, config: CardConfig) {
    const { scenes, missing } = cardScenes(attrs, config.scenes);
    return html`
      ${
        missing.length
          ? html`<div class="warning missing">No such scene here: ${missing.join(", ")}</div>`
          : nothing
      }
      ${
        scenes.length
          ? html`<div class="scenes" style=${styleMap({ "--columns": String(config.columns) })}>
              ${scenes.map((scene) => this._renderScene(attrs, scene))}
            </div>`
          : nothing
      }
    `;
  }

  private _renderScene(attrs: SelectAttributes, scene: StatusScene) {
    const active = activeScene(attrs) === scene.id;
    const playing = runningScene(attrs) === scene.id && isMoving(attrs);
    const playable = isPlayable(scene);
    return html`<div class="scene ${active ? "active" : ""} ${playable ? "playable" : ""}">
      <button
        class="scene-main"
        title=${scene.name}
        aria-pressed=${active ? "true" : "false"}
        @click=${() => this._activate(scene.id, activationOptions(attrs, scene.id, scene))}
      >
        <span
          class="swatch ${playing && scene.swatches.length > 1 ? "moving" : ""}"
          style=${styleMap({ "--swatch": swatchBackground(scene.swatches) })}
        ></span>
        <span class="scene-name">${scene.name}</span>
      </button>
      ${
        playable
          ? html`<button
              class="play"
              aria-label=${playing ? `Hold colours of ${scene.name}` : `Play ${scene.name}`}
              title=${playing ? "Hold colours" : "Play colours"}
              @click=${() => this._toggleMotion(attrs, scene.id)}
            >
              <ha-icon .icon=${playing ? "mdi:pause" : "mdi:play"}></ha-icon>
            </button>`
          : nothing
      }
    </div>`;
  }

  private _renderLights(attrs: SelectAttributes) {
    const lights = Object.entries(attrs.lights ?? {});
    if (!lights.length) return nothing;
    return html`<div class="lights">
      ${lights.map(([entityId, status]) => {
        const light = this.hass!.states[entityId];
        const rgb = light?.attributes.rgb_color as Rgb | undefined;
        const tint = rgb && readableTint([rgbToHex(rgb)], this.hass!.themes?.darkMode);
        return html`<button
          class="light is-${status} ${light?.state === "on" ? "on" : ""}"
          @click=${() => showMoreInfo(this, entityId)}
        >
          ${
            light
              ? html`<ha-state-icon
                  .stateObj=${light}
                  style=${styleMap(tint ? { color: tint } : {})}
                ></ha-state-icon>`
              : nothing
          }
          <span class="light-name">${light?.attributes.friendly_name ?? entityId}</span>
          <span class="light-status">${LIGHT_STATUS[status] ?? status}</span>
        </button>`;
      })}
    </div>`;
  }

  private _renderControls(view: TargetStatusView, names: Lookup) {
    const attrs = view.attrs;
    const status = attrs?.status;
    const runningId = runningScene(attrs);
    const running = attrs && runningId ? names.sceneOf(runningId, attrs.via ?? attrs.target_id) : undefined;
    const playing = isMoving(attrs);
    const brightness = runningId ? attrs?.brightness : null;
    return html`<div class="controls">
      <button
        class="control"
        .disabled=${!attrs || status === "off"}
        @click=${() => this._call(api.turnOff(this.hass!, this._config!.entity))}
      >
        <ha-icon icon="mdi:power"></ha-icon><span class="label">Off</span>
      </button>
      <button
        class="control"
        title="Stop the scene and leave the lights as they are"
        .disabled=${status !== "scene" && status !== "mixed"}
        @click=${() => this._call(api.release(this.hass!, this._config!.entity))}
      >
        <ha-icon icon="mdi:hand-back-right-outline"></ha-icon><span class="label">Stop scene</span>
      </button>
      ${
        attrs && running && isPlayable(running)
          ? html`<button
              class="control"
              title=${playing ? "Hold colours" : "Play colours"}
              @click=${() => this._toggleMotion(attrs, running.id)}
            >
              <ha-icon .icon=${playing ? "mdi:pause" : "mdi:play"}></ha-icon>
              <span class="label">${playing ? "Hold" : "Play"}</span>
            </button>`
          : nothing
      }
      <div class="brightness" role="group" aria-label="Brightness">
        <button
          class="step"
          aria-label="Dimmer"
          .disabled=${brightness == null || brightness <= 1}
          @click=${() => this._brightness(-BRIGHTNESS_STEP)}
        >
          <ha-icon icon="mdi:minus"></ha-icon>
        </button>
        <span class="value">${brightness == null ? "–" : `${brightness}%`}</span>
        <button
          class="step"
          aria-label="Brighter"
          .disabled=${brightness == null || brightness >= 100}
          @click=${() => this._brightness(BRIGHTNESS_STEP)}
        >
          <ha-icon icon="mdi:plus"></ha-icon>
        </button>
      </div>
    </div>`;
  }

  private _activate(sceneId: string, options: api.ActivateOptions): void {
    this._call(api.activateScene(this.hass!, this._config!.entity, sceneId, options));
  }

  private _toggleMotion(attrs: SelectAttributes, sceneId: string): void {
    this._call(toggleMotion(this.hass!, this._config!.entity, attrs, sceneId));
  }

  private _brightness(step: number): void {
    this._call(api.adjustBrightness(this.hass!, this._config!.entity, { step }));
  }

  /** hass.callService already reports failures as a toast. */
  private _call(action: Promise<unknown>): void {
    fire(this, "haptic", "light");
    action.catch(() => undefined);
  }

  private _moreInfo(): void {
    showMoreInfo(this, this._config!.entity);
  }

  static styles = cardStyles;
}
