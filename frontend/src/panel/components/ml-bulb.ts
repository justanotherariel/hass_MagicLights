// One light as an orb in its live or previewed colour, with its name below. A button:
// selecting a light is the caller's business (aria-pressed follows `selected`).

import { LitElement, css, html } from "lit";
import { property } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import { hexAlpha, swatchBackground } from "../../shared/color";
import { defineElement } from "../../shared/util";

class MlBulb extends LitElement {
  /** #rrggbb at full brightness; unset while the light is off. */
  @property() color?: string;

  /** Colours along a gradient strip, drawn instead of `color`. */
  @property({ attribute: false }) gradient?: readonly string[];

  /** 1..100; dims the glow, never the colour. */
  @property({ type: Number }) brightness = 100;

  @property() label = "";

  @property() icon = "mdi:lightbulb";

  @property({ type: Boolean, reflect: true }) selected = false;

  @property({ type: Boolean, reflect: true }) unavailable = false;

  @property({ type: Boolean }) disabled = false;

  protected render() {
    const colors = this.gradient?.length ? this.gradient : this.color ? [this.color] : [];
    const on = colors.length > 0 && !this.unavailable;
    const level = Math.max(0.15, Math.min(1, this.brightness / 100));
    const style = on
      ? { "--fill": swatchBackground(colors), "--glow": hexAlpha(colors[0], 0.55 * level) }
      : {};
    return html`<button
      class="bulb"
      aria-pressed=${this.selected ? "true" : "false"}
      .disabled=${this.disabled}
      title=${this.label}
    >
      <span class="orb ${on ? "on" : ""}" style=${styleMap(style)}>
        <ha-icon .icon=${this.unavailable ? "mdi:lightbulb-off-outline" : this.icon}></ha-icon>
      </span>
      <span class="label">${this.label}</span>
    </button>`;
  }

  static styles = css`
    :host {
      display: inline-block;
      width: 76px;
    }
    .bulb {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--ha-space-1);
      width: 100%;
      min-height: 44px;
      margin: 0;
      padding: var(--ha-space-1) 0;
      border: none;
      border-radius: var(--ha-border-radius-md);
      background: none;
      color: var(--secondary-text-color);
      font: inherit;
      cursor: pointer;
    }
    .bulb:disabled {
      cursor: default;
    }
    .bulb:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
    }
    .orb {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: var(--ha-color-fill-neutral-quiet-resting, var(--secondary-background-color));
      color: var(--state-inactive-color, var(--disabled-text-color));
      box-shadow: inset 0 0 0 1px var(--divider-color);
      --mdc-icon-size: 22px;
      transition: box-shadow var(--ha-animation-duration-fast, 150ms) ease;
    }
    .orb.on {
      background: var(--fill);
      color: rgba(0, 0, 0, 0.55);
      box-shadow:
        inset 0 0 0 1px var(--divider-color),
        0 0 14px 2px var(--glow);
    }
    :host([selected]) .orb {
      outline: 2px solid var(--primary-color);
      outline-offset: 2px;
    }
    :host([selected]) .label {
      color: var(--primary-text-color);
      font-weight: var(--ha-font-weight-medium);
    }
    :host([unavailable]) .orb {
      opacity: 0.5;
    }
    .label {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      overflow: hidden;
      max-width: 100%;
      font-size: var(--ha-font-size-xs, 11px);
      line-height: var(--ha-line-height-condensed);
      text-align: center;
      overflow-wrap: anywhere;
    }
  `;
}

defineElement("ml-bulb", MlBulb);
