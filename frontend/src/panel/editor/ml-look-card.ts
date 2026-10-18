// The shared look of a scene: its name and icon, the part being edited, what that part's
// lights do (the effect) and the effect's form. For an effect of the adaptive category
// the form carries the day curve of the preview room. Edits go up as ml-scene-edit.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import type { EffectInfo, HomeAssistant, Layer, PreviewFrame, StoredScene } from "../../shared/types";
import { defineElement, fire } from "../../shared/util";
import "../components/ml-swatch";
import type { MlStore } from "../store";
import { changedParams, effectOrder, layerSwatches, type Problem } from "./editor-model";
import type { PartOption, PartSelect, SceneEdit, TimeSelect } from "./editor-events";
import "./ml-day-curve";
import "./ml-plugin-form";

/** More effects than this read better as a dropdown. */
const MAX_EFFECT_SEGMENTS = 8;

/** What an edited or untouched preset tells the user. */
export interface PresetNote {
  edited: boolean;
  /** Rooms whose menu shows it. */
  rooms: string[];
  /** The preset's own look, one line. */
  original: string;
}

class MlLookCard extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) scene!: StoredScene;

  @property({ type: Number }) selected = 0;

  @property({ attribute: false }) parts: PartOption[] = [];

  /** The preset this scene edits, to mark what changed. */
  @property({ attribute: false }) original?: StoredScene;

  @property({ attribute: false }) preset?: PresetNote;

  /** Under the name: what kind of scene this is. */
  @property() caption = "";

  @property({ attribute: false }) problems: Problem[] = [];

  /** Parts that took over the last edit. */
  @property({ attribute: false }) followed: string[] = [];

  /** A day of the preview room, for an adaptive part. */
  @property({ attribute: false }) day?: { frames: PreviewFrame[]; t: number; lights: string[]; room: string };

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _naming = false;

  @state() private _iconOpen = false;

  /** Params a part had under an effect it was switched away from, by "index:effect". */
  private _stash = new Map<string, Record<string, unknown>>();

  private get _layer(): Layer {
    return this.scene.layers[Math.min(this.selected, this.scene.layers.length - 1)];
  }

  protected render() {
    const layer = this._layer;
    const effect = this.store.effect(layer.effect);
    const original = this.original?.layers[this.selected];
    const changed = changedParams(effect, layer, original);
    const names = this.problems.filter((p) => p.layer === undefined);
    const own = this.problems.filter((p) => p.layer === this.selected);
    return html`<ha-card>
      <div class="card-content">
        <div class="head">
          ${this._renderIdentity()} ${this.narrow ? nothing : this._renderParts()}
        </div>
        ${names.map((p) => html`<p class="problem">${p.message}</p>`)}
        ${this.narrow ? html`<div class="field"><span class="label">Part</span>${this._renderParts()}</div>` : nothing}
        ${
          this.followed.length
            ? html`<p class="hint followed" role="status">
                <ha-icon icon="mdi:link-variant"></ha-icon>
                Also changed in ${this.followed.map((name) => `“${name}”`).join(", ")}
              </p>`
            : nothing
        }
        ${this._renderPresetNote()}
        <div class="field">
          <span class="label">
            What the lights do
            ${original && original.effect !== layer.effect ? html`<span class="dot" title="Changed"></span>` : nothing}
          </span>
          ${this._renderEffects(layer)}
          ${effect?.description ? html`<span class="hint">${effect.description}</span>` : nothing}
        </div>
        ${own.map((p) => html`<ha-alert alert-type="warning">${p.message}</ha-alert>`)}
        ${
          effect
            ? html`<ml-plugin-form
                .hass=${this.hass}
                .schema=${effect.schema}
                .data=${layer.params}
                .defaults=${effect.defaults}
                .palettes=${this.store.config.palettes}
                .changed=${changed}
                @value-changed=${(ev: CustomEvent<{ value: Record<string, unknown> }>) => {
                  ev.stopPropagation();
                  this._setLayer({ ...layer, params: ev.detail.value }, true);
                }}
              >
                ${this._renderDay(effect)}
              </ml-plugin-form>`
            : nothing
        }
      </div>
    </ha-card>
    ${this._renderIconDialog()}`;
  }

  private _renderIdentity() {
    const scene = this.scene;
    const swatches = scene.layers
      .flatMap((l) => layerSwatches(this.store.effect(l.effect), l.params))
      .slice(0, 5);
    const locked = !!scene.preset;
    return html`<div class="identity">
      <button class="swatch" aria-label="Change icon" title="Change icon" @click=${() => (this._iconOpen = true)}>
        <ml-swatch size="56" .swatches=${swatches} .icon=${scene.icon ?? "mdi:palette-outline"}></ml-swatch>
      </button>
      <div class="naming">
        ${
          this._naming
            ? html`<ha-input
                class="name-input"
                label="Name"
                autofocus
                .value=${scene.name}
                @keydown=${(ev: KeyboardEvent) => {
                  if (ev.key === "Enter" || ev.key === "Escape") (ev.target as HTMLElement).blur();
                }}
                @input=${(ev: Event) =>
                  this._edit({ ...scene, name: (ev.target as HTMLInputElement).value ?? "" })}
                @blur=${() => (this._naming = false)}
              ></ha-input>`
            : html`<div class="name-row">
                <h2 class="name">${scene.name || "Unnamed scene"}</h2>
                ${
                  locked
                    ? html`<ha-icon
                        class="lock"
                        icon="mdi:lock-outline"
                        title="A preset keeps its name, so automations that call it keep working."
                      ></ha-icon>`
                    : html`<ha-icon-button .label=${"Rename"} @click=${() => (this._naming = true)}>
                        <ha-icon icon="mdi:pencil"></ha-icon>
                      </ha-icon-button>`
                }
              </div>`
        }
        <span class="hint">${locked ? "Preset · its name stays, for automations" : this.caption}</span>
      </div>
    </div>`;
  }

  private _renderParts() {
    return html`<ha-control-select
      class="parts"
      style=${styleMap({ "--parts": String(this.parts.length) })}
      .options=${this.parts.map((p) => ({ value: String(p.index), label: p.label }))}
      .value=${String(this.selected)}
      .label=${"Part"}
      @value-changed=${(ev: CustomEvent<{ value: string }>) => {
        ev.stopPropagation();
        fire<PartSelect>(this, "ml-part-select", { index: Number(ev.detail.value) });
      }}
    ></ha-control-select>`;
  }

  private _renderPresetNote() {
    const note = this.preset;
    if (!note) return nothing;
    const rooms = note.rooms.length ? ` (${note.rooms.join(", ")})` : "";
    if (!note.edited) {
      return html`<ha-alert alert-type="info">
        <b>Preset.</b> Your changes apply in every room that shows it${rooms}.
      </ha-alert>`;
    }
    return html`<ha-alert alert-type="warning">
      <b>Edited preset.</b> Your values apply in every room that shows it${rooms}.
      <div class="original">
        <span>Original: ${note.original}</span>
        <ha-button appearance="plain" size="s" @click=${() => fire(this, "ml-reset")}>
          <ha-icon slot="start" icon="mdi:restore"></ha-icon>
          Reset to original
        </ha-button>
      </div>
    </ha-alert>`;
  }

  private _renderEffects(layer: Layer) {
    const effects = effectOrder(this.store.config.effects);
    const options = effects.map((e) => ({
      value: e.key,
      label: e.name,
      ariaLabel: e.name,
      icon: html`<ha-icon .icon=${e.icon}></ha-icon>`,
    }));
    if (!this.store.effect(layer.effect)) {
      options.push({
        value: layer.effect,
        label: `${layer.effect} (not installed)`,
        ariaLabel: `${layer.effect} (not installed)`,
        icon: html`<ha-icon icon="mdi:help-circle-outline"></ha-icon>`,
      });
    }
    const changed = (ev: CustomEvent<{ value?: string }>) => {
      ev.stopPropagation();
      if (ev.detail.value) this._switchEffect(ev.detail.value);
    };
    if (options.length > MAX_EFFECT_SEGMENTS) {
      return html`<ha-select
        .label=${"Effect"}
        .options=${options.map(({ value, label }) => ({ value, label }))}
        .value=${layer.effect}
        @selected=${changed}
      ></ha-select>`;
    }
    return html`<ha-control-select
      class="effects"
      .options=${options}
      .value=${layer.effect}
      .label=${"What the lights do"}
      ?hide-option-label=${this.narrow}
      @value-changed=${changed}
    ></ha-control-select>`;
  }

  private _renderDay(effect: EffectInfo) {
    const day = this.day;
    if (effect.category !== "adaptive" || !day?.frames.length) return nothing;
    return html`<ml-day-curve
      slot="preview"
      .frames=${day.frames}
      .t=${day.t}
      .lights=${day.lights}
      .label=${this.narrow ? "Today" : `Today in ${day.room}`}
      .hint=${this.narrow ? "" : "drag to scrub, the bulbs follow"}
      @value-changed=${(ev: CustomEvent<{ value: number }>) => {
        ev.stopPropagation();
        fire<TimeSelect>(this, "ml-time", { t: ev.detail.value });
      }}
    ></ml-day-curve>`;
  }

  private _renderIconDialog() {
    return html`<ha-dialog
      .open=${this._iconOpen}
      width="small"
      header-title="Scene icon"
      @closed=${(ev: Event) => {
        if (ev.target === ev.currentTarget) this._iconOpen = false;
      }}
    >
      ${
        this._iconOpen
          ? html`<ha-icon-picker
              .hass=${this.hass}
              .label=${"Icon"}
              .value=${this.scene.icon ?? undefined}
              @value-changed=${(ev: CustomEvent<{ value?: string }>) =>
                this._edit({ ...this.scene, icon: ev.detail.value || null })}
            ></ha-icon-picker>`
          : nothing
      }
      <ha-dialog-footer slot="footer">
        <ha-button slot="primaryAction" data-dialog="close">Done</ha-button>
      </ha-dialog-footer>
    </ha-dialog>`;
  }

  private _switchEffect(key: string): void {
    const layer = this._layer;
    const effect = this.store.effect(key);
    if (!effect || key === layer.effect) return;
    this._stash.set(`${this.selected}:${layer.effect}`, layer.params);
    const params = this._stash.get(`${this.selected}:${key}`) ?? structuredClone(effect.defaults);
    this._setLayer({ ...layer, effect: key, params }, false);
  }

  private _setLayer(layer: Layer, follow: boolean): void {
    const layers = this.scene.layers.map((l, i) => (i === this.selected ? layer : l));
    this._edit({ ...this.scene, layers }, follow);
  }

  private _edit(scene: StoredScene, follow = false): void {
    fire<SceneEdit>(this, "ml-scene-edit", { scene, follow });
  }

  static styles = css`
    :host {
      display: block;
    }
    ha-card {
      display: block;
    }
    .card-content {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-5);
    }
    .head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--ha-space-4);
    }
    .identity {
      display: flex;
      align-items: center;
      gap: var(--ha-space-3);
      min-width: 0;
      flex: 1;
    }
    .swatch {
      flex: none;
      display: flex;
      padding: 4px;
      border: none;
      border-radius: 50%;
      background: none;
      cursor: pointer;
    }
    .swatch:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
    }
    .naming {
      display: flex;
      flex-direction: column;
      min-width: 0;
      flex: 1;
    }
    .name-row {
      display: flex;
      align-items: center;
      gap: var(--ha-space-1);
      min-height: 48px;
    }
    .name {
      margin: 0;
      font-size: var(--ha-font-size-xl, 22px);
      font-weight: var(--ha-font-weight-normal);
      line-height: var(--ha-line-height-condensed);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .lock {
      flex: none;
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
    }
    ha-icon-button {
      flex: none;
      color: var(--secondary-text-color);
    }
    .name-input {
      --ha-input-padding-bottom: 0;
    }
    .head .parts {
      flex: 0 1 calc(var(--parts) * 150px);
      min-width: 0;
    }
    ha-control-select {
      --control-select-thickness: 52px;
      --control-select-border-radius: var(--ha-border-radius-lg, 12px);
    }
    /* Two label lines under the icon. */
    ha-control-select.effects {
      --control-select-thickness: 72px;
      --mdc-icon-size: 22px;
    }
    :host([narrow]) ha-control-select.effects {
      --control-select-thickness: 52px;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
    }
    .label {
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      font-weight: var(--ha-font-weight-medium);
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--primary-color);
    }
    .hint {
      margin: 0;
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .followed {
      margin-top: calc(-1 * var(--ha-space-3));
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      --mdc-icon-size: 16px;
    }
    .problem {
      margin: calc(-1 * var(--ha-space-3)) 0 0;
      font-size: var(--ha-font-size-s);
      color: var(--error-color);
    }
    .original {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: var(--ha-space-2);
      font-size: var(--ha-font-size-s);
    }
    ha-alert {
      display: block;
    }
    ha-select {
      display: block;
    }
    ha-icon-picker {
      display: block;
    }
  `;
}

defineElement("ml-look-card", MlLookCard);

