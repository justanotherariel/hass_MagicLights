// An effect's params form built from its serialised schema: one ha-selector per
// item, our own fields for the custom colour selectors, ha-form visibility conditions,
// and a YAML object editor when the schema could not be serialised. Fires value-changed
// with the whole params object; hidden fields are dropped, visible ones prefilled.

import { LitElement, css, html } from "lit";
import { property } from "lit/decorators.js";
import { getHiddenFields } from "../../shared/conditions";
import type { HaFormSchema, HomeAssistant, PaletteInfo } from "../../shared/types";
import { defineElement, fire, humanise } from "../../shared/util";
import { labelledSelector, withDefaults } from "./editor-model";
import "./ml-color-field";
import "./ml-palette-field";

type Params = Record<string, unknown>;

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
    const hidden = getHiddenFields(this.schema, values);
    return this.schema
      .filter((item) => !hidden.has(item.name))
      .map((item) => this._renderItem(item, values[item.name]));
  }

  private _renderItem(item: HaFormSchema, value: unknown) {
    const selector = item.selector ?? {};
    const label = item.label ?? humanise(item.name);
    const changed = (ev: CustomEvent<{ value?: unknown }>) => {
      ev.stopPropagation();
      this._changed(item.name, ev.detail.value);
    };
    if ("magic_lights_color" in selector) {
      return html`<ml-color-field
        .hass=${this.hass}
        .value=${value}
        .config=${selector.magic_lights_color ?? undefined}
        .palettes=${this.palettes}
        .label=${label}
        .helper=${item.helper}
        @value-changed=${changed}
      ></ml-color-field>`;
    }
    if ("magic_lights_palette" in selector) {
      return html`<ml-palette-field
        .hass=${this.hass}
        .value=${value}
        .config=${selector.magic_lights_palette ?? undefined}
        .palettes=${this.palettes}
        .label=${label}
        .helper=${item.helper}
        @value-changed=${changed}
      ></ml-palette-field>`;
    }
    // A defaulted field is never empty for the backend, so it is not marked required.
    return html`<ha-selector
      .hass=${this.hass}
      .selector=${labelledSelector(selector)}
      .value=${value}
      .label=${label}
      .helper=${item.helper}
      .required=${!!item.required && item.default === undefined}
      @value-changed=${changed}
    ></ha-selector>`;
  }

  private _changed(name: string, value: unknown): void {
    const next = withDefaults(this.defaults, {
      ...this.data,
      [name]: value === "" ? undefined : value,
    });
    for (const field of getHiddenFields(this.schema ?? [], next)) delete next[field];
    this._emit(next);
  }

  private _emit(value: Params): void {
    fire(this, "value-changed", { value });
  }

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-6);
    }
    ha-selector,
    ml-color-field,
    ml-palette-field {
      display: block;
    }
    .empty {
      margin: 0;
      color: var(--secondary-text-color);
    }
  `;
}

defineElement("ml-plugin-form", MlPluginForm);
