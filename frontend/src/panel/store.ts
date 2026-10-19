// Immutable snapshot of the backend config plus the actions that change it. Every
// config write refetches and hands the panel a new MlStore, so `.store` bindings
// re-render their elements. Live status is read from hass.states (statusOf).

import * as api from "../shared/api";
import { activationOptions, toggleMotion } from "../shared/scene-actions";
import { statusOf, type StatusNames, type TargetStatusView } from "../shared/status";
import type {
  ConfigDto,
  EffectInfo,
  HomeAssistant,
  LightInfo,
  Part,
  PartCategory,
  PartInfo,
  PreviewFrame,
  PreviewRequest,
  SceneInfo,
  Settings,
  StoredScene,
  TargetId,
  TargetInfo,
  TargetOptions,
  Zone,
} from "../shared/types";
import { newPartId, newSceneId } from "../shared/util";

export interface StoreHost {
  readonly hass: HomeAssistant;
  storeChanged(store: MlStore): void;
}

/** Shared by every snapshot of one panel. */
interface StoreContext {
  host: StoreHost;
  seq: number;
  /** The newest snapshot; writes build their payload from it. */
  latest?: MlStore;
  /** Config writes run one after the other, each after the refresh of the previous one. */
  writes: Promise<unknown>;
}

/** A change, or how to make it from the latest saved value. */
export type Update<T, C> = Partial<T> | ((current: C) => Partial<T>);

/** What the editor changes before Save: the scene, and the parts it adds or edits. */
export interface Draft {
  scene: StoredScene;
  parts: Part[];
}

export type PartGroupKey = "yours" | PartCategory;

/** Picker and select sections in order. */
export const PART_GROUPS: readonly [PartGroupKey, string][] = [
  ["yours", "Your parts"],
  ["white", "Whites"],
  ["color", "Colour palettes"],
  ["dynamic", "Dynamic"],
  ["adaptive", "Adaptive"],
];

export interface PartGroup {
  key: PartGroupKey;
  title: string;
  parts: PartInfo[];
}

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

/** Names the scene menu keeps for itself (validation.py RESERVED_NAMES). */
const RESERVED = new Set(["off", "manual"]);

const DRAFT_PREFIX = "magic_lights.draft.";
const PART_DRAFT_PREFIX = "magic_lights.part-draft.";

/** A new scene's icon when its part has none: your parts carry no icon. */
export const SCENE_ICON = "mdi:palette-outline";

const storage = (): Storage | undefined => {
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
};

const readItem = <T>(item: string): T | undefined => {
  try {
    const raw = storage()?.getItem(item);
    return raw ? (JSON.parse(raw) as T) : undefined;
  } catch {
    return undefined;
  }
};

const writeItem = (item: string, value: object | undefined): void => {
  try {
    if (value) storage()?.setItem(item, JSON.stringify(value));
    else storage()?.removeItem(item);
  } catch {
    // Storage full or blocked: the draft lives only in the editor.
  }
};

const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name);

const fold = (name: string): string => name.trim().toLowerCase();

/** `base`, else "base 2", "base 3" …: the first that is not taken (case-insensitive). */
const freeName = (base: string, taken: ReadonlySet<string>): string => {
  let name = base;
  for (let n = 2; taken.has(fold(name)); n++) name = `${base} ${n}`;
  return name;
};

/** The stored shape of a scene, without the DTO fields. */
export const toStoredScene = (scene: StoredScene): StoredScene => ({
  id: scene.id,
  name: scene.name,
  icon: scene.icon,
  layers: structuredClone(scene.layers),
  others: scene.others,
  transition: scene.transition,
});

/** The stored shape of a part: the catalogue keeps icons and categories. */
export const toStoredPart = (part: Part): Part => ({
  id: part.id,
  name: part.name,
  effect: part.effect,
  params: structuredClone(part.params),
});

/** A part's section: yours, or its built-in category. */
export const partGroup = (part: PartInfo): PartGroupKey => (part.builtin && part.category) || "yours";

/** The non-empty sections of `parts`, in picker order. */
export const groupParts = (parts: readonly PartInfo[]): PartGroup[] =>
  PART_GROUPS.map(([key, title]) => ({ key, title, parts: parts.filter((p) => partGroup(p) === key) })).filter(
    (group) => group.parts.length,
  );

/** ha-select options of `parts` under a disabled heading per section. */
export const partOptions = (parts: readonly PartInfo[]): SelectOption[] =>
  groupParts(parts).flatMap((group) => [
    { value: `__group_${group.key}`, label: group.title, disabled: true },
    ...group.parts.map((part) => ({ value: part.id, label: part.name })),
  ]);

