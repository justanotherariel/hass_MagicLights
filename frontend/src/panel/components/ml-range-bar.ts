// A min/max pair on one bar: two handles (role="slider", arrow / Page / Home / End keys)
// that never pass each other, both values printed in the bar (above it while the range
// is too narrow for them). Over a `background`
// gradient the range stays clear and the rest is veiled; without one the range is filled.
// Fires value-changed with [low, high] when a drag or key press ends.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import { defineElement, fire, withUnit } from "../../shared/util";
import { formatStep, moveHandle, type Handle } from "./bar-math";

const KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"]);

/** A narrower range shows both values above the bar, so they do not overlap. */
const INSIDE_FROM = 160;


class MlRangeBar extends LitElement {
  @property({ type: Number }) low = 0;

  @property({ type: Number }) high = 100;

  @property({ type: Number }) min = 0;

  @property({ type: Number }) max = 100;

  @property({ type: Number }) step = 1;

  @property() unit = "";

  /** Names of the two handles, e.g. "Warmest white" and "Coolest white". */
  @property({ attribute: false }) labels: readonly [string, string] = ["Minimum", "Maximum"];

  /** CSS gradient under the bar. */
  @property() background?: string;

  /** CSS colour filling the range when there is no background. */
  @property() color?: string;

  @property({ type: Boolean }) disabled = false;

  @state() private _values?: [number, number];

  /** The bar's width in px, for where the values fit. */
  @state() private _width = 0;

  private _dragging?: Handle;

  private _resize = new ResizeObserver(([entry]) => (this._width = entry.contentRect.width));

  connectedCallback(): void {
    super.connectedCallback();
    this._resize.observe(this);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this._resize.disconnect();
  }

  protected willUpdate(changed: PropertyValues<this>): void {
    if ((changed.has("low") || changed.has("high")) && this._dragging === undefined) this._values = undefined;
  }

  private get _current(): [number, number] {
    return this._values ?? [this.low, this.high];
  }

  private _share(value: number): number {
    return Math.min(1, Math.max(0, (value - this.min) / (this.max - this.min || 1)));
  }

  protected render() {
    const [low, high] = this._current;
    const a = this._share(low);
    const b = this._share(high);
    const inside = (b - a) * this._width >= INSIDE_FROM;
    const style = {
      "--a": String(a),
      "--b": String(b),
      ...(this.background ? { "--ml-range-background": this.background } : {}),
      ...(this.color ? { "--ml-range-color": this.color } : {}),
    };
    return html`${
      inside
        ? nothing
        : html`<div class="caption" aria-hidden="true">${this._text(low)} – ${this._text(high)}</div>`
    }<div
      class="track ${this.background ? "gradient" : "fill"}"
      style=${styleMap(style)}
      @pointerdown=${this._down}
      @pointermove=${this._move}
      @pointerup=${this._up}
      @pointercancel=${this._up}
    >
      <span class="surface">
        <span class="veil start"></span>
        <span class="range"></span>
        <span class="veil end"></span>
      </span>
      ${
        inside
          ? html`<span class="value low" aria-hidden="true">${this._text(low)}</span>
              <span class="value high" aria-hidden="true">${this._text(high)}</span>`
          : nothing
      }
      ${this._renderHandle(0, low, a)} ${this._renderHandle(1, high, b)}
    </div>`;
  }

  private _text(value: number): string {
    return withUnit(formatStep(value, this.step), this.unit);
  }

  private _renderHandle(which: Handle, value: number, share: number) {
    const [low, high] = this._current;
    return html`<span
      class="handle"
      role="slider"
      tabindex=${this.disabled ? -1 : 0}
      style=${styleMap({ "--at": String(share) })}
      aria-label=${this.labels[which]}
      aria-valuemin=${which === 0 ? this.min : low + this.step}
      aria-valuemax=${which === 0 ? high - this.step : this.max}
      aria-valuenow=${value}
      aria-valuetext=${this._text(value)}
      aria-disabled=${this.disabled ? "true" : nothing}
      @keydown=${(ev: KeyboardEvent) => this._key(ev, which)}
      @keyup=${(ev: KeyboardEvent) => KEYS.has(ev.key) && this._commit()}
    ><span class="grip"></span></span>`;
  }

  private _bounds() {
    return { min: this.min, max: this.max, step: this.step };
  }

