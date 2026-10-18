// A room's Scenes tab below the hero: its menu as tiles (a scene without lights here as a
// ghost), "Add scenes", one row per scene that needs setting up here, and Arrange mode
// (drag to reorder, the eye hides; Done writes the menu once). Fires ml-arrange-done.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import { isPlayable } from "../shared/scene-actions";
import { activeScene } from "../shared/status";
import type { HomeAssistant, SceneInfo, TargetInfo } from "../shared/types";
import { defineElement, fire, toastOnError } from "../shared/util";
import "./components/ml-swatch";
import "./ml-scene-card";
import type { SceneAction } from "./ml-scene-card";
import "./ml-scene-picker";
import { editorPath, go } from "./routes";
import { movedBy, tileLine } from "./scene-facts";
import { menuId, menuIds, type MlStore } from "./store";
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

  /** Arrange mode's menu order and the scenes it hides, until Done. */
  @state() private _order: string[] = [];

  @state() private _hidden = new Set<string>();

  @state() private _saving = false;

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("arranging") && this.arranging) {
      const target = this.store.target(this.targetId);
      this._order = target ? menuIds(target) : [];
      this._hidden = new Set();
    }
  }

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    return html`${this.arranging ? this._renderArrange(target) : this._renderTiles(target)}
    ${
      this._picking
        ? html`<ml-scene-picker
            .hass=${this.hass}
            .store=${this.store}
            .targetId=${target.id}
            @ml-dialog-closed=${() => (this._picking = false)}
          ></ml-scene-picker>`
        : nothing
    }`;
  }

  private _menu(target: TargetInfo): SceneInfo[] {
    return menuIds(target).flatMap((id) => this.store.resolveScene(id) ?? []);
  }

  private _fits(scene: SceneInfo, target: TargetInfo): boolean {
    return scene.fits.includes(target.id);
  }

  private _renderTiles(target: TargetInfo) {
    const attrs = this.store.status(this.hass, target.id)?.attrs;
    const active = activeScene(attrs);
    const menu = this._menu(target);
    const ghosts = menu.filter((scene) => !this._fits(scene, target));
    return html`
      <div class="head">
        <h2 class="section-title">Scenes</h2>
        <span class="legend" ?hidden=${!menu.length}>
          ${
            this.narrow
              ? "Tap: run · swatch: edit"
              : "Tap a tile to run it · tap its swatch to edit · Arrange to reorder or hide"
          }
        </span>
      </div>
      ${
        menu.length
          ? nothing
          : html`<p class="hint">No scenes here yet. Add your own or presets; they stay one tap away.</p>`
      }
      <div class="grid">
        ${repeat(
          menu,
          (scene) => menuId(scene),
          (scene) => {
            const id = menuId(scene);
            return html`<ml-scene-card
              .scene=${scene}
              .swatches=${this.store.swatchesOf(id, target.id) ?? scene.swatches}
              .line=${tileLine(scene, (key) => this.store.effect(key))}
              .active=${active === id}
              .animated=${isPlayable(scene)}
              .ghost=${!this._fits(scene, target)}
              .disabled=${!attrs}
              @ml-scene-action=${(ev: CustomEvent<{ action: SceneAction }>) => this._action(id, ev.detail.action)}
            ></ml-scene-card>`;
          },
        )}
        <button class="new add" @click=${() => (this._picking = true)}>
          <ha-icon icon="mdi:plus"></ha-icon>
          Add scenes
        </button>
      </div>
      ${ghosts.map((scene) => this._renderSetUp(target, scene))}
    `;
  }

  /** One quiet row per ghost: why it is grey and the one step that fixes it. */
  private _renderSetUp(target: TargetInfo, scene: SceneInfo) {
    const id = menuId(scene);
    const others = scene.fits.filter((t) => this.store.target(t)?.kind === "room").map((t) => this.store.targetName(t));
    const title = others.length ? `${scene.name} is set up for other lights` : `${scene.name} has no lights yet`;
    const text = others.length
      ? `Pick which ${target.name} lights do what, once. Its look stays shared with ${others.join(", ")}.`
      : `Pick which ${target.name} lights do what.`;
    return html`<ha-card class="setup">
      <ml-swatch size="32" .swatches=${scene.swatches} .icon=${scene.icon ?? undefined} ghost></ml-swatch>
      <div class="setup-text">
        <span class="setup-title">${title}</span>
        <span class="secondary">${text}</span>
      </div>
      <ha-button appearance="plain" @click=${() => go(editorPath(id, target.id))}>
        <ha-icon slot="start" icon="mdi:swap-horizontal"></ha-icon>
        ${this.narrow ? "Set up" : `Set up for ${target.name}`}
      </ha-button>
    </ha-card>`;
  }

  private _renderArrange(target: TargetInfo) {
    const scenes = this._order.flatMap((id) => {
      const scene = this.store.resolveScene(id);
      return scene ? [{ id, scene }] : [];
    });
    return html`
      <div class="arrange-bar">
        <span class="secondary">
          Drag to reorder. The eye hides a scene from ${target.name}; it stays in the library.
        </span>
        <ha-button appearance="plain" @click=${() => fire(this, "ml-arrange-done")}>Cancel</ha-button>
        <ha-button .loading=${this._saving} @click=${() => this._done(target)}>Done</ha-button>
      </div>
      <ha-sortable handle-selector=".handle" @item-moved=${(ev: CustomEvent<Move>) => this._moved(ev.detail)}>
        <div class="grid arrange">
          ${repeat(
            scenes,
            (s) => s.id,
            ({ id, scene }) => {
              const hidden = this._hidden.has(id);
              const fits = this._fits(scene, target);
              const line = hidden
                ? "Hidden here"
                : fits
                  ? tileLine(scene, (key) => this.store.effect(key))
                  : "Needs set-up";
              return html`<div class="item ${hidden ? "hidden" : ""}">
                <button
                  class="handle"
                  aria-label=${`Move ${scene.name}; arrow keys move it`}
                  @keydown=${(ev: KeyboardEvent) => this._key(ev, id)}
                >
                  <ha-icon icon="mdi:drag"></ha-icon>
                </button>
                <ml-swatch
                  size="32"
                  .swatches=${this.store.swatchesOf(id, target.id) ?? scene.swatches}
                  .icon=${scene.icon ?? undefined}
                  ?ghost=${hidden || !fits}
                ></ml-swatch>
                <span class="item-text">
                  <span class="item-name">${scene.name}</span>
                  <span class="secondary">${line}</span>
                </span>
                <ha-icon-button
                  .label=${hidden ? `Show ${scene.name} in ${target.name}` : `Hide ${scene.name} from ${target.name}`}
                  @click=${() => this._toggleHidden(id)}
                >
                  <ha-icon .icon=${hidden ? "mdi:eye-outline" : "mdi:eye-off-outline"}></ha-icon>
                </ha-icon-button>
              </div>`;
            },
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

  private _toggleHidden(id: string): void {
    const hidden = new Set(this._hidden);
    if (!hidden.delete(id)) hidden.add(id);
    this._hidden = hidden;
  }

  private async _done(target: TargetInfo): Promise<void> {
    const next = this._order.filter((id) => !this._hidden.has(id));
    const current = menuIds(target);
    if (next.length !== current.length || next.some((id, i) => id !== current[i])) {
      this._saving = true;
      const ok = await toastOnError(this, this.store.setMenu(target.id, next), "Could not change the menu");
      this._saving = false;
      if (!ok) return;
    }
    fire(this, "ml-arrange-done");
  }

  private _action(id: string, action: SceneAction): void {
    if (action === "edit") {
      go(editorPath(id, this.targetId));
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
      .setup {
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
        margin-top: var(--ha-space-4);
        padding: var(--ha-space-3) var(--ha-space-2) var(--ha-space-3) var(--ha-space-4);
      }
      .setup-text {
        display: flex;
        flex-direction: column;
        flex: 1;
        min-width: 0;
        font-size: var(--ha-font-size-s);
      }
      .setup-title {
        font-size: var(--ha-font-size-m);
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
        padding-inline-end: var(--ha-space-1);
        border: 1px solid var(--divider-color);
        border-radius: var(--ha-card-border-radius, var(--ha-border-radius-lg));
        background: var(--card-background-color);
      }
      .item.hidden .item-name {
        color: var(--secondary-text-color);
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
