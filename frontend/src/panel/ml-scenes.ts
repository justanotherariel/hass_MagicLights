// Home's Scenes tab: every scene once, with the rooms that show it. Yours first (each one,
// even a scene that fits no room any more), then the presets a room shows or you edited;
// the rest of the library folds into one row. A row opens the editor for all rooms.
// The search field shows while `search` is a string; edits fire ml-search with the new
// query, or undefined when Escape closes it.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import type { HomeAssistant } from "../shared/types";
import { defineElement, fire } from "../shared/util";
import "./components/ml-swatch";
import { editorPath, go, newScenePath } from "./routes";
import {
  FILTERS,
  lookSummary,
  matchesFilter,
  matchesQuery,
  type SceneEntry,
  type SceneFilter,
  sceneEntries,
  sceneSections,
} from "./scene-facts";
import type { MlStore } from "./store";
import { pageStyles } from "./styles";

/** Names the folded row spells out before "…". */
const PREVIEW = 6;

class MlScenes extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ type: Boolean, reflect: true }) narrow = false;

  /** The search query; undefined while the field is closed. */
  @property({ attribute: false }) search?: string;

  @state() private _filter: SceneFilter = "all";

  @state() private _more = false;

  protected render() {
    const query = this.search ?? "";
    const all = sceneEntries(this.store).filter((e) => matchesQuery(e, query));
    const entries = all.filter((e) => matchesFilter(e, this._filter));
    const { own, presets, more } = sceneSections(entries);
    const searching = !!query.trim();
    return html`
      ${
        this.search === undefined
          ? nothing
          : html`<ha-input
              class="search"
              placeholder="Search scenes"
              aria-label="Search scenes"
              autocomplete="off"
              autofocus
              with-clear
              .value=${this.search}
              @input=${(ev: Event) => fire(this, "ml-search", { value: (ev.target as HTMLInputElement).value ?? "" })}
              @keydown=${(ev: KeyboardEvent) => {
                if (ev.key === "Escape") fire(this, "ml-search", { value: undefined });
              }}
            >
              <ha-icon slot="start" icon="mdi:magnify"></ha-icon>
            </ha-input>`
      }
      <div class="toolbar">
        <ha-chip-set class="filters">
          ${FILTERS.map(([key, label]) => {
            const count = all.filter((e) => matchesFilter(e, key)).length;
            return html`<ha-filter-chip
              .label=${key === "all" ? label : `${label} · ${count}`}
              .selected=${this._filter === key}
              @click=${(ev: Event) => {
                ev.preventDefault();
                this._filter = key;
              }}
            ></ha-filter-chip>`;
          })}
        </ha-chip-set>
        <ha-button @click=${() => go(newScenePath())}>
          <ha-icon slot="start" icon="mdi:plus"></ha-icon>
          New scene
        </ha-button>
      </div>
      ${
        own.length || (this._filter === "all" && !searching)
          ? html`<h2 class="section-title">Your scenes</h2>
              <ha-card>
                ${
                  own.length
                    ? this._renderList("Your scenes", own)
                    : html`<p class="empty">
                        No scenes of yours yet. Start one with New scene, or edit a preset to change it for
                        every room.
                      </p>`
                }
              </ha-card>`
          : nothing
      }
      ${
        presets.length
          ? html`<h2 class="section-title">Presets shown in a room</h2>
              <ha-card>${this._renderList("Presets shown in a room", presets)}</ha-card>`
          : nothing
      }
      ${more.length ? this._renderMore(more, searching) : nothing}
      ${entries.length ? nothing : html`<p class="empty">No scene matches.</p>`}
    `;
  }

  private _renderList(label: string, entries: SceneEntry[]) {
    return html`<ha-list-base aria-label=${label}>
      ${repeat(
        entries,
        (e) => e.id,
        (e) => this._renderRow(e),
      )}
    </ha-list-base>`;
  }

  private _renderRow(entry: SceneEntry) {
    const scene = entry.scene;
    const rooms = !entry.isDefault && !entry.shown.length ? nothing : html`<span class="rooms">
      ${entry.isDefault ? html`<span class="room">Switch-on default</span>` : nothing}
      ${entry.shown.map((a) =>
        a.needsSetUp
          ? html`<span class="room warn"><span class="dot"></span>${a.target.name} · needs set-up</span>`
          : html`<span class="room">${a.target.name}</span>`,
      )}
    </span>`;
    return html`<ha-list-item-button @click=${() => go(editorPath(entry.id))}>
      <ml-swatch slot="start" size="32" .swatches=${scene.swatches} .icon=${scene.icon ?? undefined}></ml-swatch>
      <div slot="content" class="row">
        <div class="text">
        <span class="headline">
          <span class="name">${scene.name}</span>
          ${
            entry.edited
              ? html`<ha-badge class="edited" .label=${""}>
                  <ha-icon slot="icon" icon="mdi:pencil"></ha-icon>
                  Edited
                </ha-badge>`
              : nothing
          }
        </span>
        <span class="summary">${lookSummary(scene, (key) => this.store.effect(key))}</span>
        </div>
        ${rooms}
      </div>
      <ha-icon slot="end" class="chevron" icon="mdi:chevron-right"></ha-icon>
    </ha-list-item-button>`;
  }

  /** The library a room does not show yet: one row that opens to the same rows. */
  private _renderMore(more: SceneEntry[], searching: boolean) {
    // A match must not hide behind the fold.
    const open = this._more || searching;
    const names = more.slice(0, PREVIEW).map((e) => e.scene.name);
    const content = html`
      <span slot="start" class="stack">
        ${more.slice(0, 5).map((e) => html`<ml-swatch size="32" .swatches=${e.scene.swatches}></ml-swatch>`)}
      </span>
      <span slot="headline">${more.length} more presets</span>
      <span slot="supporting-text">
        Not shown in any room yet · ${names.join(", ")}${more.length > PREVIEW ? " …" : ""}
      </span>
    `;
    return html`<ha-card class="more">
      <ha-list-base aria-label="More presets">
        ${
          searching
            ? html`<ha-list-item-base>${content}</ha-list-item-base>`
            : html`<ha-list-item-button aria-expanded=${open ? "true" : "false"} @click=${() => (this._more = !this._more)}>
                ${content}
                <ha-icon slot="end" .icon=${open ? "mdi:chevron-up" : "mdi:chevron-down"}></ha-icon>
              </ha-list-item-button>`
        }
      </ha-list-base>
      ${open ? html`<div class="divider"></div>${this._renderList("More presets", more)}` : nothing}
    </ha-card>`;
  }

  static styles = [
    pageStyles,
    css`
      ha-input.search {
        display: block;
        margin: var(--ha-space-1) 0 var(--ha-space-2);
      }
      .toolbar {
        display: flex;
        align-items: flex-start;
        gap: var(--ha-space-3);
        margin: var(--ha-space-1) 0 var(--ha-space-2);
      }
      .filters {
        flex: 1;
        min-width: 0;
      }
      :host([narrow]) .toolbar {
        flex-direction: column;
        align-items: stretch;
      }
      /* One scrolling row on a phone rather than four wrapped ones. */
      :host([narrow]) .filters {
        flex-wrap: nowrap;
        overflow-x: auto;
        scrollbar-width: none;
        padding-bottom: 2px;
      }
      :host([narrow]) .filters > * {
        flex: none;
      }
      :host([narrow]) .toolbar ha-button {
        align-self: flex-end;
      }
      ha-card {
        overflow: hidden;
      }
      ha-card.more {
        margin-top: var(--ha-space-6);
      }
      ha-list-base {
        display: block;
      }
      ha-list-item-button {
        --ha-row-item-min-height: 64px;
      }
      ha-list-item-button + ha-list-item-button {
        border-top: 1px solid var(--divider-color);
      }
      .divider {
        border-top: 1px solid var(--divider-color);
      }
      .row {
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
        min-width: 0;
        width: 100%;
      }
      :host([narrow]) .row {
        flex-direction: column;
        align-items: stretch;
        gap: var(--ha-space-1);
      }
      .text {
        display: flex;
        flex-direction: column;
        gap: 2px;
        flex: 1 1 200px;
        min-width: 0;
      }
      /* Stacked, the 200px basis would be a height. */
      :host([narrow]) .text {
        flex: none;
      }
      .headline {
        display: flex;
        align-items: center;
        gap: var(--ha-space-2);
        min-width: 0;
      }
      .name {
        font-weight: var(--ha-font-weight-medium);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .summary {
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      ha-badge.edited {
        flex: none;
        --ha-badge-size: 22px;
        --ha-badge-font-size: var(--ha-font-size-xs);
        --ha-badge-icon-size: 12px;
        --badge-color: var(--warning-color, #ffa600);
        --ha-card-background: color-mix(in srgb, var(--warning-color, #ffa600) 16%, transparent);
        --ha-card-border-color: transparent;
        --ha-card-box-shadow: none;
      }
      .rooms {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: var(--ha-space-1);
        flex: 0 1 auto;
        max-width: 60%;
        min-width: 0;
      }
      :host([narrow]) .rooms {
        justify-content: flex-start;
        max-width: none;
      }
      .room {
        display: inline-flex;
        align-items: center;
        gap: var(--ha-space-1);
        height: 28px;
        padding: 0 var(--ha-space-3);
        border: 1px solid var(--divider-color);
        border-radius: var(--ha-border-radius-pill);
        font-size: var(--ha-font-size-s);
        white-space: nowrap;
        color: var(--primary-text-color);
      }
      .room.warn {
        border-color: var(--warning-color, #ffa600);
      }
      .room .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--warning-color, #ffa600);
      }
      .chevron {
        flex: none;
        color: var(--secondary-text-color);
      }
      .stack {
        display: flex;
        padding-inline-end: var(--ha-space-2);
      }
      .stack ml-swatch + ml-swatch {
        margin-inline-start: -14px;
      }
      .empty {
        margin: 0;
        padding: var(--ha-space-4);
        color: var(--secondary-text-color);
      }
    `,
  ];
}

defineElement("ml-scenes", MlScenes);
