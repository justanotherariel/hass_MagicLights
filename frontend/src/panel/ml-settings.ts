// Global settings: fade, default scene, native effects, bus pacing and light → bus overrides.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import type { BusInfo, BusLimits, HomeAssistant, Settings } from "../shared/types";
import { defineElement, plural, saveSwitch, showToast, toastOnError } from "../shared/util";
import type { MlStore, Update } from "./store";
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

const TRANSITION: Field = { label: "Fade", unit: "s", step: 0.1, min: 0, max: 300 };

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

  protected render() {
    const settings = this.store.config.settings;
    return html`
      <h2 class="section-title">General</h2>
      <ha-card>${this._renderGeneral(settings)}</ha-card>
      <h2 class="section-title">Buses</h2>
      <p class="hint">
        Lights on one radio network or bridge share its message budget. Dynamic scenes pace
        themselves to it; raise the limits only if your network keeps up.
      </p>
      <ha-card>
        ${this._buses().map((bus) => this._renderBus(bus, settings))}
        ${this._renderLightBuses(settings)}
      </ha-card>
      <p class="version">Magic Lights ${this.store.config.version}</p>
    `;
  }

  private _renderGeneral(settings: Settings) {
    const scenes = this.store.globalScenes();
    return html`
      <ha-settings-row>
        <span slot="heading">Fade</span>
        <span slot="description">How long switching scenes takes. A scene can set its own.</span>
        <ha-input
          type="number"
          .min=${TRANSITION.min}
          .max=${TRANSITION.max}
          .step=${TRANSITION.step}
          class="number"
          aria-label=${TRANSITION.label}
          .value=${String(settings.transition)}
          @change=${(ev: Event) => this._saveTransition(ev.target as Input)}
        >
          <span slot="end">${TRANSITION.unit}</span>
        </ha-input>
      </ha-settings-row>
      <ha-settings-row .narrow=${this.narrow}>
        <span slot="heading">When a light is switched on</span>
        <span slot="description">
          Scene for a light switched on in a room that is off. Rooms can override this.
        </span>
        <ha-select
          .label=${"Scene when a light is switched on"}
          .options=${[
            { value: NONE, label: "Nothing" },
            ...this.store.sceneOptions(scenes, (scene) => scene.id),
          ]}
          .value=${settings.default_scene ?? NONE}
          @selected=${(ev: CustomEvent<{ value: string }>) =>
            this._save({ default_scene: ev.detail.value === NONE ? null : ev.detail.value })}
        ></ha-select>
      </ha-settings-row>
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

  private _saveTransition(input: Input): void {
    const revert = () => (input.value = String(this.store.config.settings.transition));
    const value = this._valid(input.value.trim(), TRANSITION);
    if (value === undefined) this._reject(TRANSITION, revert);
    else this._save({ transition: value }, revert);
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
      ha-input.number {
        width: 120px;
        --ha-input-padding-bottom: 0;
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
