// A small config (two rooms, one zone, built-ins with an edit, parts of yours shared by
// rooms, a scene without lights in its room) and a fake hass.

import type {
  ConfigDto,
  HomeAssistant,
  PartInfo,
  SceneInfo,
  SelectAttributes,
  TargetInfo,
} from "../src/shared/types";

const options = {
  excluded_lights: [],
  extra_lights: [],
  default_scene: null,
  reclaim_on_turn_on: true,
  detect_changes: true,
};

const target = (id: TargetInfo["id"], name: string, lights: string[], scenes: string[]): TargetInfo => ({
  id,
  kind: id.startsWith("room:") ? "room" : "zone",
  name,
  icon: null,
  area_id: id.startsWith("room:") ? id.slice(5) : null,
  rooms: id.startsWith("room:") ? [id.slice(5)] : ["living", "kitchen"],
  lights,
  options,
  scenes,
  select_entity_id: `select.${name.toLowerCase().replace(/ /g, "_")}_scene`,
});

const SUNSET = ["#4000ff", "#ff5c2f"];

const part = (p: Partial<PartInfo> & Pick<PartInfo, "id" | "name">): PartInfo => ({
  effect: "white",
  params: { kelvin: 2700, brightness: 100 },
  icon: null,
  category: null,
  builtin: false,
  edited: false,
  original: null,
  swatches: ["#ffa757"],
  dynamic: false,
  used_by: [],
  dormant: [],
  ...p,
});

const scene = (
  s: Partial<SceneInfo> & Pick<SceneInfo, "id" | "name" | "target"> & { part: string },
): SceneInfo => {
  const { part: partId, ...rest } = s;
  return {
    icon: null,
    layers: [{ lights: "all", part: partId }],
    others: "off",
    transition: null,
    swatches: ["#ffa757"],
    dynamic: false,
    fits: true,
    dormant: false,
    ...rest,
  };
};

export const CONFIG: ConfigDto = {
  zones: { downstairs: { id: "downstairs", name: "Downstairs", icon: null, rooms: ["living", "kitchen"], lights: [] } },
  targets: [
    target("zone:downstairs", "Downstairs", ["light.lamp", "light.ceiling"], ["s_00000006"]),
    target("room:living", "Living room", ["light.lamp"], ["s_00000001", "s_00000002", "s_00000003"]),
    target("room:kitchen", "Kitchen", ["light.ceiling"], ["s_00000004", "s_00000005"]),
  ],
  parts: [
    part({
      id: "relax",
      name: "Relax",
      icon: "mdi:sofa",
      category: "white",
      builtin: true,
      original: { effect: "white", params: { kelvin: 2700, brightness: 100 } },
      swatches: ["#ff942b"],
      used_by: ["s_00000001", "s_00000004"],
    }),
    part({
      id: "savanna_sunset",
      name: "Savanna sunset",
      icon: "mdi:weather-sunset",
      category: "color",
      builtin: true,
      edited: true,
      effect: "palette",
      params: { motion: "dynamic" },
      original: { effect: "palette", params: { motion: "static" } },
      dynamic: true,
      swatches: SUNSET,
      used_by: ["s_00000005", "s_00000006"],
    }),
    part({ id: "p_00000001", name: "Movie", used_by: ["s_00000002"] }),
    part({
      id: "p_00000002",
      name: "Nachtlicht",
      effect: "palette",
      params: { motion: "static" },
      swatches: ["#ff5c2f"],
      used_by: ["s_00000003"],
      dormant: ["s_0000000a"],
    }),
  ],
  scenes: [
    scene({ id: "s_00000006", name: "Savanna sunset", target: "zone:downstairs", part: "savanna_sunset", swatches: SUNSET, dynamic: true }),
    scene({ id: "s_00000001", name: "Relax", target: "room:living", part: "relax", icon: "mdi:sofa", swatches: ["#ff942b"] }),
    scene({ id: "s_00000002", name: "Movie", target: "room:living", part: "p_00000001" }),
    // The hallway light it plays on left the living room.
    {
      ...scene({ id: "s_00000003", name: "Nachtlicht", target: "room:living", part: "p_00000002", fits: false }),
      layers: [{ lights: ["light.hall"], part: "p_00000002" }],
    },
    scene({ id: "s_00000004", name: "Relax", target: "room:kitchen", part: "relax", swatches: ["#ff942b"] }),
    scene({
      id: "s_00000005",
      name: "Savanna sunset",
      target: "room:kitchen",
      part: "savanna_sunset",
      swatches: ["#ff5c2f"],
      dynamic: true,
    }),
    // A scene of a room that is gone.
    scene({ id: "s_0000000a", name: "Nachtlicht", target: "room:attic", part: "p_00000002", fits: false, dormant: true }),
  ],
  lights: [],
  effects: [
    {
      key: "white",
      name: "White",
      description: "",
      icon: "mdi:white-balance-sunny",
      category: "white",
      dynamic: false,
      source: "builtin",
      schema: [{ name: "kelvin" }, { name: "brightness" }],
      defaults: { kelvin: 2700, brightness: 100 },
    },
    {
      key: "palette",
      name: "Colour palette",
      description: "",
      icon: "mdi:palette",
      category: "color",
      dynamic: true,
      source: "builtin",
      schema: [{ name: "palette" }, { name: "motion" }, { name: "speed", visible: { field: "motion", value: "dynamic" } }],
      defaults: { motion: "static" },
    },
  ],
  buses: [],
  palettes: [],
  settings: {
    transition: 1,
    default_scene: null,
    native_effects: true,
    buses: {},
    light_buses: {},
  },
  version: "3.0.0",
};

export const attrs = (a: Partial<SelectAttributes>): SelectAttributes => ({
  status: "off",
  scene_id: null,
  scene_name: null,
  via: null,
  partial: false,
  dynamic: false,
  motion: false,
  brightness: null,
  manual: [],
  lights: {},
  elsewhere: {},
  parts: [],
  since: null,
  errors: [],
  target_id: "room:living",
  kind: "room",
  scenes: [],
  options: [],
  ...a,
});

export const fakeHass = (states: Record<string, SelectAttributes> = {}, available = true): HomeAssistant =>
  ({
    states: Object.fromEntries(
      Object.entries(states).map(([entity_id, attributes]) => [
        entity_id,
        { entity_id, state: available ? "Relax" : "unavailable", attributes },
      ]),
    ),
    entities: {},
    devices: {},
    areas: {},
    callWS: async <T>(message: { type: string }) => {
      if (message.type !== "magic_lights/config") throw new Error(`unexpected ${message.type}`);
      return structuredClone(CONFIG) as T;
    },
  }) as unknown as HomeAssistant;

/** A sessionStorage for the editor's drafts; returns its items. */
export const draftStorage = (): Map<string, string> => {
  const items = new Map<string, string>();
  const sessionStorage = {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  };
  Object.assign(globalThis, { window: { sessionStorage } });
  return items;
};
