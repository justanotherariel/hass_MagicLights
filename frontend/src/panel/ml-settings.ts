// Global settings: switching scenes first (fade, switch-on scene), then native effects,
// then bus pacing and light → bus overrides folded into one row.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import type { BusInfo, BusLimits, HomeAssistant, Settings } from "../shared/types";
import { defineElement, plural, saveSwitch, showToast, toastOnError } from "../shared/util";
import "./components/ml-fade-select";
import { type MlStore, partOptions, type Update } from "./store";
import { pageStyles } from "./styles";

const NONE = "__none__";

const BUS_KINDS: Record<string, string> = {
  zigbee: "Zigbee",
  hue: "Hue bridge",
  zwave: "Z-Wave",
  thread: "Thread",
  bridge: "Hub",
  ip: "Network",
  yeelight: "Yeelight",
  ble: "Bluetooth",
  cloud: "Cloud",
  unknown: "Unknown",
};

interface Field {
  label: string;
  unit: string;
  step: number;
  min: number;
  max: number;
  whole?: boolean;
}

/** The backend's ranges (BUS_LIMITS_SCHEMA). */
const LIMITS: [keyof BusLimits, Field][] = [
  ["rate", { label: "Messages per second", unit: "msg/s", step: 0.1, min: 0.1, max: 1000 }],
  ["burst", { label: "Burst", unit: "msg", step: 1, min: 1, max: 1000, whole: true }],
  ["min_interval", { label: "Per-light interval", unit: "s", step: 0.05, min: 0, max: 60 }],
];

type Input = HTMLInputElement & { value: string };

