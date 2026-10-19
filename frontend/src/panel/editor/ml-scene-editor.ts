// Scene editor of one room's scene. The scene card names it; the lights card sets which
// of the room's lights play which part; the part card edits the selected part in place,
// for every scene that uses it, or Copy forks it for this scene. Try runs the draft in
// the room; Save stores the scene and its changed parts at once. The working copy lives
// in sessionStorage under the scene id until it is saved or discarded, so leaving the
// editor loses nothing.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import type { HomeAssistant, Part, PreviewFrame, TargetId, TargetInfo } from "../../shared/types";
import {
  confirmAction,
  confirmRemoveScene,
  debounce,
  defineElement,
  errorMessage,
  fire,
  showToast,
} from "../../shared/util";
import { goBack, targetPath } from "../routes";
import { type Draft, type MlStore, toStoredPart, toStoredScene } from "../store";
import { pageStyles } from "../styles";
import type { PartEdit, PartSelect, SceneEdit, TimeSelect } from "./editor-events";
import {
  canSave,
  changedParts,
  forkPart,
  frameAt,
  isDirty,
  layerOwners,
  layerSwatches,
  sceneProblems,
  withPart,
  withoutUnusedParts,
  type Problem,
} from "./editor-model";
import type { EditorTitle } from "./ml-editor-bar";
import { partsIn } from "./parts";
import { dropWorkingCopy, keepWorkingCopy, openWorkingCopy, type WorkingCopy } from "./working-copy";
import "./ml-look-card";
import "./ml-room-card";
import "./ml-scene-options";

const LIVE_DELAY = 400;
const STALE_NOTE: Record<NonNullable<WorkingCopy["stale"]>, string> = {
  saved: "The scene or its parts were saved elsewhere since; saving these changes replaces that.",
  deleted: "The scene was deleted since; saving these changes brings it back.",
};
const MOTION_PREVIEW = { duration: 60, samples: 120 };
const DAY_PREVIEW = { duration: 86400, samples: 96 };
/** How often a moving preview advances, in ms. */
const PLAY_STEP = 500;

type Busy = "save" | "try" | "delete";

const reducedMotion = (): boolean => matchMedia("(prefers-reduced-motion: reduce)").matches;

const secondsToday = (): number => {
  const now = new Date();
  return now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
};

