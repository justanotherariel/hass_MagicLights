// Backend DTOs and the slice of HA's frontend types the bundles use.

export type TargetId = `room:${string}` | `zone:${string}`;
export type TargetKind = "room" | "zone";

/** Output form; inputs also accept `{rgb: [r, g, b]}` and `"#rrggbb"`. */
export type Color = { kelvin: number } | { xy: [number, number] };

// ---- storage

export interface Zone {
  id: string; // "" on create: the backend slugs the name
  name: string;
  icon: string | null;
  rooms: string[]; // area ids
  lights: string[]; // extra entity ids
}

export interface TargetOptions {
  excluded_lights: string[];
  extra_lights: string[];
  default_scene: string | null; // one of the target's scenes; null = inherit settings.default_scene; "none" = no default
  reclaim_on_turn_on: boolean;
  detect_changes: boolean;
}

/** `"all"` or entity ids / `"room:<area_id>"` entries. */
export type LayerLights = "all" | string[];

/** What a part's lights do. */
export interface Look {
  effect: string;
  params: Record<string, unknown>;
}

/** A named look, shared by every scene that uses it. */
export interface Part extends Look {
  id: string; // yours "p_" + 8 hex (client generated); a built-in's slug ("relax")
  name: string; // unique over all parts
}

/** Which of the target's lights play a part; a later layer wins. */
export interface PartLayer {
  lights: LayerLights;
  part: string;
}

/** One target's scene. */
export interface StoredScene {
  id: string; // "s_" + 8 hex (client generated)
  name: string; // unique within its target
  icon: string | null;
  layers: PartLayer[];
  others: "off" | "keep";
  transition: number | null; // s; null = settings.transition
}

export interface BusLimits {
  rate: number; // msg/s
  burst: number;
  min_interval: number; // s per light
}

export interface Settings {
  transition: number;
  default_scene: string | null;
  native_effects: boolean;
  buses: Record<string, Partial<BusLimits>>;
  light_buses: Record<string, string>; // entity_id -> bus id
}

// ---- websocket

export interface LightCaps {
  brightness: boolean;
  color: boolean;
  color_temp: boolean;
  min_kelvin: number;
  max_kelvin: number;
  transition: boolean;
  effects: string[];
  gradient_points: number;
  driver: string;
}

export interface LightInfo {
  entity_id: string;
  name: string;
  area_id: string | null;
  bus_id: string;
  caps: LightCaps;
}

export interface TargetInfo {
  id: TargetId;
  kind: TargetKind;
  name: string;
  icon: string | null;
  area_id: string | null;
  rooms: string[];
  lights: string[];
  options: TargetOptions;
  scenes: string[]; // its scene ids in menu order
  select_entity_id: string;
}

export type PartCategory = "white" | "color" | "dynamic" | "adaptive";

export interface PartInfo extends Part {
  icon: string | null; // built-ins only
  category: PartCategory | null; // built-ins only
  builtin: boolean;
  edited: boolean;
  /** A built-in's own look, before any edit. */
  original: Look | null;
  swatches: string[];
  dynamic: boolean;
  used_by: string[]; // scene ids of existing targets
  dormant: string[]; // scene ids of targets that are gone
}

export interface SceneInfo extends StoredScene {
  target: TargetId;
  swatches: string[]; // of the layers that reach its target
  dynamic: boolean;
  fits: boolean; // it has lights in its target
  dormant: boolean; // its target is gone
}

export type EffectCategory = "white" | "color" | "dynamic" | "adaptive" | "other";

export interface EffectInfo {
  key: string;
  name: string;
  description: string;
  icon: string;
  category: EffectCategory;
  dynamic: boolean;
  source: string;
  schema: HaFormSchema[] | null; // null: serialisation failed, edit params as an object
  defaults: Record<string, unknown>;
}

export interface BusInfo {
  id: string;
  kind: string;
  label: string;
  limits: BusLimits;
  defaults: BusLimits;
  lights: string[];
}

export interface PaletteInfo {
  id: string;
  name: string;
  colors: Color[];
}

export interface ConfigDto {
  zones: Record<string, Zone>;
  targets: TargetInfo[];
  parts: PartInfo[]; // built-ins in catalogue order, then yours
  scenes: SceneInfo[];
  lights: LightInfo[];
  effects: EffectInfo[];
  buses: BusInfo[];
  palettes: PaletteInfo[];
  settings: Settings;
  version: string;
}

export interface Adjust {
  brightness?: number;
  dynamic?: boolean;
}

export interface PreviewLight {
  on: boolean;
  brightness: number;
  rgb: string; // hex at full brightness
  gradient?: string[];
  effect?: string;
}

export interface PreviewFrame {
  t: number;
  lights: Record<string, PreviewLight>;
}

export interface PreviewRequest {
  target_id: TargetId;
  scene: StoredScene;
  /** Unsaved parts the scene uses, over the saved ones. */
  parts?: Part[];
  duration?: number;
  samples?: number;
  start?: string; // ISO
  adjust?: Adjust;
}

export type SubscribeEvent =
  | { config_changed: true }
  | { targets_changed: true }
  | { effects_changed: true };

// ---- select entity attributes = the status DTO