  private _key(ev: KeyboardEvent, which: Handle): void {
    if (this.disabled || !KEYS.has(ev.key)) return;
    ev.preventDefault();
    const current = this._current;
    const page = Math.max(this.step, (this.max - this.min) / 10);
    const delta: Record<string, number> = {
      ArrowLeft: -this.step,
      ArrowDown: -this.step,
      ArrowRight: this.step,
      ArrowUp: this.step,
      PageDown: -page,
      PageUp: page,
    };
    const to = ev.key === "Home" ? this.min : ev.key === "End" ? this.max : current[which] + delta[ev.key];
    this._values = moveHandle(current, which, to, this._bounds());
  }

  private _valueAt(ev: PointerEvent): number {
    const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    const share = Math.min(1, Math.max(0, (ev.clientX - rect.left) / (rect.width || 1)));
    return this.min + share * (this.max - this.min);
  }

  private _down(ev: PointerEvent): void {
    if (this.disabled || ev.button !== 0) return;
    const to = this._valueAt(ev);
    const [low, high] = this._current;
    // The nearer handle; between equally near ones, the side the pointer is on.
    const which: Handle = Math.abs(to - low) < Math.abs(to - high) || to < low ? 0 : 1;
    this._dragging = which;
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    this._values = moveHandle(this._current, which, to, this._bounds());
    (this.shadowRoot?.querySelectorAll<HTMLElement>(".handle")[which])?.focus({ preventScroll: true });
    ev.preventDefault();
  }

  private _move(ev: PointerEvent): void {
    if (this._dragging === undefined) return;
    this._values = moveHandle(this._current, this._dragging, this._valueAt(ev), this._bounds());
  }

  private _up(): void {
    if (this._dragging === undefined) return;
    this._dragging = undefined;
    this._commit();
  }

  private _commit(): void {
    const [low, high] = this._current;
    if (low !== this.low || high !== this.high) fire(this, "value-changed", { value: [low, high] });
  }

  static styles = css`
    :host {
      display: block;
      --ml-range-thickness: 56px;
      --ml-range-radius: var(--ha-border-radius-lg, 12px);
    }
    .track {
      position: relative;
      height: var(--ml-range-thickness);
      touch-action: none;
      cursor: pointer;
      user-select: none;
      font-weight: var(--ha-font-weight-medium);
      font-variant-numeric: tabular-nums;
    }
    /* Clips the colours; the handles' touch targets may reach past the ends. */
    .surface {
      position: absolute;
      inset: 0;
      overflow: hidden;
      border-radius: var(--ml-range-radius);
    }
    .gradient .surface {
      background: var(--ml-range-background);
    }
    .fill .surface {
      background: color-mix(in srgb, var(--ml-range-color, var(--primary-color)) 20%, transparent);
    }
    .veil,
    .range {
      position: absolute;
      top: 0;
      bottom: 0;
    }
    .veil.start {
      left: 0;
      width: calc(var(--a) * 100%);
    }
    .veil.end {
      right: 0;
      width: calc((1 - var(--b)) * 100%);
    }
    .gradient .veil {
      background: color-mix(in srgb, var(--card-background-color, #fff) 70%, transparent);
    }
    .range {
      left: calc(var(--a) * 100%);
      width: calc((var(--b) - var(--a)) * 100%);
    }
    .fill .range {
      background: var(--ml-range-color, var(--primary-color));
    }
    .value {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      white-space: nowrap;
      pointer-events: none;
      padding: 0 var(--ha-space-3);
    }
    /* The text sits on the gradient or the fill: dark for contrast. */
    .value {
      color: rgba(0, 0, 0, 0.87);
    }
    .value.low {
      left: calc(var(--a) * 100%);
    }
    .value.high {
      right: calc((1 - var(--b)) * 100%);
    }
    .caption {
      margin: calc(-1 * var(--ha-space-1)) 0 var(--ha-space-1);
      text-align: end;
      font-weight: var(--ha-font-weight-medium);
      font-variant-numeric: tabular-nums;
      color: var(--primary-text-color);
    }
    .handle {
      position: absolute;
      top: 0;
      bottom: 0;
      /* A 44 px target around a 4 px grip. */
      width: 44px;
      left: calc(var(--at) * 100% - 22px);
      display: flex;
      align-items: center;
      justify-content: center;
      outline: none;
      z-index: 1;
    }
    .grip {
      width: 4px;
      height: 50%;
      border-radius: 4px;
      background: white;
      box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.25);
    }
    .handle:focus-visible .grip {
      box-shadow:
        0 0 0 2px var(--primary-color),
        0 0 0 4px white;
    }
  `;
}

defineElement("ml-range-bar", MlRangeBar);
