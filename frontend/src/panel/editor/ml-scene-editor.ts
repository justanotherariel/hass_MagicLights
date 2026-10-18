// Scene editor. A scene is one look shared by every room that shows it: the look card
// edits it, the room card sets which of the preview room's lights each part sets, the
// options card its fade, brightness and menus. Try runs the draft in the preview room;
// Save stores it for every room. The working copy lives in sessionStorage under the
// scene id until it is saved or discarded, so leaving the editor or switching the
// preview room loses nothing.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import type { HomeAssistant, PreviewFrame, StoredScene, TargetId, TargetInfo } from "../../shared/types";
import { confirmAction, confirmRemoveScene, debounce, defineElement, errorMessage, fire, plural, showToast } from "../../shared/util";
import { editorPath, goBack, homePath, replaceWith } from "../routes";
import { menuId, menuIds, toStored, type MlStore } from "../store";
import { lookLine } from "../scene-facts";
import { pageStyles } from "../styles";
import type { PartSelect, RoomSelect, SceneEdit, TimeSelect } from "./editor-events";
import {
  canSave,
  frameAt,
  isSharedLayer,
  layerOwners,
  sceneProblems,
  stableJson,
  variantsFollow,
  type Problem,
} from "./editor-model";
import type { EditorTitle } from "./ml-editor-bar";
import type { PresetNote } from "./ml-look-card";
import { partLabel, partsIn, switcherParts } from "./parts";
import { dropWorkingCopy, openWorkingCopy, type WorkingCopy } from "./working-copy";
import "./ml-look-card";
import "./ml-room-card";
import "./ml-scene-options";

