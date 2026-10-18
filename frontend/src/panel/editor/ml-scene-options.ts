// The scene's own settings beside its look: its fade, its overall brightness and the
// rooms whose menu shows it. Menus belong to the rooms, not to the scene's draft, so a
// chip writes them at once (as the Scenes list does); a scene that was never saved has
// no menus yet and gets them on its first save, in every room it is set up in.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import type { StoredScene, TargetInfo } from "../../shared/types";
import { defineElement, fire, plural, toastOnError } from "../../shared/util";
import "../components/ml-fade-select";
import "../components/ml-value-bar";
import { fadeLabel } from "../fade";
import { menuId, menuIds, type MlStore } from "../store";
import type { SceneEdit } from "./editor-events";
import { expandLights } from "./editor-model";

class MlSceneOptions extends LitElement {
  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) scene!: StoredScene;

  /** The scene is stored, so menus can hold it. */
  @property({ type: Boolean }) stored = false;

  /** The preset this scene edits, to mark what changed. */
  @property({ attribute: false }) original?: StoredScene;

  @property({ type: Boolean, reflect: true }) narrow = false;

  protected render() {
    const scene = this.scene;
    const original = this.original;
    return html`<ha-card>
      <div class="card-content">
        <div class="field">
          <span class="label">
            Fade <span class="hint">for this scene</span>
            ${original && original.transition !== scene.transition ? html`<span class="dot"></span>` : nothing}
          </span>
          <ml-fade-select
            .value=${scene.transition}
            .fallback=${this.store.config.settings.transition}
            @value-changed=${(ev: CustomEvent<{ value: number | null }>) => {
              ev.stopPropagation();
              this._edit({ ...scene, transition: ev.detail.value });
            }}
          ></ml-fade-select>
          <span class="hint">Default follows the Magic Lights settings: ${fadeLabel(this.store.config.settings.transition)}.</span>
        </div>
        <div class="field">
          <span class="label">
            Overall brightness <span class="hint">scales every part</span>
            ${original && original.brightness !== scene.brightness ? html`<span class="dot"></span>` : nothing}
          </span>
          <ml-value-bar
            size="compact"
            .value=${scene.brightness}
            .min=${1}
            .max=${100}
            unit="%"
            .label=${"Overall brightness"}
            @value-changed=${(ev: CustomEvent<{ value: number }>) => {
              ev.stopPropagation();
              this._edit({ ...scene, brightness: Math.round(ev.detail.value) });
            }}
          ></ml-value-bar>
        </div>
        ${this._renderShown()}
      </div>
    </ha-card>`;
  }

  private _renderShown() {
    const id = menuId(this.scene);
    const shown = this.store.targets.filter((t) => menuIds(t).includes(id));
    const others = this.store.targets.filter((t) => !shown.includes(t));
    const rooms = shown.filter((t) => t.kind === "room").length;
    if (!this.stored && !this.scene.preset) {
      return html`<div class="field">
        <span class="label">Shown in</span>
        <span class="hint">Once saved, the scene shows in every room it is set up in.</span>
      </div>`;
    }
    return html`<div class="field">
      <span class="label">Shown in <span class="hint">${plural(rooms, "room")}</span></span>
      <div class="chips">
        ${shown.map(
          (t) => html`<ha-filter-chip
            selected
            .label=${t.name}
            title=${`Shown in ${t.name}; tap to hide it there`}
            @click=${(ev: Event) => {
              ev.preventDefault();
              this._show(t, false);
            }}
          ></ha-filter-chip>`,
        )}
        ${
          others.length
            ? html`<ha-dropdown
                placement="bottom-start"
                @wa-select=${(ev: CustomEvent<{ item: { value: string } }>) => {
                  const target = this.store.target(ev.detail.item.value);
                  if (target) this._show(target, true);
                }}
              >
                <ha-button slot="trigger" appearance="outlined" size="s">
                  <ha-icon slot="start" icon="mdi:plus"></ha-icon>
                  Add
                </ha-button>
                ${others.map(
                  (t) => html`<ha-dropdown-item value=${t.id}>
                    ${t.name}${this._fits(t) ? "" : " · needs setting up"}
                  </ha-dropdown-item>`,
                )}
              </ha-dropdown>`
            : nothing
        }
      </div>
    </div>`;
  }

  private _fits(target: TargetInfo): boolean {
    const roomLights = (id: string) => this.store.target(id)?.lights;
    return this.scene.layers.some((l) => expandLights(l.lights, target, roomLights).length > 0);
  }

  private _show(target: TargetInfo, shown: boolean): void {
    toastOnError(
      this,
      this.store.setInMenus(menuId(this.scene), [target.id], shown),
      shown ? `Could not add it to ${target.name}` : `Could not hide it in ${target.name}`,
    );
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
    .dot {
      align-self: center;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--primary-color);
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--ha-space-2);
    }
    /* 44 px touch targets. */
    ha-filter-chip {
      --ha-button-height: 44px;
      --md-filter-chip-selected-container-color: rgba(var(--rgb-primary-color), 0.15);
    }
  `;
}

defineElement("ml-scene-options", MlSceneOptions);
