// One scene in the gallery: colour art, name, ▶ for palette motion and the scene menu.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import { luminance, swatchBackground } from "../shared/color";
import type { SceneInfo } from "../shared/types";
import { defineElement, fire } from "../shared/util";
import { tileStyles } from "./styles";

export type SceneAction =
  | "activate"
  | "play"
  | "edit"
  | "duplicate"
  | "hide"
  | "show"
  | "reset"
  | "delete";

export type SceneKind = "preset" | "edited" | "user";

export const sceneKind = (scene: SceneInfo): SceneKind =>
  scene.builtin ? "preset" : scene.preset ? "edited" : "user";

class MlSceneCard extends LitElement {
  @property({ attribute: false }) scene!: SceneInfo;

  @property({ type: Boolean, reflect: true }) active = false;

  /** Running with motion. */
  @property({ type: Boolean }) playing = false;

  @property({ type: Boolean }) playable = false;

  /** Shown in the target's menu (visible scenes). */
  @property({ type: Boolean }) listed = true;

  /** The target cannot run scenes now (its select is unavailable); the menu still works. */
  @property({ type: Boolean }) disabled = false;

  protected render() {
    const scene = this.scene;
    const kind = sceneKind(scene);
    const dark = !!scene.swatches[0] && luminance(scene.swatches[0]) < 0.35;
    return html`
      <ha-card>
        <button
          class="plain main"
          aria-pressed=${this.active}
          .disabled=${this.disabled}
          @click=${() => this._fire("activate")}
        >
          <span
            class="art ${this.playing ? "moving" : ""} ${dark ? "dark" : ""}"
            style=${styleMap({ "--art": swatchBackground(scene.swatches) })}
          >
            ${scene.icon ? html`<ha-icon .icon=${scene.icon}></ha-icon>` : nothing}
            <span class="tags">
              ${
                this.active
                  ? html`<span class="tag on">${this.playing ? "Playing" : "Active"}</span>`
                  : nothing
              }
              ${kind === "edited" ? html`<span class="tag">Edited</span>` : nothing}
            </span>
          </span>
          <span class="label ${this.playable ? "two-tools" : ""}">
            <span class="scene-name">${scene.name}</span>
          </span>
        </button>
        <div class="tools">
          ${
            this.playable
              ? html`<ha-icon-button
                  .label=${this.playing ? `Hold colours of ${scene.name}` : `Play ${scene.name}`}
                  .disabled=${this.disabled}
                  @click=${() => this._fire("play")}
                >
                  <ha-icon .icon=${this.playing ? "mdi:pause" : "mdi:play"}></ha-icon>
                </ha-icon-button>`
              : nothing
          }
          ${this._renderMenu(kind)}
        </div>
      </ha-card>
    `;
  }

  private _renderMenu(kind: SceneKind) {
    const item = (action: SceneAction, icon: string, label: string, danger = false) =>
      html`<ha-dropdown-item value=${action} variant=${danger ? "danger" : "default"}>
        <ha-icon slot="icon" .icon=${icon}></ha-icon>
        ${label}
      </ha-dropdown-item>`;
    return html`<ha-dropdown
      placement="bottom-end"
      @wa-select=${(ev: CustomEvent<{ item: { value: SceneAction } }>) => this._fire(ev.detail.item.value)}
    >
      <ha-icon-button slot="trigger" .label=${`Options for ${this.scene.name}`}>
        <ha-icon icon="mdi:dots-vertical"></ha-icon>
      </ha-icon-button>
      ${item("edit", "mdi:pencil-outline", "Edit")}
      ${item("duplicate", "mdi:content-copy", "Duplicate")}
      ${
        this.listed
          ? item("hide", "mdi:eye-off-outline", "Hide")
          : item("show", "mdi:eye-outline", "Show in menu")
      }
      ${kind === "edited" ? item("reset", "mdi:restore", "Reset to preset", true) : nothing}
      ${kind === "user" ? item("delete", "mdi:delete-outline", "Delete", true) : nothing}
    </ha-dropdown>`;
  }

  private _fire(action: SceneAction): void {
    fire(this, "ml-scene-action", { action });
  }

  static styles = [
    tileStyles,
    css`
      :host {
        display: block;
      }
      ha-card {
        position: relative;
        height: 100%;
        overflow: hidden;
        transition: border-color var(--ha-animation-duration-fast) ease;
      }
      :host([active]) ha-card {
        border-color: var(--primary-color);
        box-shadow: 0 0 0 1px var(--primary-color);
      }
      button.plain.main {
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 0;
        width: 100%;
        height: 100%;
        border-radius: 0;
      }
      button.plain.main:disabled {
        cursor: default;
      }
      .art {
        position: relative;
        display: flex;
        align-items: flex-end;
        height: 72px;
        padding: var(--ha-space-2) var(--ha-space-3);
        box-sizing: border-box;
        background: var(--art);
        /* Keeps near-white art apart from the card. */
        box-shadow: inset 0 -1px 0 var(--divider-color);
        color: rgba(0, 0, 0, 0.55);
        --mdc-icon-size: 22px;
      }
      .art.dark {
        color: rgba(255, 255, 255, 0.85);
      }
      .art.moving {
        background-size: 300% 100%;
        animation: ml-drift 12s ease-in-out infinite alternate;
      }
      .tags {
        position: absolute;
        top: var(--ha-space-2);
        inset-inline-end: var(--ha-space-2);
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: var(--ha-space-1);
      }
      .tag {
        font-size: var(--ha-font-size-xs);
        font-weight: var(--ha-font-weight-medium);
        line-height: 20px;
        padding: 0 var(--ha-space-2);
        border-radius: var(--ha-border-radius-pill);
        color: var(--primary-text-color);
        background: var(--card-background-color);
      }
      .tag.on {
        color: var(--text-primary-color, #fff);
        background: var(--primary-color);
      }
      /* Leaves room for the tools in the card's bottom corner. */
      .label {
        display: flex;
        align-items: center;
        min-height: 48px;
        padding-block: var(--ha-space-1);
        padding-inline: var(--ha-space-3) 48px;
        box-sizing: border-box;
        min-width: 0;
      }
      .label.two-tools {
        padding-inline-end: 88px;
      }
      .scene-name {
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
        overflow: hidden;
        font-weight: var(--ha-font-weight-medium);
        line-height: var(--ha-line-height-condensed);
      }
      .tools {
        position: absolute;
        inset-inline-end: 0;
        bottom: 0;
        display: flex;
        align-items: center;
        height: 48px;
        padding: 0 var(--ha-space-1);
        --ha-icon-button-size: 40px;
      }
      @media (prefers-reduced-motion: reduce) {
        .art.moving {
          animation: none;
        }
      }
    `,
  ];
}

defineElement("ml-scene-card", MlSceneCard);