const LIVE_DELAY = 400;
const STALE_NOTE: Record<NonNullable<WorkingCopy["stale"]>, string> = {
  saved: "The scene was saved elsewhere since; saving these changes replaces that.",
  deleted: "The scene was deleted since; saving these changes brings it back.",
  reset: "The preset was reset since; saving these changes edits it again.",
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

  /** The room the editor previews and tries in. */
  @property({ attribute: false }) targetId = "";

  @property({ attribute: false }) sceneId = "";

  /** Where Back, Save and Delete lead when there is no in-panel history. */
  @property({ attribute: false }) parent = homePath("scenes");

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _scene?: StoredScene;

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

  /** The restored changes are older than the scene's last save. */
  @state() private _stale: WorkingCopy["stale"];

  /** Parts that followed the last edit of the look. */
  @state() private _followed: string[] = [];

  /** The scene the working copy belongs to. */
  private _openScene?: string;

  /** The room the preview and Try are for. */
  private _room?: string;

  private _baseline = "";

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
    if (this._openScene !== this.sceneId) {
      this._close();
      this._open();
    } else if (this._room !== this.targetId) {
      this._roomChanged();
    }
  }

  /** The panel's top bar shows the name, what kind of scene it is, Try and Save. */
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

  private _open(): void {
    const copy = openWorkingCopy(this.store, this.sceneId);
    this._openScene = this.sceneId;
    this._room = this.targetId;
    this._frames = undefined;
    this._error = undefined;
    this._followed = [];
    this._selected = 0;
    this._leaving = false;
    this._missing = !copy;
    this._scene = copy?.scene;
    if (!copy) return;
    this._baseline = stableJson(copy.baseline);
    this._restored = copy.restored && this._dirty;
    this._stale = this._restored ? copy.stale : undefined;
    // A draft running here is this editor's from before a reload.
    this._trying = !this._stopping && this._draftRunning(this.targetId);
    this._refreshPreview();
  }

  private _close(): void {
    const sceneId = this._openScene;
    if (!sceneId) return;
    this._live.cancel();
    this._stopTrying();
    if (!this._dirty) dropWorkingCopy(this.store, sceneId);
    this._openScene = undefined;
    this._room = undefined;
  }

  /** Try belongs to the room it ran in; the preview follows the new one. */
  private _roomChanged(): void {
    this._stopTrying();
    this._room = this.targetId;
    const scene = this._scene;
    const target = this.store.target(this.targetId);
    if (scene && target && !switcherParts(scene.layers, target, this._roomLights).includes(this._selected)) {
      this._selected = 0;
    }
    this._refreshPreview();
  }

  private _stopTrying(): void {
    const room = this._room;
    if (!this._trying || !room) return;
    this._trying = false;
    this._stopping = true;
    this._queueTry(() => this.store.stopTry(room))
      .catch(() => undefined)
      .finally(() => (this._stopping = false));
  }

  /** The config's copy of the scene being edited (a user scene or an edited preset). */
  private _storedScene(scene: StoredScene) {
    const stored = this.store.scene(scene.id);
    return stored && !stored.builtin ? stored : undefined;
  }

  /** The scene as opened, or its stored copy. */
  private get _original(): StoredScene {
    return JSON.parse(this._baseline) as StoredScene;
  }

  private get _dirty(): boolean {
    return !!this._scene && stableJson(this._scene) !== this._baseline;
  }

  private get _problems(): Problem[] {
    return this._scene ? sceneProblems(this._scene, (key) => this.store.effect(key)) : [];
  }

  private _roomLights = (id: string) => this.store.target(id)?.lights;

  private _partName(scene: StoredScene, index: number): string {
    return partLabel(scene.layers[index], index, {
      light: (e) => this.store.lightName(e),
      room: (ref) => this.store.targetName(ref),
    });
  }

  private _update(scene: StoredScene): void {
    this._scene = scene;
    this._restored &&= this._dirty;
    if (!this._restored) this._stale = undefined;
    if (this._openScene) this.store.setDraft(this._openScene, scene);
    this._live();
  }

  private _onEdit(ev: CustomEvent<SceneEdit>): void {
    ev.stopPropagation();
    const before = this._scene;
    if (!before) return;
    let scene = ev.detail.scene;
    this._followed = [];
    if (ev.detail.follow) {
      const rooms = this.store.targets.filter((t) => t.kind === "room");
      const followed = variantsFollow(before, scene, (i) =>
        isSharedLayer(before.layers[i].lights, i, rooms, this._roomLights),
      );
      scene = followed.scene;
      this._followed = followed.layers.map((i) => this._partName(scene, i));
    }
    if (ev.detail.select !== undefined) this._selected = ev.detail.select;
    this._selected = Math.min(this._selected, scene.layers.length - 1);
    this._update(scene);
  }

  private _payload(scene: StoredScene): StoredScene {
    return { ...toStored(scene), name: scene.name.trim() };
  }

  private _discard(): void {
    this._restored = false;
    this._stale = undefined;
    this._update(this._original);
  }

  // ---- live preview and try

  private _isDay(scene: StoredScene): boolean {
    return scene.layers.some((layer) => this.store.effect(layer.effect)?.category === "adaptive");
  }

  private async _refreshPreview(): Promise<void> {
    const scene = this._scene;
    const room = this._room;
    if (!scene || !room || this._problems.length) return;
    const seq = ++this._previewSeq;
    const day = this._isDay(scene);
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    try {
      const frames = await this.store.preview({
        target_id: room as TargetId,
        scene: this._payload(scene),
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
    const room = this._room;
    if (!room || !this._trying) return;
    this._queueTry(async () => {
      if (!this._trying || !this._scene || this._problems.length) return;
      await this.store.tryScene(room, this._payload(this._scene));
    }).catch((err) => (this._error = `Could not update the lights: ${errorMessage(err)}`));
  }

  /** A draft of the editor's Try runs here: its status has no scene id (engine/status.py). */
  private _draftRunning(targetId: string): boolean {
    const attrs = this.store.status(this.hass, targetId)?.attrs;
    return attrs?.status === "scene" && attrs.scene_id === null && attrs.via === null;
  }

  // ---- actions

  private _try = async (): Promise<void> => {
    const scene = this._scene;
    const room = this._room;
    if (!scene || !room) return;
    this._busy = "try";
    this._error = undefined;
    try {
      await this._queueTry(() => this.store.tryScene(room, this._payload(scene)));
      this._trying = true;
    } catch (err) {
      this._error = `Could not try the scene: ${errorMessage(err)}`;
    } finally {
      this._busy = undefined;
    }
  };

  private _stopTry = async (): Promise<void> => {
    const room = this._room;
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
    const scene = this._scene;
    if (!scene) return;
    this._busy = "save";
    this._error = undefined;
    try {
      // An edit equal to its preset is not kept: the backend removes it.
      await this.store.saveScene(this._payload(scene));
    } catch (err) {
      this._error = errorMessage(err);
      this._busy = undefined;
      return;
    }
    // Saving turns a running draft of this scene into the scene itself.
    this._trying = false;
    this._busy = undefined;
    showToast(this, `Saved ${scene.name.trim()}`);
    this._leave();
  };

  private async _delete(reset: boolean): Promise<void> {
    const scene = this._scene;
    if (!scene) return;
    if (!(await confirmRemoveScene(this, scene, reset))) return;
    this._busy = "delete";
    this._error = undefined;
    try {
      const room = this._room;
      if (this._trying && room) {
        this._trying = false;
        await this._queueTry(() => this.store.stopTry(room));
      }
      await this.store.deleteScene(scene.id);
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
    if (this._openScene) dropWorkingCopy(this.store, this._openScene);
    goBack(this.parent);
  }

  /** The panel's back arrow asks before unsaved changes are dropped. */
  private _guardBack = async (ev: MouseEvent): Promise<void> => {
    const sceneId = this._openScene;
    if (!sceneId || this._leaving || !this._dirty) return;
    if (!ev.composedPath().some((el) => (el as Element).localName === "ha-icon-button-arrow-prev")) return;
    ev.stopPropagation();
    ev.preventDefault();
    const isNew = !this._storedScene(this._original);
    const discard = await confirmAction(this, {
      title: "Discard changes?",
      text: isNew ? "This new scene has not been saved." : "Your changes to this scene have not been saved.",
      confirmText: "Discard",
      destructive: true,
    });
    if (discard && this._openScene === sceneId) this._leave();
  };

  private _beforeUnload = (ev: BeforeUnloadEvent): void => {
    if (this._dirty && !this._leaving) ev.preventDefault();
  };

  // ---- what the scene is

  /** Rooms (and zones) whose menu shows the scene. */
  private _shownIn(scene: StoredScene): TargetInfo[] {
    const id = menuId(scene);
    return this.store.targets.filter((t) => menuIds(t).includes(id));
  }

  private _badge(scene: StoredScene): string {
    if (scene.preset) return this._storedScene(scene) ? "Preset · Edited" : "Preset";
    if (!this._storedScene(scene)) return "New scene";
    const rooms = this._shownIn(scene).filter((t) => t.kind === "room").length;
    return `Your scene · ${plural(rooms, "room")}`;
  }

  private _setUpIn(scene: StoredScene, target: TargetInfo): boolean {
    return partsIn(scene.layers, target, this._roomLights).length > 0;
  }

  private _bar(): EditorTitle | undefined {
    const scene = this._scene;
    const target = this.store?.target(this.targetId);
    if (!scene || !target) return undefined;
    const stored = !!this._storedScene(scene);
    const valid = this._problems.length === 0;
    return {
      sceneId: this.sceneId,
      title: scene.name.trim(),
      badge: this._badge(scene),
      edited: !!scene.preset && stored,
      room: target.name,
      trying: this._trying,
      canTry: valid && this._setUpIn(scene, target),
      canSave: valid && canSave(scene, this._original, stored),
      busy: this._busy,
      onTry: this._try,
      onStopTry: this._stopTry,
      onSave: this._save,
    };
  }

  private _presetNote(scene: StoredScene): PresetNote | undefined {
    const preset = scene.preset ? this.store.scene(scene.preset) : undefined;
    if (!preset) return undefined;
    const first = preset.layers[0];
    return {
      edited: !!this._storedScene(scene),
      rooms: this._shownIn(scene).map((t) => t.name),
      original: first ? lookLine(this.store.effect(first.effect), first) : "",
    };
  }

  // ---- rendering

  protected render() {
    const target = this.store?.target(this.targetId);
    if (!target) return nothing;
    const scene = this._scene;
    if (this._missing || !scene) {
      return html`<ha-alert alert-type="warning" title="Scene not found">
        This scene does not exist (any more).
        <ha-button slot="action" appearance="plain" @click=${() => goBack(this.parent)}>Back</ha-button>
      </ha-alert>`;
    }
    const selected = Math.min(this._selected, scene.layers.length - 1);
    const parts = switcherParts(scene.layers, target, this._roomLights);
    if (!parts.includes(selected)) parts.push(selected);
    const preset = scene.preset ? this.store.scene(scene.preset) : undefined;
    const original = preset ? toStored(preset) : undefined;
    const frame = this._frames ? frameAt(this._frames, this._t) : undefined;
    const owners = layerOwners(scene.layers, target, this._roomLights);
    const partLights = target.lights.filter((e) => owners.get(e) === selected);
    return html`
      ${this._renderNotes(scene, target)}
      <div class="body">
        <div class="columns">
          <ml-look-card
            .hass=${this.hass}
            .store=${this.store}
            .scene=${scene}
            .selected=${selected}
            .parts=${parts.map((index) => ({ index, label: this._partName(scene, index) }))}
            .original=${original}
            .preset=${this._presetNote(scene)}
            .caption=${this.narrow ? this._badge(scene) : "Tap the name or the swatch to change them"}
            .problems=${this._problems}
            .followed=${this._followed}
            .day=${this._day && this._frames ? { frames: this._frames, t: this._t, lights: partLights, room: target.name } : undefined}
            .narrow=${this.narrow}
            @ml-scene-edit=${this._onEdit}
            @ml-part-select=${this._onPart}
            @ml-time=${(ev: CustomEvent<TimeSelect>) => (this._t = ev.detail.t)}
            @ml-reset=${() => this._delete(true)}
          ></ml-look-card>
          <div class="side">
            <ml-room-card
              .hass=${this.hass}
              .store=${this.store}
              .scene=${scene}
              .target=${target}
              .selected=${selected}
              .frame=${frame}
              .narrow=${this.narrow}
              @ml-scene-edit=${this._onEdit}
              @ml-part-select=${this._onPart}
              @ml-room-select=${(ev: CustomEvent<RoomSelect>) =>
                replaceWith(editorPath(this.sceneId, ev.detail.targetId))}
            ></ml-room-card>
            <ml-scene-options
              .store=${this.store}
              .scene=${scene}
              .stored=${!!this._storedScene(scene)}
              .original=${original}
              .narrow=${this.narrow}
              @ml-scene-edit=${this._onEdit}
            ></ml-scene-options>
          </div>
        </div>
      </div>
      ${this._renderFooter(scene)}
    `;
  }

  private _onPart(ev: CustomEvent<PartSelect>): void {
    ev.stopPropagation();
    this._selected = ev.detail.index;
    this._followed = [];
  }

  private _renderNotes(scene: StoredScene, target: TargetInfo) {
    const rooms = this._shownIn(scene).filter((t) => t.kind === "room").length;
    const name = scene.name.trim() || "this scene";
    return html`
      ${
        scene.preset
          ? nothing
          : html`<ha-alert alert-type="info">
              ${
                rooms > 1
                  ? html`The look is shared: what you change here changes <b>all ${rooms} rooms that show ${name}</b>.`
                  : html`The look is the same in every room that shows ${name}.`
              }
              Which lights do what is set per room.
            </ha-alert>`
      }
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

  private _renderFooter(scene: StoredScene) {
    const stored = !!this._storedScene(scene);
    const busy = this._busy !== undefined;
    const remove = stored
      ? html`<ha-button
          appearance="plain"
          variant="danger"
          .disabled=${busy}
          .loading=${this._busy === "delete"}
          @click=${() => this._delete(!!scene.preset)}
        >
          <ha-icon slot="start" .icon=${scene.preset ? "mdi:restore" : "mdi:delete-outline"}></ha-icon>
          ${scene.preset ? "Reset to original" : "Delete scene"}
        </ha-button>`
      : nothing;
    if (this.narrow) return stored ? html`<div class="end">${remove}</div>` : nothing;
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
        gap: var(--ha-space-4);
        align-items: start;
      }
      .side {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-4);
      }
      @container (min-width: 900px) {
        .columns {
          grid-template-columns: minmax(0, 11fr) minmax(0, 8fr);
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
