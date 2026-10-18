// Home's Scenes tab: every scene of yours and every edited or picked preset once, with
// the rooms that show it. Opening a row edits the scene for all rooms.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import type { HomeAssistant, SceneInfo } from "../shared/types";
import { defineElement } from "../shared/util";
import "./components/ml-swatch";
import { editorPath, go, newScenePath } from "./routes";
import { menuId, type MlStore } from "./store";
import { pageStyles } from "./styles";

class MlScenes extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ type: Boolean }) narrow = false;

  protected render() {
    const listed = this.store.menuScenes().filter((scene) => {
      const id = menuId(scene);
      return !scene.builtin && !scene.preset
        ? true
        : this.store.edited(id) || this.store.availability(id).some((a) => a.shown);
    });
    return html`
      <div class="toolbar">
        <ha-button @click=${() => go(newScenePath())}>
          <ha-icon slot="start" icon="mdi:plus"></ha-icon>
          New scene
        </ha-button>
      </div>
      <ha-card>
        <ha-list-base aria-label="Scenes">
          ${listed.map((scene) => this._renderRow(scene))}
        </ha-list-base>
      </ha-card>
    `;
  }

  private _renderRow(scene: SceneInfo) {
    const id = menuId(scene);
    const rooms = this.store.availability(id).filter((a) => a.shown);
    return html`<ha-list-item-button @click=${() => go(editorPath(id))}>
      <ml-swatch slot="start" size="32" .swatches=${scene.swatches} .icon=${scene.icon ?? undefined}></ml-swatch>
      <span slot="headline">${scene.name}${this.store.edited(id) ? " · Edited" : nothing}</span>
      <span slot="supporting-text">
        ${rooms.map((a) => `${a.target.name}${a.needsSetUp ? " (needs set-up)" : ""}`).join(", ") || "Not shown in any room"}
      </span>
    </ha-list-item-button>`;
  }

  static styles = [
    pageStyles,
    css`
      .toolbar {
        display: flex;
        justify-content: flex-end;
        margin-bottom: var(--ha-space-4);
      }
      ha-list-base {
        display: block;
      }
    `,
  ];
}

defineElement("ml-scenes", MlScenes);
