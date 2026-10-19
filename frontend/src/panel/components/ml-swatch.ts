// A scene's look as a glowing round swatch: its colours as a soft orb, its icon on top.
// `ghost` is a scene that needs setting up, `off` a room that is dark.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import { hexAlpha, luminance } from "../../shared/color";
import { defineElement } from "../../shared/util";

export type SwatchSize = 24 | 32 | 48 | 56;

/** The orb's fill: one colour lit from the top left, or the colours around it. */
export const orbBackground = (swatches: readonly string[]): string => {
  if (!swatches.length) return "var(--ha-color-fill-neutral-normal-resting, var(--divider-color))";
  const light = "radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.55), rgba(255, 255, 255, 0) 55%)";
  if (swatches.length === 1) return `${light}, ${swatches[0]}`;
  return `${light}, conic-gradient(from 200deg, ${[...swatches, swatches[0]].join(", ")})`;
};

class MlSwatch extends LitElement {
  @property({ attribute: false }) swatches: readonly string[] = [];

  @property() icon?: string;

  @property({ type: Number, reflect: true }) size: SwatchSize = 48;

  /** Not set up here: desaturated, no glow. */
  @property({ type: Boolean, reflect: true }) ghost = false;

  /** Grey and without glow, like a light that is off. */
  @property({ type: Boolean, reflect: true }) off = false;

  protected render() {
    const colors = this.off ? [] : this.swatches;
    const first = colors[0];
    const glow = first && !this.ghost ? hexAlpha(first, 0.45) : "transparent";
    const dark = first ? luminance(first) < 0.3 : false;
    return html`<span
      class="orb ${dark ? "dark" : ""}"
      style=${styleMap({ "--orb": orbBackground(colors), "--glow": glow })}
    >
      ${this.icon ? html`<ha-icon .icon=${this.icon}></ha-icon>` : nothing}
    </span>`;
  }

  static styles = css`
    :host {
      display: inline-flex;
      flex: none;
      width: var(--ml-swatch-size, 48px);
      height: var(--ml-swatch-size, 48px);
      --mdc-icon-size: calc(var(--ml-swatch-size, 48px) * 0.46);
    }
    :host([size="24"]) {
      --ml-swatch-size: 24px;
    }
    :host([size="32"]) {
      --ml-swatch-size: 32px;
    }
    :host([size="56"]) {
      --ml-swatch-size: 56px;
    }
    .orb {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 100%;
      border-radius: var(--ha-border-radius-circle, 50%);
      background: var(--orb);
      /* The ring keeps near-white swatches visible on light cards and near-black ones on dark. */
      box-shadow:
        inset 0 0 0 1px var(--divider-color),
        0 0 0 calc(var(--ml-swatch-size, 48px) / 12) color-mix(in srgb, var(--glow) 40%, transparent),
        0 0 calc(var(--ml-swatch-size, 48px) / 3) var(--glow);
      color: rgba(0, 0, 0, 0.6);
    }
    .orb.dark {
      color: rgba(255, 255, 255, 0.9);
    }
    :host([ghost]) .orb {
      filter: grayscale(0.85) opacity(0.6);
    }
    :host([off]) .orb {
      color: var(--state-inactive-color, var(--disabled-text-color));
      background: var(--ha-color-fill-neutral-quiet-resting, var(--secondary-background-color));
    }
  `;
}

defineElement("ml-swatch", MlSwatch);
