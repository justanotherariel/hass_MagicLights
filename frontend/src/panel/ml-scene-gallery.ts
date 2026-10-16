// Scenes tab: the target's scenes grouped by category, hidden ones folded away,
// and "New scene" (blank or from any scene).

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { swatchBackground } from "../shared/color";
import { isPlayable } from "../shared/scene-actions";
import { activeScene, isMoving, runningScene } from "../shared/status";
import type { HomeAssistant, SceneInfo, SelectAttributes } from "../shared/types";
import { confirmRemoveScene, defineElement, toastOnError } from "../shared/util";
import "./ml-scene-card";
import type { SceneAction } from "./ml-scene-card";
import { editorPath, go } from "./routes";
import { menuId, SCENE_GROUPS, type MlStore } from "./store";
import { dotStyles, newCardStyles, pageStyles } from "./styles";

/** Below this many scenes the picker needs no search. */
const SEARCH_FROM = 12;

class MlSceneGallery extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @state() private _picking = false;

  @state() private _search = "";

  protected render() {
    const visible = this.store.scenesFor(this.targetId);
    const hidden = this.store.scenesFor(this.targetId, "hidden");
    const view = this.store.status(this.hass, this.targetId);
    return html`
      ${SCENE_GROUPS.map(([category, title]) => {
        const scenes = visible.filter((scene) => this.store.groupOf(scene) === category);
        if (!scenes.length && category !== "custom") return nothing;
        return html`<h2 class="section-title">${title}</h2>
          <div class="grid">
            ${scenes.map((scene) => this._renderCard(scene, true, view?.attrs))}
            ${category === "custom" ? this._renderNewCard() : nothing}
          </div>`;
      })}
      ${
        hidden.length
          ? html`<ha-expansion-panel
              outlined
              header="Hidden scenes"
              .secondary=${`${hidden.length} not in the menu`}
            >
              <div class="grid hidden">
                ${hidden.map((scene) => this._renderCard(scene, false, view?.attrs))}
              </div>
            </ha-expansion-panel>`
          : nothing
      }
      ${this._picking ? this._renderPicker([...visible, ...hidden]) : nothing}
    `;
  }

  private _renderCard(scene: SceneInfo, listed: boolean, attrs: SelectAttributes | undefined) {
    const id = menuId(scene);
    return html`<ml-scene-card
      .scene=${scene}
      .active=${activeScene(attrs) === id}
      .playing=${runningScene(attrs) === id && isMoving(attrs)}
      .playable=${isPlayable(scene)}
      .listed=${listed}
      .disabled=${!attrs}
      @ml-scene-action=${(ev: CustomEvent<{ action: SceneAction }>) => this._action(scene, ev.detail.action)}
    ></ml-scene-card>`;
  }

  private _renderNewCard() {
    return html`<button class="new" @click=${this._openPicker}>
      <ha-icon icon="mdi:plus"></ha-icon>
      New scene
    </button>`;
  }

  private _openPicker(): void {
    this._search = "";
    this._picking = true;
  }

  private _renderPicker(scenes: SceneInfo[]) {
    const query = this._search.trim().toLowerCase();
    const found = query ? scenes.filter((scene) => scene.name.toLowerCase().includes(query)) : scenes;
    return html`<ha-adaptive-dialog
      open
      header-title="New scene"
      header-subtitle="Start blank or from a copy of a scene"
      @closed=${(ev: Event) => ev.target === ev.currentTarget && (this._picking = false)}
    >
      ${
        scenes.length >= SEARCH_FROM
          ? html`<ha-input
              class="search"
              placeholder="Search scenes"
              aria-label="Search scenes"
              autocomplete="off"
              with-clear
              .value=${this._search}
              @input=${(ev: Event) => (this._search = (ev.target as HTMLInputElement).value ?? "")}
            >
              <ha-icon slot="start" icon="mdi:magnify"></ha-icon>
            </ha-input>`
          : nothing
      }
      ${
        query
          ? nothing
          : html`<ha-list-base aria-label="Blank">
              <ha-list-item-button @click=${() => this._create()}>
                <ha-icon slot="start" icon="mdi:file-outline"></ha-icon>
                <span slot="headline">Blank scene</span>
                <span slot="supporting-text">One layer, all lights</span>
              </ha-list-item-button>
            </ha-list-base>`
      }
      ${this.store.groupScenes(found).map(
        (group) => html`<h3 class="group">${group.title}</h3>
          <ha-list-base aria-label=${group.title}>
            ${group.scenes.map(
              (scene) => html`<ha-list-item-button @click=${() => this._create(scene)}>
                <span slot="start" class="dot" style="background: ${swatchBackground(scene.swatches)}"></span>
                <span slot="headline">${scene.name}</span>
              </ha-list-item-button>`,
            )}
          </ha-list-base>`,
      )}
      ${query && !found.length ? html`<p class="none">No scene matches “${this._search.trim()}”.</p>` : nothing}
    </ha-adaptive-dialog>`;
  }

  private _create(from?: SceneInfo): void {
    const draft = this.store.newScene(this.targetId, from);
    this.store.setDraft(this.targetId, draft.id, draft);
    this._picking = false;
    go(editorPath(this.targetId, draft.id));
  }

  private async _action(scene: SceneInfo, action: SceneAction): Promise<void> {
    const target = this.targetId;
    const id = menuId(scene);
    switch (action) {
      case "activate":
        await toastOnError(this, this.store.activate(target, id), "Could not activate the scene");
        return;
      case "play":
        await toastOnError(this, this.store.toggleMotion(target, id), "Could not change the motion");
        return;
      case "edit":
        return go(editorPath(target, id));
      case "duplicate":
        return this._create(scene);
      case "hide":
      case "show":
        await toastOnError(
          this,
          this.store.setSceneVisible(target, id, action === "show"),
          "Could not change the menu",
        );
        return;
      case "reset":
      case "delete": {
        const reset = action === "reset";
        if (await confirmRemoveScene(this, scene, reset)) {
          await toastOnError(
            this,
            this.store.deleteScene(scene.id),
            reset ? "Could not reset the scene" : "Could not delete the scene",
          );
        }
        return;
      }
    }
  }

  static styles = [
    pageStyles,
    dotStyles,
    newCardStyles,
    css`
      .grid {
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 160px), 1fr));
        gap: var(--ha-space-3);
      }
      .grid.hidden {
        padding: var(--ha-space-2) 0 var(--ha-space-2);
      }
      ha-expansion-panel {
        display: block;
        margin-top: var(--ha-space-6);
        --expansion-panel-content-padding: 0 var(--ha-space-3);
      }
      .new {
        min-height: 128px;
      }
      ha-list-base {
        display: block;
      }
      .search {
        display: block;
        margin-bottom: var(--ha-space-2);
      }
      .group {
        margin: var(--ha-space-4) var(--ha-space-4) var(--ha-space-1);
        font-size: var(--ha-font-size-s);
        font-weight: var(--ha-font-weight-medium);
        color: var(--secondary-text-color);
      }
      .none {
        margin: var(--ha-space-4);
        color: var(--secondary-text-color);
      }
      .dot {
        width: 24px;
        height: 24px;
      }
    `,
  ];
}

defineElement("ml-scene-gallery", MlSceneGallery);
