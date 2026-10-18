// A small config (two rooms, one zone, presets, an edited preset, a user scene) and a fake hass.

import type { ConfigDto, HomeAssistant, SceneInfo, SelectAttributes, TargetInfo } from "../src/shared/types";

const options = {
  excluded_lights: [],
  extra_lights: [],
  visible_scenes: null,
  default_scene: null,
  reclaim_on_turn_on: true,
  detect_changes: true,
};

const target = (
  id: TargetInfo["id"],
  name: string,
  lights: string[],
  scenes: string[],
  swatches: TargetInfo["swatches"],
): TargetInfo => ({
  id,
  kind: id.startsWith("room:") ? "room" : "zone",
  name,
  icon: null,
  area_id: id.startsWith("room:") ? id.slice(5) : null,
  rooms: id.startsWith("room:") ? [id.slice(5)] : ["living", "kitchen"],
  lights,
  options,
  scenes,
  hidden: [],
  swatches,
  select_entity_id: `select.${name.toLowerCase().replace(/ /g, "_")}_scene`,
});

// The edit of Savanna sunset has a second layer for the living room only.
const SUNSET = ["#4000ff", "#ff5c2f"];

const scene = (s: Partial<SceneInfo> & Pick<SceneInfo, "id" | "name">): SceneInfo => ({
  icon: null,
  preset: null,
  layers: [{ lights: "all", effect: "white", params: { kelvin: 2700, brightness: 100 } }],
  others: "off",
  brightness: 100,
  transition: null,
  builtin: false,
  category: "custom",
  swatches: ["#ffa757"],
  dynamic: false,
  override: null,
  fits: ["room:kitchen", "room:living", "zone:downstairs"],
  ...s,
});

export const CONFIG: ConfigDto = {
  zones: { downstairs: { id: "downstairs", name: "Downstairs", icon: null, rooms: ["living", "kitchen"], lights: [] } },
  targets: [
    target("zone:downstairs", "Downstairs", ["light.lamp", "light.ceiling"], ["relax", "savanna_sunset"], {
      relax: ["#ff942b"],
      savanna_sunset: SUNSET,
      s_22222222: ["#ffa757"],
    }),
    target("room:living", "Living room", ["light.lamp"], ["relax", "savanna_sunset", "s_22222222"], {
      relax: ["#ff942b"],
      savanna_sunset: SUNSET,
      s_22222222: ["#ffa757"],
    }),
    target("room:kitchen", "Kitchen", ["light.ceiling"], ["relax", "savanna_sunset"], {
      relax: ["#ff942b"],
      savanna_sunset: ["#ff5c2f"],
    }),
  ],
  scenes: [
    scene({ id: "relax", name: "Relax", builtin: true, category: "white", swatches: ["#ff942b"] }),
    scene({
      id: "savanna_sunset",
      name: "Savanna sunset",
      builtin: true,
      category: "color",
      dynamic: true,
      swatches: ["#ff5c2f", "#ffbd56"],
      layers: [{ lights: "all", effect: "palette", params: { motion: "static" } }],
      override: "s_11111111",
      fits: [],
    }),
    scene({
      id: "s_11111111",
      name: "Savanna sunset",
      preset: "savanna_sunset",
      category: "custom",
      dynamic: true,
      swatches: SUNSET,
      layers: [
        { lights: "all", effect: "palette", params: { motion: "dynamic" } },
        { lights: ["room:living"], effect: "palette", params: { motion: "dynamic" } },
      ],
    }),
    scene({
      id: "s_22222222",
      name: "Movie",
      layers: [{ lights: ["room:living"], effect: "white", params: { kelvin: 2700, brightness: 100 } }],
      fits: ["room:living", "zone:downstairs"],
    }),
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
  version: "2.1.0",
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
  hidden_scenes: [],
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
