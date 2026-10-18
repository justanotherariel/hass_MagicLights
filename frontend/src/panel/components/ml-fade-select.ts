// How long switching scenes takes, as segments: Instant · Quick · Smooth · Slow, plus the
// stored value when it is none of them. With `fallback` (the setting) a scene's control
// leads with "Default", which stores null. Fires value-changed with seconds or null.

import { LitElement, css, html } from "lit";
import { property } from "lit/decorators.js";
import { defineElement, fire } from "../../shared/util";
import { fadeChoices, fadeSeconds, fadeValue } from "../fade";

/** Wrapping keeps a value whole: "Quick" / "0.4 s", "Default" / "1 s". */
const keepWhole = (label: string): string => label.replace(/ (\S+) s$/, " $1\u00a0s");

class MlFadeSelect extends LitElement {
  /** Seconds; null follows the setting. */
  @property({ attribute: false }) value: number | null = null;

  /** The setting a scene's "Default" follows; unset on the settings page itself. */
  @property({ attribute: false }) fallback?: number;

  @property() label = "Fade";

  @property({ type: Boolean }) disabled = false;

  protected render() {
    const options = fadeChoices(this.value, this.fallback).map((choice) => ({
      value: choice.value,
      label: keepWhole(choice.label),
      icon: choice.icon ? html`<ha-icon .icon=${choice.icon}></ha-icon>` : undefined,
    }));
    return html`<ha-control-select
      .options=${options}
      .value=${fadeValue(this.value)}
      .label=${this.label}
      .disabled=${this.disabled}
      @value-changed=${(ev: CustomEvent<{ value: string }>) => {
        ev.stopPropagation();
        fire(this, "value-changed", { value: fadeSeconds(ev.detail.value) });
      }}
    ></ha-control-select>`;
  }

  static styles = css`
    :host {
      display: block;
    }
    ha-control-select {
      font-size: var(--ha-font-size-s);
      --control-select-thickness: 56px;
      --control-select-border-radius: var(--ha-border-radius-lg, 12px);
      --mdc-icon-size: 18px;
    }
  `;
}

defineElement("ml-fade-select", MlFadeSelect);
