// Immutable snapshot of the backend config plus the actions that change it. Every
// config write refetches and hands the panel a new MlStore, so `.store` bindings
// re-render their elements. Live status is read from hass.states (statusOf).

import * as api from "../shared/api";
import { activationOptions, toggleMotion } from "../shared/scene-actions";
import { statusOf, type StatusNames, type TargetStatusView } from "../shared/status";
import type {
  Adjust,
  ConfigDto,
  EffectInfo,
  HomeAssistant,
  Layer,
  LightInfo,
  PreviewFrame,
  PreviewRequest,
  SceneCategory,
  SceneInfo,
  Settings,
  StoredScene,
  TargetId,
  TargetInfo,
  TargetOptions,
  Zone,
} from "../shared/types";
import { newSceneId } from "../shared/util";
import { wholeTarget } from "./editor/editor-model";

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

/** Gallery sections in order; categories outside them count as the user's. */
export const SCENE_GROUPS: readonly [SceneCategory, string][] = [
  ["custom", "Yours"],
  ["white", "Whites"],
  ["color", "Colour palettes"],
  ["dynamic", "Dynamic"],
  ["adaptive", "Adaptive"],
];

export interface SceneGroup {
  category: SceneCategory;
  title: string;
  scenes: SceneInfo[];
}

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

const DRAFT_PREFIX = "magic_lights.draft.";

const storage = (): Storage | undefined => {
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
};

const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name);

/** The stored shape of a scene, without the catalogue fields. */
export const toStored = (scene: StoredScene): StoredScene => ({
  id: scene.id,
  name: scene.name,
  icon: scene.icon,
  preset: scene.preset,
  layers: structuredClone(scene.layers),
  others: scene.others,
  brightness: scene.brightness,
  transition: scene.transition,
});

/** The id a scene has in menus, services and runtime: an edited preset goes by its preset's id. */
export const menuId = (scene: StoredScene): string => scene.preset ?? scene.id;

/** The ids a target's menu holds, fitting or not; a default menu is its fitting user scenes. */
export const menuIds = (target: TargetInfo): string[] => [...(target.options.visible_scenes ?? target.scenes)];

/** A menu with `sceneId` appended (kept in place if there) or removed. */
export const withScene = (ids: readonly string[], sceneId: string, shown: boolean): string[] => {
  if (!shown) return ids.filter((id) => id !== sceneId);
  return ids.includes(sceneId) ? [...ids] : [...ids, sceneId];
};

/** Where a scene stands in one target. */
export interface Availability {
  target: TargetInfo;
  /** In the target's menu. */
  shown: boolean;
  /** It has lights there and can run. */
  fits: boolean;
  /** In the menu without lights there: it shows greyed out until it is set up. */
  needsSetUp: boolean;
  /** It runs when a light is switched on while the room is off. */
  isDefault: boolean;
}

/** `scene` as menus know it (a preset resolved to its edit); `globalDefault` is settings.default_scene. */
export const availabilityIn = (
  scene: SceneInfo,
  target: TargetInfo,
  globalDefault: string | null,
): Availability => {
  const id = menuId(scene);
  const shown = menuIds(target).includes(id);
  const fits = scene.fits.includes(target.id);
  const fallback = target.options.default_scene ?? globalDefault;
  return {
    target,
    shown,
    fits,
    needsSetUp: shown && !fits,
    isDefault: target.kind === "room" && fallback === id,
  };
};

export class MlStore {
  static async load(host: StoreHost): Promise<MlStore> {
    const ctx: StoreContext = { host, seq: 0, writes: Promise.resolve() };
    ctx.latest = new MlStore(ctx, await api.fetchConfig(host.hass));
    return ctx.latest;
  }

  readonly targets: readonly TargetInfo[];

  private readonly _targets: Map<string, TargetInfo>;

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

