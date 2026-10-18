// Scene editor: name, icon, brightness, transition and other lights; the layer list
// and the selected layer, edited for this room; a live preview; Try / Stop trying, Save,
// Reset to preset and Delete. A scene is the same in every room. The working copy lives
// in sessionStorage under the route until it is saved or discarded, so leaving the
// editor loses nothing.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { swatchBackground } from "../../shared/color";
import type { HomeAssistant, Layer, PreviewFrame, StoredScene, TargetId, TargetInfo } from "../../shared/types";
import {
  confirmAction,
  confirmRemoveScene,
  debounce,
  defineElement,
  errorMessage,
  fire,
  plural,
  showToast,
} from "../../shared/util";
import { goBack, targetPath } from "../routes";
import { toStored, type MlStore } from "../store";
import { pageStyles } from "../styles";
import {
  expandLights,
  isWhole,
  layerCaption,
  layerOwners,
  layerSummary,
  layerSwatches,
  roomSlice,
  sceneProblems,
  stableJson,
  type Problem,
} from "./editor-model";
import type { LayerChanged } from "./ml-layer-card";
import type { BulbClick } from "./ml-preview";
import { dropWorkingCopy, openWorkingCopy, type WorkingCopy } from "./working-copy";
import "./ml-layer-card";
import "./ml-preview";

const LIVE_DELAY = 400;
const STALE_NOTE: Record<NonNullable<WorkingCopy["stale"]>, string> = {
  saved: " The scene was saved since; saving these changes replaces that.",
  removed: " The scene was removed since; saving these changes brings it back.",
};
const MOTION_PREVIEW = { duration: 60, samples: 120 };
const DAY_PREVIEW = { duration: 86400, samples: 48 };

type Busy = "save" | "try" | "delete";

interface Route {
  targetId: string;
  sceneId: string;
}

export interface EditorTitle {
  sceneId: string;
  title: string;
}