/** The whole target playing one part, as the backend runs a part called there. */
export const isWholeRoomOf = (scene: StoredScene, partId: string): boolean =>
  scene.layers.length === 1 &&
  scene.layers[0].lights === "all" &&
  scene.layers[0].part === partId &&
  scene.others === "off" &&
  scene.transition === null;

export class MlStore {
  static async load(host: StoreHost): Promise<MlStore> {
    const ctx: StoreContext = { host, seq: 0, writes: Promise.resolve() };
    ctx.latest = new MlStore(ctx, await api.fetchConfig(host.hass));
    return ctx.latest;
  }

  readonly targets: readonly TargetInfo[];

  private readonly _targets: Map<string, TargetInfo>;

  private readonly _parts: Map<string, PartInfo>;

  private readonly _scenes: Map<string, SceneInfo>;

  private readonly _effects: Map<string, EffectInfo>;

  private readonly _lights: Map<string, LightInfo>;

  private constructor(
    private readonly _ctx: StoreContext,
    readonly config: ConfigDto,
  ) {
    const rooms = config.targets.filter((t) => t.kind === "room").sort(byName);
    const zones = config.targets.filter((t) => t.kind === "zone").sort(byName);
    this.targets = [...rooms, ...zones];
    this._targets = new Map(config.targets.map((t) => [t.id, t]));
    this._parts = new Map(config.parts.map((p) => [p.id, p]));
    this._scenes = new Map(config.scenes.map((s) => [s.id, s]));
    this._effects = new Map(config.effects.map((e) => [e.key, e]));
    this._lights = new Map(config.lights.map((l) => [l.entity_id, l]));
  }

  private get _hass(): HomeAssistant {
    return this._ctx.host.hass;
  }

  // ---- lookups

  target(id: string): TargetInfo | undefined {
    return this._targets.get(id);
  }

  /** Built-ins in catalogue order, then yours. */
  parts(): readonly PartInfo[] {
    return this.config.parts;
  }

  part(id: string): PartInfo | undefined {
    return this._parts.get(id);
  }

  /** A scene by id; ids are unique over all targets. */
  scene(id: string): SceneInfo | undefined {
    return this._scenes.get(id);
  }

  /** A target's scenes in menu order, those without lights there included. */
  scenesOf(targetId: string): SceneInfo[] {
    return (this._targets.get(targetId)?.scenes ?? []).flatMap((id) => this._scenes.get(id) ?? []);
  }

  /** The scenes of existing targets that use a part, rooms by name, then zones, each in menu order. */
  usesOf(partId: string): SceneInfo[] {
    const used = new Set(this._parts.get(partId)?.used_by);
    return this.targets.flatMap((target) => this.scenesOf(target.id).filter((scene) => used.has(scene.id)));
  }

  /** The scenes of targets that are gone that use a part. */
  dormantUsesOf(partId: string): SceneInfo[] {
    return (this._parts.get(partId)?.dormant ?? []).flatMap((id) => this._scenes.get(id) ?? []);
  }

  /** The target's scene of the whole room playing the part, if it has one. */
  wholeRoomScene(targetId: string, partId: string): SceneInfo | undefined {
    return this.scenesOf(targetId).find((scene) => isWholeRoomOf(scene, partId));
  }

  /** What a status id names: `owner`'s scene, else a part run on its own. */
  running(owner: string, id: string): SceneInfo | PartInfo | undefined {
    const scene = this._scenes.get(id);
    return scene?.target === owner ? scene : this._parts.get(id);
  }

  /** The parts a scene's layers play, or the part itself. */
  looksOf(item: SceneInfo | PartInfo): PartInfo[] {
    if (!("layers" in item)) return [item];
    return item.layers.flatMap((layer) => this._parts.get(layer.part) ?? []);
  }

  effect(key: string): EffectInfo | undefined {
    return this._effects.get(key);
  }

  light(entityId: string): LightInfo | undefined {
    return this._lights.get(entityId);
  }

  /** The zone behind a "zone:<id>" target. */
  zoneOf(targetId: string): Zone | undefined {
    return targetId.startsWith("zone:") ? this.config.zones[targetId.slice("zone:".length)] : undefined;
  }

  targetName(id: string): string {
    return this._targets.get(id)?.name ?? id.replace(/^(room|zone):/, "");
  }

  lightName(entityId: string): string {
    return (
      this._lights.get(entityId)?.name ??
      this._hass.states[entityId]?.attributes.friendly_name ??
      entityId
    );
  }

