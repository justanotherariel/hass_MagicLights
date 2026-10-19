// Home tile: a swatch glowing in what runs, the live facts, a strip, the menu's scenes as
// chips (the active one first) and the target menu. An empty menu offers "New scene".

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { swatchBackground } from "../shared/color";
import type { HomeAssistant, SceneInfo, TargetInfo } from "../shared/types";
import { defineElement, plural, toastOnError } from "../shared/util";
import { go, targetPath } from "./routes";
import type { MlStore } from "./store";
import "./ml-part-picker";
import { chipStyles, dotStyles, tileStyles } from "./styles";
import { renderStrip, renderTargetSwatch, targetVisuals, type TargetVisuals } from "./target-ui";

const CHIPS = 4;

class MlTargetTile extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) target!: TargetInfo;

  @state() private _picking = false;

  protected render() {
    const target = this.target;
    const visuals = targetVisuals(this.store, this.hass, target);
    const menu = this.store.scenesOf(target.id).filter((scene) => scene.fits);
    const first = menu.find((scene) => scene.id === visuals.active);
    const chips = first ? [first, ...menu.filter((scene) => scene !== first)] : menu;
    return html`
      <ha-card>
        <div class="header">
          <button class="plain open" @click=${this._open}>
            ${renderTargetSwatch(this.hass, target, visuals)}
            <span class="info">
              <span class="name">${target.name}</span>
              <span class="status">${visuals.line}</span>
            </span>
          </button>
          ${this._renderMotion(visuals)} ${this._renderMenu(visuals)}
        </div>
        ${renderStrip(visuals)}
        <div class="chips">
          ${chips
            .slice(0, CHIPS)
            .map((scene) => this._renderChip(scene, scene.id === visuals.active, visuals.available))}
          ${
            chips.length > CHIPS
              ? html`<ha-assist-chip
                  class="more"
                  .label=${`+${chips.length - CHIPS}`}
                  aria-label=${`${chips.length - CHIPS} more scenes in ${target.name}`}
                  @click=${this._open}
                ></ha-assist-chip>`
              : nothing
          }
          ${
            chips.length
              ? nothing
              : html`<button class="add" @click=${() => (this._picking = true)}>
                  <ha-icon icon="mdi:plus"></ha-icon>
                  New scene
                </button>`
          }
        </div>
      </ha-card>
      ${
        this._picking
          ? html`<ml-part-picker
              .hass=${this.hass}
              .store=${this.store}
              .targetId=${target.id}
              mode="add"
              @ml-dialog-closed=${() => (this._picking = false)}
            ></ml-part-picker>`
          : nothing
      }
    `;
  }

  private _renderChip(scene: SceneInfo, active: boolean, available: boolean) {
    return html`<ha-assist-chip
      filled
      class=${active ? "active" : ""}
      .active=${active}
      .disabled=${!available}
      .label=${scene.name}
      @click=${() => this._activate(scene.id)}
    >
      <span slot="icon" class="dot" style="background: ${swatchBackground(scene.swatches)}"></span>
    </ha-assist-chip>`;
  }

  private _renderMotion(visuals: TargetVisuals) {
    const running = visuals.running;
    if (!visuals.playable || !running) return nothing;
    return html`<ha-icon-button
      .label=${visuals.moving ? "Hold colours" : "Play colours"}
      @click=${() => this._toggleMotion(running.id)}
    >
      <ha-icon .icon=${visuals.moving ? "mdi:pause" : "mdi:play"}></ha-icon>
    </ha-icon-button>`;
  }

  private _renderMenu(visuals: TargetVisuals) {
    const own = visuals.view?.attrs?.status === "scene" && !visuals.view.attrs.via;
    return html`<ha-dropdown placement="bottom-end" @wa-select=${this._menuSelect}>
      <ha-icon-button slot="trigger" .label=${`${this.target.name} options`}>
        <ha-icon icon="mdi:dots-vertical"></ha-icon>
      </ha-icon-button>
      <ha-dropdown-item value="off" .disabled=${!visuals.on}>
        <ha-icon slot="icon" icon="mdi:power"></ha-icon>
        Turn off
      </ha-dropdown-item>
      <ha-dropdown-item value="release" .disabled=${!own}>
        <ha-icon slot="icon" icon="mdi:hand-back-right-outline"></ha-icon>
        Stop scene
      </ha-dropdown-item>
      ${
        visuals.manual.length
          ? html`<ha-dropdown-item value="reclaim">
              <ha-icon slot="icon" icon="mdi:backup-restore"></ha-icon>
              Reclaim ${plural(visuals.manual.length, "light")}
            </ha-dropdown-item>`
          : nothing
      }
      <ha-dropdown-item value="open">
        <ha-icon slot="icon" icon="mdi:arrow-right"></ha-icon>
        Open
      </ha-dropdown-item>
    </ha-dropdown>`;
  }

  private _menuSelect(ev: CustomEvent<{ item: { value: string } }>): void {
    const id = this.target.id;
    switch (ev.detail.item.value) {
      case "off":
        toastOnError(this, this.store.turnOff(id), "Could not turn off");
        break;
      case "release":
        toastOnError(this, this.store.release(id), "Could not stop the scene");
        break;
      case "reclaim":
        toastOnError(this, this.store.reclaim(id), "Could not reclaim the lights");
        break;
      case "open":
        this._open();
        break;
    }
  }

  private _activate(sceneId: string): void {
    toastOnError(this, this.store.activate(this.target.id, sceneId), "Could not activate the scene");
  }

  private _toggleMotion(sceneId: string): void {
    toastOnError(this, this.store.toggleMotion(this.target.id, sceneId), "Could not change the motion");
  }

  private _open(): void {
    go(targetPath(this.target.id));
  }

  static styles = [
    tileStyles,
    dotStyles,
    chipStyles,
    css`
      :host {
        display: block;
      }
      ha-card {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-3);
        height: 100%;
        box-sizing: border-box;
        padding: var(--ha-space-3) var(--ha-space-2) var(--ha-space-4) var(--ha-space-4);
      }
      .header {
        display: flex;
        align-items: center;
        gap: var(--ha-space-1);
        min-height: 48px;
      }
      .open {
        flex: 1;
        padding: var(--ha-space-1) 0;
      }
      .info {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .strip {
        margin-inline-end: var(--ha-space-2);
      }
      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: var(--ha-space-2);
        margin-inline-end: var(--ha-space-2);
      }
      .dot {
        width: 14px;
        height: 14px;
      }
      .more {
        --ha-assist-chip-filled-container-color: transparent;
      }
      ha-assist-chip {
        --ha-button-height: 36px;
      }
      .add {
        display: inline-flex;
        align-items: center;
        gap: var(--ha-space-2);
        height: 36px;
        padding: 0 var(--ha-space-4) 0 var(--ha-space-3);
        border: 1px dashed var(--divider-color);
        border-radius: var(--ha-border-radius-pill);
        background: none;
        color: var(--secondary-text-color);
        font: inherit;
        cursor: pointer;
        --mdc-icon-size: 18px;
      }
      .add:hover {
        color: var(--primary-color);
        border-color: var(--primary-color);
      }
      .add:focus-visible {
        outline: 2px solid var(--ha-color-focus, var(--primary-color));
      }
    `,
  ];
}

defineElement("ml-target-tile", MlTargetTile);