class MlSettings extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _newLight?: string;

  /** The seconds field behind "Custom…". */
  @state() private _custom = false;

  protected render() {
    const settings = this.store.config.settings;
    return html`
      <h2 class="section-title">Switching scenes</h2>
      <ha-card>${this._renderSwitching(settings)}</ha-card>
      <h2 class="section-title">Lights</h2>
      <ha-card>${this._renderLights(settings)} ${this._renderBuses(settings)}</ha-card>
      <p class="version">Magic Lights ${this.store.config.version}</p>
    `;
  }

  private _renderSwitching(settings: Settings) {
    return html`
      <div class="fade">
        <span class="field-label">Fade <span class="secondary">how long a scene change takes, in every room</span></span>
        <div class="fade-row">
          <ml-fade-select
            .value=${settings.transition}
            @value-changed=${(ev: CustomEvent<{ value: number | null }>) => {
              if (ev.detail.value !== null) this._save({ transition: ev.detail.value });
            }}
          ></ml-fade-select>
          ${
            this._custom
              ? html`<ha-input
                  class="custom"
                  type="number"
                  min="0"
                  max="300"
                  step="0.1"
                  autofocus
                  .label=${"Fade"}
                  .value=${String(settings.transition)}
                  @change=${(ev: Event) => this._saveCustomFade(ev.target as Input)}
                >
                  <span slot="end">s</span>
                </ha-input>`
              : html`<ha-button appearance="plain" @click=${() => (this._custom = true)}>Custom…</ha-button>`
          }
        </div>
        <span class="secondary hint-small">
          A scene can set its own fade in its editor. Adaptive and candle scenes use it for their first step too.
        </span>
      </div>
      <ha-settings-row .narrow=${this.narrow}>
        <span slot="heading">When a light is switched on</span>
        <span slot="description">
          The part a light switched on in a room that is off plays. Rooms can pick one of their scenes instead.
        </span>
        <ha-select
          .label=${"Part when a light is switched on"}
          .options=${[
            { value: NONE, label: "Nothing" },
            ...partOptions(this.store.parts()),
          ]}
          .value=${settings.default_scene ?? NONE}
          @selected=${(ev: CustomEvent<{ value: string }>) =>
            this._save({ default_scene: ev.detail.value === NONE ? null : ev.detail.value })}
        ></ha-select>
      </ha-settings-row>
    `;
  }

  private _renderLights(settings: Settings) {
    return html`
      <ha-settings-row>
        <span slot="heading">Native effects</span>
        <span slot="description">
          Use firmware effects and gradients (Hue on Zigbee2MQTT). Off sends plain colour commands
          to every light.
        </span>
        <ha-switch
          .checked=${settings.native_effects}
          @change=${(ev: Event) =>
            saveSwitch(
              this,
              ev,
              (checked) => this.store.saveSettings({ native_effects: checked }),
              "Could not save the setting",
            )}
        ></ha-switch>
      </ha-settings-row>
    `;
  }

  /** Pacing, folded: the row says how many networks and lights it covers. */
  private _renderBuses(settings: Settings) {
    const buses = this._buses();
    const lights = buses.reduce((n, bus) => n + bus.lights.length, 0);
    const summary = [
      buses.length === 1 ? this._busLabel(buses[0]) : plural(buses.length, "bus", "buses"),
      plural(lights, "light"),
      ...(buses.length === 1 ? [`${buses[0].limits.rate} msg/s`] : []),
    ].join(" · ");
    return html`<ha-expansion-panel class="all-buses" header="Buses" .secondary=${summary}>
      <p class="hint">
        Lights on one radio network or bridge share its message budget. Dynamic scenes pace
        themselves to it; raise the limits only if your network keeps up.
      </p>
      ${buses.map((bus) => this._renderBus(bus, settings))} ${this._renderLightBuses(settings)}
    </ha-expansion-panel>`;
  }

  /** Any number of seconds; an entry out of range is not saved. */
  private _saveCustomFade(input: Input): void {
    const value = Number(input.value);
    if (input.value.trim() === "" || !(value >= 0 && value <= 300)) {
      input.value = String(this.store.config.settings.transition);
      showToast(this, "Fade must be a number of seconds from 0 to 300");
      return;
    }
    this._custom = false;
    this._save({ transition: Math.round(value * 100) / 100 });
  }

  /** A light nothing could classify is its own bus, labelled by its entity id. */
  private _busLabel(bus: BusInfo): string {
    return bus.id.startsWith("entity:") && bus.lights.length === 1
      ? this.store.lightName(bus.lights[0])
      : bus.label;
  }

  /** Known networks first, then the unclassified lights. */
  private _buses(): BusInfo[] {
    const rank = (bus: BusInfo) => (bus.kind === "unknown" ? 1 : 0);
    return [...this.store.config.buses].sort(
      (a, b) => rank(a) - rank(b) || this._busLabel(a).localeCompare(this._busLabel(b)),
    );
  }

  private _renderBus(bus: BusInfo, settings: Settings) {
    const override = settings.buses[bus.id] ?? {};
    const kind = BUS_KINDS[bus.kind] ?? bus.kind;
    return html`<ha-expansion-panel
      .header=${this._busLabel(bus)}
      .secondary=${`${kind} · ${plural(bus.lights.length, "light")} · ${bus.limits.rate} msg/s`}
    >
      <div class="limits">
        ${LIMITS.map(
          ([key, field]) => html`<ha-input
            type="number"
            .min=${field.min}
            .max=${field.max}
            .step=${field.step}
            .label=${field.label}
            .placeholder=${String(bus.defaults[key])}
            .value=${override[key] === undefined ? "" : String(override[key])}
            .hint=${`Default ${bus.defaults[key]} ${field.unit}`}
            @change=${(ev: Event) => this._saveLimit(bus.id, key, field, ev.target as Input)}
          >
            <span slot="end">${field.unit}</span>
          </ha-input>`,
        )}
      </div>
      <p class="lights">${bus.lights.map((e) => this.store.lightName(e)).join(", ")}</p>
    </ha-expansion-panel>`;
  }

  private _renderLightBuses(settings: Settings) {
    const busOptions = this._buses().map((bus) => ({ value: bus.id, label: this._busLabel(bus) }));
    const overrides = Object.entries(settings.light_buses);
    return html`<ha-expansion-panel
      header="Light → bus overrides"
      .secondary=${overrides.length ? plural(overrides.length, "override") : "For lights on the wrong bus"}
    >
      ${overrides.map(
        ([entityId, busId]) => html`<div class="override">
          <span class="label">${this.store.lightName(entityId)}</span>
          <ha-select
            .label=${"Bus"}
            .options=${busOptions}
            .value=${busId}
            @selected=${(ev: CustomEvent<{ value: string }>) =>
              this._save((s) => ({ light_buses: { ...s.light_buses, [entityId]: ev.detail.value } }))}
          ></ha-select>
          <ha-icon-button .label=${"Remove override"} @click=${() => this._removeLightBus(entityId)}>
            <ha-icon icon="mdi:close"></ha-icon>
          </ha-icon-button>
        </div>`,
      )}
      <div class="override">
        <ha-selector
          class="label"
          .hass=${this.hass}
          .selector=${{
            entity: {
              include_entities: this.store.config.lights
                .map((l) => l.entity_id)
                .filter((e) => !(e in settings.light_buses)),
            },
          }}
          .label=${"Light"}
          .required=${false}
          .value=${this._newLight}
          @value-changed=${(ev: CustomEvent<{ value?: string }>) => (this._newLight = ev.detail.value)}
        ></ha-selector>
        ${
          this._newLight
            ? html`<ha-select
                .label=${"Bus"}
                .options=${busOptions}
                @selected=${(ev: CustomEvent<{ value: string }>) => this._addLightBus(ev.detail.value)}
              ></ha-select>`
            : nothing
        }
      </div>
    </ha-expansion-panel>`;
  }

  /** An entry out of range is not saved; the field shows the stored value again. */
  private _reject(field: Field, revert: () => void): void {
    revert();
    const kind = field.whole ? "a whole number" : "a number";
    showToast(this, `${field.label} must be ${kind} from ${field.min} to ${field.max} ${field.unit}`);
  }

  private _valid(raw: string, field: Field): number | undefined {
    const value = Number(raw);
    const ok = (!field.whole || Number.isInteger(value)) && value >= field.min && value <= field.max;
    return raw && ok ? value : undefined;
  }

  /** An empty field drops the override. */
  private _saveLimit(busId: string, key: keyof BusLimits, field: Field, input: Input): void {
    const stored = this.store.config.settings.buses[busId]?.[key];
    const revert = () => (input.value = stored === undefined ? "" : String(stored));
    const raw = input.value.trim();
    const value = this._valid(raw, field);
    if (raw && value === undefined) {
      this._reject(field, revert);
      return;
    }
    this._save(({ buses }) => {
      const limits: Partial<BusLimits> = { ...buses[busId] };
      if (value === undefined) delete limits[key];
      else limits[key] = value;
      const next = { ...buses, [busId]: limits };
      if (!Object.keys(limits).length) delete next[busId];
      return { buses: next };
    }, revert);
  }

  private _addLightBus(busId: string): void {
    const entityId = this._newLight;
    if (!entityId) return;
    this._newLight = undefined;
    this._save((s) => ({ light_buses: { ...s.light_buses, [entityId]: busId } }));
  }

  private _removeLightBus(entityId: string): void {
    this._save((s) => {
      const { [entityId]: _removed, ...rest } = s.light_buses;
      return { light_buses: rest };
    });
  }

  private _save(update: Update<Settings, Settings>, failed?: () => void): void {
    toastOnError(this, this.store.saveSettings(update), "Could not save the setting").then((ok) => {
      if (!ok) failed?.();
    });
  }

  static styles = [
    pageStyles,
    css`
      ha-card {
        overflow: hidden;
      }
      ha-select {
        min-width: 200px;
      }
      .fade {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-2);
        padding: var(--ha-space-4);
      }
      .field-label {
        font-weight: var(--ha-font-weight-medium);
      }
      .field-label .secondary {
        margin-inline-start: var(--ha-space-2);
        font-size: var(--ha-font-size-s);
        font-weight: var(--ha-font-weight-normal);
      }
      .fade-row {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--ha-space-2);
      }
      ml-fade-select {
        flex: 1 1 320px;
        max-width: 560px;
      }
      ha-input.custom {
        width: 120px;
        --ha-input-padding-bottom: 0;
      }
      ha-expansion-panel.all-buses {
        border-top: 1px solid var(--divider-color);
        border-bottom: none;
        --expansion-panel-content-padding: 0;
      }
      .all-buses .hint {
        margin: 0;
        padding: 0 var(--ha-space-4) var(--ha-space-3);
      }
      .all-buses ha-expansion-panel {
        border-top: 1px solid var(--divider-color);
        border-bottom: none;
      }
      .hint-small {
        font-size: var(--ha-font-size-s);
      }
      .fade + ha-settings-row {
        border-top: 1px solid var(--divider-color);
      }
      .hint {
        margin: 0 var(--ha-space-1) var(--ha-space-3);
        color: var(--secondary-text-color);
      }
      ha-expansion-panel {
        display: block;
        border-bottom: 1px solid var(--divider-color);
        --expansion-panel-summary-padding: 0 var(--ha-space-4);
        --expansion-panel-content-padding: 0 var(--ha-space-4) var(--ha-space-3);
      }
      ha-expansion-panel:last-child {
        border-bottom: none;
      }
      .limits {
        display: grid;
        gap: var(--ha-space-3);
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      }
      .lights {
        margin: var(--ha-space-2) 0 0;
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
      }
      .override {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--ha-space-2);
        min-height: 48px;
      }
      .override .label {
        flex: 1 1 200px;
        min-width: 0;
      }
      .version {
        margin: var(--ha-space-6) var(--ha-space-1) 0;
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
      }
    `,
  ];
}

defineElement("ml-settings", MlSettings);