  /** "Living Room · Hell" for a scene, "Unknown room · Hell" once its target is gone. */
  sceneLabel(scene: SceneInfo): string {
    return `${scene.dormant ? "Unknown room" : this.targetName(scene.target)} · ${scene.name}`;
  }

  /** A scene that plays a part: its label, flagged when it has no lights in its room. */
  useLabel(scene: SceneInfo): string {
    return scene.fits ? this.sceneLabel(scene) : `${this.sceneLabel(scene)} · needs set-up`;
  }

  status(hass: HomeAssistant, targetId: string): TargetStatusView | undefined {
    return statusOf(hass, this._targets.get(targetId)?.select_entity_id);
  }

  readonly statusNames: StatusNames = {
    target: (id) => this.targetName(id),
    scene: (id, owner) => this.running(owner, id)?.name,
  };

  /** Colours of what runs: the owner's scene where it reaches, else the part's. */
  readonly swatchesOf = (id: string, owner: TargetId): readonly string[] | undefined =>
    this.running(owner, id)?.swatches;

  // ---- names and new things (nothing is stored before Save)

  /** A name none of the target's scenes has, nor the scene menu's Off and Manual. */
  uniqueSceneName(targetId: string, base: string, taken: readonly string[] = []): string {
    const names = [...this.scenesOf(targetId).map((s) => s.name), ...taken, ...RESERVED];
    return freeName(base, new Set(names.map(fold)));
  }

  /** A name no part has, built-ins included. */
  uniquePartName(base: string, taken: readonly string[] = []): string {
    return freeName(base, new Set([...this.config.parts.map((p) => p.name), ...taken].map(fold)));
  }

  /**
   * A new scene of the target: the whole room playing `partId`, or (null) a blank white
   * part that is created with the scene.
   */
  newScene(targetId: string, partId: string | null): Draft {
    const scene = (part: string, name: string, icon: string): StoredScene => ({
      id: newSceneId(),
      name: this.uniqueSceneName(targetId, name),
      icon,
      layers: [{ lights: "all", part }],
      others: "off",
      transition: null,
    });
    if (partId === null) {
      const blank = this.newPart();
      return { scene: scene(blank.id, "New scene", SCENE_ICON), parts: [blank] };
    }
    const part = this._parts.get(partId);
    return { scene: scene(partId, part?.name ?? partId, part?.icon ?? SCENE_ICON), parts: [] };
  }

  /** "<name> copy" with the part's look, under a new id; `taken` are the draft's own names. */
  copyPart(part: Part, taken: readonly string[] = []): Part {
    return {
      id: newPartId(),
      name: this.uniquePartName(`${part.name} copy`, taken),
      effect: part.effect,
      params: structuredClone(part.params),
    };
  }

  /** A blank white part; `taken` are the draft's own names. */
  newPart(taken: readonly string[] = []): Part {
    const effect = this._effects.get("white") ?? this.config.effects[0];
    return {
      id: newPartId(),
      name: this.uniquePartName("New part", taken),
      effect: effect?.key ?? "white",
      params: { ...effect?.defaults },
    };
  }

  // ---- editor drafts (sessionStorage, keyed by scene or part; editor/working-copy.ts
  // keeps each draft's base next to it)

  draft<T = Draft>(key: string): T | undefined {
    return readItem<T>(`${DRAFT_PREFIX}${key}`);
  }

  setDraft(key: string, value: object | undefined): void {
    writeItem(`${DRAFT_PREFIX}${key}`, value);
  }

  /** The part editor's draft of a part (editor/working-copy.ts). */
  partDraft<T>(partId: string): T | undefined {
    return readItem<T>(`${PART_DRAFT_PREFIX}${partId}`);
  }

  setPartDraft(partId: string, value: object | undefined): void {
    writeItem(`${PART_DRAFT_PREFIX}${partId}`, value);
  }

  // ---- config writes (each refreshes the store)

  async refresh(): Promise<MlStore> {
    const seq = ++this._ctx.seq;
    const next = new MlStore(this._ctx, await api.fetchConfig(this._hass));
    if (seq === this._ctx.seq) {
      this._ctx.latest = next;
      this._ctx.host.storeChanged(next);
    }
    return next;
  }

  /**
   * Queues a write. Its payload is built when it runs, from the snapshot the previous
   * write refreshed, so quick successive edits do not resend values from before each other.
   */
  private _write<T>(request: (latest: MlStore) => Promise<T>): Promise<T> {
    const run = async () => {
      const result = await request(this._ctx.latest ?? this);
      await this.refresh();
      return result;
    };
    const result = this._ctx.writes.then(run, run);
    this._ctx.writes = result.catch(() => undefined);
    return result;
  }

