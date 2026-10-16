// The selected layer: its lights ("All lights", entities, zone rooms), its effect and
// the effect's params form. Fires ml-layer-changed with the edited layer.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import type { HomeAssistant, Layer, TargetInfo } from "../../shared/types";
import { checkedOf, defineElement, fire, plural } from "../../shared/util";
import type { MlStore } from "../store";
import { chipStyles } from "../styles";
import { expandLights } from "./editor-model";
import "./ml-plugin-form";

export interface LayerChanged {
  layer: Layer;
}

// Effects light_effect emulates on lights without them (effects/light_effect.py FLAMES).
const EMULATED_EFFECTS = new Set(["candle", "fireplace"]);

class MlLayerCard extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) target!: TargetInfo;

  @property({ attribute: false }) layer!: Layer;

  /** Lights of this layer that a later layer sets instead. */
  @property({ attribute: false }) overridden: string[] = [];

  @property({ attribute: false }) problems: string[] = [];

  protected render() {
    const layer = this.layer;
    const effect = this.store.effect(layer.effect);
    return html`<ha-card>
      <div class="card-content">
        ${this.problems.map((p) => html`<ha-alert alert-type="warning">${p}</ha-alert>`)}
        <section>
          <ha-switch .checked=${layer.lights === "all"} @change=${this._allChanged}>All lights</ha-switch>
          ${layer.lights === "all" ? nothing : this._renderPickers(layer.lights)}
          ${
            this.overridden.length
              ? html`<p class="hint">
                  ${plural(this.overridden.length, "light")} of this layer
                  ${this.overridden.length === 1 ? "is" : "are"} set by a later layer:
                  ${this.overridden.map((e) => this.store.lightName(e)).join(", ")}.
                </p>`
              : nothing
          }
        </section>
        <section>
          <ha-select
            .label=${"Layer effect"}
            .options=${this._effectOptions()}
            .value=${layer.effect}
            .helper=${effect?.description || undefined}
            @selected=${this._effectChanged}
          ></ha-select>
          ${this._renderNativeHint()}
          ${
            effect
              ? html`<ml-plugin-form
                  .hass=${this.hass}
                  .schema=${effect.schema}
                  .data=${layer.params}
                  .defaults=${effect.defaults}
                  .palettes=${this.store.config.palettes}
                  @value-changed=${(ev: CustomEvent<{ value: Record<string, unknown> }>) => {
                    ev.stopPropagation();
                    this._emit({ ...layer, params: ev.detail.value });
                  }}
                ></ml-plugin-form>`
              : nothing
          }
        </section>
      </div>
    </ha-card>`;
  }

  private _renderPickers(lights: string[]) {
    const rooms = lights.filter((l) => l.startsWith("room:"));
    const entities = lights.filter((l) => !l.startsWith("room:"));
    return html`
      ${
        this.target.kind === "zone" && this.target.rooms.length
          ? html`<div class="rooms" role="group" aria-label="Rooms">
              ${this.target.rooms.map((area) => {
                const ref = `room:${area}`;
                const active = rooms.includes(ref);
                return html`<ha-assist-chip
                  filled
                  class=${active ? "active" : ""}
                  .active=${active}
                  .label=${this.hass.areas?.[area]?.name ?? this.store.targetName(ref)}
                  @click=${() =>
                    this._setLights(active ? lights.filter((l) => l !== ref) : [...lights, ref])}
                >
                  <ha-icon
                    slot="icon"
                    .icon=${active ? "mdi:check" : (this.hass.areas?.[area]?.icon ?? "mdi:texture-box")}
                  ></ha-icon>
                </ha-assist-chip>`;
              })}
            </div>`
          : nothing
      }
      <ha-selector
        .hass=${this.hass}
        .selector=${{ entity: { multiple: true, include_entities: this.target.lights } }}
        .value=${entities}
        .label=${rooms.length ? "More lights" : "Lights"}
        .required=${false}
        @value-changed=${(ev: CustomEvent<{ value?: string[] }>) => {
          ev.stopPropagation();
          this._setLights([...rooms, ...(ev.detail.value ?? [])]);
        }}
      ></ha-selector>
    `;
  }

  /** Which of the layer's lights run the chosen firmware effect themselves. */
  private _renderNativeHint() {
    const name = this.layer.params.effect;
    if (this.layer.effect !== "light_effect" || typeof name !== "string" || !name) return nothing;
    const lights = expandLights(this.layer.lights, this.target, (id) => this.store.target(id)?.lights);
    if (!lights.length) return nothing;
    const native = lights.filter((e) => this.store.light(e)?.caps.effects.includes(name));
    const others = lights.length - native.length;
    const emulated = EMULATED_EFFECTS.has(name);
    let text: string;
    if (!native.length) {
      text = emulated
        ? "None of these lights has it built in, so it is emulated."
        : "None of these lights has it built in, so they show the colour.";
    } else {
      text = `Built in on ${native.map((e) => this.store.lightName(e)).join(", ")}.`;
      if (others) {
        text += emulated
          ? ` Emulated on the other ${plural(others, "light")}.`
          : ` The other ${plural(others, "light")} show the colour.`;
      }
    }
    return html`<ha-alert alert-type="info">${text}</ha-alert>`;
  }

  private _effectOptions() {
    const options = this.store.config.effects.map((effect) => ({ value: effect.key, label: effect.name }));
    if (!this.store.effect(this.layer.effect)) {
      options.push({ value: this.layer.effect, label: `${this.layer.effect} (not installed)` });
    }
    return options;
  }

  private _allChanged(ev: Event): void {
    this._setLights(checkedOf(ev) ? "all" : []);
  }

  private _setLights(lights: Layer["lights"]): void {
    this._emit({ ...this.layer, lights });
  }

  private _effectChanged(ev: CustomEvent<{ value?: string }>): void {
    ev.stopPropagation();
    const effect = ev.detail.value ? this.store.effect(ev.detail.value) : undefined;
    if (!effect || effect.key === this.layer.effect) return;
    this._emit({ ...this.layer, effect: effect.key, params: structuredClone(effect.defaults) });
  }

  private _emit(layer: Layer): void {
    fire<LayerChanged>(this, "ml-layer-changed", { layer });
  }

  static styles = [
    chipStyles,
    css`
      :host {
        display: block;
      }
      section {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-4);
      }
      section + section {
        margin-top: var(--ha-space-6);
        padding-top: var(--ha-space-6);
        border-top: 1px solid var(--divider-color);
      }
      .rooms {
        display: flex;
        flex-wrap: wrap;
        gap: var(--ha-space-2);
      }
      .hint {
        margin: 0;
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
      }
      ha-select {
        display: block;
      }
      ha-alert {
        display: block;
      }
      .card-content > ha-alert {
        margin-bottom: var(--ha-space-4);
      }
    `,
  ];
}

defineElement("ml-layer-card", MlLayerCard);
