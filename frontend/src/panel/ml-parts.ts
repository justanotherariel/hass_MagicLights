// Home's Parts tab: every part once, with the scenes that play it. Yours first (each one,
// even a part no scene uses), then the built-ins in use, edited or the switch-on default;
// the rest of the catalogue folds by category. A row opens the part editor, a scene chip
// that scene's editor. The search field shows while `search` is a string; edits fire
// ml-search with the new query, or undefined when Escape closes it.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import { swatchBackground } from "../shared/color";
import type { HomeAssistant, SceneInfo } from "../shared/types";
import { defineElement, fire } from "../shared/util";
import "./components/ml-swatch";
import { editorPath, go, partPath } from "./routes";
import { lookLine, matchesQuery, type PartEntry, type PartFold, partEntries, partSections } from "./scene-facts";
import type { MlStore, PartGroupKey } from "./store";
import { chipStyles, dotStyles, pageStyles } from "./styles";

/** Names a folded category spells out before "…". */
const PREVIEW = 6;

class MlParts extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ type: Boolean, reflect: true }) narrow = false;

  /** The search query; undefined while the field is closed. */
  @property({ attribute: false }) search?: string;

  @state() private _unfolded = new Set<PartGroupKey>();

  protected render() {
    const query = this.search ?? "";
    const entries = partEntries(this.store).filter((e) => matchesQuery(e.part.name, query));
    const { yours, builtins, more } = partSections(entries);
    const searching = !!query.trim();
    return html`
      ${this._renderSearch()}
      ${
        yours.length || !searching
          ? html`<h2 class="section-title">Your parts</h2>
              <ha-card>
                ${
                  yours.length
                    ? this._renderList("Your parts", yours)
                    : html`<p class="empty">
                        No parts of yours yet. Copy a part in a scene or here to make it your own.
                      </p>`
                }
              </ha-card>`
          : nothing
      }
      ${
        builtins.length
          ? html`<h2 class="section-title">Built-ins in use</h2>
              <ha-card>${this._renderList("Built-ins in use", builtins)}</ha-card>`
          : nothing
      }
      ${
        more.length
          ? html`<h2 class="section-title">More built-ins</h2>
              <ha-card>${more.map((fold) => this._renderFold(fold, searching))}</ha-card>`
          : nothing
      }
      ${entries.length ? nothing : html`<p class="empty">No part matches.</p>`}
    `;
  }

  private _renderSearch() {
    if (this.search === undefined) return nothing;
    return html`<ha-input
      class="search"
      placeholder="Search parts"
      aria-label="Search parts"
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
    </ha-input>`;
  }

  private _renderList(label: string, entries: readonly PartEntry[]) {
    return html`<div class="list" role="list" aria-label=${label}>
      ${repeat(
        entries,
        (e) => e.part.id,
        (e) => this._renderRow(e),
      )}
    </div>`;
  }

  /** The row opens the part; its scene chips sit above that and open their scene. */
  private _renderRow(entry: PartEntry) {
    const { part, uses } = entry;
    return html`<div class="row" role="listitem">
      <button class="open" @click=${() => go(partPath(part.id))}>
        <ml-swatch size="32" .swatches=${part.swatches} .icon=${part.icon ?? undefined}></ml-swatch>
        <span class="text">
          <span class="headline">
            <span class="name">${part.name}</span>
            ${
              part.edited
                ? html`<ha-badge class="edited" .label=${""}>
                    <ha-icon slot="icon" icon="mdi:pencil"></ha-icon>
                    Edited
                  </ha-badge>`
                : nothing
            }
          </span>
          <span class="summary">${lookLine(this.store.effect(part.effect), part)}</span>
        </span>
      </button>
      <span class="uses">
        ${entry.isDefault ? html`<span class="tag">Switch-on default</span>` : nothing}
        ${uses.map((scene) => this._renderUse(scene))}
        ${
          uses.length || entry.isDefault || part.builtin
            ? nothing
            : html`<span class="unused">${part.dormant.length ? "Only in rooms that are gone" : "In no scene"}</span>`
        }
      </span>
      <ha-icon class="chevron" icon="mdi:chevron-right"></ha-icon>
    </div>`;
  }

  private _renderUse(scene: SceneInfo) {
    return html`<ha-assist-chip
      filled
      .label=${this.store.useLabel(scene)}
      @click=${() => go(editorPath(scene.target, scene.id))}
    >
      <span slot="icon" class="dot" style="background: ${swatchBackground(scene.swatches)}"></span>
    </ha-assist-chip>`;
  }

  /** A category of the catalogue no scene uses: one row that opens to its parts. */
  private _renderFold(fold: PartFold, searching: boolean) {
    // A match must not hide behind the fold.
    const open = searching || this._unfolded.has(fold.key);
    const names = fold.entries.slice(0, PREVIEW).map((e) => e.part.name);
    const toggle = () => {
      const next = new Set(this._unfolded);
      if (!next.delete(fold.key)) next.add(fold.key);
      this._unfolded = next;
    };
    return html`<section class="fold">
      <button class="fold-head" aria-expanded=${open ? "true" : "false"} .disabled=${searching} @click=${toggle}>
        <span class="stack">
          ${fold.entries.slice(0, 5).map((e) => html`<ml-swatch size="32" .swatches=${e.part.swatches}></ml-swatch>`)}
        </span>
        <span class="text">
          <span class="name">${fold.title} · ${fold.entries.length}</span>
          <span class="summary">${names.join(", ")}${fold.entries.length > PREVIEW ? " …" : ""}</span>
        </span>
        ${searching ? nothing : html`<ha-icon .icon=${open ? "mdi:chevron-up" : "mdi:chevron-down"}></ha-icon>`}
      </button>
      ${open ? this._renderList(fold.title, fold.entries) : nothing}
    </section>`;
  }

  static styles = [
    pageStyles,
    chipStyles,
    dotStyles,
    css`
      ha-input.search {
        display: block;
        margin: var(--ha-space-1) 0 var(--ha-space-2);
      }
      ha-card {
        overflow: hidden;
      }
      .row {
        position: relative;
        display: grid;
        grid-template-columns: minmax(0, 1fr) fit-content(60%) 24px;
        grid-template-areas: "open uses chevron";
        align-items: center;
        gap: var(--ha-space-3);
        min-height: 64px;
        padding: var(--ha-space-2) var(--ha-space-4);
        box-sizing: border-box;
      }
      .row + .row,
      .fold + .fold,
      .fold-head + .list {
        border-top: 1px solid var(--divider-color);
      }
      .row:hover {
        background: rgba(var(--rgb-primary-text-color), 0.04);
      }
      :host([narrow]) .row {
        grid-template-columns: minmax(0, 1fr) 24px;
        grid-template-areas: "open chevron" "uses uses";
        row-gap: var(--ha-space-2);
      }
      button {
        margin: 0;
        padding: 0;
        border: none;
        background: none;
        color: inherit;
        font: inherit;
        text-align: start;
        cursor: pointer;
      }
      button:focus-visible {
        outline: 2px solid var(--ha-color-focus, var(--primary-color));
        outline-offset: -2px;
      }
      .open {
        grid-area: open;
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
        min-width: 0;
      }
      /* The whole row opens the part; the chips above it open their scene. */
      .open::after {
        content: "";
        position: absolute;
        inset: 0;
      }
      .uses {
        grid-area: uses;
        position: relative;
        z-index: 1;
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: var(--ha-space-1);
        min-width: 0;
      }
      :host([narrow]) .uses {
        justify-content: flex-start;
        padding-inline-start: 44px;
      }
      :host([narrow]) .uses:empty {
        display: none;
      }
      .chevron {
        grid-area: chevron;
        color: var(--secondary-text-color);
      }
      .text {
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 0;
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
      ha-assist-chip {
        max-width: 100%;
      }
      .tag,
      .unused {
        display: inline-flex;
        align-items: center;
        height: 32px;
        font-size: var(--ha-font-size-s);
        white-space: nowrap;
      }
      .tag {
        padding: 0 var(--ha-space-3);
        border: 1px solid var(--divider-color);
        border-radius: var(--ha-border-radius-pill);
      }
      .unused {
        color: var(--secondary-text-color);
      }
      .fold-head {
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
        width: 100%;
        min-height: 64px;
        padding: var(--ha-space-2) var(--ha-space-4);
        box-sizing: border-box;
      }
      .fold-head:disabled {
        cursor: default;
      }
      .fold-head:not(:disabled):hover {
        background: rgba(var(--rgb-primary-text-color), 0.04);
      }
      .fold-head .text {
        flex: 1;
      }
      .fold-head ha-icon {
        flex: none;
        color: var(--secondary-text-color);
      }
      /* Room for five overlapping swatches, so the fold titles line up. */
      .stack {
        display: flex;
        flex: none;
        width: 104px;
      }
      .stack ml-swatch + ml-swatch {
        margin-inline-start: -14px;
      }
      :host([narrow]) .stack {
        width: 68px;
      }
      :host([narrow]) .stack ml-swatch:nth-child(n + 4) {
        display: none;
      }
      .fold .list {
        background: rgba(var(--rgb-primary-text-color), 0.02);
      }
      .empty {
        margin: 0;
        padding: var(--ha-space-4);
        color: var(--secondary-text-color);
      }
    `,
  ];
}

defineElement("ml-parts", MlParts);