  /** A scene by its own id (preset, user scene or the edit of a preset). */
  scene(id: string): SceneInfo | undefined {
    return this._scenes.get(id);
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

  /** What a menu id runs, in every room: a preset's edit, else the preset or the stored scene. */
  resolveScene(sceneId: string): SceneInfo | undefined {
    const scene = this._scenes.get(sceneId);
    return (scene?.override && this._scenes.get(scene.override)) || scene;
  }

  /** The preset an edit replaces, else the scene itself. */
  origin(scene: SceneInfo): SceneInfo {
    return (scene.preset && this._scenes.get(scene.preset)) || scene;
  }

  /** A preset that has an edit for every room (or that edit itself). */
  edited(sceneId: string): boolean {
    const scene = this._scenes.get(sceneId);
    return !!(scene?.override || scene?.preset);
  }

  /** Per target: whether the scene is in its menu, fits there, needs setting up, is its switch-on scene. */
  availability(sceneId: string): Availability[] {
    const scene = this.resolveScene(sceneId);
    if (!scene) return [];
    const globalDefault = this.config.settings.default_scene;
    return this.targets.map((target) => availabilityIn(scene, target, globalDefault));
  }

  /** The room the target-free editor previews a scene in: the first it fits, else the first room. */
  previewTarget(sceneId: string): TargetInfo | undefined {
    const fits = new Set(this.resolveScene(sceneId)?.fits ?? []);
    const rooms = this.targets.filter((t) => t.kind === "room");
    return rooms.find((t) => fits.has(t.id)) ?? this.targets.find((t) => fits.has(t.id)) ?? rooms[0];
  }

  /** Visible (or hidden) scenes of a target, resolved and in menu order. */
  scenesFor(targetId: string, which: "visible" | "hidden" = "visible"): SceneInfo[] {
    const target = this._targets.get(targetId);
    const ids = (which === "visible" ? target?.scenes : target?.hidden) ?? [];
    return ids.flatMap((id) => this.resolveScene(id) ?? []);
  }

  /** Every scene once, as menus know it: presets (an edited one as its edit) and the user's. */
  menuScenes(): SceneInfo[] {
    return this.config.scenes.flatMap((s) => (s.preset ? [] : (this.resolveScene(s.id) ?? [])));
  }

  /** The gallery section of a scene; an edited preset sits where its preset does. */
  groupOf(scene: SceneInfo): SceneCategory {
    const category = this.origin(scene).category;
    return SCENE_GROUPS.some(([key]) => key === category) ? category : "custom";
  }

  /** The non-empty sections of `scenes`, in gallery order. */
  groupScenes(scenes: readonly SceneInfo[]): SceneGroup[] {
    return SCENE_GROUPS.map(([category, title]) => ({
      category,
      title,
      scenes: scenes.filter((scene) => this.groupOf(scene) === category),
    })).filter((group) => group.scenes.length);
  }

  /** ha-select options of `scenes` under a disabled heading per section. */
  sceneOptions(scenes: readonly SceneInfo[], valueOf: (scene: SceneInfo) => string): SelectOption[] {
    return this.groupScenes(scenes).flatMap((group) => [
      { value: `__group_${group.category}`, label: group.title, disabled: true },
      ...group.scenes.map((scene) => ({ value: valueOf(scene), label: scene.name })),
    ]);
  }

  status(hass: HomeAssistant, targetId: string): TargetStatusView | undefined {
    return statusOf(hass, this._targets.get(targetId)?.select_entity_id);
  }

  readonly statusNames: StatusNames = {
    target: (id) => this.targetName(id),
    scene: (id) => this.resolveScene(id)?.name,
  };

  /** A layer for one room only shows in that room. */
  readonly swatchesOf = (sceneId: string, owner: TargetId): readonly string[] | undefined =>
    this._targets.get(owner)?.swatches[sceneId] ?? this.resolveScene(sceneId)?.swatches;

  // ---- editor drafts (sessionStorage, keyed by scene: one scene, one draft in every room;
  // editor/working-copy.ts keeps the draft's base next to it)

  draft<T = StoredScene>(sceneId: string): T | undefined {
    try {
      const raw = storage()?.getItem(`${DRAFT_PREFIX}${sceneId}`);
      return raw ? (JSON.parse(raw) as T) : undefined;
    } catch {
      return undefined;
    }
  }

  setDraft(sceneId: string, scene: object | undefined): void {
    const key = `${DRAFT_PREFIX}${sceneId}`;
    try {
      if (scene) storage()?.setItem(key, JSON.stringify(scene));
      else storage()?.removeItem(key);
    } catch {
      // Storage full or blocked: the draft lives only in the editor.
    }
  }

  /**
   * What the editor opens at /scene/<sceneId>: the draft, else the stored scene (a
   * preset id opens its edit), else a new edit of the preset for every room, seeded
   * from the preset. Undefined for unknown ids.
   */
  editableScene(sceneId: string): StoredScene | undefined {
    const draft = this.draft(sceneId);
    const override = this._scenes.get(sceneId)?.override;
    // The preset was edited elsewhere since: the draft becomes that edit.
    if (draft?.preset && override) return { ...draft, id: override };
    if (draft) return draft;
    const scene = this.resolveScene(sceneId);
    if (!scene) return undefined;
    if (!scene.builtin) return toStored(scene);
    return { ...toStored(scene), id: newSceneId(), preset: scene.id };
  }

  /** A new user scene, set up in the target when given (layer 1: its rooms). */
  newScene(targetId?: string): StoredScene {
    return {
      id: newSceneId(),
      name: this.uniqueName("New scene"),
      icon: null,
      preset: null,
      layers: [this._blankLayer(targetId)],
      others: "off",
      brightness: 100,
      transition: null,
    };
  }

  /** A name no scene has: a scene can later be set up in any room. */
  uniqueName(base: string): string {
    const taken = new Set(this.config.scenes.map((s) => s.name.toLowerCase()));
    let name = base;
    for (let n = 2; taken.has(name.toLowerCase()); n++) name = `${base} ${n}`;
    return name;
  }

  private _blankLayer(targetId?: string): Layer {
    const effect = this._effects.get("white") ?? this.config.effects[0];
    const target = targetId ? this._targets.get(targetId) : undefined;
    return {
      lights: target ? wholeTarget(target, (id) => this._targets.get(id)?.lights) : [],
      effect: effect?.key ?? "white",
      params: { ...effect?.defaults },
    };
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

  saveScene(scene: StoredScene): Promise<SceneInfo> {
    return this._write(() => api.saveScene(this._hass, scene));
  }

  /** Deleting the edit of a preset resets it in every room. */
  async deleteScene(sceneId: string): Promise<void> {
    await this._write(() => api.deleteScene(this._hass, sceneId));
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

  /** Several targets' menus in one write (null: the default menu), built from the latest snapshot. */
  saveMenus(menus: (latest: MlStore) => Record<string, string[] | null>): Promise<TargetInfo[]> {
    return this._write((latest) => api.saveMenus(this._hass, menus(latest) as Record<TargetId, string[] | null>));
  }

  setMenu(targetId: string, ids: string[] | null): Promise<TargetInfo[]> {
    return this.saveMenus(() => ({ [targetId]: ids }));
  }

  /** Shows (appends) or hides a scene in the menus of the targets. */
  setInMenus(sceneId: string, targetIds: readonly string[], shown: boolean): Promise<TargetInfo[]> {
    return this.saveMenus((latest) =>
      Object.fromEntries(targetIds.map((id) => [id, withScene(latest._menu(id), sceneId, shown)])),
    );
  }

  private _target(id: string): TargetInfo {
    const target = this._targets.get(id);
    if (!target) throw new Error(`Unknown target ${id}`);
    return target;
  }

  private _menu(id: string): string[] {
    return menuIds(this._target(id));
  }

  // ---- runtime (no config change)

  preview(request: PreviewRequest): Promise<PreviewFrame[]> {
    return api.fetchPreview(this._hass, request);
  }

  async tryScene(targetId: string, scene: StoredScene, adjust?: Adjust): Promise<void> {
    await api.tryScene(this._hass, targetId as TargetId, scene, adjust);
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

  /** Activates a scene by menu id; the active one is applied again with its adjustments. */
  async activate(targetId: string, sceneId: string): Promise<void> {
    const attrs = this.status(this._hass, targetId)?.attrs;
    const scene = this.resolveScene(sceneId);
    const options = activationOptions(attrs, sceneId, scene);
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
