// One scene of a room as a tile card: the body runs it, the swatch (its own button) edits
// it. A scene in the menu without lights here is a ghost whose whole tile opens set-up.
// Fires ml-scene-action with "activate" or "edit".

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { classMap } from "lit/directives/class-map.js";
import type { SceneInfo } from "../shared/types";
import { defineElement, fire } from "../shared/util";
import "./components/ml-swatch";

export type SceneAction = "activate" | "edit";

class MlSceneCard extends LitElement {
  @property({ attribute: false }) scene!: SceneInfo;

  /** Colours of the layers that reach this room. */
  @property({ attribute: false }) swatches: readonly string[] = [];

  /** "Adaptive · 100%": what it is, in one line. */
  @property() line = "";

  /** Running here. */
  @property({ type: Boolean }) active = false;

  /** Its colours can move. */
  @property({ type: Boolean }) animated = false;

  /** In the menu, but without lights here. */
  @property({ type: Boolean }) ghost = false;

  /** The room cannot run scenes now (its select is unavailable); editing still works. */
  @property({ type: Boolean }) disabled = false;

  protected render() {
    const { scene, ghost } = this;
    const badge = this._renderBadge();
    return html`<ha-card class=${classMap({ active: this.active, ghost, badged: badge !== nothing })}>
      <ha-tile-container
        .interactive=${ghost || !this.disabled}
        @action=${() => this._fire(ghost ? "edit" : "activate")}
      >
        <button slot="icon" class="swatch" aria-label=${`Edit ${scene.name}`} @click=${() => this._fire("edit")}>
          <ml-swatch size="32" .swatches=${this.swatches} .icon=${scene.icon ?? undefined} ?ghost=${ghost}></ml-swatch>
          <span class="pencil" aria-hidden="true"><ha-icon icon="mdi:pencil"></ha-icon></span>
        </button>
        <ha-tile-info slot="info">
          <span slot="primary">${scene.name}</span>
          <span slot="secondary">${ghost ? "Needs set-up · choose lights" : this.line}</span>
        </ha-tile-info>
      </ha-tile-container>
      ${badge}
    </ha-card>`;
  }

  private _renderBadge() {
    const [kind, icon, label] = this.ghost
      ? ["warn", "mdi:alert", "Needs set-up"]
      : this.active
        ? ["on", "mdi:check", "Running"]
        : this.animated
          ? ["play", "mdi:play", "Animated"]
          : [];
    if (!kind) return nothing;
    return html`<ha-tile-badge class=${kind} title=${label}>
      <ha-icon .icon=${icon}></ha-icon>
    </ha-tile-badge>`;
  }

  private _fire(action: SceneAction): void {
    fire(this, "ml-scene-action", { action });
  }

  static styles = css`
    :host {
      display: block;
    }
    ha-card {
      position: relative;
      height: 100%;
      --tile-color: var(--primary-color);
      transition:
        border-color var(--ha-animation-duration-fast, 150ms) ease,
        box-shadow var(--ha-animation-duration-fast, 150ms) ease;
    }
    ha-card.active {
      border-color: var(--primary-color);
      box-shadow: 0 0 0 1px var(--primary-color);
    }
    ha-card.ghost {
      border-style: dashed;
      background: none;
      --ha-tile-info-primary-color: var(--secondary-text-color);
    }
    ha-tile-container {
      --row-height: 60px;
    }
    ha-tile-info {
      --ha-tile-info-secondary-color: var(--secondary-text-color);
    }
    /* The name ellipsizes before the corner badge (16 px at 6 px in). */
    ha-card.badged ha-tile-info {
      padding-inline-end: var(--ha-space-4, 16px);
    }
    /* A visibly separate button: a 44 px target that shows a pencil on hover and focus. */
    .swatch {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      flex: none;
      width: 44px;
      height: 44px;
      margin: 0 -4px 0 -6px;
      padding: 0;
      border: none;
      border-radius: 50%;
      background: none;
      cursor: pointer;
      pointer-events: auto;
      transition: background-color var(--ha-animation-duration-fast, 150ms) ease;
    }
    .swatch:hover,
    .swatch:focus-visible {
      background: var(--ha-color-fill-neutral-quiet-hover, rgba(127, 127, 127, 0.16));
    }
    .swatch:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
    }
    .pencil {
      position: absolute;
      right: 0;
      bottom: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--card-background-color, #fff);
      color: var(--primary-text-color);
      box-shadow: 0 0 0 1px var(--divider-color);
      opacity: 0;
      transition: opacity var(--ha-animation-duration-fast, 150ms) ease;
      --mdc-icon-size: 12px;
    }
    .swatch:hover .pencil,
    .swatch:focus-visible .pencil {
      opacity: 1;
    }
    @media (hover: none) and (pointer: coarse) {
      .pencil {
        opacity: 1;
      }
    }
    ha-tile-badge {
      position: absolute;
      top: 6px;
      inset-inline-end: 6px;
      pointer-events: none;
    }
    ha-tile-badge.play {
      --tile-badge-background-color: var(--secondary-text-color);
    }
    ha-tile-badge.warn {
      --tile-badge-background-color: var(--warning-color, #ffa600);
      --tile-badge-icon-color: #000;
    }
  `;
}

defineElement("ml-scene-card", MlSceneCard);
