// Field of the magic_lights_color selector: a white (kelvin) or a colour (xy).
// Fires value-changed with a Color.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { colorToHex, colorToRgb, isKelvin, kelvinToRgb, rgbToXy, type Rgb } from "../../shared/color";
import type { Color, ColorSelectorConfig, HomeAssistant, PaletteInfo } from "../../shared/types";
import { defineElement, fire } from "../../shared/util";
import { hsToRgb, parseColor, rgbToHs, swatchColors, xyToKelvin } from "./colors";

type Mode = "white" | "color";

const DEFAULT_CONFIG: ColorSelectorConfig = {
  allow_white: true,
  allow_color: true,
  min_kelvin: 1000,
  max_kelvin: 10000,
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

class MlColorField extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  /** Any accepted colour input; emitted values are {kelvin} or {xy}. */
  @property({ attribute: false }) value?: unknown;

  @property({ attribute: false }) config?: Partial<ColorSelectorConfig>;

  @property({ attribute: false }) palettes: readonly PaletteInfo[] = [];

  @property() label?: string;

  @property() helper?: string;

  private get _config(): ColorSelectorConfig {
    return { ...DEFAULT_CONFIG, ...this.config };
  }

  private get _color(): Color | undefined {
    return parseColor(this.value);
  }

  private get _mode(): Mode {
    const { allow_white, allow_color } = this._config;
    if (!allow_color) return "white";
    if (!allow_white) return "color";
    const color = this._color;
    return color && !isKelvin(color) ? "color" : "white";
  }

  private get _kelvin(): number {
    const { min_kelvin, max_kelvin } = this._config;
    const color = this._color;
    const kelvin = !color ? 2700 : isKelvin(color) ? color.kelvin : xyToKelvin(color.xy);
    return clamp(kelvin, min_kelvin, max_kelvin);
  }

  private get _xy(): [number, number] {
    const color = this._color;
    if (color && !isKelvin(color)) return color.xy;
    return rgbToXy(kelvinToRgb(this._kelvin));
  }

  protected render() {
    const config = this._config;
    const mode = this._mode;
    return html`
      ${this.label ? html`<div class="label">${this.label}</div>` : nothing}
      ${
        config.allow_white && config.allow_color
          ? html`<ha-control-select
              .options=${[
                { value: "white", label: "White" },
                { value: "color", label: "Colour" },
              ]}
              .value=${mode}
              .label=${this.label ?? "Colour"}
              @value-changed=${this._modeChanged}
            ></ha-control-select>`
          : nothing
      }
      ${mode === "white" ? this._renderWhite(config) : this._renderColor()}
      ${this.helper ? html`<div class="helper">${this.helper}</div>` : nothing}
    `;
  }

  private _renderWhite(config: ColorSelectorConfig) {
    return html`<ha-selector
      .hass=${this.hass}
      .selector=${{ color_temp: { unit: "kelvin", min: config.min_kelvin, max: config.max_kelvin } }}
      .value=${this._kelvin}
      .required=${false}
      @value-changed=${this._kelvinChanged}
    ></ha-selector>`;
  }

  private _renderColor() {
    const current = colorToHex({ xy: this._xy });
    const swatches = swatchColors(this.palettes);
    const wheel = !!customElements.get("ha-hs-color-picker");
    return html`
      ${
        swatches.length
          ? html`<div class="swatches" role="group" aria-label="Suggested colours">
              ${swatches.map((color) => {
                const hex = colorToHex(color);
                return html`<button
                  class="swatch ${hex === current ? "selected" : ""}"
                  style="background: ${hex}"
                  title=${hex}
                  aria-label=${hex}
                  aria-pressed=${hex === current}
                  @click=${() => this._emit(color)}
                ></button>`;
              })}
            </div>`
          : nothing
      }
      <div class="picker">
        ${
          wheel
            ? html`<ha-hs-color-picker
                .value=${rgbToHs(colorToRgb({ xy: this._xy }))}
                @value-changed=${this._hsChanged}
              ></ha-hs-color-picker>`
            : html`<ha-selector
                .hass=${this.hass}
                .selector=${{ color_rgb: {} }}
                .value=${colorToRgb({ xy: this._xy })}
                .label=${"Custom colour"}
                .required=${false}
                @value-changed=${this._rgbChanged}
              ></ha-selector>`
        }
        ${
          wheel
            ? html`<span class="current" style="background: ${current}" title=${current}></span>`
            : nothing
        }
      </div>
    `;
  }

  private _modeChanged(ev: CustomEvent<{ value: Mode }>): void {
    ev.stopPropagation();
    if (ev.detail.value === this._mode) return;
    this._emit(ev.detail.value === "white" ? { kelvin: this._kelvin } : { xy: this._xy });
  }

  private _kelvinChanged(ev: CustomEvent<{ value?: number }>): void {
    ev.stopPropagation();
    if (typeof ev.detail.value === "number") this._emit({ kelvin: Math.round(ev.detail.value) });
  }

  private _hsChanged(ev: CustomEvent<{ value?: [number, number] }>): void {
    ev.stopPropagation();
    if (ev.detail.value) this._emit({ xy: rgbToXy(hsToRgb(ev.detail.value)) });
  }

  private _rgbChanged(ev: CustomEvent<{ value?: Rgb }>): void {
    ev.stopPropagation();
    const color = parseColor({ rgb: ev.detail.value });
    if (color) this._emit(color);
  }

  private _emit(value: Color): void {
    fire(this, "value-changed", { value });
  }

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3);
    }
    .label {
      color: var(--primary-text-color);
    }
    .helper {
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .swatches {
      display: flex;
      flex-wrap: wrap;
      gap: var(--ha-space-2);
    }
    .swatch {
      width: 32px;
      height: 32px;
      padding: 0;
      border: none;
      border-radius: var(--ha-border-radius-circle);
      box-shadow: inset 0 0 0 1px var(--divider-color);
      cursor: pointer;
    }
    .swatch.selected {
      box-shadow:
        0 0 0 2px var(--card-background-color),
        0 0 0 4px var(--primary-color);
    }
    .swatch:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
      outline-offset: 2px;
    }
    .picker {
      display: flex;
      align-items: center;
      gap: var(--ha-space-4);
    }
    .picker ha-selector {
      flex: 1;
    }
    ha-hs-color-picker {
      width: min(220px, 100%);
      aspect-ratio: 1;
    }
    .current {
      flex: none;
      width: 40px;
      height: 40px;
      border-radius: var(--ha-border-radius-md);
      box-shadow: inset 0 0 0 1px var(--divider-color);
    }
  `;
}

defineElement("ml-color-field", MlColorField);