class MlSceneEditor extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;

  @property({ attribute: false }) store!: MlStore;

  @property({ attribute: false }) targetId = "";

  @property({ attribute: false }) sceneId = "";

  @property({ type: Boolean, reflect: true }) narrow = false;

  @state() private _scene?: StoredScene;

  @state() private _missing = false;

  @state() private _selected = 0;

  @state() private _frames?: PreviewFrame[];

  @state() private _frameStart = 0;

  @state() private _previewLoading = false;

  @state() private _previewError?: string;

  @state() private _trying = false;

  @state() private _busy?: Busy;

  @state() private _error?: string;

  /** The working copy came back from an earlier visit with unsaved changes. */
  @state() private _restored = false;

  /** The restored changes are older than the scene's last save. */
  @state() private _stale: WorkingCopy["stale"];

  /** The route the working copy belongs to. */
  private _route?: Route;

  private _baseline = "";

  private _previewSeq = 0;

  /** Saved, deleted or discarded: leaving needs no guard. */
  private _leaving = false;

  /** try / try/stop calls in order, so a late Try never outlives Stop. */
  private _tryQueue: Promise<unknown> = Promise.resolve();

  /** A stop on leaving is on its way; the status may still show the draft. */
  private _stopping = false;

  private _title?: string;

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

  /** The panel's top bar shows the name being edited. */
  protected updated(): void {
    const title = this._scene?.name.trim() ?? "";
    if (title === this._title) return;
    this._title = title;
    fire<EditorTitle>(this, "ml-editor-title", { sceneId: this.sceneId, title });
  }

  protected willUpdate(): void {
    if (!this.store || !this.targetId || !this.sceneId) return;
    const route = this._route;
    if (route?.targetId === this.targetId && route.sceneId === this.sceneId) return;
    this._close();
    this._open({ targetId: this.targetId, sceneId: this.sceneId });
  }

  // ---- working copy

  private _open(route: Route): void {
    const copy = openWorkingCopy(this.store, route.sceneId);
    this._frames = undefined;
    this._previewError = undefined;
    this._error = undefined;
    this._selected = 0;
    this._leaving = false;
    this._missing = !copy;
    this._scene = copy?.scene;
    if (!copy) return;
    this._route = route;
    this._baseline = stableJson(copy.baseline);
    this._restored = copy.restored && this._dirty;
    this._stale = this._restored ? copy.stale : undefined;
    // A draft running here is this editor's from before a reload.
    this._trying = !this._stopping && this._draftRunning(route.targetId);
    this._refreshPreview();
  }

  private _close(): void {
    const route = this._route;
    if (!route) return;
    this._live.cancel();
    if (this._trying) {
      this._trying = false;
      this._stopping = true;
      this._queueTry(() => this.store.stopTry(route.targetId))
        .catch(() => undefined)
        .finally(() => (this._stopping = false));
    }
    if (!this._dirty) dropWorkingCopy(this.store, route.sceneId);
    this._route = undefined;
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

  private _update(scene: StoredScene): void {
    this._scene = scene;
    this._restored &&= this._dirty;
    if (!this._restored) this._stale = undefined;
    if (this._route) this.store.setDraft(this._route.sceneId, scene);
    this._live();
  }

  private _setField<K extends keyof StoredScene>(key: K, value: StoredScene[K]): void {
    if (this._scene && this._scene[key] !== value) this._update({ ...this._scene, [key]: value });
  }

  private _setLayers(layers: Layer[], selected = this._selected): void {
    if (!this._scene) return;
    this._selected = Math.max(0, Math.min(selected, layers.length - 1));
    this._update({ ...this._scene, layers });
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
    const route = this._route;
    if (!scene || !route || this._problems.length) return;
    const seq = ++this._previewSeq;
    const day = this._isDay(scene);
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    this._previewLoading = true;
    try {
      const frames = await this.store.preview({
        target_id: route.targetId as TargetId,
        scene: this._payload(scene),
        ...(day ? { ...DAY_PREVIEW, start: start.toISOString() } : MOTION_PREVIEW),
      });
      if (seq !== this._previewSeq) return;
      this._frames = frames;
      this._frameStart = start.getTime();
      this._previewError = undefined;
    } catch (err) {
      if (seq === this._previewSeq) this._previewError = errorMessage(err);
    } finally {
      if (seq === this._previewSeq) this._previewLoading = false;
    }
  }

  private _queueTry(action: () => Promise<void>): Promise<void> {
    const run = this._tryQueue.then(action);
    this._tryQueue = run.catch(() => undefined);
    return run;
  }

  /** While trying, the room follows the edits. */
  private _refreshTry(): void {
    const route = this._route;
    if (!route || !this._trying) return;
    this._queueTry(async () => {
      if (!this._trying || !this._scene || this._problems.length) return;
      await this.store.tryScene(route.targetId, this._payload(this._scene));
    }).catch((err) => (this._error = `Could not update the lights: ${errorMessage(err)}`));
  }

  /** A draft of the editor's Try runs here: its status has no scene id (engine/status.py). */
  private _draftRunning(targetId: string): boolean {
    const attrs = this.store.status(this.hass, targetId)?.attrs;
    return attrs?.status === "scene" && attrs.scene_id === null && attrs.via === null;
  }

  // ---- actions

  private async _try(): Promise<void> {
    const scene = this._scene;
    const route = this._route;
    if (!scene || !route) return;
    this._busy = "try";
    this._error = undefined;
    try {
      await this._queueTry(() => this.store.tryScene(route.targetId, this._payload(scene)));
      this._trying = true;
    } catch (err) {
      this._error = `Could not try the scene: ${errorMessage(err)}`;
    } finally {
      this._busy = undefined;
    }
  }

  private async _stopTry(): Promise<void> {
    const route = this._route;
    if (!route) return;
    this._busy = "try";
    this._trying = false;
    try {
      await this._queueTry(() => this.store.stopTry(route.targetId));
    } catch (err) {
      this._error = `Could not stop trying: ${errorMessage(err)}`;
    } finally {
      this._busy = undefined;
    }
  }

  private async _save(): Promise<void> {
    const scene = this._scene;
    const route = this._route;
    if (!scene || !route) return;
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
    this._leave(route);
  }

  private async _delete(reset: boolean): Promise<void> {
    const scene = this._scene;
    const route = this._route;
    if (!scene || !route) return;
    if (!(await confirmRemoveScene(this, scene, reset))) return;
    this._busy = "delete";
    this._error = undefined;
    try {
      if (this._trying) {
        this._trying = false;
        await this._queueTry(() => this.store.stopTry(route.targetId));
      }
      await this.store.deleteScene(scene.id);
    } catch (err) {
      this._error = errorMessage(err);
      this._busy = undefined;
      return;
    }
    this._busy = undefined;
    this._leave(route);
  }

  private _leave(route: Route): void {
    this._leaving = true;
    dropWorkingCopy(this.store, route.sceneId);
    goBack(targetPath(route.targetId));
  }

  /** The panel's back arrow asks before unsaved changes are dropped. */
  private _guardBack = async (ev: MouseEvent): Promise<void> => {
    const route = this._route;
    if (!route || this._leaving || !this._dirty) return;
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
    if (discard && this._route === route) this._leave(route);
  };

  private _beforeUnload = (ev: BeforeUnloadEvent): void => {
    if (this._dirty && !this._leaving) ev.preventDefault();
  };

  // ---- rendering

  protected render() {
    const target = this.store?.target(this.targetId);
    if (!target) return nothing;
    const scene = this._scene;
    if (this._missing || !scene) {
      return html`<ha-alert alert-type="warning" title="Scene not found">
        This scene does not exist (any more).
        <ha-button slot="action" appearance="plain" @click=${() => goBack(targetPath(this.targetId))}>
          Back
        </ha-button>
      </ha-alert>`;
    }
    const problems = this._problems;
    const roomLights = (id: string) => this.store.target(id)?.lights;
    const owners = layerOwners(scene.layers, target, roomLights);
    const selected = Math.min(this._selected, scene.layers.length - 1);
    // With one layer every light would be marked.
    const highlight =
      scene.layers.length > 1 ? [...owners].filter(([, i]) => i === selected).map(([e]) => e) : [];
    return html`
      <div class="preview-bar">
        <ml-preview
          .hass=${this.hass}
          .store=${this.store}
          .target=${target}
          .frames=${this._frames}
          .loading=${this._previewLoading}
          .error=${problems.length ? `Preview paused: ${problems[0].message}` : this._previewError}
          .highlight=${highlight}
          .day=${this._isDay(scene)}
          .start=${this._frameStart}
          .narrow=${this.narrow}
          @ml-bulb-click=${(ev: CustomEvent<BulbClick>) => {
            const owner = owners.get(ev.detail.entityId);
            if (owner !== undefined) this._selected = owner;
          }}
        ></ml-preview>
      </div>
      ${this._renderAlerts(target)}
      <div class="body">
        <div class="columns">
          <div>
            <h2 class="section-title">Scene</h2>
            ${this._renderScene(scene, problems)}
            <h2 class="section-title">Layers</h2>
            ${this._renderLayers(target, scene, owners, problems, selected)}
          </div>
          <div>
            <h2 class="section-title">Layer ${selected + 1}</h2>
            <ml-layer-card
              .hass=${this.hass}
              .store=${this.store}
              .target=${target}
              .layer=${scene.layers[selected]}
              .overridden=${expandLights(scene.layers[selected].lights, target, roomLights).filter(
                (e) => owners.get(e) !== selected,
              )}
              .problems=${problems.filter((p) => p.layer === selected).map((p) => p.message)}
              @ml-layer-changed=${(ev: CustomEvent<LayerChanged>) =>
                this._setLayers(scene.layers.map((l, i) => (i === selected ? ev.detail.layer : l)))}
            ></ml-layer-card>
          </div>
        </div>
      </div>
      ${this._renderFooter(scene, problems)}
    `;
  }

  private _renderAlerts(target: TargetInfo) {
    return html`
      ${
        this._error
          ? html`<ha-alert
              alert-type="error"
              dismissable
              @alert-dismissed-clicked=${() => (this._error = undefined)}
            >
              ${this._error}
            </ha-alert>`
          : nothing
      }
      ${
        this._restored
          ? html`<ha-alert alert-type="info" title="Unsaved changes">
              These changes are from an earlier visit and have not been saved yet.${
                this._stale ? STALE_NOTE[this._stale] : ""
              }
              <ha-button slot="action" appearance="plain" @click=${this._discard}>Discard</ha-button>
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

  private _renderScene(scene: StoredScene, problems: Problem[]) {
    const defaultTransition = this.store.config.settings.transition;
    const nameProblems = problems.filter((p) => p.layer === undefined);
    return html`<ha-card>
      <div class="card-content form">
        <div class="name-row">
          <ha-input
            label="Name"
            required
            .readonly=${!!scene.preset}
            .hint=${scene.preset ? "An edited preset keeps its name." : ""}
            .value=${scene.name}
            @input=${(ev: Event) => this._setField("name", (ev.target as HTMLInputElement).value ?? "")}
          ></ha-input>
          <ha-icon-picker
            .label=${"Icon"}
            .value=${scene.icon ?? undefined}
            @value-changed=${(ev: CustomEvent<{ value?: string }>) =>
              this._setField("icon", ev.detail.value || null)}
          ></ha-icon-picker>
        </div>
        ${nameProblems.map((p) => html`<p class="problem">${p.message}</p>`)}
        ${
          scene.preset
            ? html`<p class="hint">Changes to this preset apply in every room and zone.</p>`
            : nothing
        }
        <ha-selector
          .hass=${this.hass}
          .selector=${{ number: { min: 1, max: 100, step: 1, mode: "slider", unit_of_measurement: "%" } }}
          .value=${scene.brightness}
          .label=${"Brightness"}
          .helper=${"Scales the brightness of every layer."}
          .required=${false}
          @value-changed=${(ev: CustomEvent<{ value?: number }>) => {
            ev.stopPropagation();
            const value = ev.detail.value;
            if (typeof value === "number") this._setField("brightness", Math.round(value));
          }}
        ></ha-selector>
        <ha-selector
          .hass=${this.hass}
          .selector=${{ number: { min: 0, max: 300, step: 0.1, mode: "box", unit_of_measurement: "s" } }}
          .value=${scene.transition ?? undefined}
          .placeholder=${defaultTransition}
          .label=${"Fade"}
          .helper=${`How long switching to this scene takes. Empty: the default (${defaultTransition} s).`}
          .required=${false}
          @value-changed=${(ev: CustomEvent<{ value?: number }>) => {
            ev.stopPropagation();
            this._setField("transition", typeof ev.detail.value === "number" ? ev.detail.value : null);
          }}
        ></ha-selector>
        <div class="field">
          <span class="field-label">Lights without a layer</span>
          <ha-control-select
            .options=${[
              { value: "off", label: "Turn off" },
              { value: "keep", label: "Leave as they are" },
            ]}
            .value=${scene.others}
            .label=${"Lights without a layer"}
            @value-changed=${(ev: CustomEvent<{ value: StoredScene["others"] }>) => {
              ev.stopPropagation();
              this._setField("others", ev.detail.value);
            }}
          ></ha-control-select>
        </div>
      </div>
    </ha-card>`;
  }

  private _renderLayers(
    target: TargetInfo,
    scene: StoredScene,
    owners: Map<string, number>,
    problems: Problem[],
    selected: number,
  ) {
    const several = scene.layers.length > 1;
    return html`<ha-card>
      ${
        several
          ? html`<p class="hint">Later layers win where they share lights. Drag to reorder.</p>`
          : nothing
      }
      <ha-sortable handle-selector=".handle" .disabled=${!several} @item-moved=${this._layerMoved}>
        <div class="layer-list">
          ${scene.layers.map((layer, i) =>
            this._renderLayerRow(target, layer, i, i === selected, owners, problems, several),
          )}
        </div>
      </ha-sortable>
      <div class="card-actions">
        <ha-button appearance="plain" @click=${this._addLayer}>
          <ha-icon slot="start" icon="mdi:plus"></ha-icon>
          Add layer
        </ha-button>
      </div>
    </ha-card>`;
  }

  private _renderLayerRow(
    target: TargetInfo,
    layer: Layer,
    index: number,
    selected: boolean,
    owners: Map<string, number>,
    problems: Problem[],
    several: boolean,
  ) {
    const effect = this.store.effect(layer.effect);
    const summary = layerSummary(effect, layer.params);
    const swatches = layerSwatches(effect, layer.params);
    const effective = [...owners.values()].filter((i) => i === index).length;
    const broken = problems.some((p) => p.layer === index);
    return html`<div class="layer ${selected ? "selected" : ""}">
      ${several ? html`<ha-icon class="handle" icon="mdi:drag"></ha-icon>` : nothing}
      <button class="layer-main" aria-pressed=${selected} @click=${() => (this._selected = index)}>
        <span class="layer-art" style="background: ${swatchBackground(swatches)}">
          <ha-icon .icon=${effect?.icon ?? "mdi:help-circle-outline"}></ha-icon>
        </span>
        <span class="layer-text">
          <span class="layer-title">
            ${this._lightsLabel(target, layer)} · ${effect?.name ?? layer.effect}
          </span>
          <span class="layer-summary">
            ${summary}${
              layer.lights !== "all" || several
                ? html`${summary ? " · " : ""}sets ${plural(effective, "light")}`
                : nothing
            }
          </span>
        </span>
        ${broken ? html`<ha-icon class="warn" icon="mdi:alert-circle-outline"></ha-icon>` : nothing}
      </button>
      <ha-icon-button
        .label=${"Remove layer"}
        .disabled=${!several}
        @click=${() => this._removeLayer(index)}
      >
        <ha-icon icon="mdi:delete-outline"></ha-icon>
      </ha-icon-button>
    </div>`;
  }

  /** This room's part of the layer, and the rooms it also reaches. */
  private _lightsLabel(target: TargetInfo, layer: Layer): string {
    const roomLights = (id: string) => this.store.target(id)?.lights;
    const rooms = this.store.targets.filter((t) => t.kind === "room");
    const caption = layerCaption(layer.lights, target, rooms, roomLights);
    let here: string;
    if (isWhole(layer.lights, target, roomLights)) here = "All lights";
    else {
      const slice = roomSlice(layer.lights, target, roomLights);
      const refs = slice
        .filter((l) => l.startsWith("room:"))
        .map((l) => this.hass.areas?.[l.slice(5)]?.name ?? this.store.targetName(l));
      const entities = slice.filter((l) => !l.startsWith("room:"));
      const parts = [...refs];
      if (entities.length === 1 && !refs.length) parts.push(this.store.lightName(entities[0]));
      else if (entities.length) parts.push(plural(entities.length, "light"));
      here = parts.join(" + ");
    }
    if (!here) return caption ?? "No lights";
    return caption ? `${here} (${caption})` : here;
  }

  private _addLayer(): void {
    const scene = this._scene;
    if (!scene) return;
    const last = scene.layers[scene.layers.length - 1];
    const layer: Layer = { lights: [], effect: last.effect, params: structuredClone(last.params) };
    this._setLayers([...scene.layers, layer], scene.layers.length);
  }

  private _removeLayer(index: number): void {
    const scene = this._scene;
    if (!scene || scene.layers.length < 2) return;
    const selected = this._selected > index ? this._selected - 1 : this._selected;
    this._setLayers(
      scene.layers.filter((_, i) => i !== index),
      selected,
    );
  }

  private _layerMoved(ev: CustomEvent<{ oldIndex: number; newIndex: number }>): void {
    ev.stopPropagation();
    const scene = this._scene;
    if (!scene) return;
    const { oldIndex, newIndex } = ev.detail;
    const layers = [...scene.layers];
    layers.splice(newIndex, 0, ...layers.splice(oldIndex, 1));
    const ids = scene.layers.map((_, i) => i);
    ids.splice(newIndex, 0, ...ids.splice(oldIndex, 1));
    this._setLayers(layers, ids.indexOf(this._selected));
  }

  private _renderFooter(scene: StoredScene, problems: Problem[]) {
    const stored = !!this._storedScene(scene);
    const busy = this._busy !== undefined;
    const valid = problems.length === 0;
    return html`<div class="footer">
      ${
        stored
          ? html`<ha-button
              appearance="plain"
              variant="danger"
              .disabled=${busy}
              .loading=${this._busy === "delete"}
              @click=${() => this._delete(!!scene.preset)}
            >
              <ha-icon slot="start" .icon=${scene.preset ? "mdi:restore" : "mdi:delete-outline"}></ha-icon>
              ${scene.preset ? "Reset to preset" : "Delete"}
            </ha-button>`
          : nothing
      }
      <span class="spacer"></span>
      ${
        this._trying
          ? html`<ha-button
              appearance="plain"
              .disabled=${busy}
              .loading=${this._busy === "try"}
              @click=${this._stopTry}
            >
              <ha-icon slot="start" icon="mdi:stop"></ha-icon>
              Stop trying
            </ha-button>`
          : html`<ha-button
              appearance="plain"
              .disabled=${busy || !valid}
              .loading=${this._busy === "try"}
              @click=${this._try}
            >
              <ha-icon slot="start" icon="mdi:play-circle-outline"></ha-icon>
              Try
            </ha-button>`
      }
      <ha-button
        .disabled=${busy || !valid || (stored && !this._dirty)}
        .loading=${this._busy === "save"}
        @click=${this._save}
      >
        Save
      </ha-button>
    </div>`;
  }

  static styles = [
    pageStyles,
    css`
      .preview-bar {
        position: sticky;
        top: 0;
        z-index: 3;
        margin: calc(-1 * var(--ha-space-4)) calc(-1 * var(--ha-space-1)) 0;
        padding: var(--ha-space-4) var(--ha-space-1) var(--ha-space-3);
        background: var(--primary-background-color);
      }
      ha-alert {
        margin: var(--ha-space-2) 0 0;
      }
      .body {
        container-type: inline-size;
      }
      .columns {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        column-gap: var(--ha-space-6);
      }
      .columns > div:last-child .section-title {
        margin-top: var(--ha-space-6);
      }
      @container (min-width: 820px) {
        .columns {
          grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
          align-items: start;
        }
        .columns > div:last-child .section-title {
          margin-top: var(--ha-space-2);
        }
      }
      .form {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-5);
      }
      .name-row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: var(--ha-space-3);
        align-items: start;
      }
      :host([narrow]) .name-row {
        grid-template-columns: minmax(0, 1fr);
      }
      ha-input {
        --ha-input-padding-bottom: 0;
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: var(--ha-space-2);
      }
      .field-label {
        color: var(--primary-text-color);
      }
      .problem {
        margin: calc(-1 * var(--ha-space-3)) 0 0;
        font-size: var(--ha-font-size-s);
        color: var(--error-color);
      }
      .hint {
        margin: 0;
        padding: var(--ha-space-3) var(--ha-space-4) 0;
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
      }
      .layer-list {
        display: flex;
        flex-direction: column;
        padding: var(--ha-space-2) 0;
      }
      .layer {
        display: flex;
        align-items: center;
        gap: var(--ha-space-1);
        padding: 0 var(--ha-space-2) 0 var(--ha-space-3);
        background: var(--card-background-color);
        border-inline-start: 3px solid transparent;
      }
      .layer.selected {
        border-inline-start-color: var(--primary-color);
        background: rgba(var(--rgb-primary-color), 0.08);
      }
      .handle {
        flex: none;
        color: var(--secondary-text-color);
        cursor: grab;
      }
      .layer-main {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        gap: var(--ha-space-3);
        padding: var(--ha-space-2) 0;
        border: none;
        background: none;
        color: inherit;
        font: inherit;
        text-align: start;
        cursor: pointer;
        border-radius: var(--ha-border-radius-md);
      }
      .layer-main:focus-visible {
        outline: 2px solid var(--ha-color-focus, var(--primary-color));
      }
      .layer-art {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 40px;
        height: 40px;
        border-radius: var(--ha-border-radius-md);
        box-shadow: inset 0 0 0 1px var(--divider-color);
        --mdc-icon-size: 20px;
        color: #fff;
      }
      .layer-art ha-icon {
        filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.6));
      }
      .layer-text {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .layer-title,
      .layer-summary {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .layer-summary {
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
      }
      .warn {
        flex: none;
        color: var(--warning-color);
      }
      .card-actions {
        display: flex;
        padding: var(--ha-space-2);
        border-top: 1px solid var(--divider-color);
      }
      .footer {
        position: sticky;
        bottom: 0;
        z-index: 3;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: var(--ha-space-2);
        margin: var(--ha-space-6) calc(-1 * var(--ha-space-1)) 0;
        padding: var(--ha-space-3) var(--ha-space-1);
        background: var(--primary-background-color);
        border-top: 1px solid var(--divider-color);
      }
      .spacer {
        flex: 1;
      }
    `,
  ];
}

defineElement("ml-scene-editor", MlSceneEditor);
