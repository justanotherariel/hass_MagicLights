// Settings tab: default scene, turn-on/override behaviour, the scene menu (order and
// visibility) and, for zones, edit/delete.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import { swatchBackground } from "../shared/color";
import type { HomeAssistant, SceneInfo, TargetInfo, TargetOptions } from "../shared/types";
import { confirmAction, defineElement, fire, plural, saveSwitch, toastOnError } from "../shared/util";
import { goBack, homePath } from "./routes";
import { menuId, type MlStore } from "./store";
import { dotStyles, pageStyles } from "./styles";

interface Move {
  oldIndex: number;
  newIndex: number;
}

const INHERIT = "__inherit__";
const NONE = "none";

class MlTargetSettings extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @property({ type: Boolean, reflect: true }) narrow = false;

  /** Menu order while a drag is being saved. */
  @state() private _order?: string[];

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("store")) this._order = undefined;
  }

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    return html`
      ${this._renderBehaviour(target)} ${this._renderMenu(target)}
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
    const globalName = globalId ? (this.store.scene(globalId)?.name ?? globalId) : "nothing";
    const value = target.options.default_scene ?? INHERIT;
    const scenes = [...this.store.scenesFor(target.id), ...this.store.scenesFor(target.id, "hidden")];
    const options = [
      { value: INHERIT, label: `Like all rooms (${globalName})` },
      { value: NONE, label: "Nothing" },
      ...this.store.sceneOptions(scenes, menuId),
    ];
    return html`<ha-settings-row .narrow=${this.narrow}>
      <span slot="heading">When a light is switched on</span>
      <span slot="description">Scene for the switched-on light while this room is off.</span>
      <ha-select
        .label=${"Scene when a light is switched on"}
        .options=${options}
        .value=${value}
        @selected=${(ev: CustomEvent<{ value: string }>) =>
          this._save({ default_scene: ev.detail.value === INHERIT ? null : ev.detail.value })}
      ></ha-select>
    </ha-settings-row>`;
  }

  private _renderMenu(target: TargetInfo) {
    const order = this._order ?? target.scenes;
    const visible = order.flatMap((id) => this.store.resolveScene(target.id, id) ?? []);
    const hidden = this.store.scenesFor(target.id, "hidden");
    return html`<h2 class="section-title">Scene menu</h2>
      <ha-card>
        <p class="hint">
          The select entity and the cards offer these scenes in this order. Drag to reorder, or use a
          scene's menu.
        </p>
        <ha-sortable
          handle-selector=".handle"
          @item-moved=${(ev: CustomEvent<Move>) => this._moved(order, ev.detail)}
        >
          <div class="menu">
            ${repeat(
              visible,
              (scene) => menuId(scene),
              (scene) => this._renderMenuRow(target, scene, order),
            )}
          </div>
        </ha-sortable>
        ${
          hidden.length
            ? html`<div class="menu hidden">
                ${hidden.map((scene) => this._renderMenuRow(target, scene))}
              </div>`
            : nothing
        }
        <div class="card-actions">
          <ha-button
            appearance="plain"
            .disabled=${target.options.visible_scenes === null}
            @click=${() => this._save({ visible_scenes: null })}
          >
            Reset to defaults
          </ha-button>
        </div>
      </ha-card>`;
  }

  /** `order`: the menu a listed scene is in; hidden scenes have none. */
  private _renderMenuRow(target: TargetInfo, scene: SceneInfo, order?: string[]) {
    const listed = !!order;
    return html`<div class="row ${listed ? "" : "off"}">
      ${listed ? html`<ha-icon class="handle" icon="mdi:drag"></ha-icon>` : html`<span class="handle"></span>`}
      <span class="dot" style="background: ${swatchBackground(scene.swatches)}"></span>
      <span class="label">${scene.name}</span>
      <ha-switch
        .checked=${listed}
        title=${listed ? "Hide from the menu" : "Show in the menu"}
        aria-label=${`Show ${scene.name} in the menu`}
        @change=${(ev: Event) =>
          saveSwitch(
            this,
            ev,
            (checked) => this.store.setSceneVisible(target.id, menuId(scene), checked),
            "Could not change the menu",
          )}
      ></ha-switch>
      ${order ? this._renderMove(scene, order) : html`<span class="move"></span>`}
    </div>`;
  }

  /** Move up / down: the keyboard and screen reader way to reorder. */
  private _renderMove(scene: SceneInfo, order: string[]) {
    const index = order.indexOf(menuId(scene));
    return html`<ha-dropdown
      class="move"
      placement="bottom-end"
      @wa-select=${(ev: CustomEvent<{ item: { value: string } }>) =>
        this._moved(order, { oldIndex: index, newIndex: index + (ev.detail.item.value === "up" ? -1 : 1) })}
    >
      <ha-icon-button slot="trigger" .label=${`Move ${scene.name}`}>
        <ha-icon icon="mdi:dots-vertical"></ha-icon>
      </ha-icon-button>
      <ha-dropdown-item value="up" .disabled=${index === 0}>
        <ha-icon slot="icon" icon="mdi:arrow-up"></ha-icon>
        Move up
      </ha-dropdown-item>
      <ha-dropdown-item value="down" .disabled=${index === order.length - 1}>
        <ha-icon slot="icon" icon="mdi:arrow-down"></ha-icon>
        Move down
      </ha-dropdown-item>
    </ha-dropdown>`;
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

  private _moved(order: string[], { oldIndex, newIndex }: Move): void {
    const next = [...order];
    const [moved] = next.splice(oldIndex, 1);
    next.splice(newIndex, 0, moved);
    this._order = next;
    const saved = this.store.moveScene(this.targetId, moved, newIndex);
    toastOnError(this, saved, "Could not change the menu").then((ok) => {
      if (!ok) this._order = undefined;
    });
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
    dotStyles,
    css`
      ha-card {
        overflow: hidden;
      }
      ha-select {
        min-width: 200px;
      }
      .hint {
        margin: 0;
        padding: var(--ha-space-4) var(--ha-space-4) var(--ha-space-2);
        color: var(--secondary-text-color);
      }
      .menu {
        display: flex;
        flex-direction: column;
      }
      .menu.hidden {
        border-top: 1px solid var(--divider-color);
      }
      .row {
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
        min-height: 48px;
        padding: 0 var(--ha-space-4);
        background: var(--card-background-color);
      }
      .row.off .label,
      .row.off .dot {
        opacity: 0.55;
      }
      .handle {
        width: 24px;
        flex: none;
        color: var(--secondary-text-color);
        cursor: grab;
      }
      .row.off .handle {
        cursor: default;
      }
      .move {
        flex: none;
        width: 48px;
        margin-inline-end: calc(-1 * var(--ha-space-3));
      }
      .label {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .card-actions {
        display: flex;
        justify-content: flex-end;
        padding: var(--ha-space-2);
        border-top: 1px solid var(--divider-color);
      }
    `,
  ];
}

defineElement("ml-target-settings", MlTargetSettings);