export type TargetState = "off" | "scene" | "manual" | "mixed";
/** "manual": given up by an activation (reclaimable); "on": on, but no activation owns it. */
export type LightStatus = "scene" | "manual" | "elsewhere" | "off" | "on" | "unavailable";

/** The activation owning a light that runs elsewhere; a draft (the editor's Try) has no scene id. */
export interface StatusOwner {
  target_id: TargetId;
  scene_id: string | null;
}

/** One foreign activation of a mixed target. */
export interface StatusPart {
  target_id: TargetId;
  scene_id: string;
  scene_name: string;
  lights: string[];
}

export interface StatusScene {
  id: string;
  name: string;
  swatches: string[];
  dynamic: boolean;
}

export interface StatusDto {
  status: TargetState;
  scene_id: string | null;
  scene_name: string | null;
  via: TargetId | null;
  partial: boolean;
  dynamic: boolean; // some layer animates
  motion: boolean; // the running scene's colours move (its ▶ state)
  brightness: number | null; // level of the brightest light, 1..100
  manual: string[];
  lights: Record<string, LightStatus>;
  elsewhere: Record<string, StatusOwner>; // by light
  parts: StatusPart[];
  since: string | null;
  errors: string[];
  target_id: TargetId;
  kind: TargetKind;
  scenes: StatusScene[]; // the menu: its scenes that have lights here
}

export interface SelectAttributes extends StatusDto {
  options: string[];
  friendly_name?: string;
  icon?: string;
}

// ---- custom selectors

export interface ColorSelectorConfig {
  allow_white: boolean;
  allow_color: boolean;
  min_kelvin: number;
  max_kelvin: number;
}

export interface PaletteSelectorConfig {
  min: number;
  max: number;
}

// ---- ha-form schema subset (fe-2026.8 src/components/ha-form/types.ts)

export type HaFormConditionOperator = "eq" | "not_eq" | "in" | "not_in" | "exists" | "not_exists";

export interface HaFormFieldCondition {
  field: string;
  operator?: HaFormConditionOperator;
  value?: unknown;
}

export interface HaFormGroupCondition {
  condition: "and" | "or" | "not";
  conditions: readonly HaFormCondition[];
}

export type HaFormCondition = HaFormFieldCondition | HaFormGroupCondition;

/**
 * One serialised schema item. `label`, `helper` and `visible` are lifted from the key's
 * description, and so are the panel's layout hints (docs/plugins.md).
 */
export interface HaFormSchema {
  name: string;
  selector?: Record<string, unknown>;
  type?: string;
  required?: boolean;
  optional?: boolean;
  default?: unknown;
  visible?: boolean | HaFormCondition | HaFormCondition[];
  label?: string;
  helper?: string;
  /** Helper text per value of the field, shown for the current one in place of `helper`. */
  hints?: Record<string, string>;
  description?: { suffix?: string; suggested_value?: unknown };
  /** In the collapsed "Advanced" part of the form. */
  advanced?: boolean;
  /** The lower key of a min/max pair, drawn with its partner as one range. */
  pair?: { max: string; label?: string };
  /** Values offered as a segmented control. */
  steps?: { value: number; label: string }[];
  /** Words under a unitless scale, low end first: ["Slow", "Lively"]. */
  ends?: string[];
  /** Consecutive fields with the same row sit side by side. */
  row?: string;
}

// ---- Home Assistant frontend slice (fe-2026.8 src/types.ts)

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, any>;
  last_changed: string;
  last_updated: string;
  context: { id: string; parent_id: string | null; user_id: string | null };
}

export interface HassEntityRegistryEntry {
  entity_id: string;
  name?: string;
  icon?: string;
  device_id?: string;
  area_id?: string;
  labels: string[];
  hidden?: boolean;
  entity_category?: string;
  platform?: string;
}

export interface HassDevice {
  id: string;
  area_id: string | null;
  name: string | null;
  name_by_user: string | null;
}

export interface HassArea {
  area_id: string;
  name: string;
  icon: string | null;
  floor_id: string | null;
}

export type Unsubscribe = () => Promise<void>;

export interface HassConnection {
  subscribeMessage<T>(
    callback: (message: T) => void,
    message: { type: string } & Record<string, unknown>,
    options?: { resubscribe?: boolean },
  ): Promise<Unsubscribe>;
}

export interface ServiceTarget {
  entity_id?: string | string[];
  device_id?: string | string[];
  area_id?: string | string[];
}

export interface HomeAssistant {
  states: Record<string, HassEntity>;
  entities: Record<string, HassEntityRegistryEntry>;
  devices: Record<string, HassDevice>;
  areas: Record<string, HassArea>;
  language: string;
  localize(key: string, values?: Record<string, unknown>): string;
  themes: { darkMode: boolean };
  user?: { id: string; name: string; is_admin: boolean };
  connection: HassConnection;
  connected: boolean;
  callWS<T>(message: { type: string } & Record<string, unknown>): Promise<T>;
  callService(
    domain: string,
    service: string,
    data?: Record<string, unknown>,
    target?: ServiceTarget,
    notifyOnError?: boolean,
    returnResponse?: boolean,
  ): Promise<unknown>;
  formatEntityState(stateObj: HassEntity, state?: string): string;
}

export interface Route {
  prefix: string;
  path: string;
}

export interface PanelInfo {
  url_path: string;
  title: string | null;
  icon: string | null;
  config: Record<string, unknown>;
}
