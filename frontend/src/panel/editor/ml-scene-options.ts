// The scene card: the scene's name and icon, its fade and what its lights in no part do.
// Edits go up as ml-scene-edit.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import type { HomeAssistant, StoredScene } from "../../shared/types";
import { defineElement, fire } from "../../shared/util";
import "../components/ml-fade-select";
import "../components/ml-swatch";
import { fadeLabel } from "../fade";
import { SCENE_ICON, type MlStore } from "../store";
import type { Problem } from "./editor-model";
import type { SceneEdit } from "./editor-events";

class MlSceneOptions extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) scene!: StoredScene;

  /** Colours of its parts in this room. */
  @property({ attribute: false }) swatches: readonly string[] = [];

  /** Under the name: where the scene is. */
  @property() caption = "";

  /** The scene's own problems (its name). */
  @property({ attribute: false }) problems: Problem[] = [];

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _naming = false;

  @state() private _iconOpen = false;

  protected render() {
    const scene = this.scene;
    const fade = this.store.config.settings.transition;
    return html`<ha-card>
        <div class="card-content">
          ${this._renderIdentity()}
          ${this.problems.map((p) => html`<p class="problem">${p.message}</p>`)}
          <div class="field">
            <span class="label">Fade <span class="hint">for this scene</span></span>
            <ml-fade-select
              .value=${scene.transition}
              .fallback=${fade}
              @value-changed=${(ev: CustomEvent<{ value: number | null }>) => {
                ev.stopPropagation();
                this._edit({ ...scene, transition: ev.detail.value });
              }}
            ></ml-fade-select>
            <span class="hint">Default follows the Magic Lights settings: ${fadeLabel(fade)}.</span>
          </div>
          <div class="field">
            <span class="label">Lights in no part</span>
            <ha-control-select
              .options=${[
                { value: "off", label: "Turn off" },
                { value: "keep", label: "Leave as they are" },
              ]}
              .value=${scene.others}
              .label=${"Lights in no part"}
              @value-changed=${(ev: CustomEvent<{ value: StoredScene["others"] }>) => {
                ev.stopPropagation();
                this._edit({ ...scene, others: ev.detail.value });
              }}
            ></ha-control-select>
          </div>
        </div>
      </ha-card>
      ${this._renderIconDialog()}`;
  }

  private _renderIdentity() {
    const scene = this.scene;
    return html`<div class="identity">
      <button class="swatch" aria-label="Change icon" title="Change icon" @click=${() => (this._iconOpen = true)}>
        <ml-swatch size="56" .swatches=${this.swatches} .icon=${scene.icon ?? SCENE_ICON}></ml-swatch>
      </button>
      <div class="naming">
        ${
          this._naming
            ? html`<ha-input
                class="name-input"
                label="Scene name"
                autofocus
                .value=${scene.name}
                @keydown=${(ev: KeyboardEvent) => {
                  if (ev.key === "Enter" || ev.key === "Escape") (ev.target as HTMLElement).blur();
                }}
                @input=${(ev: Event) => this._edit({ ...scene, name: (ev.target as HTMLInputElement).value ?? "" })}
                @blur=${() => (this._naming = false)}
              ></ha-input>`
            : html`<div class="name-row">
                <h2 class="name">${scene.name || "Unnamed scene"}</h2>
                <ha-icon-button .label=${"Rename scene"} @click=${() => (this._naming = true)}>
                  <ha-icon icon="mdi:pencil"></ha-icon>
                </ha-icon-button>
              </div>`
        }
        <span class="hint">${this.caption}</span>
      </div>
    </div>`;
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

  private _edit(scene: StoredScene): void {
    fire<SceneEdit>(this, "ml-scene-edit", { scene });
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
    .identity {
      display: flex;
      align-items: center;
      gap: var(--ha-space-3);
      min-width: 0;
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
      min-width: 0;
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
    ha-icon-button {
      flex: none;
      color: var(--secondary-text-color);
    }
    .name-input {
      --ha-input-padding-bottom: 0;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
    }
    .label {
      display: flex;
      align-items: baseline;
      flex-wrap: wrap;
      gap: var(--ha-space-2);
      font-weight: var(--ha-font-weight-medium);
    }
    .hint {
      font-size: var(--ha-font-size-s);
      font-weight: var(--ha-font-weight-normal);
      color: var(--secondary-text-color);
    }
    .problem {
      margin: calc(-1 * var(--ha-space-3)) 0 0;
      font-size: var(--ha-font-size-s);
      color: var(--error-color);
    }
    ha-control-select {
      --control-select-thickness: 48px;
      --control-select-border-radius: var(--ha-border-radius-lg, 12px);
    }
    ha-icon-picker {
      display: block;
    }
  `;
}

defineElement("ml-scene-options", MlSceneOptions);