  saveLibrary(library: api.Library): Promise<api.SavedLibrary> {
    return this._write(() => api.saveLibrary(this._hass, library));
  }

  /** One whole-room scene per part, in one write, named after the parts. */
  addScenes(targetId: string, partIds: readonly string[]): Promise<api.SavedLibrary> {
    return this._write((latest) => {
      const names: string[] = [];
      const scenes = partIds.map((partId) => {
        const { scene } = latest.newScene(targetId, partId);
        scene.name = latest.uniqueSceneName(targetId, latest.part(partId)?.name ?? partId, names);
        names.push(scene.name);
        return { target: targetId as TargetId, scene };
      });
      return api.saveLibrary(this._hass, { scenes });
    });
  }

  async deleteScene(sceneId: string): Promise<void> {
    await this._write(() => api.deleteScene(this._hass, sceneId));
  }

  async deletePart(partId: string): Promise<void> {
    await this._write(() => api.deletePart(this._hass, partId));
  }

  orderScenes(targetId: string, sceneIds: string[]): Promise<TargetInfo> {
    return this._write(() => api.orderScenes(this._hass, targetId as TargetId, sceneIds));
  }

  saveZone(zone: Zone): Promise<Zone> {
    return this._write(() => api.saveZone(this._hass, zone));
  }

  async deleteZone(zoneId: string): Promise<void> {
    await this._write(() => api.deleteZone(this._hass, zoneId));
  }

  saveTargetOptions(targetId: string, update: Update<TargetOptions, TargetInfo>): Promise<TargetInfo> {
    return this._write((latest) => {
      let options = update;
      if (typeof options === "function") {
        const target = latest.target(targetId);
        if (!target) throw new Error(`Unknown target ${targetId}`);
        options = options(target);
      }
      return api.saveTargetOptions(this._hass, targetId as TargetId, options);
    });
  }

  async saveSettings(update: Update<Settings, Settings>): Promise<void> {
    await this._write((latest) =>
      api.saveSettings(this._hass, typeof update === "function" ? update(latest.config.settings) : update),
    );
  }

  /** How long switching scenes takes in every room, in seconds. */
  setFade(seconds: number): Promise<void> {
    return this.saveSettings({ transition: seconds });
  }

  // ---- runtime (no config change)

  preview(request: PreviewRequest): Promise<PreviewFrame[]> {
    return api.fetchPreview(this._hass, request);
  }

  async tryScene(targetId: string, draft: Draft): Promise<void> {
    await api.tryScene(this._hass, targetId as TargetId, draft.scene, draft.parts);
  }

  async stopTry(targetId: string): Promise<void> {
    await api.stopTry(this._hass, targetId as TargetId);
  }

  async reclaim(targetId: string, entityId?: string): Promise<void> {
    await api.reclaimLights(this._hass, targetId as TargetId, entityId);
  }

  async reshuffle(targetId: string): Promise<void> {
    await api.reshuffle(this._hass, targetId as TargetId);
  }

  private _select(targetId: string): string {
    const entityId = this._targets.get(targetId)?.select_entity_id;
    if (!entityId) throw new Error(`Unknown target ${targetId}`);
    return entityId;
  }

  /** Activates one of the target's scenes; the active one is applied again with its adjustments. */
  async activate(targetId: string, sceneId: string): Promise<void> {
    const attrs = this.status(this._hass, targetId)?.attrs;
    const options = activationOptions(attrs, sceneId, this.running(targetId, sceneId));
    await api.activateScene(this._hass, this._select(targetId), sceneId, options);
  }

  /** ▶ of a scene: starts or holds the running one's motion, or starts another one moving. */
  async toggleMotion(targetId: string, sceneId: string): Promise<void> {
    const attrs = this.status(this._hass, targetId)?.attrs;
    await toggleMotion(this._hass, this._select(targetId), attrs, sceneId);
  }

  /** Level of the brightest light, 1..100. */
  async setBrightness(targetId: string, brightness: number): Promise<void> {
    await api.adjustBrightness(this._hass, this._select(targetId), { brightness });
  }

  async turnOff(targetId: string): Promise<void> {
    await api.turnOff(this._hass, this._select(targetId));
  }

  async release(targetId: string): Promise<void> {
    await api.release(this._hass, this._select(targetId));
  }
}
