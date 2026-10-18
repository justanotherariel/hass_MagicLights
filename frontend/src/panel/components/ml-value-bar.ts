// HA's ha-control-slider with its value printed in the bar. The readout is drawn twice
// and each copy clipped to one side of the fill's edge: dark on the fill, the theme's text
// colour on the track, so it stays readable (>= 4.5:1) wherever the handle is. With a
// `background` gradient the bar is a cursor over it instead (colour temperature).
// Fires value-changed when the value is set; the readout follows while dragging.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import { defineElement, fire, withUnit } from "../../shared/util";
import { formatStep } from "./bar-math";

export type BarSize = "big" | "compact";

/** Brightness bars are amber, like HA's light dialog. */
export const AMBER = "var(--state-light-active-color, var(--amber-color, #ffc107))";


class MlValueBar extends LitElement {
  @property({ type: Number }) value = 0;

  @property({ type: Number }) min = 0;

  @property({ type: Number }) max = 100;

  @property({ type: Number }) step = 1;

  @property() unit = "";

  /** Accessible name; printed in the bar with `show-label`. */
  @property() label = "";

  @property({ type: Boolean, attribute: "show-label" }) showLabel = false;

  @property() icon?: string;

  @property({ reflect: true }) size: BarSize = "big";

  /** CSS colour of the fill; HA's primary colour if unset. */
  @property() color?: string;

  /** CSS gradient under a cursor, instead of a fill. */
  @property() background?: string;

  @property({ type: Boolean }) disabled = false;

  /** The value while dragging. */
  @state() private _moving?: number;

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("value")) this._moving = undefined;
  }

  protected render() {
    const value = this._moving ?? this.value;
    const span = this.max - this.min || 1;
    const p = Math.min(1, Math.max(0, (value - this.min) / span));
    const cursor = !!this.background;
    const text = withUnit(formatStep(value, this.step), this.unit);
    const content = html`${this.icon ? html`<ha-icon .icon=${this.icon}></ha-icon>` : nothing}
      ${this.showLabel ? html`<span class="label">${this.label}</span>` : nothing}
      <span class="spacer"></span>
      <span class="value">${text}</span>`;
    return html`<div
      class="bar ${cursor ? "cursor" : ""} ${p > 0.5 ? "high" : ""}"
      style=${styleMap({ "--p": String(p), ...(this.color ? { "--ml-bar-color": this.color } : {}) })}
    >
      <ha-control-slider
        .value=${value}
        .min=${this.min}
        .max=${this.max}
        .step=${this.step}
        .unit=${this.unit}
        .label=${this.label}
        .disabled=${this.disabled}
        mode=${cursor ? "cursor" : "start"}
        ?show-handle=${!cursor}
        tooltip-mode="never"
        @slider-moved=${this._moved}
        @value-changed=${this._changed}
      >
        ${
          cursor
            ? html`<div slot="background" class="gradient" style=${styleMap({ background: this.background })}></div>`
            : nothing
        }
      </ha-control-slider>
      ${
        cursor
          ? html`<div class="readout on-gradient" aria-hidden="true">${content}</div>`
          : html`<div class="readout on-track" aria-hidden="true">${content}</div>
              <div class="readout on-fill" aria-hidden="true">${content}</div>`
      }
    </div>`;
  }

  private _moved(ev: CustomEvent<{ value?: number }>): void {
    ev.stopPropagation();
    this._moving = ev.detail.value;
  }

  private _changed(ev: CustomEvent<{ value?: number }>): void {
    ev.stopPropagation();
    const value = ev.detail.value;
    if (typeof value !== "number" || Number.isNaN(value)) return;
    this._moving = value;
    fire(this, "value-changed", { value });
  }

  static styles = css`
    :host {
      display: block;
      --ml-bar-thickness: 56px;
      --ml-bar-on-fill-color: rgba(0, 0, 0, 0.87);
    }
    :host([size="compact"]) {
      --ml-bar-thickness: 44px;
    }
    .bar {
      position: relative;
      /* The track ha-control-slider leaves right of the fill for its handle. */
      --s: calc(var(--ml-bar-thickness) / 4 + 4px);
    }
    ha-control-slider {
      --control-slider-thickness: var(--ml-bar-thickness);
      --control-slider-color: var(--ml-bar-color, var(--primary-color));
      --control-slider-background: var(--ml-bar-color, var(--primary-color));
      --control-slider-background-opacity: 0.2;
      --control-slider-border-radius: var(--ha-border-radius-lg, 12px);
    }
    .gradient {
      border-radius: var(--ha-border-radius-lg, 12px);
    }
    .readout {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      padding: 0 var(--ha-space-4);
      pointer-events: none;
      font-weight: var(--ha-font-weight-medium);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      overflow: hidden;
      --mdc-icon-size: 20px;
    }
    :host([size="compact"]) .readout {
      font-size: var(--ha-font-size-s);
    }
    :host([size="big"]) .value {
      font-size: var(--ha-font-size-l);
    }
    .spacer {
      flex: 1;
    }
    .label {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .on-track {
      color: var(--primary-text-color);
      clip-path: inset(0 0 0 calc(var(--s) + var(--p) * (100% - var(--s))));
    }
    .on-fill {
      color: var(--ml-bar-on-fill-color);
      clip-path: inset(0 calc((1 - var(--p)) * (100% - var(--s))) 0 0);
    }
    /* Colour temperatures are light enough for dark text; the value keeps clear of the cursor. */
    .on-gradient {
      color: var(--ml-bar-on-fill-color);
    }
    .cursor.high .on-gradient {
      flex-direction: row-reverse;
    }
  `;
}

defineElement("ml-value-bar", MlValueBar);
