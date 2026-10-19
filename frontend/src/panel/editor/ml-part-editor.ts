// Part editor at /part/<id>: one part, outside any scene. The look card edits it for every
// scene that uses it; room chips pick where it previews and where Try runs it on all
// lights; the used-in card opens the scenes that play it and deletes those of rooms that
// are gone. The footer saves, copies (a new part with this look, kept by its own Save),
// and deletes a part of yours (refused while a scene or the switch-on default uses it) or
// resets an edited built-in. The working copy lives in sessionStorage under the part id.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { swatchBackground } from "../../shared/color";
import type { HomeAssistant, Part, PreviewFrame, SceneInfo, StoredScene, TargetId, TargetInfo } from "../../shared/types";
import { confirmAction, debounce, defineElement, errorMessage, fire, plural, showToast, toastOnError } from "../../shared/util";
import "../components/ml-bulb";
import { editorPath, go, goBack, homePath, partPath, replaceWith } from "../routes";
import { partHolders } from "../scene-facts";
import { type Draft, type MlStore, toStoredPart } from "../store";
import { chipStyles, dotStyles, pageStyles } from "../styles";
import type { PartEdit, TimeSelect } from "./editor-events";
import { frameAt, partProblems, type Problem } from "./editor-model";
import { draftRunning, PLAY_STEP, previewSpan, reducedMotion, secondsToday, TryQueue } from "./live";
import type { EditorTitle } from "./ml-editor-bar";
import "./ml-look-card";
import {
  type DraftStale,
  dropPartCopy,
  isPartDirty,
  keepPartCopy,
  openPartCopy,
  startPartCopy,
} from "./working-copy";

const LIVE_DELAY = 400;
const STALE_NOTE: Record<DraftStale, string> = {
  saved: "The part was saved elsewhere since; saving these changes replaces that.",
  deleted: "The part was deleted since; saving these changes brings it back.",
};

type Busy = "save" | "try" | "delete" | "reset";

