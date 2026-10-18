// "Add scenes to <room>": every scene as a pick tile, grouped by kind, with search and
// filters. Taps only toggle; Done writes the room's menu once. Fires ml-dialog-closed.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import type { HomeAssistant, SceneCategory, TargetInfo } from "../shared/types";
import { defineElement, fire, plural, toastOnError } from "../shared/util";
import "./components/ml-swatch";
import { go, newScenePath } from "./routes";
import { matchesFilter, matchesQuery, pickLine, type SceneEntry, sceneEntries } from "./scene-facts";
import { menuIds, type MlStore, SCENE_GROUPS, withScene } from "./store";

type PickFilter = "all" | SceneCategory | "here";

/** A longer group shows this many tiles and folds the rest behind "N more". */
const TAIL_FROM = 9;
const SHOWN = 7;

class MlScenePicker extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId!: string;

  @state() private _open = true;

  /** The menu as picked so far, in order: the room's, then new picks as tapped. */
  @state() private _picked: string[] = [];

  @state() private _search = "";

  @state() private _filter: PickFilter = "all";

  @state() private _unfolded = new Set<SceneCategory>();

  @state() private _saving = false;

  private _initial: string[] = [];

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("targetId")) {
      const target = this.store.target(this.targetId);
      this._initial = target ? menuIds(target) : [];
      this._picked = [...this._initial];
    }
  }

  protected render() {
    const target = this.store.target(this.targetId);
    if (!target) return nothing;
    const query = this._search.trim();
    const entries = sceneEntries(this.store).filter(
      (e) =>
        matchesQuery(e, query) &&
        (this._filter === "all" ||
          (this._filter === "here" ? this._picked.includes(e.id) : matchesFilter(e, this._filter))),
    );
    const filters: [PickFilter, string][] = [...SCENE_GROUPS, ["here", `Shown here · ${this._picked.length}`]];
    return html`<ha-adaptive-dialog
      .open=${this._open}
      width="large"
      flexcontent
      header-title=${`Add scenes to ${target.name}`}
      @closed=${this._closed}
    >
      <div class="top">
        <ha-input
          class="search"
          placeholder="Search scenes"
          aria-label="Search scenes"
          autocomplete="off"
          with-clear
          .value=${this._search}
          @input=${(ev: Event) => (this._search = (ev.target as HTMLInputElement).value ?? "")}
        >
          <ha-icon slot="start" icon="mdi:magnify"></ha-icon>
        </ha-input>
        <ha-chip-set>
          ${[["all", "All"] as [PickFilter, string], ...filters].map(
            ([key, label]) => html`<ha-filter-chip
              .label=${label}
              .selected=${this._filter === key}
              @click=${(ev: Event) => {
                ev.preventDefault();
                this._filter = key;
              }}
            ></ha-filter-chip>`,
          )}
        </ha-chip-set>
      </div>
      <div class="library">
        ${SCENE_GROUPS.map(([category, title]) =>
          this._renderGroup(target, category, title, entries.filter((e) => e.group === category), !!query),
        )}
        ${entries.length ? nothing : html`<p class="none">No scene matches.</p>`}
      </div>
      <ha-dialog-footer slot="footer">
        <span slot="secondaryAction" class="count">
          ${plural(this._picked.length, "scene")} shown in ${target.name}<span class="hint">
            · tap a tile to add or remove it</span
          >
        </span>
        <ha-button slot="secondaryAction" appearance="plain" @click=${this._newScene}>
          <ha-icon slot="start" icon="mdi:plus"></ha-icon>
          New scene
        </ha-button>
        <ha-button slot="primaryAction" .loading=${this._saving} @click=${this._done}>Done</ha-button>
      </ha-dialog-footer>
    </ha-adaptive-dialog>`;
  }

  private _renderGroup(
    target: TargetInfo,
    category: SceneCategory,
    title: string,
    entries: SceneEntry[],
    searching: boolean,
  ) {
    if (!entries.length) return nothing;
    const folded = !searching && this._filter === "all" && !this._unfolded.has(category) && entries.length >= TAIL_FROM;
    const shown = folded
      ? entries.filter((e, i) => i < SHOWN || this._picked.includes(e.id))
      : entries;
    const rest = entries.length - shown.length;
    return html`<h3 class="group">${title}</h3>
      <div class="grid">
        ${repeat(shown, (e) => e.id, (e) => this._renderTile(target, e))}
        ${
          rest
            ? html`<button
                class="tile more"
                @click=${() => (this._unfolded = new Set([...this._unfolded, category]))}
              >
                <ha-icon icon="mdi:chevron-right"></ha-icon>
                ${rest} more ${title.toLowerCase()}
              </button>`
            : nothing
        }
      </div>`;
  }

  private _renderTile(target: TargetInfo, entry: SceneEntry) {
    const picked = this._picked.includes(entry.id);
    const line = pickLine(this.store, entry, target, picked);
    const ghost = entry.own && !entry.scene.fits.includes(target.id);
    return html`<button
      class="tile ${picked ? "picked" : ""}"
      aria-pressed=${picked ? "true" : "false"}
      @click=${() => (this._picked = withScene(this._picked, entry.id, !picked))}
    >
      <ml-swatch
        size="32"
        .swatches=${this.store.swatchesOf(entry.id, target.id) ?? entry.scene.swatches}
        .icon=${entry.scene.icon ?? undefined}
        ?ghost=${ghost}
      ></ml-swatch>
      <span class="text">
        <span class="name">${entry.scene.name}</span>
        <span class="line">${line.text}</span>
      </span>
      ${
        picked
          ? html`<span class="badge ${line.warn ? "warn" : ""}">
              <ha-icon .icon=${line.warn ? "mdi:alert" : "mdi:check"}></ha-icon>
            </span>`
          : nothing
      }
    </button>`;
  }

  private _changed(): boolean {
    return this._picked.length !== this._initial.length || this._picked.some((id, i) => id !== this._initial[i]);
  }

  private async _save(): Promise<boolean> {
    if (!this._changed()) return true;
    this._saving = true;
    const ok = await toastOnError(this, this.store.setMenu(this.targetId, this._picked), "Could not change the menu");
    this._saving = false;
    return ok;
  }

  private async _done(): Promise<void> {
    if (await this._save()) this._open = false;
  }

  private async _newScene(): Promise<void> {
    if (!(await this._save())) return;
    this._open = false;
    go(newScenePath(this.targetId));
  }

  private _closed(ev: Event): void {
    if (ev.target !== ev.currentTarget) return;
    fire(this, "ml-dialog-closed");
  }

  static styles = css`
    .top {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3);
      margin-bottom: var(--ha-space-2);
    }
    ha-input {
      --ha-input-padding-bottom: 0;
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
    .tile.more {
      justify-content: center;
      border-style: dashed;
      color: var(--secondary-text-color);
      font-weight: var(--ha-font-weight-medium);
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
    .badge.warn {
      background: var(--warning-color, #ffa600);
      color: #000;
    }
    .none {
      color: var(--secondary-text-color);
    }
    @media (max-width: 600px) {
      .hint {
        display: none;
      }
      /* One scrolling row of filters rather than three wrapped ones. */
      ha-chip-set {
        flex-wrap: nowrap;
        overflow-x: auto;
        scrollbar-width: none;
      }
      ha-chip-set > * {
        flex: none;
      }
      .grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: var(--ha-space-2);
      }
      .tile {
        gap: var(--ha-space-2);
        padding: var(--ha-space-2);
      }
    }
    .count {
      flex: 1;
      min-width: 0;
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
  `;
}

defineElement("ml-scene-picker", MlScenePicker);
