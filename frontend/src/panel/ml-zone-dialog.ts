// Create or edit a zone: name, icon, rooms and extra lights. Fires ml-dialog-closed.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import type { HomeAssistant, Zone } from "../shared/types";
import { defineElement, errorMessage, fire, plural } from "../shared/util";
import { go, targetPath } from "./routes";
import type { MlStore } from "./store";

class MlZoneDialog extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  /** Undefined creates a zone. */
  @property({ attribute: false }) zone?: Zone;

  @state() private _open = true;

  @state() private _name = "";

  @state() private _icon: string | null = null;

  @state() private _rooms: string[] = [];

  @state() private _lights: string[] = [];

  @state() private _saving = false;

  @state() private _error?: string;

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("zone")) {
      this._name = this.zone?.name ?? "";
      this._icon = this.zone?.icon ?? null;
      this._rooms = [...(this.zone?.rooms ?? [])];
      this._lights = [...(this.zone?.lights ?? [])];
    }
  }

  protected render() {
    const count = this._lightCount();
    return html`<ha-adaptive-dialog
      .open=${this._open}
      header-title=${this.zone ? "Edit zone" : "New zone"}
      .headerSubtitle=${plural(count, "light")}
      prevent-scrim-close
      @closed=${this._closed}
    >
      <div class="form">
        ${this._error ? html`<ha-alert alert-type="error">${this._error}</ha-alert>` : nothing}
        <ha-input
          autofocus
          label="Name"
          required
          .value=${this._name}
          @input=${(ev: Event) => (this._name = (ev.target as HTMLInputElement).value ?? "")}
        ></ha-input>
        <ha-selector
          .hass=${this.hass}
          .selector=${{ icon: { placeholder: "mdi:lightbulb-group" } }}
          .label=${"Icon"}
          .required=${false}
          .value=${this._icon ?? undefined}
          @value-changed=${(ev: CustomEvent<{ value?: string }>) => (this._icon = ev.detail.value || null)}
        ></ha-selector>
        <ha-selector
          .hass=${this.hass}
          .selector=${{ area: { multiple: true, entity: { domain: "light" } } }}
          .label=${"Rooms"}
          .required=${false}
          .value=${this._rooms}
          @value-changed=${(ev: CustomEvent<{ value?: string[] }>) => (this._rooms = ev.detail.value ?? [])}
        ></ha-selector>
        <ha-selector
          .hass=${this.hass}
          .selector=${{ entity: { multiple: true, filter: { domain: "light" } } }}
          .label=${"Extra lights"}
          .helper=${"Lights outside these rooms that belong to the zone too."}
          .required=${false}
          .value=${this._lights}
          @value-changed=${(ev: CustomEvent<{ value?: string[] }>) => (this._lights = ev.detail.value ?? [])}
        ></ha-selector>
      </div>
      <ha-dialog-footer slot="footer">
        <ha-button slot="secondaryAction" appearance="plain" data-dialog="close">Cancel</ha-button>
        <ha-button
          slot="primaryAction"
          .disabled=${!this._name.trim() || count === 0}
          .loading=${this._saving}
          @click=${this._save}
        >
          ${this.zone ? "Save" : "Create zone"}
        </ha-button>
      </ha-dialog-footer>
    </ha-adaptive-dialog>`;
  }

  private _lightCount(): number {
    const rooms = new Set(this._rooms);
    const lights = new Set(this._lights);
    for (const light of this.store.config.lights) {
      if (light.area_id && rooms.has(light.area_id)) lights.add(light.entity_id);
    }
    return lights.size;
  }

  private async _save(): Promise<void> {
    this._saving = true;
    this._error = undefined;
    try {
      const saved = await this.store.saveZone({
        id: this.zone?.id ?? "",
        name: this._name.trim(),
        icon: this._icon,
        rooms: this._rooms,
        lights: this._lights,
      });
      this._open = false;
      if (!this.zone) go(targetPath(`zone:${saved.id}`));
    } catch (err) {
      this._error = errorMessage(err);
    } finally {
      this._saving = false;
    }
  }

  private _closed(ev: Event): void {
    // Pickers inside the dialog fire their own "closed".
    if (ev.target !== ev.currentTarget) return;
    fire(this, "ml-dialog-closed");
  }

  static styles = css`
    .form {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-4);
    }
    ha-input {
      --ha-input-padding-bottom: 0;
    }
  `;
}

defineElement("ml-zone-dialog", MlZoneDialog);
