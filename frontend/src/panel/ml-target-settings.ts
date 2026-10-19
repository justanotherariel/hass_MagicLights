// Settings tab: switch-on scene, turn-on/override behaviour and, for zones, edit/delete.
// The scenes themselves are made and arranged on the Scenes tab.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import type { HomeAssistant, TargetInfo, TargetOptions } from "../shared/types";
import { confirmAction, defineElement, fire, plural, saveSwitch, toastOnError } from "../shared/util";
import { goBack, homePath } from "./routes";
import type { MlStore } from "./store";
import { pageStyles } from "./styles";

const INHERIT = "__inherit__";
const NONE = "none";

class MlTargetSettings extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @property({ type: Boolean, reflect: true }) narrow = false;

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    return html`
      ${this._renderBehaviour(target)}
      ${target.kind === "zone" ? this._renderZone(target) : nothing}
    `;
  }

  private _renderBehaviour(target: TargetInfo) {
    const o = target.options;
    return html`<h2 class="section-title">Behaviour</h2>
      <ha-card>
        ${target.kind === "room" ? this._renderDefaultScene(target) : nothing}
        <ha-settings-row>
          <span slot="heading">Reclaim on turn-on</span>
          <span slot="description">Switching a manual light on again returns it to the running scene.</span>
          <ha-switch
            .checked=${o.reclaim_on_turn_on}
            @change=${(ev: Event) => this._saveSwitch(ev, (checked) => ({ reclaim_on_turn_on: checked }))}
          ></ha-switch>
        </ha-settings-row>
        <ha-settings-row>
          <span slot="heading">Detect changes</span>
          <span slot="description">
            A light whose colour or brightness is changed elsewhere (app, remote) becomes manual.
          </span>
          <ha-switch
            .checked=${o.detect_changes}
            @change=${(ev: Event) => this._saveSwitch(ev, (checked) => ({ detect_changes: checked }))}
          ></ha-switch>
        </ha-settings-row>
      </ha-card>`;
  }

  private _renderDefaultScene(target: TargetInfo) {
    const globalId = this.store.config.settings.default_scene;
    const globalName = globalId ? (this.store.part(globalId)?.name ?? globalId) : "nothing";
    const value = target.options.default_scene ?? INHERIT;
    const options = [
      { value: INHERIT, label: `Like all rooms (${globalName})` },
      { value: NONE, label: "Nothing" },
      ...this.store.scenesOf(target.id).map((scene) => ({
        value: scene.id,
        label: scene.fits ? scene.name : `${scene.name} (no lights here)`,
      })),
    ];
    return html`<ha-settings-row .narrow=${this.narrow}>
      <span slot="heading">When a light is switched on</span>
      <span slot="description">One of this room's scenes, for the switched-on light while the room is off.</span>
      <ha-select
        .label=${"Scene when a light is switched on"}
        .options=${options}
        .value=${value}
        @selected=${(ev: CustomEvent<{ value: string }>) =>
          this._save({ default_scene: ev.detail.value === INHERIT ? null : ev.detail.value })}
      ></ha-select>
    </ha-settings-row>`;
  }

  private _renderZone(target: TargetInfo) {
    const zone = this.store.zoneOf(target.id);
    return html`<h2 class="section-title">Zone</h2>
      <ha-card>
        <ha-settings-row>
          <span slot="heading">Rooms and lights</span>
          <span slot="description">
            ${zone?.rooms.map((room) => this.hass.areas?.[room]?.name ?? room).join(", ") || "No rooms"}
            ${zone?.lights.length ? ` · ${plural(zone.lights.length, "extra light")}` : ""}
          </span>
          <ha-button appearance="plain" @click=${() => fire(this, "ml-zone-dialog", { zone })}>
            Edit
          </ha-button>
        </ha-settings-row>
        <ha-settings-row>
          <span slot="heading">Delete zone</span>
          <span slot="description">Removes the zone and its select entity. Its lights keep their state.</span>
          <ha-button variant="danger" appearance="plain" @click=${() => this._deleteZone(target)}>
            Delete
          </ha-button>
        </ha-settings-row>
      </ha-card>`;
  }

  private _save(options: Partial<TargetOptions>): void {
    toastOnError(this, this.store.saveTargetOptions(this.targetId, options), "Could not save the setting");
  }

  private _saveSwitch(ev: Event, options: (checked: boolean) => Partial<TargetOptions>): void {
    saveSwitch(
      this,
      ev,
      (checked) => this.store.saveTargetOptions(this.targetId, options(checked)),
      "Could not save the setting",
    );
  }

  private async _deleteZone(target: TargetInfo): Promise<void> {
    const confirmed = await confirmAction(this, {
      title: `Delete ${target.name}?`,
      text: "The zone and its select entity are removed. Its lights stay as they are.",
      confirmText: "Delete",
      destructive: true,
    });
    const zone = this.store.zoneOf(target.id);
    if (!confirmed || !zone) return;
    await toastOnError(
      this,
      this.store.deleteZone(zone.id).then(() => goBack(homePath())),
      "Could not delete the zone",
    );
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
    `,
  ];
}

defineElement("ml-target-settings", MlTargetSettings);
