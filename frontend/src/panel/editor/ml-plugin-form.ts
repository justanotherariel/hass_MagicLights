// An effect's params form built from its serialised schema. Each field gets the control
// form-rules.ts picks from its selector's shape and layout hints: bars with the value in
// them, segmented choices, a kelvin gradient, one range for a min/max pair, our own
// colour fields, else HA's ha-selector; "advanced" fields fold into one panel. ha-form
// visibility conditions apply, and a YAML object editor stands in when the schema could
// not be serialised. Fires value-changed with the whole params object, prefilled; hidden
// fields keep their values.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { kelvinGradient } from "../../shared/color";
import type { HaFormSchema, HomeAssistant, PaletteInfo } from "../../shared/types";
import { defineElement, fire } from "../../shared/util";
import "../components/ml-range-bar";
import { AMBER } from "../components/ml-value-bar";
import "../components/ml-value-bar";
import { labelledSelector, nextParams, withDefaults } from "./editor-model";
import { advancedSummary, formLayout, stepChoices, type Control } from "./form-rules";
import "./ml-color-field";
import "./ml-palette-field";

type Params = Record<string, unknown>;

const num = (value: unknown, fallback: number): number => (typeof value === "number" ? value : fallback);

class MlPluginForm extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  /** null: the effect's schema could not be serialised. */
  @property({ attribute: false }) schema: HaFormSchema[] | null = [];

  @property({ attribute: false }) data: Params = {};

  @property({ attribute: false }) defaults: Params = {};

  @property({ attribute: false }) palettes: readonly PaletteInfo[] = [];

  protected render() {
    if (this.schema === null) {
      return html`<ha-selector
        .hass=${this.hass}
        .selector=${{ object: {} }}
        .value=${this.data}
        .label=${"Parameters"}
        .helper=${"This effect has no form; edit its parameters as YAML."}
        .required=${false}
        @value-changed=${(ev: CustomEvent<{ value?: Params }>) => {
          ev.stopPropagation();
          this._emit(ev.detail.value ?? {});
        }}
      ></ha-selector>`;
    }
    if (!this.schema.length) return html`<p class="empty">This effect has no settings.</p>`;
    const values = withDefaults(this.defaults, this.data);
    const { main, advanced } = formLayout(this.schema, values);
    return html`
      ${main.map((row) => this._renderRow(row, values))}
      ${
        advanced.length
          ? html`<ha-expansion-panel outlined header="Advanced" .secondary=${advancedSummary(advanced)}>
              <div class="advanced">${advanced.map((row) => this._renderRow(row, values))}</div>
            </ha-expansion-panel>`
          : nothing
      }
    `;
  }

  private _renderRow(row: Control[], values: Params) {
    if (row.length === 1) return this._renderField(row[0], values);
    return html`<div class="row">${row.map((control) => this._renderField(control, values))}</div>`;
  }

  /** Label above, the control, the helper below; HA's own fields bring their own label. */
  private _renderField(control: Control, values: Params) {
    if (control.kind === "selector" || control.kind === "color" || control.kind === "palette") {
      return this._renderControl(control, values);
    }
    return html`<div class="field">
      <span class="field-label">${control.label}</span>
      ${this._renderControl(control, values)}
      ${control.item.helper ? html`<span class="helper">${control.item.helper}</span>` : nothing}
    </div>`;
  }

  private _renderControl(control: Control, values: Params) {
    const { item } = control;
    const value = values[item.name];
    const changed = (ev: CustomEvent<{ value?: unknown }>) => {
      ev.stopPropagation();
      this._changed({ [item.name]: ev.detail.value });
    };
    switch (control.kind) {
      case "color":
        return html`<ml-color-field
          .hass=${this.hass}
          .value=${value}
          .config=${item.selector?.magic_lights_color ?? undefined}
          .palettes=${this.palettes}
          .label=${control.label}
          .helper=${item.helper}
          @value-changed=${changed}
        ></ml-color-field>`;
      case "palette":
        return html`<ml-palette-field
          .hass=${this.hass}
          .value=${value}
          .config=${item.selector?.magic_lights_palette ?? undefined}
          .palettes=${this.palettes}
          .label=${control.label}
          .helper=${item.helper}
          @value-changed=${changed}
        ></ml-palette-field>`;
      case "bar":
        return html`<ml-value-bar
          .value=${num(value, control.min)}
          .min=${control.min}
          .max=${control.max}
          .step=${control.step}
          .unit=${control.unit}
          .label=${control.label}
          .icon=${control.big ? "mdi:brightness-6" : undefined}
          .color=${control.big ? AMBER : undefined}
          size=${control.big ? "big" : "compact"}
          @value-changed=${changed}
        ></ml-value-bar>`;
      case "kelvin":
        return html`<ml-value-bar
          .value=${num(value, control.min)}
          .min=${control.min}
          .max=${control.max}
          .step=${50}
          unit="K"
          .label=${control.label}
          .background=${kelvinGradient(control.min, control.max)}
          size="compact"
          @value-changed=${changed}
        ></ml-value-bar>`;
      case "range": {
        const high = control.high;
        return html`<ml-range-bar
          .low=${num(value, control.min)}
          .high=${num(values[high.name], control.max)}
          .min=${control.min}
          .max=${control.max}
          .step=${control.step}
          .unit=${control.unit}
          .labels=${[control.item.label ?? control.item.name, high.label ?? high.name]}
          .background=${control.kelvin ? kelvinGradient(control.min, control.max) : undefined}
          .color=${control.kelvin ? undefined : AMBER}
          @value-changed=${(ev: CustomEvent<{ value: [number, number] }>) => {
            ev.stopPropagation();
            const [low, up] = ev.detail.value;
            this._changed({ [item.name]: low, [high.name]: up });
          }}
        ></ml-range-bar>`;
      }
      case "steps":
        return this._renderSegments(
          control,
          stepChoices(control.steps, value, control.unit),
          typeof value === "number" ? String(value) : undefined,
          (picked) => Number(picked),
        );
      case "segments":
        return this._renderSegments(
          control,
          control.options,
          typeof value === "string" ? value : undefined,
          (picked) => picked,
        );
      case "selector":
        // A defaulted field is never empty for the backend, so it is not marked required.
        return html`<ha-selector
          .hass=${this.hass}
          .selector=${labelledSelector(control.selector)}
          .value=${value}
          .label=${control.label}
          .helper=${item.helper}
          .required=${!!item.required && item.default === undefined}
          @value-changed=${changed}
        ></ha-selector>`;
    }
  }

  private _renderSegments(
    control: Control,
    options: { value: string; label: string }[],
    value: string | undefined,
    parse: (picked: string) => unknown,
  ) {
    return html`<ha-control-select
      .options=${options}
      .value=${value}
      .label=${control.label}
      @value-changed=${(ev: CustomEvent<{ value: string }>) => {
        ev.stopPropagation();
        this._changed({ [control.item.name]: parse(ev.detail.value) });
      }}
    ></ha-control-select>`;
  }

  private _changed(update: Params): void {
    let params = this.data;
    for (const [name, value] of Object.entries(update)) params = nextParams(this.defaults, params, name, value);
    this._emit(params);
  }

  private _emit(value: Params): void {
    fire(this, "value-changed", { value });
  }

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-5);
      container-type: inline-size;
    }
    ha-selector,
    ml-color-field,
    ml-palette-field {
      display: block;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
      min-width: 0;
    }
    .field-label {
      font-weight: var(--ha-font-weight-medium);
      color: var(--primary-text-color);
    }
    .helper {
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .row {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: var(--ha-space-5) var(--ha-space-4);
    }
    @container (min-width: 480px) {
      .row {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    ha-control-select {
      --control-select-thickness: 52px;
      --control-select-border-radius: var(--ha-border-radius-lg, 12px);
    }
    ha-expansion-panel {
      display: block;
      --expansion-panel-content-padding: 0 var(--ha-space-4);
    }
    .advanced {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-5);
      padding: var(--ha-space-2) 0 var(--ha-space-4);
    }
    .empty {
      margin: 0;
      color: var(--secondary-text-color);
    }
  `;
}

defineElement("ml-plugin-form", MlPluginForm);
