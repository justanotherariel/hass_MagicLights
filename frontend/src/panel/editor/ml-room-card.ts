// "In <room>": which of the room's lights each part of the scene sets. The room is the
// preview room, picked from chips; its parts are boxes holding its bulbs in the preview's
// colours. A bulb moves between parts by drag or from its menu; "+ Part" makes a part for
// this room only. Every change edits only this room's entries of the layers (parts.ts).

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import type { HomeAssistant, PreviewFrame, StoredScene, TargetInfo } from "../../shared/types";
import { defineElement, fire, plural } from "../../shared/util";
import "../components/ml-bulb";
import { menuId, menuIds, type MlStore } from "../store";
import { lookLine } from "../scene-facts";
import { layerCaption, layerOwners } from "./editor-model";
import type { PartSelect, RoomSelect, SceneEdit } from "./editor-events";
import {
  addPart,
  assignLight,
  lightsInNoPart,
  partLabel,
  partsIn,
  setUpHere,
  switcherParts,
  type RoomContext,
} from "./parts";

const NEW = "new";

/** layerCaption's "also Kitchen" as the box reads it. */
const sharedLine = (caption: string): string =>
  caption.startsWith("also ") ? `Shared with ${caption.slice(5)}` : `Elsewhere: ${caption}`;
const NONE = "none";

interface Choosing {
  /** The part the picked lights join; NEW makes one after the selected part. */
  into: number | typeof NEW;
  picked: Set<string>;
}