class MlPartEditor extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) partId = "";

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _draft?: Part;

  @state() private _missing = false;

  /** Where the part previews and tries. */
  @state() private _room?: string;

  @state() private _frames?: PreviewFrame[];

  /** The frames span a day from midnight (adaptive), else a minute of motion. */
  @state() private _day = false;

  /** Seconds into the frames the bulbs show. */
  @state() private _t = 0;

  @state() private _trying = false;

  @state() private _busy?: Busy;

  @state() private _error?: string;

  /** The working copy came back from an earlier visit with unsaved changes. */
  @state() private _restored = false;

  @state() private _stale?: DraftStale;

  /** The part the working copy belongs to. */
  private _open?: string;

  /** The part Copy made: its name is typed next. */
  private _nameNext?: string;

  private _previewSeq = 0;

  /** Saved, deleted or copied: leaving needs no guard. */
  private _leaving = false;

  private _tries = new TryQueue();

  /** A stop on leaving is on its way; the status may still show the draft. */
  private _stopping = false;

  private _player?: ReturnType<typeof setInterval>;

  private _barKey?: string;

  private _live = debounce(() => {
    this._refreshPreview();
    this._refreshTry();
  }, LIVE_DELAY);

  connectedCallback(): void {
    super.connectedCallback();
    window.addEventListener("click", this._guardBack, true);
    window.addEventListener("beforeunload", this._beforeUnload);
    if (this.hasUpdated) this.requestUpdate();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    window.removeEventListener("click", this._guardBack, true);
    window.removeEventListener("beforeunload", this._beforeUnload);
    this._close();
  }

  protected willUpdate(): void {
    if (!this.store || !this.partId) return;
    if (this._open !== this.partId) {
      this._close();
      this._openCopy();
    } else if (!this._room || !this.store.target(this._room)) {
      this._room = this._defaultRoom();
    }
  }

  /** The panel's top bar shows the name, Try and Save. */
  protected updated(): void {
    this._play();
    const bar = this._bar();
    const key = bar && JSON.stringify(bar);
    if (bar && key !== this._barKey) {
      this._barKey = key;
      fire<EditorTitle>(this, "ml-editor-title", bar);
    }
    if (this._nameNext && this._draft?.id === this._nameNext) {
      this._nameNext = undefined;
      this.renderRoot.querySelector<HTMLElement & { focusName(): Promise<void> }>("ml-look-card")?.focusName();
    }
  }

  // ---- working copy

  private _openCopy(): void {
    const copy = openPartCopy(this.store, this.partId);
    this._open = this.partId;
    this._frames = undefined;
    this._error = undefined;
    this._leaving = false;
    this._missing = !copy;
    this._draft = copy?.draft;
    if (!copy) return;
    // A part Copy just made is new, not left over from an earlier visit.
    this._restored = copy.restored && this._dirty && this._nameNext !== this.partId;
    this._stale = this._restored ? copy.stale : undefined;
    this._room = this._defaultRoom();
    // A draft running there is this editor's from before a reload.
    this._trying = !this._stopping && draftRunning(this.store, this.hass, this._room ?? "");
    this._refreshPreview();
  }

  private _close(): void {
    const partId = this._open;
    if (!partId) return;
    this._live.cancel();
    if (this._room) this._stopTrying(this._room);
    if (!this._dirty) dropPartCopy(this.store, partId);
    this._open = undefined;
  }

  private _stopTrying(room: string): void {
    if (!this._trying) return;
    this._trying = false;
    this._stopping = true;
    this._tries
      .run(() => this.store.stopTry(room))
      .catch(() => undefined)
      .finally(() => (this._stopping = false));
  }

  /** The draft's part as saved now. */
  private get _saved(): Part | null {
    const part = this.store.part(this._draft?.id ?? this.partId);
    return part ? toStoredPart(part) : null;
  }

  private get _dirty(): boolean {
    return !!this._draft && isPartDirty(this._draft, this._saved);
  }

  private get _problems(): Problem[] {
    return this._draft ? partProblems(this._draft, (key) => this.store.effect(key)) : [];
  }

  /** Targets with lights: the scenes that use the part first, then the rest. */
  private _rooms(): TargetInfo[] {
    const targets = this.store.targets.filter((t) => t.lights.length);
    const used = new Set(this.store.usesOf(this.partId).map((scene) => scene.target));
    return [...targets.filter((t) => used.has(t.id)), ...targets.filter((t) => !used.has(t.id))];
  }

  private _defaultRoom(): string | undefined {
    return this._rooms()[0]?.id;
  }

  private _onPartEdit(ev: CustomEvent<PartEdit>): void {
    ev.stopPropagation();
    this._update(ev.detail.part);
  }

  private _update(part: Part): void {
    this._draft = part;
    this._restored &&= this._dirty;
    if (!this._restored) this._stale = undefined;
    keepPartCopy(this.store, part);
    this._live();
  }

  private _discard(): void {
    const saved = this._saved;
    if (!saved) return;
    this._restored = false;
    this._stale = undefined;
    this._update(saved);
  }

  /** What preview, Try and Save send: the part, and for preview and Try its whole-room scene. */
  private _payload(draft: Part): Draft {
    const part = { ...toStoredPart(draft), name: draft.name.trim() };
    const scene: StoredScene = {
      id: part.id,
      name: part.name,
      icon: null,
      layers: [{ lights: "all", part: part.id }],
      others: "off",
      transition: null,
    };
    return { scene, parts: [part] };
  }

  // ---- live preview and try

  private async _refreshPreview(): Promise<void> {
    const draft = this._draft;
    const room = this._room;
    if (!draft || !room || this._problems.length) return;
    const seq = ++this._previewSeq;
    const day = this.store.effect(draft.effect)?.category === "adaptive";
    const { scene, parts } = this._payload(draft);
    try {
      const frames = await this.store.preview({ target_id: room as TargetId, scene, parts, ...previewSpan(day) });
      if (seq !== this._previewSeq) return;
      if (day !== this._day || !this._frames) this._t = day ? secondsToday() : 0;
      this._day = day;
      this._frames = frames;
    } catch (err) {
      if (seq === this._previewSeq) this._frames = undefined;
      console.warn("[magic-lights] preview failed:", errorMessage(err));
    }
  }

  private _play(): void {
    const moving = !this._day && (this._frames?.length ?? 0) > 1 && !reducedMotion();
    if (moving && !this._player) {
      this._player = setInterval(() => {
        const end = this._frames?.at(-1)?.t ?? 0;
        this._t = end > 0 ? (this._t + PLAY_STEP / 1000) % end : 0;
      }, PLAY_STEP);
    } else if (!moving && this._player) {
      clearInterval(this._player);
      this._player = undefined;
    }
  }

  private _refreshTry(): void {
    const room = this._room;
    if (!room || !this._trying) return;
    this._tries
      .run(async () => {
        if (!this._trying || !this._draft || this._problems.length) return;
        await this.store.tryScene(room, this._payload(this._draft));
      })
      .catch((err) => (this._error = `Could not update the lights: ${errorMessage(err)}`));
  }

  private _pickRoom(room: string): void {
    if (room === this._room) return;
    if (this._room) this._stopTrying(this._room);
    this._room = room;
    this._frames = undefined;
    this._refreshPreview();
  }

  // ---- actions

  private _try = async (): Promise<void> => {
    const draft = this._draft;
    const room = this._room;
    if (!draft || !room) return;
    this._busy = "try";
    const tried = await toastOnError(
      this,
      this._tries.run(() => this.store.tryScene(room, this._payload(draft))),
      "Could not try the part",
    );
    if (tried) this._trying = true;
    this._busy = undefined;
  };

  private _stopTry = async (): Promise<void> => {
    const room = this._room;
    if (!room) return;
    this._busy = "try";
    this._trying = false;
    await toastOnError(this, this._tries.run(() => this.store.stopTry(room)), "Could not stop trying");
    this._busy = undefined;
  };

  private _save = async (): Promise<void> => {
    const draft = this._draft;
    if (!draft) return;
    const { parts } = this._payload(draft);
    this._busy = "save";
    const saved = await toastOnError(this, this.store.saveLibrary({ parts }), `Could not save ${parts[0].name}`);
    if (!saved) {
      this._busy = undefined;
      return;
    }
    // Saving turns a running draft of this part into the part itself.
    this._trying = false;
    this._busy = undefined;
    showToast(this, `Saved ${parts[0].name}`);
    this._leave();
  };

  /** A new part with this look, pending edits included; this part stays as saved. */
  private _copy(): void {
    const draft = this._draft;
    if (!draft) return;
    const copy = this.store.copyPart(this._payload(draft).parts[0]);
    startPartCopy(this.store, copy);
    if (this._room) this._stopTrying(this._room);
    dropPartCopy(this.store, this.partId);
    this._leaving = true;
    this._nameNext = copy.id;
    replaceWith(partPath(copy.id));
  }

  /** Yours, once nothing plays it; a dialog says what still does, where Delete was clicked. */
  private async _delete(): Promise<void> {
    const draft = this._draft;
    if (!draft) return;
    const holders = partHolders(this.store, this.partId);
    if (holders.length) {
      await confirmAction(this, {
        title: `${draft.name} is in use`,
        text: `Used by ${holders.join(", ")}. It can be deleted once nothing plays it.`,
        confirmText: "OK",
        notice: true,
      });
      return;
    }
    if (!(await this._confirmDelete(draft))) return;
    this._busy = "delete";
    const deleted = await toastOnError(this, this.store.deletePart(this.partId), `Could not delete ${draft.name}`);
    this._busy = undefined;
    if (!deleted) return;
    showToast(this, `Deleted ${draft.name}`);
    this._leave();
  }

  private _confirmDelete(part: Part): Promise<boolean> {
    const dormant = this.store.dormantUsesOf(part.id).length;
    return confirmAction(this, {
      title: `Delete ${part.name}?`,
      text: dormant
        ? `The part is removed, with ${plural(dormant, "scene")} of rooms that are gone.`
        : "No scene uses the part; it is removed.",
      confirmText: "Delete",
      destructive: true,
    });
  }

  /** An edited built-in goes back to how it ships, for every scene that uses it. */
  private async _reset(): Promise<void> {
    const info = this.store.part(this.partId);
    const original = info?.original;
    if (!info || !original) return;
    const ok = await confirmAction(this, {
      title: `Reset ${info.name}?`,
      text: "Every scene that uses it plays it as it ships again.",
      confirmText: "Reset",
      destructive: true,
    });
    if (!ok) return;
    this._busy = "reset";
    const reset = await toastOnError(this, this.store.deletePart(this.partId), `Could not reset ${info.name}`);
    this._busy = undefined;
    if (!reset) return;
    const part = { id: info.id, name: info.name, effect: original.effect, params: structuredClone(original.params) };
    startPartCopy(this.store, part, part);
    this._draft = part;
    this._restored = false;
    this._stale = undefined;
    this._live();
    showToast(this, `Reset ${info.name}`);
  }

  private async _deleteDormant(scene: SceneInfo): Promise<void> {
    const ok = await confirmAction(this, {
      title: `Delete ${scene.name}?`,
      text: "Its room is gone; the scene only waits for the room to come back.",
      confirmText: "Delete",
      destructive: true,
    });
    if (ok) await toastOnError(this, this.store.deleteScene(scene.id), "Could not delete the scene");
  }

  private _leave(): void {
    this._leaving = true;
    dropPartCopy(this.store, this.partId);
    goBack(homePath("parts"));
  }

  /** The panel's back arrow asks before unsaved changes are dropped. */
  private _guardBack = async (ev: MouseEvent): Promise<void> => {
    const open = this._open;
    if (!open || this._leaving || !this._dirty) return;
    if (!ev.composedPath().some((el) => (el as Element).localName === "ha-icon-button-arrow-prev")) return;
    ev.stopPropagation();
    ev.preventDefault();
    const discard = await confirmAction(this, {
      title: "Discard changes?",
      text: this._saved ? "Your changes to this part have not been saved." : "This new part has not been saved.",
      confirmText: "Discard",
      destructive: true,
    });
    if (discard && this._open === open) this._leave();
  };

  private _beforeUnload = (ev: BeforeUnloadEvent): void => {
    if (this._dirty && !this._leaving) ev.preventDefault();
  };

  // ---- rendering

  private _bar(): EditorTitle | undefined {
    const draft = this._draft;
    if (!draft || !this.store) return undefined;
    const info = this.store.part(this.partId);
    const valid = this._problems.length === 0;
    return {
      id: this.partId,
      title: draft.name.trim(),
      badge: !info ? "New part" : info.builtin ? "Built-in" : "Part",
      room: this._room ? this.store.targetName(this._room) : "",
      trying: this._trying,
      canTry: valid && !!this._room,
      canSave: valid && this._dirty,
      busy: this._busy,
      onTry: this._try,
      onStopTry: this._stopTry,
      onSave: this._save,
    };
  }

  protected render() {
    const draft = this._draft;
    if (this._missing || !draft) {
      return html`<ha-alert alert-type="warning" title="Part not found">
        There is no such part (any more).
        <ha-button slot="action" appearance="plain" @click=${() => goBack(homePath("parts"))}>Parts</ha-button>
      </ha-alert>`;
    }
    const room = this._room ? this.store.target(this._room) : undefined;
    const frame = this._frames ? frameAt(this._frames, this._t) : undefined;
    return html`
      ${this._renderNotes(room)}
      <div class="body">
        <div class="columns">
          ${this._renderPreview(room, frame)}
          <ml-look-card
            class="part"
            standalone
            .hass=${this.hass}
            .store=${this.store}
            .draftPart=${draft}
            .problems=${this._problems}
            .day=${this._day && this._frames && room ? { frames: this._frames, t: this._t, lights: room.lights, room: room.name } : undefined}
            .narrow=${this.narrow}
            @ml-part-edit=${this._onPartEdit}
            @ml-time=${(ev: CustomEvent<TimeSelect>) => (this._t = ev.detail.t)}
          ></ml-look-card>
          ${this._renderUses()}
        </div>
      </div>
      ${this._renderFooter()}
    `;
  }

  private _renderNotes(room: TargetInfo | undefined) {
    return html`
      ${
        this._error
          ? html`<ha-alert alert-type="error" dismissable @alert-dismissed-clicked=${() => (this._error = undefined)}>
              ${this._error}
            </ha-alert>`
          : nothing
      }
      ${
        this._restored
          ? html`<ha-alert alert-type="warning" title="Unsaved changes">
              These changes are from an earlier visit.${this._stale ? html` ${STALE_NOTE[this._stale]}` : nothing}
              ${
                this._saved
                  ? html`<ha-button slot="action" appearance="plain" @click=${this._discard}>
                      ${this._stale ? "Load the saved part" : "Discard"}
                    </ha-button>`
                  : nothing
              }
            </ha-alert>`
          : nothing
      }
      ${
        this._trying && room
          ? html`<ha-alert alert-type="info" title=${`Trying in ${room.name}`}>
              All its lights play the part and follow your edits. Stop trying brings back what ran before.
            </ha-alert>`
          : nothing
      }
    `;
  }

  /** Room chips, and the chosen room's lights playing the part. */
  private _renderPreview(room: TargetInfo | undefined, frame: PreviewFrame | undefined) {
    const rooms = this._rooms();
    return html`<ha-card class="preview">
      <div class="card-content">
        <div class="title">
          <h2>Preview</h2>
          <span class="hint">on all lights of a room</span>
        </div>
        ${
          rooms.length
            ? html`<div class="chips" role="radiogroup" aria-label="Preview room">
                ${rooms.map(
                  (t) => html`<ha-assist-chip
                    filled
                    role="radio"
                    aria-checked=${t.id === room?.id ? "true" : "false"}
                    class=${t.id === room?.id ? "active" : ""}
                    .active=${t.id === room?.id}
                    .label=${t.name}
                    @click=${() => this._pickRoom(t.id)}
                  ></ha-assist-chip>`,
                )}
              </div>`
            : html`<p class="hint">No room has lights yet.</p>`
        }
        ${
          room
            ? html`<div class="bulbs">
                ${room.lights.map((entityId) => {
                  const light = frame?.lights[entityId];
                  const on = !!light?.on;
                  return html`<ml-bulb
                    .label=${this.store.lightName(entityId)}
                    .color=${on ? light?.rgb : undefined}
                    .gradient=${on ? light?.gradient : undefined}
                    .brightness=${on ? Math.round((light?.brightness ?? 1) * 100) : 100}
                    .unavailable=${this.hass.states[entityId]?.state === "unavailable"}
                    disabled
                  ></ml-bulb>`;
                })}
              </div>`
            : nothing
        }
      </div>
    </ha-card>`;
  }

  /** The scenes that play the part; those of rooms that are gone can go. */
  private _renderUses() {
    const uses = this.store.usesOf(this.partId);
    const dormant = this.store.dormantUsesOf(this.partId);
    const isDefault = this.store.config.settings.default_scene === this.partId;
    return html`<ha-card class="uses">
      <div class="card-content">
        <div class="title">
          <h2>Used in</h2>
          <span class="hint">${plural(uses.length, "scene")}</span>
        </div>
        ${
          uses.length || isDefault
            ? html`<div class="chips">
                ${isDefault ? html`<span class="tag">Switch-on default</span>` : nothing}
                ${uses.map(
                  (scene) => html`<ha-assist-chip
                    filled
                    .label=${this.store.useLabel(scene)}
                    @click=${() => go(editorPath(scene.target, scene.id))}
                  >
                    <span slot="icon" class="dot" style="background: ${swatchBackground(scene.swatches)}"></span>
                  </ha-assist-chip>`,
                )}
              </div>
              ${uses.length ? html`<p class="hint">Changes apply to all of them.</p>` : nothing}`
            : html`<p class="hint">No scene uses this part. Add it to a room with New scene there.</p>`
        }
        ${
          dormant.length
            ? html`<span class="subtitle">In rooms that are gone</span>
                <div class="chips">
                  ${dormant.map(
                    (scene) => html`<span class="dormant">
                      <span class="dormant-label">${this.store.sceneLabel(scene)}</span>
                      <ha-icon-button
                        .label=${`Delete ${this.store.sceneLabel(scene)}`}
                        @click=${() => this._deleteDormant(scene)}
                      >
                        <ha-icon icon="mdi:close"></ha-icon>
                      </ha-icon-button>
                    </span>`,
                  )}
                </div>`
            : nothing
        }
      </div>
    </ha-card>`;
  }

  /** Delete for yours once saved, Reset for an edited built-in. */
  private _renderRemove(busy: boolean) {
    const info = this.store.part(this.partId);
    if (info?.builtin && info.edited) {
      return html`<ha-button
        appearance="plain"
        variant="danger"
        .disabled=${busy}
        .loading=${this._busy === "reset"}
        @click=${() => this._reset()}
      >
        <ha-icon slot="start" icon="mdi:restore"></ha-icon>
        Reset to built-in
      </ha-button>`;
    }
    if (!info || info.builtin) return nothing;
    return html`<ha-button
      appearance="plain"
      variant="danger"
      .disabled=${busy}
      .loading=${this._busy === "delete"}
      @click=${() => this._delete()}
    >
      <ha-icon slot="start" icon="mdi:delete-outline"></ha-icon>
      Delete part
    </ha-button>`;
  }

  private _renderFooter() {
    const bar = this._bar();
    if (!bar) return nothing;
    const busy = this._busy !== undefined;
    // An unsaved part has nothing to copy from yet; copying it would drop it.
    const copy = html`<ha-button
      appearance="plain"
      .disabled=${busy || this._problems.length > 0 || !this.store.part(this.partId)}
      @click=${() => this._copy()}
    >
      <ha-icon slot="start" icon="mdi:content-copy"></ha-icon>
      Copy
    </ha-button>`;
    if (this.narrow) return html`<div class="end">${this._renderRemove(busy)}<span class="spacer"></span>${copy}</div>`;
    return html`<div class="footer">
      ${this._renderRemove(busy)} ${copy}
      <span class="spacer"></span>
      <ha-button
        appearance="plain"
        .disabled=${busy || (!bar.trying && !bar.canTry)}
        .loading=${this._busy === "try"}
        @click=${bar.trying ? this._stopTry : this._try}
      >
        <ha-icon slot="start" .icon=${bar.trying ? "mdi:stop" : "mdi:play"}></ha-icon>
        ${bar.trying ? "Stop trying" : "Try"}
      </ha-button>
      <ha-button .disabled=${busy || !bar.canSave} .loading=${this._busy === "save"} @click=${this._save}>
        Save
      </ha-button>
    </div>`;
  }

  static styles = [
    pageStyles,
    chipStyles,
    dotStyles,
    css`
      ha-alert {
        margin: 0 0 var(--ha-space-3);
      }
      .body {
        container-type: inline-size;
      }
      .columns {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        grid-template-areas: "preview" "part" "uses";
        gap: var(--ha-space-4);
        align-items: start;
      }
      .preview {
        grid-area: preview;
      }
      .part {
        grid-area: part;
      }
      .uses {
        grid-area: uses;
      }
      @container (min-width: 900px) {
        .columns {
          grid-template-columns: minmax(0, 11fr) minmax(0, 8fr);
          grid-template-rows: auto auto 1fr;
          grid-template-areas: "part preview" "part uses" "part .";
        }
      }
      .card-content {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-3);
      }
      .title {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: var(--ha-space-2);
      }
      h2 {
        margin: 0;
        font-size: var(--ha-font-size-l);
        font-weight: var(--ha-font-weight-medium);
      }
      .hint {
        margin: 0;
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
      }
      .subtitle {
        margin-top: var(--ha-space-2);
        font-size: var(--ha-font-size-s);
        font-weight: var(--ha-font-weight-medium);
        color: var(--secondary-text-color);
      }
      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: var(--ha-space-2);
      }
      .bulbs {
        display: flex;
        flex-wrap: wrap;
        gap: var(--ha-space-2);
      }
      .tag,
      .dormant {
        display: inline-flex;
        align-items: center;
        height: 32px;
        box-sizing: border-box;
        border: 1px solid var(--divider-color);
        border-radius: var(--ha-border-radius-pill);
        font-size: var(--ha-font-size-s);
      }
      .tag {
        padding: 0 var(--ha-space-3);
      }
      .dormant {
        max-width: 100%;
        padding-inline-start: var(--ha-space-3);
        border-style: dashed;
        color: var(--secondary-text-color);
        --ha-icon-button-size: 32px;
        --mdc-icon-size: 16px;
      }
      .dormant-label {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .footer {
        position: sticky;
        bottom: 0;
        z-index: 3;
        display: flex;
        align-items: center;
        gap: var(--ha-space-2);
        margin: var(--ha-space-6) calc(-1 * var(--ha-space-4)) 0;
        padding: var(--ha-space-3) var(--ha-space-4);
        background: var(--primary-background-color);
        border-top: 1px solid var(--divider-color);
      }
      .spacer {
        flex: 1;
      }
      .end {
        display: flex;
        align-items: center;
        gap: var(--ha-space-2);
        margin-top: var(--ha-space-4);
      }
    `,
  ];
}

defineElement("ml-part-editor", MlPartEditor);
