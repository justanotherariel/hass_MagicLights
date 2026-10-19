// A room's Scenes tab below the hero: its scenes as tiles (one without lights here as a
// ghost that opens the editor), "New scene" (the part picker) and Arrange mode (drag to
// reorder; Done writes the order once). Fires ml-arrange-done.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import { isPlayable } from "../shared/scene-actions";
import { activeScene } from "../shared/status";
import type { HomeAssistant, SceneInfo, TargetInfo } from "../shared/types";
import { defineElement, fire, toastOnError } from "../shared/util";
import "./components/ml-swatch";
import "./ml-part-picker";
import "./ml-scene-card";
import type { SceneAction } from "./ml-scene-card";
import { editorPath, go } from "./routes";
import { movedBy, tileLine } from "./scene-facts";
import type { MlStore } from "./store";
import { newCardStyles, pageStyles } from "./styles";

interface Move {
  oldIndex: number;
  newIndex: number;
}

class MlSceneGallery extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @property({ type: Boolean, reflect: true }) narrow = false;

  @property({ type: Boolean }) arranging = false;

  @state() private _picking = false;

  /** Arrange mode's order, until Done. */
  @state() private _order: string[] = [];

  @state() private _saving = false;

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("arranging") && this.arranging) {
      this._order = [...(this.store.target(this.targetId)?.scenes ?? [])];
    }
  }

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    return html`${this.arranging ? this._renderArrange(target) : this._renderTiles(target)}
    ${
      this._picking
        ? html`<ml-part-picker
            .hass=${this.hass}
            .store=${this.store}
            .targetId=${target.id}
            mode="add"
            @ml-dialog-closed=${() => (this._picking = false)}
          ></ml-part-picker>`
        : nothing
    }`;
  }

  private _line(scene: SceneInfo): string {
    return tileLine(
      scene,
      (id) => this.store.part(id),
      (key) => this.store.effect(key),
    );
  }

  private _renderTiles(target: TargetInfo) {
    const attrs = this.store.status(this.hass, target.id)?.attrs;
    const active = activeScene(attrs);
    const scenes = this.store.scenesOf(target.id);
    return html`
      <div class="head">
        <h2 class="section-title">Scenes</h2>
        <span class="legend" ?hidden=${!scenes.length}>
          ${this.narrow ? "Tap: run · swatch: edit" : "Tap a tile to run it · tap its swatch to edit · Arrange to reorder"}
        </span>
      </div>
      ${
        scenes.length
          ? nothing
          : html`<p class="hint">No scenes here yet. Pick parts, such as a white or a colour palette, to make some.</p>`
      }
      <div class="grid">
        ${repeat(
          scenes,
          (scene) => scene.id,
          (scene) => html`<ml-scene-card
            .scene=${scene}
            .swatches=${scene.swatches}
            .line=${this._line(scene)}
            .active=${active === scene.id}
            .animated=${isPlayable(scene)}
            .ghost=${!scene.fits}
            .disabled=${!attrs}
            @ml-scene-action=${(ev: CustomEvent<{ action: SceneAction }>) => this._action(scene.id, ev.detail.action)}
          ></ml-scene-card>`,
        )}
        <button class="new add" @click=${() => (this._picking = true)}>
          <ha-icon icon="mdi:plus"></ha-icon>
          New scene
        </button>
      </div>
    `;
  }

  private _renderArrange(target: TargetInfo) {
    const scenes = this._order.flatMap((id) => this.store.scene(id) ?? []);
    return html`
      <div class="arrange-bar">
        <span class="secondary">Drag to reorder ${target.name}'s scenes, as its menu lists them.</span>
        <ha-button appearance="plain" @click=${() => fire(this, "ml-arrange-done")}>Cancel</ha-button>
        <ha-button .loading=${this._saving} @click=${() => this._done(target)}>Done</ha-button>
      </div>
      <ha-sortable handle-selector=".handle" @item-moved=${(ev: CustomEvent<Move>) => this._moved(ev.detail)}>
        <div class="grid arrange">
          ${repeat(
            scenes,
            (s) => s.id,
            (scene) => html`<div class="item">
              <button
                class="handle"
                aria-label=${`Move ${scene.name}; arrow keys move it`}
                @keydown=${(ev: KeyboardEvent) => this._key(ev, scene.id)}
              >
                <ha-icon icon="mdi:drag"></ha-icon>
              </button>
              <ml-swatch
                size="32"
                .swatches=${scene.swatches}
                .icon=${scene.icon ?? undefined}
                ?ghost=${!scene.fits}
              ></ml-swatch>
              <span class="item-text">
                <span class="item-name">${scene.name}</span>
                <span class="secondary">${scene.fits ? this._line(scene) : "Needs set-up"}</span>
              </span>
            </div>`,
          )}
        </div>
      </ha-sortable>
    `;
  }

  private _moved({ oldIndex, newIndex }: Move): void {
    const id = this._order[oldIndex];
    if (id !== undefined) this._order = movedBy(this._order, id, newIndex - oldIndex);
  }

  private _key(ev: KeyboardEvent, id: string): void {
    const by = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[ev.key];
    if (!by) return;
    ev.preventDefault();
    this._order = movedBy(this._order, id, by);
    this.updateComplete.then(() =>
      this.shadowRoot?.querySelectorAll<HTMLElement>(".handle")[this._order.indexOf(id)]?.focus(),
    );
  }

  private async _done(target: TargetInfo): Promise<void> {
    const current = target.scenes;
    if (this._order.some((id, i) => id !== current[i])) {
      this._saving = true;
      const ok = await toastOnError(this, this.store.orderScenes(target.id, this._order), "Could not change the order");
      this._saving = false;
      if (!ok) return;
    }
    fire(this, "ml-arrange-done");
  }

  private _action(id: string, action: SceneAction): void {
    if (action === "edit") {
      go(editorPath(this.targetId, id));
      return;
    }
    toastOnError(this, this.store.activate(this.targetId, id), "Could not activate the scene");
  }

  static styles = [
    pageStyles,
    newCardStyles,
    css`
      .head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: var(--ha-space-3);
        flex-wrap: wrap;
      }
      .head .section-title {
        margin-top: var(--ha-space-6);
      }
      .legend {
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
      }
      .hint {
        margin: 0 var(--ha-space-1) var(--ha-space-3);
        color: var(--secondary-text-color);
      }
      .grid {
        gap: var(--ha-space-3);
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr));
      }
      :host([narrow]) .grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: var(--ha-space-2);
      }
      .new.add {
        flex-direction: row;
        gap: var(--ha-space-2);
        min-height: 60px;
        color: var(--primary-color);
        font-weight: var(--ha-font-weight-medium);
      }
      .arrange-bar {
        display: flex;
        align-items: center;
        gap: var(--ha-space-2);
        margin: var(--ha-space-4) 0 var(--ha-space-3);
        padding: var(--ha-space-2) var(--ha-space-2) var(--ha-space-2) var(--ha-space-4);
        border: 1px dashed var(--primary-color);
        border-radius: var(--ha-card-border-radius, var(--ha-border-radius-lg));
      }
      .arrange-bar .secondary {
        flex: 1;
        min-width: 0;
        font-size: var(--ha-font-size-s);
      }
      :host([narrow]) .grid.arrange {
        grid-template-columns: 1fr;
      }
      .item {
        display: flex;
        align-items: center;
        gap: var(--ha-space-2);
        min-height: 60px;
        padding-inline-end: var(--ha-space-3);
        border: 1px solid var(--divider-color);
        border-radius: var(--ha-card-border-radius, var(--ha-border-radius-lg));
        background: var(--card-background-color);
      }
      .handle {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 44px;
        height: 44px;
        flex: none;
        border: none;
        border-radius: var(--ha-border-radius-md);
        background: none;
        color: var(--secondary-text-color);
        cursor: grab;
      }
      .handle:focus-visible {
        outline: 2px solid var(--ha-color-focus, var(--primary-color));
      }
      .item-text {
        display: flex;
        flex-direction: column;
        flex: 1;
        min-width: 0;
        font-size: var(--ha-font-size-s);
      }
      .item-name {
        font-size: var(--ha-font-size-m);
        font-weight: var(--ha-font-weight-medium);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    `,
  ];
}

defineElement("ml-scene-gallery", MlSceneGallery);