class MlRoomCard extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) scene!: StoredScene;

  @property({ attribute: false }) target!: TargetInfo;

  @property({ type: Number }) selected = 0;

  /** The preview at the time shown. */
  @property({ attribute: false }) frame?: PreviewFrame;

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _choosing?: Choosing;

  @state() private _dialogOpen = false;

  private get _ctx(): RoomContext {
    return {
      target: this.target,
      roomLights: (id) => this.store.target(id)?.lights,
      everyRoom: this.store.targets.filter((t) => t.kind === "room").map((t) => t.id),
    };
  }

  private _label(index: number): string {
    return partLabel(this.scene.layers[index], index, {
      light: (e) => this.store.lightName(e),
      room: (ref) => this.store.targetName(ref),
    });
  }

  protected render() {
    const ctx = this._ctx;
    const layers = this.scene.layers;
    const setUp = partsIn(layers, this.target, ctx.roomLights).length > 0;
    const parts = switcherParts(layers, this.target, ctx.roomLights);
    const owners = layerOwners(layers, this.target, ctx.roomLights);
    const loose = lightsInNoPart(owners, this.target);
    return html`<ha-card>
      <div class="card-content">
        <div class="title">
          <h2>In ${this.target.name}</h2>
          <span class="hint">
            ${plural(this.target.lights.length, "light")}${setUp ? ` · ${plural(parts.length, "part")}` : ""}
          </span>
        </div>
        ${this._renderRooms()}
        ${
          setUp
            ? html`${parts.map((i) => this._renderPart(i, owners))}
                ${
                  loose.length
                    ? html`<section class="part loose" aria-label="Not in this scene">
                        <div class="part-head static"><span class="part-name">Not in this scene</span></div>
                        ${this._renderBulbs(loose, null, parts)}
                      </section>`
                    : nothing
                }
                <p class="hint">
                  Drag a bulb between parts, or tap it to move it. Every part is shared by the scene; only
                  ${this.target.name}'s bulbs change here.
                </p>`
            : this._renderNotSetUp()
        }
        <div class="others">
          <div class="field">
            <span class="label">Lights in no part</span>
            <ha-control-select
              .options=${[
                { value: "off", label: "Turn off" },
                { value: "keep", label: "Leave as they are" },
              ]}
              .value=${this.scene.others}
              .label=${"Lights in no part"}
              @value-changed=${(ev: CustomEvent<{ value: StoredScene["others"] }>) => {
                ev.stopPropagation();
                this._edit({ ...this.scene, others: ev.detail.value });
              }}
            ></ha-control-select>
          </div>
          ${
            setUp
              ? html`<ha-button appearance="plain" @click=${() => this._choose(NEW, [])}>
                  <ha-icon slot="start" icon="mdi:plus"></ha-icon>
                  Part
                </ha-button>`
              : nothing
          }
        </div>
      </div>
    </ha-card>
    ${this._renderDialog()}`;
  }

  /** The rooms that show the scene or where it has lights, then the others in a menu. */
  private _renderRooms() {
    const id = menuId(this.scene);
    const roomLights = this._ctx.roomLights;
    const near = (t: TargetInfo) =>
      t.id === this.target.id || menuIds(t).includes(id) || partsIn(this.scene.layers, t, roomLights).length > 0;
    const chips = this.store.targets.filter(near);
    const others = this.store.targets.filter((t) => !near(t));
    return html`<div class="rooms" role="group" aria-label="Preview in">
      ${chips.map(
        (t) => html`<ha-filter-chip
          no-leading-icon
          .selected=${t.id === this.target.id}
          .label=${t.name}
          @click=${(ev: Event) => {
            ev.preventDefault();
            this._room(t.id);
          }}
        ></ha-filter-chip>`,
      )}
      ${
        others.length
          ? html`<ha-dropdown placement="bottom-start" @wa-select=${(ev: CustomEvent<{ item: { value: string } }>) =>
              this._room(ev.detail.item.value)}>
              <ha-button slot="trigger" appearance="plain" size="s">
                ${plural(others.length, "other room")}
                <ha-icon slot="end" icon="mdi:menu-down"></ha-icon>
              </ha-button>
              ${others.map((t) => html`<ha-dropdown-item value=${t.id}>${t.name}</ha-dropdown-item>`)}
            </ha-dropdown>`
          : nothing
      }
    </div>`;
  }

  private _renderPart(index: number, owners: Map<string, number>) {
    const layer = this.scene.layers[index];
    const lights = this.target.lights.filter((e) => owners.get(e) === index);
    const selected = index === this.selected;
    const ctx = this._ctx;
    const rooms = this.store.targets.filter((t) => t.kind === "room");
    const caption = layerCaption(layer.lights, this.target, rooms, ctx.roomLights);
    const parts = switcherParts(this.scene.layers, this.target, ctx.roomLights);
    return html`<section class="part ${selected ? "selected" : ""}" aria-label=${this._label(index)}>
      <button
        class="part-head"
        aria-pressed=${selected ? "true" : "false"}
        @click=${() => fire<PartSelect>(this, "ml-part-select", { index })}
      >
        <span class="part-name">${this._label(index)}</span>
        ${selected ? html`<span class="editing">editing</span>` : nothing}
        <span class="part-look">${lookLine(this.store.effect(layer.effect), layer)}</span>
      </button>
      ${caption ? html`<span class="caption">${sharedLine(caption)}</span>` : nothing}
      ${this._renderBulbs(lights, index, parts)}
    </section>`;
  }

  /** The bulbs of one part (null: in none), a drop target for the others. */
  private _renderBulbs(lights: string[], part: number | null, parts: number[]) {
    return html`<ha-sortable
      group="ml-parts"
      draggable-selector=".bulb"
      @item-added=${(ev: CustomEvent<{ data: string }>) => {
        ev.stopPropagation();
        if (ev.detail.data) this._assign(ev.detail.data, part);
      }}
      @item-moved=${(ev: Event) => ev.stopPropagation()}
      @item-removed=${(ev: Event) => ev.stopPropagation()}
    >
      <div class="bulbs">
        ${lights.map((e) => this._renderBulb(e, part, parts))}
        ${
          part !== null && !lights.length
            ? html`<div class="empty"><span class="drop"><ha-icon icon="mdi:plus"></ha-icon></span>drop a light here</div>`
            : nothing
        }
      </div>
    </ha-sortable>`;
  }

  private _renderBulb(entityId: string, part: number | null, parts: number[]) {
    const light = this.frame?.lights[entityId];
    const on = !!light?.on;
    return html`<ha-dropdown
      class="bulb"
      .sortableData=${entityId}
      placement="bottom"
      @wa-select=${(ev: CustomEvent<{ item: { value: string } }>) => {
        const value = ev.detail.item.value;
        if (value === NEW) this._choose(NEW, [entityId]);
        else this._assign(entityId, value === NONE ? null : Number(value));
      }}
    >
      <ml-bulb
        slot="trigger"
        .label=${this.store.lightName(entityId)}
        .color=${on ? light?.rgb : undefined}
        .gradient=${on ? light?.gradient : undefined}
        .brightness=${on ? Math.round((light?.brightness ?? 1) * 100) : 100}
        .unavailable=${this.hass.states[entityId]?.state === "unavailable"}
      ></ml-bulb>
      ${parts.map(
        (i) => html`<ha-dropdown-item value=${String(i)} .selected=${i === part}>
          <ha-icon slot="icon" .icon=${i === part ? "mdi:check" : "mdi:circle-small"}></ha-icon>
          ${this._label(i)}
        </ha-dropdown-item>`,
      )}
      <ha-dropdown-item value=${NEW}>
        <ha-icon slot="icon" icon="mdi:plus"></ha-icon>
        New part for ${this.target.name}
      </ha-dropdown-item>
      <ha-dropdown-item value=${NONE} .selected=${part === null}>
        <ha-icon slot="icon" .icon=${part === null ? "mdi:check" : "mdi:lightbulb-off-outline"}></ha-icon>
        Not in this scene
      </ha-dropdown-item>
    </ha-dropdown>`;
  }

  private _renderNotSetUp() {
    return html`<section class="unset">
      <span class="part-name">Not set up here</span>
      <span class="hint">
        ${this.scene.name || "This scene"} has no lights in ${this.target.name} yet, so it cannot run here.
      </span>
      <div class="actions">
        <ha-button @click=${() => this._edit({ ...this.scene, layers: setUpHere(this.scene.layers, this._ctx) }, 0)}>
          Use all lights here
        </ha-button>
        <ha-button appearance="outlined" @click=${() => this._choose(0, [])}>Choose lights</ha-button>
      </div>
    </section>`;
  }

  private _renderDialog() {
    const choosing = this._choosing;
    if (!choosing) return nothing;
    const title =
      choosing.into === NEW ? `New part for ${this.target.name}` : `Lights for ${this._label(choosing.into)}`;
    return html`<ha-dialog
      .open=${this._dialogOpen}
      width="small"
      .headerTitle=${title}
      @closed=${(ev: Event) => {
        if (ev.target !== ev.currentTarget) return;
        this._choosing = undefined;
        this._dialogOpen = false;
      }}
    >
      <p class="hint">
        ${
          choosing.into === NEW
            ? `The new part starts with the look of ${this._label(this.selected)} and sets only the lights you pick.`
            : "The lights you pick join this part."
        }
      </p>
      <div class="bulbs choose">
        ${this.target.lights.map(
          (e) => html`<ml-bulb
            .label=${this.store.lightName(e)}
            .selected=${choosing.picked.has(e)}
            .color=${choosing.picked.has(e) ? "#ffd27f" : undefined}
            @click=${() => {
              const picked = new Set(choosing.picked);
              if (!picked.delete(e)) picked.add(e);
              this._choosing = { ...choosing, picked };
            }}
          ></ml-bulb>`,
        )}
      </div>
      <ha-dialog-footer slot="footer">
        <ha-button slot="secondaryAction" appearance="plain" data-dialog="close">Cancel</ha-button>
        <ha-button slot="primaryAction" .disabled=${!choosing.picked.size} @click=${this._chosen}>
          ${choosing.into === NEW ? "Add part" : "Done"}
        </ha-button>
      </ha-dialog-footer>
    </ha-dialog>`;
  }

  private _choose(into: Choosing["into"], picked: string[]): void {
    this._choosing = { into, picked: new Set(picked) };
    this._dialogOpen = true;
  }

  private _chosen(): void {
    const choosing = this._choosing;
    this._dialogOpen = false;
    if (!choosing) return;
    const ctx = this._ctx;
    const lights = this.target.lights.filter((e) => choosing.picked.has(e));
    if (choosing.into === NEW) {
      const { layers, index } = addPart(this.scene.layers, this.selected, lights, ctx);
      this._edit({ ...this.scene, layers }, index);
      return;
    }
    let layers = this.scene.layers;
    let select = choosing.into;
    for (const e of lights) {
      const result = assignLight(layers, e, select, ctx);
      layers = result.layers;
      select = Math.max(0, result.moved[select]);
    }
    this._edit({ ...this.scene, layers }, select);
  }

  private _assign(entityId: string, part: number | null): void {
    const { layers, moved } = assignLight(this.scene.layers, entityId, part, this._ctx);
    this._edit({ ...this.scene, layers }, Math.max(0, moved[this.selected] ?? 0));
  }

  private _room(targetId: string): void {
    if (targetId !== this.target.id) fire<RoomSelect>(this, "ml-room-select", { targetId });
  }

  private _edit(scene: StoredScene, select?: number): void {
    fire<SceneEdit>(this, "ml-scene-edit", { scene, select });
  }

  static styles = css`
    :host {
      display: block;
    }
    ha-card {
      display: block;
    }
    .card-content {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3);
    }
    .title {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: var(--ha-space-2);
    }
    h2 {
      margin: 0;
      font-size: var(--ha-font-size-l);
      font-weight: var(--ha-font-weight-medium);
    }
    .hint {
      margin: 0;
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .rooms {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--ha-space-2);
    }
    /* 44 px touch targets. */
    ha-filter-chip {
      --ha-button-height: 44px;
      --md-filter-chip-selected-container-color: rgba(var(--rgb-primary-color), 0.2);
    }
    .part {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-1);
      padding: var(--ha-space-2) var(--ha-space-3) var(--ha-space-3);
      border: 1px solid var(--divider-color);
      border-radius: var(--ha-border-radius-lg, 12px);
    }
    .part.selected {
      border: 2px solid var(--primary-color);
      padding: calc(var(--ha-space-2) - 1px) calc(var(--ha-space-3) - 1px) calc(var(--ha-space-3) - 1px);
    }
    .part.loose {
      border-style: dashed;
    }
    .part-head {
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      min-height: 44px;
      margin: 0;
      padding: 0;
      border: none;
      background: none;
      color: inherit;
      font: inherit;
      text-align: start;
      cursor: pointer;
      border-radius: var(--ha-border-radius-md);
    }
    .part-head.static {
      cursor: default;
    }
    .part-head:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
    }
    .part-name {
      font-weight: var(--ha-font-weight-medium);
    }
    .editing {
      padding: 0 var(--ha-space-2);
      border-radius: var(--ha-border-radius-pill);
      background: rgba(var(--rgb-primary-color), 0.15);
      color: var(--primary-text-color);
      font-size: var(--ha-font-size-s);
    }
    .part-look {
      flex: 1;
      min-width: 0;
      text-align: end;
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .caption {
      margin-top: calc(-1 * var(--ha-space-2));
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .bulbs {
      display: flex;
      flex-wrap: wrap;
      gap: var(--ha-space-1);
      min-height: 72px;
    }
    .bulb {
      display: block;
    }
    .empty {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--ha-space-1);
      width: 76px;
      font-size: var(--ha-font-size-xs, 11px);
      color: var(--secondary-text-color);
      text-align: center;
    }
    .drop {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 44px;
      margin-top: var(--ha-space-1);
      border: 1px dashed var(--secondary-text-color);
      border-radius: 50%;
      --mdc-icon-size: 20px;
    }
    .unset {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
      padding: var(--ha-space-3);
      border: 1px dashed var(--secondary-text-color);
      border-radius: var(--ha-border-radius-lg, 12px);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--ha-space-2);
    }
    .others {
      display: flex;
      align-items: flex-end;
      gap: var(--ha-space-3);
    }
    .others .field {
      flex: 1;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
    }
    .label {
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    ha-control-select {
      --control-select-thickness: 48px;
      --control-select-border-radius: var(--ha-border-radius-lg, 12px);
    }
    .choose {
      margin-top: var(--ha-space-3);
    }
  `;
}

defineElement("ml-room-card", MlRoomCard);
