// An editor's part of the panel's top bar: the scene's or part's name with a badge, and
// Try / Save. The editor describes them in an ml-editor-title event; the panel renders
// them with renderEditorBar into the bar's slots.

import { LitElement, css, html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { defineElement } from "../../shared/util";

export interface EditorTitle {
  /** The scene or part being edited. */
  id: string;
  title: string;
  /** "Living Room", "New scene · Living Room", "Built-in". */
  badge: string;
  /** Where Try runs. */
  room: string;
  trying: boolean;
  canTry: boolean;
  canSave: boolean;
  busy?: "save" | "try" | "delete" | "reset";
  onTry(): void;
  onStopTry(): void;
  onSave(): void;
}

class MlEditorTitle extends LitElement {
  @property({ attribute: false }) bar?: EditorTitle;

  @property() fallback = "";

  @property({ type: Boolean, reflect: true }) narrow = false;

  protected render() {
    const bar = this.bar;
    return html`<span class="title">${bar?.title || this.fallback}</span>
      ${bar?.badge && !this.narrow ? html`<span class="badge">${bar.badge}</span>` : nothing}`;
  }

  static styles = css`
    :host {
      display: flex;
      align-items: center;
      gap: var(--ha-space-3);
      min-width: 0;
    }
    .title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .badge {
      flex: none;
      display: inline-flex;
      align-items: center;
      gap: var(--ha-space-1);
      padding: 2px var(--ha-space-2);
      border-radius: var(--ha-border-radius-pill);
      background: rgba(var(--rgb-primary-color), 0.15);
      font-size: var(--ha-font-size-s);
      line-height: 20px;
    }
  `;
}

class MlEditorActions extends LitElement {
  @property({ attribute: false }) bar!: EditorTitle;

  @property({ type: Boolean, reflect: true }) narrow = false;

  protected render() {
    const bar = this.bar;
    const busy = bar.busy !== undefined;
    const tryLabel = bar.trying ? "Stop trying" : `Try in ${bar.room}`;
    const tryAction = () => (bar.trying ? bar.onStopTry() : bar.onTry());
    return html`${
      this.narrow
        ? html`<ha-icon-button .label=${tryLabel} .disabled=${busy || (!bar.trying && !bar.canTry)} @click=${tryAction}>
            <ha-icon .icon=${bar.trying ? "mdi:stop" : "mdi:play"}></ha-icon>
          </ha-icon-button>`
        : html`<ha-button
            appearance="plain"
            .disabled=${busy || (!bar.trying && !bar.canTry)}
            .loading=${bar.busy === "try"}
            @click=${tryAction}
          >
            <ha-icon slot="start" .icon=${bar.trying ? "mdi:stop" : "mdi:play"}></ha-icon>
            ${tryLabel}
          </ha-button>`
    }
      <ha-button .disabled=${busy || !bar.canSave} .loading=${bar.busy === "save"} @click=${() => bar.onSave()}>
        Save
      </ha-button>`;
  }

  static styles = css`
    :host {
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      padding-inline-end: var(--ha-space-2);
    }
  `;
}

defineElement("ml-editor-title", MlEditorTitle);
defineElement("ml-editor-actions", MlEditorActions);

/** The title and actions of the panel's top bar while the editor is open. */
export const renderEditorBar = (bar: EditorTitle | undefined, fallback: string, narrow: boolean) =>
  html`<ml-editor-title slot="title" .bar=${bar} .fallback=${fallback} .narrow=${narrow}></ml-editor-title>
    ${bar ? html`<ml-editor-actions slot="actionItems" .bar=${bar} .narrow=${narrow}></ml-editor-actions>` : nothing}`;
