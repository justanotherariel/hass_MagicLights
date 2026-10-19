// The part card: the look the selected layer plays, edited in place for every scene that
// uses it. Its name (a built-in keeps its own), what its lights do (the effect) and the
// effect's form; for an effect of the adaptive category the form carries the day curve.
// Copy forks the part for this scene. Edits go up as ml-part-edit, Copy as ml-part-copy.
// The part editor shows it `standalone`: its own footer copies and resets a saved edit, and
// lists the scenes that use the part.

import { LitElement, css, html, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import type { EffectInfo, HomeAssistant, Look, Part, PartInfo, PreviewFrame } from "../../shared/types";
import { defineElement, fire, plural } from "../../shared/util";
import "../components/ml-swatch";
import type { MlStore } from "../store";
import { changedParams, effectOrder, layerSwatches, partUsers, type Problem } from "./editor-model";
import type { PartEdit, TimeSelect } from "./editor-events";
import "./ml-day-curve";
import "./ml-plugin-form";

/** More effects than this read better as a dropdown. */
const MAX_EFFECT_SEGMENTS = 8;

class MlLookCard extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  /** The part as the draft has it (`part` is HTMLElement's). */
  @property({ attribute: false }) draftPart!: Part;

  /** The scene being edited, which counts as a user whether saved or not. */
  @property({ attribute: false }) sceneId = "";

  @property({ attribute: false }) problems: Problem[] = [];

  /** A day of the preview room, for an adaptive part. */
  @property({ attribute: false }) day?: { frames: PreviewFrame[]; t: number; lights: string[]; room: string };

  @property({ type: Boolean, reflect: true }) narrow = false;

  /** In the part editor, outside any scene. */
  @property({ type: Boolean }) standalone = false;

  @state() private _naming = false;

  /** Params a part had under an effect it was switched away from, by "part:effect". */
  private _stash = new Map<string, Record<string, unknown>>();

  /** Opens the name for typing, its text selected: after Copy. */
  async focusName(): Promise<void> {
    // A pending switch to another part would close the field again.
    await this.updateComplete;
    this._naming = true;
    await this.updateComplete;
    const input = this.renderRoot.querySelector<HTMLElement & { select(): void; updateComplete: Promise<unknown> }>(
      ".name-input",
    );
    // Its inner field exists only once it has rendered.
    await input?.updateComplete;
    input?.focus();
    input?.select();
  }

  protected willUpdate(changed: PropertyValues<this>): void {
    // Another part selected: its name is not being typed.
    if (changed.has("draftPart") && changed.get("draftPart")?.id !== this.draftPart.id) this._naming = false;
  }

  private get _info(): PartInfo | undefined {
    return this.store.part(this.draftPart.id);
  }

  protected render() {
    const part = this.draftPart;
    const effect = this.store.effect(part.effect);
    const original = this._info?.original;
    const changed = changedParams(effect, part, original);
    return html`<ha-card>
      <div class="card-content">
        <div class="head">
          ${this._renderIdentity(effect)}
          ${
            this.standalone
              ? nothing
              : html`<ha-button appearance="outlined" size="s" @click=${() => fire(this, "ml-part-copy")}>
                  <ha-icon slot="start" icon="mdi:content-copy"></ha-icon>
                  Copy
                </ha-button>`
          }
        </div>
        ${this.problems.map((p) => html`<p class="problem">${p.message}</p>`)}
        ${this.standalone ? nothing : this._renderUsers()}
        <div class="field">
          <span class="label">
            What the lights do
            ${original && original.effect !== part.effect ? html`<span class="dot" title="Changed"></span>` : nothing}
          </span>
          ${this._renderEffects(part)}
          ${effect?.description ? html`<span class="hint">${effect.description}</span>` : nothing}
        </div>
        ${
          effect
            ? html`<ml-plugin-form
                .hass=${this.hass}
                .schema=${effect.schema}
                .data=${part.params}
                .defaults=${effect.defaults}
                .palettes=${this.store.config.palettes}
                .changed=${changed}
                @value-changed=${(ev: CustomEvent<{ value: Record<string, unknown> }>) => {
                  ev.stopPropagation();
                  this._edit({ ...part, params: ev.detail.value });
                }}
              >
                ${this._renderDay(effect)}
              </ml-plugin-form>`
            : nothing
        }
      </div>
    </ha-card>`;
  }

  private _renderIdentity(effect: EffectInfo | undefined) {
    const part = this.draftPart;
    const info = this._info;
    return html`<div class="identity">
      <ml-swatch size="48" .swatches=${layerSwatches(effect, part.params)} .icon=${info?.icon ?? undefined}></ml-swatch>
      <div class="naming">
        ${
          this._naming
            ? html`<ha-input
                class="name-input"
                label="Part name"
                .value=${part.name}
                @keydown=${(ev: KeyboardEvent) => {
                  if (ev.key === "Enter" || ev.key === "Escape") (ev.target as HTMLElement).blur();
                }}
                @input=${(ev: Event) => this._edit({ ...part, name: (ev.target as HTMLInputElement).value ?? "" })}
                @blur=${() => (this._naming = false)}
              ></ha-input>`
            : html`<div class="name-row">
                <h2 class="name">${part.name || "Unnamed part"}</h2>
                ${
                  info?.builtin
                    ? html`<ha-icon
                        class="lock"
                        icon="mdi:lock-outline"
                        title="A built-in keeps its name, so automations that call it keep working."
                      ></ha-icon>`
                    : html`<ha-icon-button .label=${"Rename part"} @click=${() => this.focusName()}>
                        <ha-icon icon="mdi:pencil"></ha-icon>
                      </ha-icon-button>`
                }
              </div>`
        }
        ${this._renderKind()}
      </div>
    </div>`;
  }

  /** "Part", "New part", or "Built-in" with "Edited · Reset"; the part editor's footer resets a saved edit. */
  private _renderKind() {
    const info = this._info;
    if (!info) {
      return html`<span class="hint">New part · saved with ${this.standalone ? "Save" : "the scene"}</span>`;
    }
    if (!info.builtin) return html`<span class="hint">Part</span>`;
    const original = info.original;
    const effect = this.store.effect(this.draftPart.effect);
    const edited = !!original && changedParams(effect, this.draftPart, original).size > 0;
    return html`<span class="kind">
      <span class="chip">Built-in</span>
      ${
        edited && original
          ? html`<span class="chip edited">Edited</span>
              ${this.standalone && info.edited ? nothing : this._renderReset(original)}`
          : nothing
      }
    </span>`;
  }

  private _renderReset(original: Look) {
    const reset = () => this._edit({ ...this.draftPart, effect: original.effect, params: structuredClone(original.params) });
    return html`<ha-button appearance="plain" size="s" @click=${reset}>
      <ha-icon slot="start" icon="mdi:restore"></ha-icon>
      Reset
    </ha-button>`;
  }

  /** "Used by 7 scenes — changes apply to all", the scenes folded under it. */
  private _renderUsers() {
    const users = partUsers(this.store.usesOf(this.draftPart.id), this.sceneId);
    if (users.length <= 1) {
      return html`<p class="usage hint">
        <ha-icon icon="mdi:link-variant-off"></ha-icon>
        Only this scene uses this part.
      </p>`;
    }
    return html`<ha-expansion-panel outlined class="usage-panel">
      <span slot="header" class="usage">
        <ha-icon icon="mdi:link-variant"></ha-icon>
        <span><b>Used by ${plural(users.length, "scene")}</b> — changes apply to all</span>
      </span>
      <ul class="users">
        ${users.map((scene) => html`<li>${scene && scene.id !== this.sceneId ? this.store.useLabel(scene) : "This scene"}</li>`)}
      </ul>
    </ha-expansion-panel>`;
  }

  private _renderEffects(part: Part) {
    const effects = effectOrder(this.store.config.effects);
    const options = effects.map((e) => ({
      value: e.key,
      label: e.name,
      ariaLabel: e.name,
      icon: html`<ha-icon .icon=${e.icon}></ha-icon>`,
    }));
    if (!this.store.effect(part.effect)) {
      options.push({
        value: part.effect,
        label: `${part.effect} (not installed)`,
        ariaLabel: `${part.effect} (not installed)`,
        icon: html`<ha-icon icon="mdi:help-circle-outline"></ha-icon>`,
      });
    }
    const changed = (ev: CustomEvent<{ value?: string }>) => {
      ev.stopPropagation();
      if (ev.detail.value) this._switchEffect(ev.detail.value);
    };
    if (options.length > MAX_EFFECT_SEGMENTS) {
      return html`<ha-select
        .label=${"Effect"}
        .options=${options.map(({ value, label }) => ({ value, label }))}
        .value=${part.effect}
        @selected=${changed}
      ></ha-select>`;
    }
    return html`<ha-control-select
      class="effects"
      .options=${options}
      .value=${part.effect}
      .label=${"What the lights do"}
      ?hide-option-label=${this.narrow}
      @value-changed=${changed}
    ></ha-control-select>`;
  }

  private _renderDay(effect: EffectInfo) {
    const day = this.day;
    if (effect.category !== "adaptive" || !day?.frames.length) return nothing;
    return html`<ml-day-curve
      slot="preview"
      .frames=${day.frames}
      .t=${day.t}
      .lights=${day.lights}
      .label=${this.narrow ? "Today" : `Today in ${day.room}`}
      .hint=${this.narrow ? "" : "drag to scrub, the bulbs follow"}
      @value-changed=${(ev: CustomEvent<{ value: number }>) => {
        ev.stopPropagation();
        fire<TimeSelect>(this, "ml-time", { t: ev.detail.value });
      }}
    ></ml-day-curve>`;
  }

  private _switchEffect(key: string): void {
    const part = this.draftPart;
    const effect = this.store.effect(key);
    if (!effect || key === part.effect) return;
    this._stash.set(`${part.id}:${part.effect}`, part.params);
    const params = this._stash.get(`${part.id}:${key}`) ?? structuredClone(effect.defaults);
    this._edit({ ...part, effect: key, params });
  }

  private _edit(part: Part): void {
    fire<PartEdit>(this, "ml-part-edit", { part });
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
      gap: var(--ha-space-5);
    }
    .head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--ha-space-3);
    }
    .head > ha-button {
      flex: none;
      margin-top: var(--ha-space-2);
    }
    .identity {
      display: flex;
      align-items: center;
      gap: var(--ha-space-3);
      min-width: 0;
      flex: 1;
    }
    .naming {
      display: flex;
      flex-direction: column;
      min-width: 0;
      flex: 1;
    }
    .name-row {
      display: flex;
      align-items: center;
      gap: var(--ha-space-1);
      min-height: 48px;
      min-width: 0;
    }
    .name {
      margin: 0;
      font-size: var(--ha-font-size-xl, 22px);
      font-weight: var(--ha-font-weight-normal);
      line-height: var(--ha-line-height-condensed);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .lock {
      flex: none;
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
    }
    ha-icon-button {
      flex: none;
      color: var(--secondary-text-color);
    }
    .name-input {
      --ha-input-padding-bottom: 0;
    }
    .kind {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--ha-space-2);
    }
    .chip {
      padding: 0 var(--ha-space-2);
      border-radius: var(--ha-border-radius-pill);
      background: rgba(var(--rgb-primary-color), 0.15);
      font-size: var(--ha-font-size-s);
      line-height: 22px;
    }
    .chip.edited {
      background: var(--ha-color-fill-warning-quiet-resting, rgba(255, 152, 0, 0.18));
    }
    .usage {
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      --mdc-icon-size: 18px;
    }
    p.usage {
      margin: calc(-1 * var(--ha-space-2)) 0 0;
    }
    .usage-panel {
      display: block;
      --expansion-panel-summary-padding: 0 var(--ha-space-3);
      --expansion-panel-content-padding: 0 var(--ha-space-3);
    }
    .users {
      margin: 0 0 var(--ha-space-3);
      padding-inline-start: var(--ha-space-6);
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    ha-control-select {
      --control-select-thickness: 52px;
      --control-select-border-radius: var(--ha-border-radius-lg, 12px);
    }
    /* Two label lines under the icon. */
    ha-control-select.effects {
      --control-select-thickness: 72px;
      --mdc-icon-size: 22px;
    }
    :host([narrow]) ha-control-select.effects {
      --control-select-thickness: 52px;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2);
    }
    .label {
      display: flex;
      align-items: center;
      gap: var(--ha-space-2);
      font-weight: var(--ha-font-weight-medium);
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--primary-color);
    }
    .hint {
      margin: 0;
      font-size: var(--ha-font-size-s);
      color: var(--secondary-text-color);
    }
    .problem {
      margin: calc(-1 * var(--ha-space-3)) 0 0;
      font-size: var(--ha-font-size-s);
      color: var(--error-color);
    }
    ha-select {
      display: block;
    }
  `;
}

defineElement("ml-look-card", MlLookCard);
