// The part picker: every part as a pick tile, yours first, the built-ins by kind, with
// search. "add" (a room's New scene) picks several and adds one whole-room scene per
// part in one save; with one pick, Choose lights opens the editor on it. "pick" (the
// editor's + Part) fires ml-part-picked on the first tap and marks the scene's own parts.
// Both start with a blank New part. Fires ml-dialog-closed.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import type { HomeAssistant, PartInfo, TargetInfo } from "../shared/types";
import { defineElement, fire, plural, toastOnError } from "../shared/util";
import "./components/ml-swatch";
import { go, newScenePath } from "./routes";
import { lookLine, matchesQuery } from "./scene-facts";
import { groupParts, type MlStore, type PartGroupKey } from "./store";

export type PickerMode = "add" | "pick";

/** "ml-part-picked": the part a layer of the editor's scene plays now; null for a blank one. */
export interface PartPicked {
  partId: string | null;
}

/** A longer group shows this many tiles and folds the rest behind "N more". */
const TAIL_FROM = 9;
const SHOWN = 7;

class MlPartPicker extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @property() mode: PickerMode = "add";

  /** "pick": the parts the editor's scene plays already; picking one selects it. */
  @property({ attribute: false }) inScene: readonly string[] = [];

  @state() private _open = true;

  /** Picked part ids, in the order tapped. */
  @state() private _picked: string[] = [];

  @state() private _search = "";

  @state() private _unfolded = new Set<PartGroupKey>();

  @state() private _saving = false;

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("targetId")) this._picked = [];
  }

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    const query = this._search.trim();
    const groups = groupParts(this.store.parts().filter((p) => matchesQuery(p.name, query)));
    const title = this.mode === "add" ? `New scene in ${target.name}` : "Add a part";
    return html`<ha-adaptive-dialog
      .open=${this._open}
      width="large"
      flexcontent
      header-title=${title}
      @closed=${this._closed}
    >
      <ha-input
        class="search"
        placeholder="Search parts"
        aria-label="Search parts"
        autocomplete="off"
        with-clear
        .value=${this._search}
        @input=${(ev: Event) => (this._search = (ev.target as HTMLInputElement).value ?? "")}
      >
        <ha-icon slot="start" icon="mdi:magnify"></ha-icon>
      </ha-input>
      <p class="hint">
        ${
          this.mode === "add"
            ? `Each part you pick becomes a scene of all lights in ${target.name}.`
            : "The part plays on the lights you choose next."
        }
      </p>
      <div class="library">
        ${this._renderGroup(target, "yours", "Your parts", groups.find((g) => g.key === "yours")?.parts ?? [], !!query)}
        ${groups
          .filter((group) => group.key !== "yours")
          .map((group) => this._renderGroup(target, group.key, group.title, group.parts, !!query))}
        ${groups.length ? nothing : html`<p class="none">No part matches.</p>`}
      </div>
      ${this.mode === "add" ? this._renderAddFooter() : this._renderPickFooter()}
    </ha-adaptive-dialog>`;
  }

  private _renderGroup(target: TargetInfo, key: PartGroupKey, title: string, parts: PartInfo[], searching: boolean) {
    const folded = !searching && key !== "yours" && !this._unfolded.has(key) && parts.length >= TAIL_FROM;
    const shown = folded ? parts.filter((p, i) => i < SHOWN || this._picked.includes(p.id)) : parts;
    const rest = parts.length - shown.length;
    return html`<h3 class="group">${title}</h3>
      <div class="grid">
        ${
          key === "yours"
            ? html`<button class="tile new" @click=${this.mode === "add" ? this._newPart : () => this._picks(null)}>
                <ha-icon icon="mdi:plus"></ha-icon>
                New part
              </button>`
            : nothing
        }
        ${repeat(shown, (p) => p.id, (p) => this._renderTile(target, p))}
        ${
          rest
            ? html`<button class="tile more" @click=${() => (this._unfolded = new Set([...this._unfolded, key]))}>
                <ha-icon icon="mdi:chevron-right"></ha-icon>
                ${rest} more ${title.toLowerCase()}
              </button>`
            : nothing
        }
      </div>`;
  }

  private _renderTile(target: TargetInfo, part: PartInfo) {
    const picked = this._picked.includes(part.id);
    const where = this.mode === "add" ? "In this room" : "In this scene";
    const here =
      this.mode === "add" ? !!this.store.wholeRoomScene(target.id, part.id) : this.inScene.includes(part.id);
    const look = lookLine(this.store.effect(part.effect), part);
    const line = here ? `${where} · ${look}` : look;
    return html`<button
      class="tile ${picked ? "picked" : ""}"
      aria-pressed=${this.mode === "add" ? (picked ? "true" : "false") : nothing}
      @click=${() => this._tap(part.id)}
    >
      <ml-swatch size="32" .swatches=${part.swatches} .icon=${part.icon ?? undefined}></ml-swatch>
      <span class="text">
        <span class="name">${part.name}</span>
        <span class="line">${line}</span>
      </span>
      ${
        picked
          ? html`<span class="badge"><ha-icon icon="mdi:check"></ha-icon></span>`
          : here
            ? html`<span class="badge here" title=${where}>
                <ha-icon .icon=${this.mode === "add" ? "mdi:home" : "mdi:lightbulb-group"}></ha-icon>
              </span>`
            : nothing
      }
    </button>`;
  }

  private _renderAddFooter() {
    const count = this._picked.length;
    return html`<ha-dialog-footer slot="footer">
      <ha-button slot="secondaryAction" appearance="plain" .disabled=${count !== 1} @click=${this._chooseLights}>
        Choose lights…
      </ha-button>
      <ha-button slot="primaryAction" .disabled=${!count} .loading=${this._saving} @click=${this._add}>
        ${count ? `Add ${plural(count, "scene")}` : "Add scenes"}
      </ha-button>
    </ha-dialog-footer>`;
  }

  private _renderPickFooter() {
    return html`<ha-dialog-footer slot="footer">
      <ha-button slot="primaryAction" appearance="plain" data-dialog="close">Cancel</ha-button>
    </ha-dialog-footer>`;
  }

  private _tap(partId: string): void {
    if (this.mode === "pick") {
      this._picks(partId);
      return;
    }
    this._picked = this._picked.includes(partId)
      ? this._picked.filter((id) => id !== partId)
      : [...this._picked, partId];
  }

  private _picks(partId: string | null): void {
    fire<PartPicked>(this, "ml-part-picked", { partId });
    this._open = false;
  }

  private async _add(): Promise<void> {
    this._saving = true;
    const count = this._picked.length;
    const ok = await toastOnError(
      this,
      this.store.addScenes(this.targetId, this._picked),
      `Could not add the ${count === 1 ? "scene" : "scenes"}`,
    );
    this._saving = false;
    if (ok) this._open = false;
  }

  private _chooseLights(): void {
    const [partId] = this._picked;
    this._open = false;
    go(newScenePath(this.targetId, partId));
  }

  private _newPart(): void {
    this._open = false;
    go(newScenePath(this.targetId));
  }

  private _closed(ev: Event): void {
    if (ev.target !== ev.currentTarget) return;
    fire(this, "ml-dialog-closed");
  }

  static styles = css`
    ha-input {
      --ha-input-padding-bottom: 0;
    }
    .hint {
      margin: var(--ha-space-2) 0 0;
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .group {
      margin: var(--ha-space-4) 0 var(--ha-space-2);
      font-size: var(--ha-font-size-s);
      font-weight: var(--ha-font-weight-medium);
      color: var(--secondary-text-color);
    }
    .grid {
      display: grid;
      gap: var(--ha-space-3);
      grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr));
    }
    .tile {
      position: relative;
      display: flex;
      align-items: center;
      gap: var(--ha-space-3);
      min-height: 60px;
      min-width: 0;
      margin: 0;
      padding: var(--ha-space-2) var(--ha-space-3);
      box-sizing: border-box;
      border: 1px solid var(--divider-color);
      border-radius: var(--ha-border-radius-lg, 12px);
      background: none;
      color: var(--primary-text-color);
      font: inherit;
      text-align: start;
      cursor: pointer;
    }
    .tile:hover {
      border-color: var(--secondary-text-color);
    }
    .tile:focus-visible {
      outline: 2px solid var(--ha-color-focus, var(--primary-color));
      outline-offset: 2px;
    }
    .tile.picked {
      border-color: var(--primary-color);
      background: color-mix(in srgb, var(--primary-color) 10%, transparent);
    }
    .tile.more,
    .tile.new {
      justify-content: center;
      gap: var(--ha-space-2);
      border-style: dashed;
      color: var(--secondary-text-color);
      font-weight: var(--ha-font-weight-medium);
    }
    .tile.new {
      color: var(--primary-color);
    }
    .text {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .name,
    .line {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      overflow: hidden;
      overflow-wrap: anywhere;
    }
    .name {
      font-weight: var(--ha-font-weight-medium);
    }
    .line {
      font-size: var(--ha-font-size-s);
      line-height: var(--ha-line-height-condensed);
      color: var(--secondary-text-color);
    }
    .badge {
      position: absolute;
      top: -7px;
      inset-inline-end: -7px;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--primary-color);
      color: var(--text-primary-color, #fff);
      box-shadow: 0 0 0 2px var(--card-background-color, #fff);
      --mdc-icon-size: 12px;
    }
    .badge.here {
      background: var(--secondary-text-color);
    }
    .none {
      color: var(--secondary-text-color);
    }
    @media (max-width: 600px) {
      .grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: var(--ha-space-2);
      }
      .tile {
        gap: var(--ha-space-2);
        padding: var(--ha-space-2);
      }
    }
  `;
}

defineElement("ml-part-picker", MlPartPicker);