class MlSceneEditor extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  /** The scene's room or zone, where it previews and tries. */
  @property({ attribute: false }) targetId = "";

  @property({ attribute: false }) sceneId = "";

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _draft?: Draft;

  @state() private _missing = false;

  @state() private _selected = 0;

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

  /** The restored changes are older than what was saved since. */
  @state() private _stale: WorkingCopy["stale"];

  /** The scene and target the working copy belongs to. */
  private _open?: { sceneId: string; targetId: string };

  private _baseline?: Draft;

  /** The scene was saved when the editor opened it. */
  private _stored = false;

  private _previewSeq = 0;

  /** Saved, deleted or discarded: leaving needs no guard. */
  private _leaving = false;

  /** try / try/stop calls in order, so a late Try never outlives Stop. */
  private _tryQueue: Promise<unknown> = Promise.resolve();

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
    // Reattached: reopen from the stored draft.
    if (this.hasUpdated) this.requestUpdate();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    window.removeEventListener("click", this._guardBack, true);
    window.removeEventListener("beforeunload", this._beforeUnload);
    this._close();
  }

  protected willUpdate(): void {
    if (!this.store || !this.sceneId || !this.targetId) return;
    if (this._open?.sceneId !== this.sceneId || this._open.targetId !== this.targetId) {
      this._close();
      this._openCopy();
    }
  }

  /** The panel's top bar shows the name, the room, Try and Save. */
  protected updated(): void {
    this._play();
    const bar = this._bar();
    if (!bar) return;
    const key = JSON.stringify(bar);
    if (key === this._barKey) return;
    this._barKey = key;
    fire<EditorTitle>(this, "ml-editor-title", bar);
  }

  // ---- working copy

  private _openCopy(): void {
    const copy = openWorkingCopy(this.store, this.targetId, this.sceneId);
    this._open = { sceneId: this.sceneId, targetId: this.targetId };
    this._frames = undefined;
    this._error = undefined;
    this._selected = 0;
    this._leaving = false;
    this._missing = !copy;
    this._draft = copy?.draft;
    if (!copy) return;
    this._baseline = copy.baseline;
    this._stored = !!this.store.scene(this.sceneId);
    this._restored = copy.restored && this._dirty;
    this._stale = this._restored ? copy.stale : undefined;
    // A draft running here is this editor's from before a reload.
    this._trying = !this._stopping && this._draftRunning(this.targetId);
    this._refreshPreview();
  }

  private _close(): void {
    const open = this._open;
    if (!open) return;
    this._live.cancel();
    this._stopTrying(open.targetId);
    if (!this._dirty) dropWorkingCopy(this.store, open.sceneId);
    this._open = undefined;
  }

  private _stopTrying(room: string): void {
    if (!this._trying) return;
    this._trying = false;
    this._stopping = true;
    this._queueTry(() => this.store.stopTry(room))
      .catch(() => undefined)
      .finally(() => (this._stopping = false));
  }

  private _saved = (id: string): Part | undefined => {
    const part = this.store.part(id);
    return part && toStoredPart(part);
  };

  /** A part as the draft has it, else as saved. */
  private _partOf = (id: string): Part | undefined => this._draft?.parts.find((p) => p.id === id) ?? this._saved(id);

  private get _dirty(): boolean {
    return !!this._draft && !!this._baseline && isDirty(this._draft, this._baseline, this._saved);
  }

  private get _problems(): Problem[] {
    return this._draft ? sceneProblems(this._draft, this._saved, (key) => this.store.effect(key)) : [];
  }

  private _roomLights = (id: string) => this.store.target(id)?.lights;

  private _update(draft: Draft, select?: number): void {
    const next = withoutUnusedParts(draft);
    this._draft = next;
    if (select !== undefined) this._selected = select;
    this._selected = Math.max(0, Math.min(this._selected, next.scene.layers.length - 1));
    this._restored &&= this._dirty;
    if (!this._restored) this._stale = undefined;
    keepWorkingCopy(this.store, next);
    this._live();
  }

  private _onSceneEdit(ev: CustomEvent<SceneEdit>): void {
    ev.stopPropagation();
    const draft = this._draft;
    if (!draft) return;
    const { scene, select, added } = ev.detail;
    this._update({ scene, parts: added ? [...draft.parts, added] : draft.parts }, select);
  }

  /** The part changes for every scene that uses it. */
  private _onPartEdit(ev: CustomEvent<PartEdit>): void {
    ev.stopPropagation();
    const draft = this._draft;
    if (!draft) return;
    this._update(withPart(draft, ev.detail.part));
  }

  /**
   * The selected part, with its pending changes, becomes "<name> copy" for this scene
   * only; the shared part goes back to how it is saved.
   */
  private async _onCopy(ev: Event): Promise<void> {
    ev.stopPropagation();
    const draft = this._draft;
    const sourceId = draft?.scene.layers[this._selected]?.part;
    const source = sourceId ? this._partOf(sourceId) : undefined;
    if (!draft || !source) return;
    const copy = this.store.copyPart(
      source,
      draft.parts.map((p) => p.name),
    );
    this._update(forkPart(draft, source.id, copy));
    await this.updateComplete;
    await this.renderRoot.querySelector<HTMLElement & { focusName(): Promise<void> }>("ml-look-card")?.focusName();
  }

  /** What preview, Try and Save send: the scene and the parts that differ from the saved ones. */
  private _payload(draft: Draft): Draft {
    return {
      scene: { ...toStoredScene(draft.scene), name: draft.scene.name.trim() },
      parts: changedParts(draft.parts, this._saved).map((p) => ({ ...p, name: p.name.trim() })),
    };
  }

  private _discard(): void {
    const baseline = this._baseline;
    if (!baseline) return;
    this._restored = false;
    this._stale = undefined;
    this._update(structuredClone(baseline));
  }

  // ---- live preview and try

  private _isDay(draft: Draft): boolean {
    return draft.scene.layers.some((layer) => {
      const part = this._partOf(layer.part);
      return !!part && this.store.effect(part.effect)?.category === "adaptive";
    });
  }

  private async _refreshPreview(): Promise<void> {
    const draft = this._draft;
    const room = this._open?.targetId;
    if (!draft || !room || this._problems.length) return;
    const seq = ++this._previewSeq;
    const day = this._isDay(draft);
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const { scene, parts } = this._payload(draft);
    try {
      const frames = await this.store.preview({
        target_id: room as TargetId,
        scene,
        parts,
        ...(day ? { ...DAY_PREVIEW, start: start.toISOString() } : MOTION_PREVIEW),
      });
      if (seq !== this._previewSeq) return;
      // A day opens at the time of day, motion at its start.
      if (day !== this._day || !this._frames) this._t = day ? secondsToday() : 0;
      this._day = day;
      this._frames = frames;
    } catch (err) {
      if (seq === this._previewSeq) this._frames = undefined;
      console.warn("[magic-lights] preview failed:", errorMessage(err));
    }
  }

  /** Motion previews play unless the user prefers less motion; a day is scrubbed by hand. */
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

  private _queueTry(action: () => Promise<void>): Promise<void> {
    const run = this._tryQueue.then(action);
    this._tryQueue = run.catch(() => undefined);
    return run;
  }

  /** While trying, the room follows the edits. */
  private _refreshTry(): void {
    const room = this._open?.targetId;
    if (!room || !this._trying) return;
    this._queueTry(async () => {
      if (!this._trying || !this._draft || this._problems.length) return;
      await this.store.tryScene(room, this._payload(this._draft));
    }).catch((err) => (this._error = `Could not update the lights: ${errorMessage(err)}`));
  }

  /** A draft of the editor's Try runs here: its status has no scene id (engine/status.py). */
  private _draftRunning(targetId: string): boolean {
    const attrs = this.store.status(this.hass, targetId)?.attrs;
    return attrs?.status === "scene" && attrs.scene_id === null && attrs.via === null;
  }

  // ---- actions

  private _try = async (): Promise<void> => {
    const draft = this._draft;
    const room = this._open?.targetId;
    if (!draft || !room) return;
    this._busy = "try";
    this._error = undefined;
    try {
      await this._queueTry(() => this.store.tryScene(room, this._payload(draft)));
      this._trying = true;
    } catch (err) {
      this._error = `Could not try the scene: ${errorMessage(err)}`;
    } finally {
      this._busy = undefined;
    }
  };

  private _stopTry = async (): Promise<void> => {
    const room = this._open?.targetId;
    if (!room) return;
    this._busy = "try";
    this._trying = false;
    try {
      await this._queueTry(() => this.store.stopTry(room));
    } catch (err) {
      this._error = `Could not stop trying: ${errorMessage(err)}`;
    } finally {
      this._busy = undefined;
    }
  };

  private _save = async (): Promise<void> => {
    const draft = this._draft;
    if (!draft) return;
    const { scene, parts } = this._payload(draft);
    this._busy = "save";
    this._error = undefined;
    try {
      await this.store.saveLibrary({ parts, scenes: [{ target: this.targetId as TargetId, scene }] });
    } catch (err) {
      this._error = errorMessage(err);
      this._busy = undefined;
      return;
    }
    // Saving turns a running draft of this scene into the scene itself.
    this._trying = false;
    this._busy = undefined;
    showToast(this, `Saved ${scene.name}`);
    this._leave();
  };

  private async _delete(): Promise<void> {
    const draft = this._draft;
    if (!draft) return;
    if (!(await confirmRemoveScene(this, draft.scene, this.store.targetName(this.targetId)))) return;
    this._busy = "delete";
    this._error = undefined;
    try {
      if (this._trying) {
        this._trying = false;
        await this._queueTry(() => this.store.stopTry(this.targetId));
      }
      await this.store.deleteScene(draft.scene.id);
    } catch (err) {
      this._error = errorMessage(err);
      this._busy = undefined;
      return;
    }
    this._busy = undefined;
    this._leave();
  }

  private _leave(): void {
    this._leaving = true;
    if (this._open) dropWorkingCopy(this.store, this._open.sceneId);
    goBack(targetPath(this.targetId));
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
      text: this._stored ? "Your changes to this scene have not been saved." : "This new scene has not been saved.",
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
    const target = this.store?.target(this.targetId);
    if (!draft || !target || !this._baseline) return undefined;
    const valid = this._problems.length === 0;
    return {
      sceneId: this.sceneId,
      title: draft.scene.name.trim(),
      badge: this._stored ? target.name : `New scene · ${target.name}`,
      room: target.name,
      trying: this._trying,
      canTry: valid && partsIn(draft.scene.layers, target, this._roomLights).length > 0,
      canSave: valid && canSave(draft, this._baseline, this._stored, this._saved),
      busy: this._busy,
      onTry: this._try,
      onStopTry: this._stopTry,
      onSave: this._save,
    };
  }

  protected render() {
    const target = this.store?.target(this.targetId);
    if (!target) return nothing;
    const draft = this._draft;
    if (this._missing || !draft) {
      return html`<ha-alert alert-type="warning" title="Scene not found">
        ${target.name} has no such scene (any more).
        <ha-button slot="action" appearance="plain" @click=${() => goBack(targetPath(this.targetId))}>Back</ha-button>
      </ha-alert>`;
    }
    const scene = draft.scene;
    const selected = Math.min(this._selected, scene.layers.length - 1);
    const part = this._partOf(scene.layers[selected]?.part ?? "");
    const frame = this._frames ? frameAt(this._frames, this._t) : undefined;
    const owners = layerOwners(scene.layers, target, this._roomLights);
    const partLights = target.lights.filter((e) => owners.get(e) === selected);
    const problems = this._problems;
    return html`
      ${this._renderNotes(target)}
      <div class="body">
        <div class="columns">
          <ml-scene-options
            class="scene"
            .hass=${this.hass}
            .store=${this.store}
            .scene=${scene}
            .swatches=${this._swatches(draft)}
            .caption=${this._caption(target)}
            .problems=${problems.filter((p) => p.layer === undefined && p.part === undefined)}
            .narrow=${this.narrow}
            @ml-scene-edit=${this._onSceneEdit}
          ></ml-scene-options>
          <ml-room-card
            class="lights"
            .hass=${this.hass}
            .store=${this.store}
            .scene=${scene}
            .partOf=${this._partOf}
            .draftNames=${draft.parts.map((p) => p.name)}
            .target=${target}
            .selected=${selected}
            .frame=${frame}
            .narrow=${this.narrow}
            @ml-scene-edit=${this._onSceneEdit}
            @ml-part-select=${this._onPart}
          ></ml-room-card>
          ${
            part
              ? html`<ml-look-card
                  class="part"
                  .hass=${this.hass}
                  .store=${this.store}
                  .draftPart=${part}
                  .sceneId=${scene.id}
                  .problems=${problems.filter((p) => p.part === part.id || p.layer === selected)}
                  .day=${this._day && this._frames ? { frames: this._frames, t: this._t, lights: partLights, room: target.name } : undefined}
                  .narrow=${this.narrow}
                  @ml-part-edit=${this._onPartEdit}
                  @ml-part-copy=${this._onCopy}
                  @ml-time=${(ev: CustomEvent<TimeSelect>) => (this._t = ev.detail.t)}
                ></ml-look-card>`
              : nothing
          }
        </div>
      </div>
      ${this._renderFooter()}
    `;
  }

  private _swatches(draft: Draft): string[] {
    const colours = draft.scene.layers.flatMap((layer) => {
      const part = this._partOf(layer.part);
      return part ? layerSwatches(this.store.effect(part.effect), part.params) : [];
    });
    return [...new Set(colours)].slice(0, 5);
  }

  private _caption(target: TargetInfo): string {
    return this._stored ? `Scene in ${target.name}` : `New scene in ${target.name} · saved with Save`;
  }

  private _onPart(ev: CustomEvent<PartSelect>): void {
    ev.stopPropagation();
    this._selected = ev.detail.index;
  }

  private _renderNotes(target: TargetInfo) {
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
              <ha-button slot="action" appearance="plain" @click=${this._discard}>
                ${this._stale ? "Load the saved scene" : "Discard"}
              </ha-button>
            </ha-alert>`
          : nothing
      }
      ${
        this._trying
          ? html`<ha-alert alert-type="info" title=${`Trying in ${target.name}`}>
              The lights follow your edits. Stop trying brings back what ran before; Save keeps this scene.
            </ha-alert>`
          : nothing
      }
    `;
  }

  private _renderFooter() {
    const busy = this._busy !== undefined;
    const remove = this._stored
      ? html`<ha-button
          appearance="plain"
          variant="danger"
          .disabled=${busy}
          .loading=${this._busy === "delete"}
          @click=${() => this._delete()}
        >
          <ha-icon slot="start" icon="mdi:delete-outline"></ha-icon>
          Delete scene
        </ha-button>`
      : nothing;
    if (this.narrow) return this._stored ? html`<div class="end">${remove}</div>` : nothing;
    const bar = this._bar();
    if (!bar) return nothing;
    return html`<div class="footer">
      ${remove}
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
        grid-template-areas: "scene" "lights" "part";
        gap: var(--ha-space-4);
        align-items: start;
      }
      .scene {
        grid-area: scene;
      }
      .lights {
        grid-area: lights;
      }
      .part {
        grid-area: part;
      }
      @container (min-width: 900px) {
        .columns {
          grid-template-columns: minmax(0, 11fr) minmax(0, 8fr);
          grid-template-rows: auto 1fr;
          grid-template-areas: "scene lights" "part lights";
        }
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
        margin-top: var(--ha-space-4);
      }
    `,
  ];
}

defineElement("ml-scene-editor", MlSceneEditor);
