// Target screen: the Scenes tab (the "now" hero and the scene tiles), Lights or Settings.

import { LitElement, html, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import type { HomeAssistant } from "../shared/types";
import { defineElement } from "../shared/util";
import "./ml-light-list";
import "./ml-scene-gallery";
import "./ml-target-hero";
import "./ml-target-settings";
import type { TargetTab } from "./routes";
import type { MlStore } from "./store";
import { pageStyles } from "./styles";

class MlTargetPage extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @property({ attribute: false }) tab: TargetTab = "scenes";

  @property({ type: Boolean }) narrow = false;

  @state() private _arranging = false;

  /** The app bar's Arrange: reorder and hide the menu's tiles. */
  arrange(): void {
    this._arranging = true;
  }

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("targetId") || changed.has("tab")) this._arranging = false;
  }

  protected render() {
    switch (this.tab) {
      case "lights":
        return html`<ml-light-list .hass=${this.hass} .store=${this.store} .targetId=${this.targetId}></ml-light-list>`;
      case "settings":
        return html`<ml-target-settings
          .hass=${this.hass}
          .store=${this.store}
          .targetId=${this.targetId}
          .narrow=${this.narrow}
        ></ml-target-settings>`;
      default:
        return html`<ml-target-hero
            .hass=${this.hass}
            .store=${this.store}
            .targetId=${this.targetId}
            .narrow=${this.narrow}
          ></ml-target-hero>
          <ml-scene-gallery
            .hass=${this.hass}
            .store=${this.store}
            .targetId=${this.targetId}
            .narrow=${this.narrow}
            .arranging=${this._arranging}
            @ml-arrange-done=${() => (this._arranging = false)}
          ></ml-scene-gallery>`;
    }
  }

  static styles = pageStyles;
}

defineElement("ml-target-page", MlTargetPage);
