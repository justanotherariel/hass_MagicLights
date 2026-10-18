// Field of the magic_lights_palette selector: an ordered list of colours as large
// swatches, drag to reorder, tap to edit, add/remove within min..max, curated palettes
// and shuffle.
// Fires value-changed with Color[].

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { colorToHex, swatchBackground } from "../../shared/color";
import type { Color, HomeAssistant, PaletteInfo, PaletteSelectorConfig } from "../../shared/types";
import { defineElement, fire } from "../../shared/util";
import { parseColor, swatchColors } from "./colors";
import "./ml-color-field";

const DEFAULT_CONFIG: PaletteSelectorConfig = { min: 1, max: 9 };

const FALLBACK_COLOR: Color = { xy: [0.5019, 0.4152] };

const shuffled = <T>(items: readonly T[]): T[] => {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  // A shuffle that changes nothing looks broken.
  return out.every((item, i) => item === items[i]) ? [...out.slice(1), out[0]] : out;
};

class MlPaletteField extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  /** Accepted colour inputs; emitted values are {kelvin} or {xy}. */
  @property({ attribute: false }) value?: unknown;

  @property({ attribute: false }) config?: Partial<PaletteSelectorConfig>;

  @property({ attribute: false }) palettes: readonly PaletteInfo[] = [];


  /** Index of the colour in the dialog; kept while the dialog animates out. */
  @state() private _editing?: number;

  @state() private _dialogOpen = false;

  private get _colors(): Color[] {
    return Array.isArray(this.value) ? this.value.flatMap((c) => parseColor(c) ?? []) : [];
  }

  private get _config(): PaletteSelectorConfig {
    return { ...DEFAULT_CONFIG, ...this.config };
  }

  protected render() {
    const colors = this._colors;
    const { max } = this._config;
    return html`
      <ha-sortable @item-moved=${this._moved} draggable-selector=".swatch">
        <div class="swatches">
          ${colors.map(
            (color, i) => html`<button
              class="swatch"
              style="background: ${colorToHex(color)}"
              aria-label=${`Colour ${i + 1}: ${colorToHex(color)}`}
              title="Tap to change, drag to reorder"
              @click=${() => this._edit(i)}
            ></button>`,
          )}
          ${
            colors.length < max
              ? html`<button class="add" aria-label="Add colour" title="Add colour" @click=${this._add}>
                  <ha-icon icon="mdi:plus"></ha-icon>
                </button>`
              : nothing
          }
        </div>
      </ha-sortable>
      <div class="actions">
        ${
          this.palettes.length
            ? html`<ha-dropdown placement="bottom-start" @wa-select=${this._load}>
                <ha-button slot="trigger" appearance="outlined" size="s">
                  <ha-icon slot="start" icon="mdi:palette-swatch-outline"></ha-icon>
                  Load palette…
                </ha-button>
                ${this.palettes.map(
                  (palette) => html`<ha-dropdown-item value=${palette.id}>
                    <span
                      slot="icon"
                      class="mini"
                      style="background: ${swatchBackground(palette.colors.map(colorToHex))}"
                    ></span>
                    ${palette.name}
                  </ha-dropdown-item>`,
                )}
              </ha-dropdown>`
            : nothing
        }
        <ha-button
          appearance="outlined"
          size="s"
          .disabled=${colors.length < 2}
          @click=${() => this._emit(shuffled(colors))}
        >
          <ha-icon slot="start" icon="mdi:shuffle-variant"></ha-icon>
          Shuffle
        </ha-button>
      </div>
      ${this._renderDialog()}
    `;
  }

  private _renderDialog() {
    const index = this._editing;
    if (index === undefined) return nothing;
    const colors = this._colors;
    return html`<ha-dialog
      .open=${this._dialogOpen}
      width="small"
      .headerTitle=${`Colour ${index + 1} of ${colors.length}`}
      @closed=${this._dialogClosed}
    >
      <ml-color-field
        .hass=${this.hass}
        .value=${colors[index]}
        .palettes=${this.palettes}
        @value-changed=${(ev: CustomEvent<{ value: Color }>) => {
          ev.stopPropagation();
          this._emit(colors.map((c, i) => (i === index ? ev.detail.value : c)));
        }}
      ></ml-color-field>
      <ha-dialog-footer slot="footer">
        <ha-button
          slot="secondaryAction"
          appearance="plain"
          variant="danger"
          .disabled=${colors.length <= this._config.min}
          @click=${() => this._remove(index)}
        >
          Remove
        </ha-button>
        <ha-button slot="primaryAction" data-dialog="close">Done</ha-button>
      </ha-dialog-footer>
    </ha-dialog>`;
  }

  private _dialogClosed(ev: Event): void {
    // Menus inside the dialog fire their own "closed".
    if (ev.target !== ev.currentTarget) return;
    this._editing = undefined;
    this._dialogOpen = false;
  }

  private _edit(index: number): void {
    this._editing = index;
    this._dialogOpen = true;
  }

  private _add(): void {
    const colors = this._colors;
    const taken = new Set(colors.map(colorToHex));
    const next =
      swatchColors(this.palettes).find((color) => !taken.has(colorToHex(color))) ?? FALLBACK_COLOR;
    this._emit([...colors, next]);
    this._edit(colors.length);
  }

  private _remove(index: number): void {
    this._dialogOpen = false;
    this._emit(this._colors.filter((_, i) => i !== index));
  }

  private _moved(ev: CustomEvent<{ oldIndex: number; newIndex: number }>): void {
    ev.stopPropagation();
    const next = [...this._colors];
    next.splice(ev.detail.newIndex, 0, ...next.splice(ev.detail.oldIndex, 1));
    this._emit(next);
  }

  private _load(ev: CustomEvent<{ item: { value: string } }>): void {
    ev.stopPropagation();
    const palette = this.palettes.find((p) => p.id === ev.detail.item.value);
    if (palette) this._emit(structuredClone(palette.colors.slice(0, this._config.max)));
  }

  private _emit(value: Color[]): void {
    fire(this, "value-changed", { value });
  }

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
    }
    .swatches {
      display: flex;
      flex-wrap: wrap;
      gap: var(--ha-space-2);
    }
    .swatch,
    .add {
      width: 56px;
      height: 56px;
      padding: 0;
      border-radius: var(--ha-border-radius-lg, 12px);
    }
    .swatch {
      border: none;
      box-shadow: inset 0 0 0 1px var(--divider-color);
      cursor: grab;
    }
    .add {
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px dashed var(--secondary-text-color);
      background: none;
      color: var(--secondary-text-color);
      cursor: pointer;
    }
    .swatch:focus-visible,
    .add:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
      outline-offset: 2px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--ha-space-1);
    }
    .mini {
      display: inline-block;
      width: 28px;
      height: 14px;
      border-radius: var(--ha-border-radius-pill);
      box-shadow: inset 0 0 0 1px var(--divider-color);
    }
  `;
}

defineElement("ml-palette-field", MlPaletteField);
