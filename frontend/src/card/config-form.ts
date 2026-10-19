// getConfigForm(): HA renders this schema with ha-form, so the card needs no editor element.

import { DOMAIN } from "../shared/api";
import type { HaFormSchema } from "../shared/types";
import { MAX_COLUMNS } from "./model";

interface FormItem extends HaFormSchema {
  schema?: FormItem[];
}

const LABELS: Record<string, string> = {
  entity: "Room or zone",
  name: "Name",
  show_scenes: "Show scenes",
  columns: "Scene columns",
  scenes: "Scenes",
  show_controls: "Show controls",
  show_lights: "Show lights",
};

const HELPERS: Record<string, string> = {
  scenes: "Ids or names of the room's scenes, in the order to show them; leave empty for the whole menu",
  show_controls: "Off, Stop scene, play colours and brightness",
};

export const CONFIG_FORM = {
  schema: [
    {
      name: "entity",
      required: true,
      selector: { entity: { filter: { integration: DOMAIN, domain: "select" } } },
    },
    { name: "name", selector: { text: {} } },
    {
      name: "",
      type: "grid",
      schema: [
        { name: "show_scenes", default: true, selector: { boolean: {} } },
        {
          name: "columns",
          default: 3,
          selector: { number: { min: 1, max: MAX_COLUMNS, step: 1, mode: "box" } },
        },
      ],
    },
    { name: "scenes", selector: { text: { multiple: true } } },
    {
      name: "",
      type: "grid",
      schema: [
        { name: "show_controls", default: true, selector: { boolean: {} } },
        { name: "show_lights", default: false, selector: { boolean: {} } },
      ],
    },
  ] satisfies FormItem[],
  computeLabel: (item: { name: string }) => LABELS[item.name],
  computeHelper: (item: { name: string }) => HELPERS[item.name],
};
