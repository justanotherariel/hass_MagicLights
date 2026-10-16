// Preview strip of the scene editor: one bulb per target light, grouped by room, showing
// the backend's preview frames. Dynamic scenes play with a scrubber; adaptive
// scenes get a time-of-day slider. Fires ml-bulb-click with the entity id.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { styleMap } from "lit/directives/style-map.js";
import type { HomeAssistant, PreviewFrame, PreviewLight, TargetInfo } from "../../shared/types";
import { defineElement, fire, humanise } from "../../shared/util";
import type { MlStore } from "../store";
import { frameAt } from "./editor-model";

export interface BulbClick {
  entityId: string;
}

/** Playback speed: real time for dynamic scenes, one hour per second through a day. */
const DAY_SPEED = 3600;

const reducedMotion = (): boolean => matchMedia("(prefers-reduced-motion: reduce)").matches;

const clockTime = (ms: number): string =>
  new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

class MlPreview extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) target!: TargetInfo;

  @property({ attribute: false }) frames?: PreviewFrame[];

  @property({ type: Boolean }) loading = false;

  @property() error?: string;

  /** Lights of the selected layer. */
  @property({ attribute: false }) highlight: readonly string[] = [];

  /** Frames span a day starting at `start` (adaptive). */
  @property({ type: Boolean }) day = false;

  /** Epoch ms of t = 0. */
  @property({ type: Number }) start = 0;

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _t = 0;

  @state() private _playing = !reducedMotion();

  private _raf = 0;

  private _last = 0;

  /** Whether the playhead was placed for a day or for a motion preview. */
  private _placedDay?: boolean;

  connectedCallback(): void {
    super.connectedCallback();
    // Reattached: updated() restarts the playback loop.
    if (this.hasUpdated) this.requestUpdate();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    cancelAnimationFrame(this._raf);
    this._raf = 0;
  }

  private get _duration(): number {
    return this.frames?.at(-1)?.t ?? 0;
  }

  private get _animated(): boolean {
    return (this.frames?.length ?? 0) > 1;
  }

  protected willUpdate(changed: PropertyValues<this>): void {
    if (!changed.has("frames") || !this._animated) return;
    if (this.day !== this._placedDay) {
      // A day opens paused at the current time of day, motion at its start.
      this._placedDay = this.day;
      this._t = this.day ? Math.min(this._duration, Math.max(0, (Date.now() - this.start) / 1000)) : 0;
      this._playing = !this.day && !reducedMotion();
    } else {
      this._t = Math.min(this._t, this._duration);
    }
  }

  protected updated(): void {
    const run = this._playing && this._animated;
    if (run && !this._raf) {
      this._last = performance.now();
      this._raf = requestAnimationFrame(this._step);
    } else if (!run && this._raf) {
      cancelAnimationFrame(this._raf);
      this._raf = 0;
    }
  }

  private _step = (now: number): void => {
    const dt = ((now - this._last) / 1000) * (this.day ? DAY_SPEED : 1);
    this._last = now;
    const duration = this._duration;
    this._t = duration > 0 ? (this._t + dt) % duration : 0;
    this._raf = requestAnimationFrame(this._step);
  };

  protected render() {
    const frame = this.frames ? frameAt(this.frames, this._t) : undefined;
    const groups = this._groups();
    const highlight = new Set(this.highlight);
    return html`
      <div class="bulbs ${this._playing ? "playing" : ""}">
        ${groups.map(
          ([label, lights]) => html`<div class="group">
            <div class="row">
              ${lights.map((e) => this._renderBulb(e, frame, highlight.has(e)))}
            </div>
            ${groups.length > 1 ? html`<span class="group-label">${label}</span>` : nothing}
          </div>`,
        )}
        ${this.loading ? html`<ha-spinner class="busy" size="tiny"></ha-spinner>` : nothing}
      </div>
      ${this.error ? html`<div class="error">${this.error}</div>` : this._renderTransport()}
    `;
  }

  /** [room name, lights]: a zone's rooms in zone order, its extra lights last. */
  private _groups(): [string, string[]][] {
    const target = this.target;
    if (target.kind === "room") return [[target.name, [...target.lights]]];
    const left = new Set(target.lights);
    const groups: [string, string[]][] = [];
    for (const area of target.rooms) {
      const room = this.store.target(`room:${area}`);
      const lights = target.lights.filter((e) => left.has(e) && room?.lights.includes(e));
      if (!lights.length) continue;
      lights.forEach((e) => left.delete(e));
      groups.push([room?.name ?? this.hass.areas?.[area]?.name ?? area, lights]);
    }
    if (left.size) groups.push(["Other lights", [...left]]);
    return groups;
  }

  private _renderBulb(entityId: string, frame: PreviewFrame | undefined, selected: boolean) {
    const name = this.store.lightName(entityId);
    const light: PreviewLight | undefined = frame?.lights[entityId];
    let kind = "unknown";
    let title = name;
    let style: Record<string, string> = {};
    if (frame && !light) {
      kind = "kept";
      title = `${name}: not changed by this scene`;
    } else if (light && !light.on) {
      kind = "off";
      title = `${name}: off`;
    } else if (light) {
      kind = "on";
      const percent = Math.round(light.brightness * 100);
      title = `${name}: ${percent}%${light.effect ? ` · ${humanise(light.effect)}` : ""}`;
      // The highlight is opaque: a translucent one would show the page through the bulb.
      const fill = light.gradient?.length
        ? "radial-gradient(circle at 35% 30%, rgba(255, 255, 255, 0.5), transparent 60%), " +
          `linear-gradient(135deg, ${light.gradient.join(", ")})`
        : `radial-gradient(circle at 35% 30%, color-mix(in srgb, #fff 50%, ${light.rgb}), ${light.rgb})`;
      style = {
        "--c": light.rgb,
        "--fill": fill,
        "--p": String(Math.sqrt(Math.min(1, Math.max(0, light.brightness)))),
      };
    }
    return html`<button
      class="bulb ${selected ? "selected" : ""}"
      title=${title}
      aria-label=${title}
      @click=${() => fire<BulbClick>(this, "ml-bulb-click", { entityId })}
    >
      <span class="lamp ${kind}" style=${styleMap(style)}>
        ${light?.effect ? html`<ha-icon class="fx" icon="mdi:creation"></ha-icon>` : nothing}
      </span>
      <span class="name">${name}</span>
    </button>`;
  }

  private _renderTransport() {
    if (!this.frames) return nothing;
    if (!this._animated) {
      return html`<div class="transport static">Static scene: the lights hold these states.</div>`;
    }
    const duration = this._duration;
    const label = this.day
      ? clockTime(this.start + this._t * 1000)
      : `${this._t.toFixed(1)} s / ${Math.round(duration)} s`;
    return html`<div class="transport">
      <ha-icon-button
        .label=${this._playing ? "Pause preview" : "Play preview"}
        @click=${() => (this._playing = !this._playing)}
      >
        <ha-icon .icon=${this._playing ? "mdi:pause" : "mdi:play"}></ha-icon>
      </ha-icon-button>
      <ha-slider
        .min=${0}
        .max=${duration}
        .step=${this.day ? 300 : 0.1}
        .value=${this._t}
        .withTooltip=${false}
        aria-label=${this.day ? "Time of day" : "Preview time"}
        @input=${(ev: Event) => {
          this._playing = false;
          this._t = Number((ev.target as HTMLInputElement).value);
        }}
      ></ha-slider>
      <span class="time">${label}</span>
    </div>`;
  }

  static styles = css`
    :host {
      display: block;
      padding: var(--ha-space-3) var(--ha-space-4) var(--ha-space-1);
      background: var(--card-background-color);
      border: 1px solid var(--divider-color);
      border-radius: var(--ha-card-border-radius, var(--ha-border-radius-lg));
    }
    .bulbs {
      position: relative;
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      align-items: flex-start;
      row-gap: var(--ha-space-3);
      padding: var(--ha-space-2) 0;
    }
    :host([narrow]) .bulbs {
      flex-wrap: nowrap;
      justify-content: safe center;
      overflow-x: auto;
      scrollbar-width: thin;
    }
    .group {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--ha-space-1);
      padding: 0 var(--ha-space-3);
      border-inline-start: 1px solid var(--divider-color);
    }
    .group:first-child {
      border-inline-start: none;
    }
    .row {
      display: flex;
      gap: var(--ha-space-2);
    }
    .group-label {
      font-size: var(--ha-font-size-xs);
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }
    .bulb {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--ha-space-2);
      width: 68px;
      padding: var(--ha-space-1) 0;
      border: none;
      background: none;
      color: inherit;
      font: inherit;
      cursor: pointer;
      border-radius: var(--ha-border-radius-md);
    }
    :host([narrow]) .bulb {
      width: 56px;
    }
    .bulb:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
    }
    .lamp {
      position: relative;
      width: 40px;
      height: 40px;
      border-radius: var(--ha-border-radius-circle);
      background: var(--ha-color-fill-neutral-quiet-resting, rgba(127, 127, 127, 0.18));
    }
    :host([narrow]) .lamp {
      width: 32px;
      height: 32px;
    }
    .lamp::before {
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
      opacity: 0;
    }
    /* Dimming darkens the bulb instead of fading it, which would mix in the page colour. */
    .lamp.on {
      background: none;
    }
    .lamp.on::before {
      background: var(--fill);
      opacity: 1;
      filter: brightness(calc(0.55 + 0.45 * var(--p)));
      box-shadow: 0 0 calc(4px + 20px * var(--p)) calc(2px * var(--p)) var(--c);
    }
    .playing .lamp::before {
      transition:
        filter 0.6s linear,
        box-shadow 0.6s linear;
    }
    .lamp.kept {
      background: none;
      box-shadow: inset 0 0 0 2px var(--divider-color);
    }
    .lamp.unknown {
      opacity: 0.5;
    }
    .bulb.selected .lamp {
      outline: 2px solid var(--primary-color);
      outline-offset: 3px;
    }
    .fx {
      position: absolute;
      inset-inline-end: -6px;
      top: -6px;
      --mdc-icon-size: 16px;
      color: var(--primary-text-color);
      background: var(--card-background-color);
      border-radius: var(--ha-border-radius-circle);
    }
    .name {
      max-width: 100%;
      font-size: var(--ha-font-size-xs);
      line-height: var(--ha-line-height-condensed);
      color: var(--secondary-text-color);
      text-align: center;
      overflow-wrap: anywhere;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      overflow: hidden;
    }
    .busy {
      position: absolute;
      top: 0;
      inset-inline-end: 0;
    }
    .transport {
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      min-height: 48px;
    }
    .transport.static {
      justify-content: center;
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
      min-height: 32px;
    }
    ha-slider {
      flex: 1;
      width: auto;
      min-width: 0;
    }
    .time {
      min-width: 88px;
      text-align: end;
      font-size: var(--ha-font-size-s);
      font-variant-numeric: tabular-nums;
      color: var(--secondary-text-color);
    }
    .error {
      padding: var(--ha-space-2) 0 var(--ha-space-3);
      text-align: center;
      font-size: var(--ha-font-size-s);
      color: var(--error-color);
    }
    @media (prefers-reduced-motion: reduce) {
      .playing .lamp::before {
        transition: none;
      }
    }
  `;
}

defineElement("ml-preview", MlPreview);
