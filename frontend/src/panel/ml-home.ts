// Home: one tile per room, then per zone and "New zone", plus the one-time migration notes.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import type { HomeAssistant, TargetInfo } from "../shared/types";
import { defineElement, fire, navigate, toastOnError } from "../shared/util";
import "./ml-target-tile";
import type { MlStore } from "./store";
import { newCardStyles, pageStyles } from "./styles";

class MlHome extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ type: Boolean }) narrow = false;

  protected render() {
    const notes = this.store.config.settings.migration_notes;
    const rooms = this.store.targets.filter((t) => t.kind === "room");
    const zones = this.store.targets.filter((t) => t.kind === "zone");
    return html`
      ${
        notes.length
          ? html`<ha-alert
              alert-type="info"
              title="Imported from Magic Lights 1"
              dismissable
              @alert-dismissed-clicked=${this._dismissNotes}
            >
              <ul>
                ${notes.map((note) => html`<li>${note}</li>`)}
              </ul>
            </ha-alert>`
          : nothing
      }
      ${
        rooms.length
          ? this._section("Rooms", rooms)
          : html`<ha-card class="empty">
              <ha-icon icon="mdi:lightbulb-question-outline"></ha-icon>
              <p>
                No rooms yet. Every area with lights becomes a room: assign your lights to areas and they
                show up here.
              </p>
              <ha-button appearance="filled" @click=${() => navigate("/config/areas/dashboard")}>
                Open areas
              </ha-button>
            </ha-card>`
      }
      ${this._section(
        "Zones",
        zones,
        html`<button class="new" @click=${() => fire(this, "ml-zone-dialog", {})}>
          <ha-icon icon="mdi:plus-box-multiple-outline"></ha-icon>
          <span class="new-title">New zone</span>
          ${zones.length ? nothing : html`<span>Combine rooms to run one scene across them.</span>`}
        </button>`,
      )}
    `;
  }

  private _section(title: string, targets: TargetInfo[], extra: unknown = nothing) {
    return html`<h2 class="section-title">${title}</h2>
      <div class="grid">
        ${targets.map(
          (target) =>
            html`<ml-target-tile .hass=${this.hass} .store=${this.store} .target=${target}></ml-target-tile>`,
        )}
        ${extra}
      </div>`;
  }

  private _dismissNotes(): void {
    toastOnError(this, this.store.saveSettings({ migration_notes: [] }), "Could not dismiss the notes");
  }

  static styles = [
    pageStyles,
    newCardStyles,
    css`
      .new {
        min-height: 120px;
        --mdc-icon-size: 28px;
      }
      .new-title {
        font-weight: var(--ha-font-weight-medium);
      }
      ul {
        margin: 0;
        padding-inline-start: var(--ha-space-5);
      }
      .empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: var(--ha-space-3);
        padding: var(--ha-space-8) var(--ha-space-4);
        text-align: center;
        color: var(--secondary-text-color);
        --mdc-icon-size: 48px;
      }
      .empty p {
        max-width: 420px;
        margin: 0;
      }
    `,
  ];
}

defineElement("ml-home", MlHome);
