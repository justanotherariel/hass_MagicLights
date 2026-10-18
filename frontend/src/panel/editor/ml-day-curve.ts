// A day of a scene in one room, drawn from the backend's preview frames: the brightness
// of some lights as a curve over 24 h, in their colours. Dragging (or the arrow keys)
// moves the time the room's bulbs show. Fires value-changed with seconds since midnight.

import { LitElement, css, html, nothing, svg } from "lit";
import { property } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import type { PreviewFrame } from "../../shared/types";
import { defineElement, fire } from "../../shared/util";
import { curvePoints, curveRange } from "./editor-model";

const DAY = 86400;
const KEY_STEP = 900;
const PAGE_STEP = 3600;
/** The curve keeps clear of the box's top and bottom edges, in % of its height. */
const TOP = 18;
const BOTTOM = 8;

const clock = (seconds: number): string => {
  const minutes = Math.round(seconds / 60) % (24 * 60);
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
};

class MlDayCurve extends LitElement {
  @property({ attribute: false }) frames: readonly PreviewFrame[] = [];

  /** Seconds since midnight the bulbs show. */
  @property({ type: Number }) t = 0;

  /** The lights the curve follows; all of the frame's when empty. */
  @property({ attribute: false }) lights: readonly string[] = [];

  @property() label = "Today";

  @property() hint = "";

  private _dragging = false;

  protected render() {
    const points = curvePoints(this.frames, this.lights);
    if (points.length < 2) return nothing;
    const [low, high] = curveRange(points);
    const y = (level: number) => TOP + (1 - level) * (100 - TOP - BOTTOM);
    const x = (t: number) => (t / DAY) * 100;
    const line = points.map((p) => `${x(p.t)},${y(p.level)}`).join(" L ");
    const stops = points.filter((p) => p.color).map((p) => `${p.color} ${x(p.t)}%`);
    const at = Math.min(DAY, Math.max(0, this.t));
    const flat = high - low < 1;
    const lowAt = points.find((p) => Math.round(p.level * 100) === low)?.t ?? 0;
    const highAt = points.find((p) => Math.round(p.level * 100) === high)?.t ?? DAY / 2;
    return html`
      <div class="head">
        <span class="label">${this.label}</span>
        ${this.hint ? html`<span class="hint">${this.hint}</span>` : nothing}
      </div>
      <div
        class="box"
        role="slider"
        tabindex="0"
        aria-label=${`${this.label}: time of day`}
        aria-valuemin="0"
        aria-valuemax=${DAY}
        aria-valuenow=${Math.round(at)}
        aria-valuetext=${clock(at)}
        style=${styleMap({ "--sky": stops.length ? `linear-gradient(to right, ${stops.join(", ")})` : "none" })}
        @pointerdown=${this._down}
        @pointermove=${this._move}
        @pointerup=${this._up}
        @pointercancel=${this._up}
        @keydown=${this._key}
      >
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          ${svg`<path class="veil" d=${`M 0,0 L ${line} L 100,0 Z`}></path>
            <path class="line" d=${`M ${line}`}></path>
            <line class="now" x1=${x(at)} x2=${x(at)} y1="0" y2="100"></line>`}
        </svg>
        ${
          flat
            ? html`<span class="tag" style=${styleMap({ left: "2%", top: `${y(high / 100)}%` })}>
                ${high} % all day
              </span>`
            : html`<span class="tag" style=${styleMap({ left: `${Math.min(80, x(lowAt) + 1)}%`, top: `${y(low / 100)}%` })}>
                  ${low} % night
                </span>
                <span class="tag" style=${styleMap({ left: `${Math.min(80, Math.max(2, x(highAt) - 4))}%`, top: `${y(high / 100)}%` })}>
                  ${high} % day
                </span>`
        }
        <span class="time" style=${styleMap({ left: `clamp(24px, ${x(at)}%, calc(100% - 24px))` })}>${clock(at)}</span>
      </div>
      <div class="axis" aria-hidden="true">
        ${["00:00", "06:00", "12:00", "18:00", "24:00"].map((h) => html`<span>${h}</span>`)}
      </div>
    `;
  }

  private _timeAt(ev: PointerEvent): number {
    const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    return Math.min(1, Math.max(0, (ev.clientX - rect.left) / (rect.width || 1))) * DAY;
  }

  private _down(ev: PointerEvent): void {
    if (ev.button !== 0) return;
    this._dragging = true;
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    this._emit(this._timeAt(ev));
  }

  private _move(ev: PointerEvent): void {
    if (this._dragging) this._emit(this._timeAt(ev));
  }

  private _up(): void {
    this._dragging = false;
  }

  private _key(ev: KeyboardEvent): void {
    const delta: Record<string, number> = {
      ArrowLeft: -KEY_STEP,
      ArrowDown: -KEY_STEP,
      ArrowRight: KEY_STEP,
      ArrowUp: KEY_STEP,
      PageDown: -PAGE_STEP,
      PageUp: PAGE_STEP,
    };
    let to: number;
    if (ev.key === "Home") to = 0;
    else if (ev.key === "End") to = DAY;
    else if (ev.key in delta) to = this.t + delta[ev.key];
    else return;
    ev.preventDefault();
    this._emit(Math.min(DAY, Math.max(0, to)));
  }

  private _emit(t: number): void {
    fire(this, "value-changed", { value: t });
  }

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
    }
    .head {
      display: flex;
      align-items: baseline;
      flex-wrap: wrap;
      gap: var(--ha-space-2);
    }
    .label {
      font-weight: var(--ha-font-weight-medium);
    }
    .hint {
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .box {
      position: relative;
      height: 100px;
      border-radius: var(--ha-border-radius-lg, 12px);
      background: var(--sky), var(--ha-color-fill-neutral-quiet-resting, var(--secondary-background-color));
      overflow: hidden;
      touch-action: none;
      cursor: ew-resize;
      outline: none;
    }
    .box:focus-visible {
      box-shadow: 0 0 0 2px var(--primary-color);
    }
    svg {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }
    .veil {
      fill: var(--card-background-color, #fff);
      opacity: 0.55;
    }
    .line {
      fill: none;
      stroke: #fff;
      stroke-width: 2.5;
      vector-effect: non-scaling-stroke;
      filter: drop-shadow(0 0 1px rgba(0, 0, 0, 0.5));
    }
    .now {
      stroke: var(--primary-color);
      stroke-width: 2;
      stroke-dasharray: 3 3;
      vector-effect: non-scaling-stroke;
    }
    /* Pills keep the text readable on any colour of the day. */
    .tag,
    .time {
      position: absolute;
      transform: translateY(-120%);
      padding: 0 var(--ha-space-2);
      border-radius: var(--ha-border-radius-pill);
      font-size: var(--ha-font-size-xs, 11px);
      line-height: 18px;
      white-space: nowrap;
      pointer-events: none;
    }
    .tag {
      background: rgba(255, 255, 255, 0.85);
      color: rgba(0, 0, 0, 0.87);
    }
    .time {
      top: auto;
      bottom: 4px;
      transform: translateX(-50%);
      background: var(--primary-color);
      color: var(--text-primary-color, #fff);
      font-weight: var(--ha-font-weight-medium);
    }
    .axis {
      display: flex;
      justify-content: space-between;
      font-size: var(--ha-font-size-xs, 11px);
      font-variant-numeric: tabular-nums;
      color: var(--secondary-text-color);
    }
  `;
}

defineElement("ml-day-curve", MlDayCurve);
