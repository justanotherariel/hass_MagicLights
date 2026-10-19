// The lights card: the room's bulbs grouped by the part they play, in the preview's
// colours. A group's header selects its part for the part card. A bulb moves between
// parts by drag or from its menu; "+ Part" picks a part (or a blank one) and the lights
// that play it. Edits go up as ml-scene-edit.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import type { HomeAssistant, Part, PreviewFrame, StoredScene, TargetInfo } from "../../shared/types";
import { defineElement, fire, plural } from "../../shared/util";
import "../components/ml-bulb";
import "../components/ml-swatch";
import "../ml-part-picker";
import type { PartPicked } from "../ml-part-picker";
import type { MlStore } from "../store";
import { lookLine } from "../scene-facts";
import { layerOwners, layerSwatches } from "./editor-model";
import type { PartSelect, SceneEdit } from "./editor-events";
import { addPart, assignLights, lightsInNoPart, type RoomContext } from "./parts";

const NONE = "none";
const NEW = "new";

interface Choosing {
  /** The part the new layer plays; a blank one is created with it. */
  part: Part;
  added: boolean;
  picked: Set<string>;
}

class MlRoomCard extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) scene!: StoredScene;

  /** A part as the draft has it, else as saved. */
  @property({ attribute: false }) partOf: (id: string) => Part | undefined = () => undefined;

  /** Names of the draft's own parts, so a blank part gets a free one. */
  @property({ attribute: false }) draftNames: readonly string[] = [];

  @property({ attribute: false }) target!: TargetInfo;

  @property({ type: Number }) selected = 0;

  /** The preview at the time shown. */
  @property({ attribute: false }) frame?: PreviewFrame;

  @property({ type: Boolean, reflect: true }) narrow = false;

  /** "+ Part" is choosing a part; these lights join it. */
  @state() private _picking?: string[];

  @state() private _choosing?: Choosing;

  @state() private _dialogOpen = false;

  private get _ctx(): RoomContext {
    return { target: this.target, roomLights: (id) => this.store.target(id)?.lights };
  }

  private _name(index: number): string {
    const id = this.scene.layers[index]?.part ?? "";
    return this.partOf(id)?.name ?? id;
  }

  protected render() {
    const layers = this.scene.layers;
    const owners = layerOwners(layers, this.target, this._ctx.roomLights);
    const loose = lightsInNoPart(owners, this.target);
    return html`<ha-card>
        <div class="card-content">
          <div class="title">
            <h2>Lights</h2>
            <span class="hint">${plural(this.target.lights.length, "light")} · ${plural(layers.length, "part")}</span>
          </div>
          ${layers.map((_, i) => this._renderPart(i, owners))}
          ${
            loose.length
              ? html`<section class="part loose" aria-label="Not in this scene">
                  <div class="part-head static"><span class="part-name">Not in this scene</span></div>
                  ${this._renderBulbs(loose, null)}
                </section>`
              : nothing
          }
          <div class="foot">
            <p class="hint">
              Drag a bulb between parts, or tap it to move it. Tap a part's name to edit its look.
            </p>
            <ha-button appearance="plain" @click=${() => (this._picking = [])}>
              <ha-icon slot="start" icon="mdi:plus"></ha-icon>
              Part
            </ha-button>
          </div>
        </div>
      </ha-card>
      ${
        this._picking
          ? html`<ml-part-picker
              .hass=${this.hass}
              .store=${this.store}
              .targetId=${this.target.id}
              .inScene=${layers.map((layer) => layer.part)}
              mode="pick"
              @ml-part-picked=${this._picked}
              @ml-dialog-closed=${() => (this._picking = undefined)}
            ></ml-part-picker>`
          : nothing
      }
      ${this._renderDialog()}`;
  }

  private _renderPart(index: number, owners: Map<string, number>) {
    const part = this.partOf(this.scene.layers[index].part);
    const lights = this.target.lights.filter((e) => owners.get(e) === index);
    const selected = index === this.selected;
    const effect = part ? this.store.effect(part.effect) : undefined;
    const swatches = part ? layerSwatches(effect, part.params) : [];
    return html`<section class="part ${selected ? "selected" : ""}" aria-label=${this._name(index)}>
      <button
        class="part-head"
        aria-pressed=${selected ? "true" : "false"}
        @click=${() => fire<PartSelect>(this, "ml-part-select", { index })}
      >
        <ml-swatch size="24" .swatches=${swatches}></ml-swatch>
        <span class="part-name">${this._name(index)}</span>
        ${selected ? html`<span class="editing">editing</span>` : nothing}
        <span class="part-look">${part ? lookLine(effect, part) : "Missing part"}</span>
      </button>
      ${this._renderBulbs(lights, index)}
    </section>`;
  }

  /** The bulbs of one part (null: in none), a drop target for the others. */
  private _renderBulbs(lights: string[], part: number | null) {
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
        ${lights.map((e) => this._renderBulb(e, part))}
        ${
          part !== null && !lights.length
            ? html`<div class="empty"><span class="drop"><ha-icon icon="mdi:plus"></ha-icon></span>drop a light here</div>`
            : nothing
        }
      </div>
    </ha-sortable>`;
  }

  private _renderBulb(entityId: string, part: number | null) {
    const light = this.frame?.lights[entityId];
    const on = !!light?.on;
    return html`<ha-dropdown
      class="bulb"
      .sortableData=${entityId}
      placement="bottom"
      @wa-select=${(ev: CustomEvent<{ item: { value: string } }>) => {
        const value = ev.detail.item.value;
        if (value === NEW) this._picking = [entityId];
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
      ${this.scene.layers.map(
        (_, i) => html`<ha-dropdown-item value=${String(i)} .selected=${i === part}>
          <ha-icon slot="icon" .icon=${i === part ? "mdi:check" : "mdi:circle-small"}></ha-icon>
          ${this._name(i)}
        </ha-dropdown-item>`,
      )}
      <ha-dropdown-item value=${NEW}>
        <ha-icon slot="icon" icon="mdi:plus"></ha-icon>
        Another part…
      </ha-dropdown-item>
      <ha-dropdown-item value=${NONE} .selected=${part === null}>
        <ha-icon slot="icon" .icon=${part === null ? "mdi:check" : "mdi:lightbulb-off-outline"}></ha-icon>
        Not in this scene
      </ha-dropdown-item>
    </ha-dropdown>`;
  }

  private _renderDialog() {
    const choosing = this._choosing;
    if (!choosing) return nothing;
    return html`<ha-dialog
      .open=${this._dialogOpen}
      width="small"
      .headerTitle=${`Lights for ${choosing.part.name}`}
      @closed=${(ev: Event) => {
        if (ev.target !== ev.currentTarget) return;
        this._choosing = undefined;
        this._dialogOpen = false;
      }}
    >
      <p class="hint">The lights you pick play ${choosing.part.name}; they leave the parts they are in.</p>
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
        <ha-button slot="primaryAction" .disabled=${!choosing.picked.size} @click=${this._chosen}>Add part</ha-button>
      </ha-dialog-footer>
    </ha-dialog>`;
  }

  private _picked(ev: CustomEvent<PartPicked>): void {
    ev.stopPropagation();
    const { partId } = ev.detail;
    const index = this.scene.layers.findIndex((layer) => layer.part === partId);
    if (index >= 0) {
      this._joinPart(index);
      return;
    }
    const part = partId === null ? this.store.newPart(this.draftNames) : this.partOf(partId);
    if (!part) return;
    this._choosing = { part, added: partId === null, picked: new Set(this._picking) };
    this._dialogOpen = true;
  }

  private _chosen(): void {
    const choosing = this._choosing;
    this._dialogOpen = false;
    if (!choosing) return;
    const lights = this.target.lights.filter((e) => choosing.picked.has(e));
    const { layers, index } = addPart(this.scene.layers, choosing.part.id, lights, this._ctx);
    fire<SceneEdit>(this, "ml-scene-edit", {
      scene: { ...this.scene, layers },
      select: index,
      ...(choosing.added ? { added: choosing.part } : {}),
    });
  }

  /** A part the scene plays already: the picked lights join its layer, which is selected. */
  private _joinPart(index: number): void {
    const lights = this._picking ?? [];
    if (!lights.length) {
      fire<PartSelect>(this, "ml-part-select", { index });
      return;
    }
    const { layers, moved } = assignLights(this.scene.layers, lights, index, this._ctx);
    fire<SceneEdit>(this, "ml-scene-edit", { scene: { ...this.scene, layers }, select: Math.max(0, moved[index]) });
  }

  private _assign(entityId: string, part: number | null): void {
    const { layers, moved } = assignLights(this.scene.layers, [entityId], part, this._ctx);
    const select = Math.max(0, moved[this.selected] ?? 0);
    fire<SceneEdit>(this, "ml-scene-edit", { scene: { ...this.scene, layers }, select });
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
      min-width: 0;
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
    .part-head ml-swatch {
      flex: none;
    }
    .part-name {
      flex: none;
      max-width: 60%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: var(--ha-font-weight-medium);
    }
    .editing {
      flex: none;
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
    .foot {
      display: flex;
      align-items: center;
      gap: var(--ha-space-3);
    }
    .foot .hint {
      flex: 1;
    }
    .choose {
      margin-top: var(--ha-space-3);
    }
  `;
}

defineElement("ml-room-card", MlRoomCard);
