// What a part or scene looks like, in a line, and where a part is used: the room page's
// tiles, the part picker, the editor and the home Parts tab. The look is read from the
// effect's schema (form-rules), never from the effect itself. Pure, tested without a DOM.

import type { EffectInfo, Look, PartInfo, SceneInfo, StoredScene } from "../shared/types";
import { humanise, plural, withUnit } from "../shared/util";
import { withDefaults } from "./editor/editor-model";
import { formLayout } from "./editor/form-rules";
import { groupParts, type MlStore, type PartGroupKey } from "./store";

const span = (values: number[], unit: string): string => {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  return lo === hi ? withUnit(lo, unit) : withUnit(`${lo}–${hi}`, unit);
};

/** "Adaptive · 40–100% · 2200–5000 K": a look's effect, its visible levels, whites and colours. */
export const layerLook = (effect: EffectInfo | undefined, look: Look): string[] => {
  const parts = [effect?.name ?? humanise(look.effect)];
  const percent: number[] = [];
  const whites: string[] = [];
  let colours: number | undefined;
  const p = withDefaults(effect?.defaults, look.params);
  for (const control of effect?.schema ? formLayout(effect.schema, p).main.flat() : []) {
    const value = p[control.item.name];
    if (control.kind === "bar" && control.big && typeof value === "number") percent.push(value);
    if (control.kind === "kelvin" && typeof value === "number") whites.push(`${value} K`);
    if (control.kind === "palette" && Array.isArray(value)) colours = value.length;
    if (control.kind === "range") {
      const high = p[control.high.name];
      if (typeof value !== "number" || typeof high !== "number") continue;
      if (control.kelvin) whites.push(span([value, high], "K"));
      else if (control.unit === "%") percent.push(value, high);
    }
  }
  if (percent.length) parts.push(span(percent, "%"));
  parts.push(...whites);
  if (colours !== undefined) parts.push(plural(colours, "colour"));
  return parts;
};

export const lookLine = (effect: EffectInfo | undefined, look: Look): string => layerLook(effect, look).join(" · ");

type EffectOf = (key: string) => EffectInfo | undefined;
type PartOf = (id: string) => Look | undefined;

/** The looks a scene's layers play, in layer order; a missing part plays nothing. */
const sceneLooks = (scene: StoredScene, partOf: PartOf): Look[] => scene.layers.flatMap((l) => partOf(l.part) ?? []);

const kind = (look: Look, effectOf: EffectOf): string => effectOf(look.effect)?.name ?? humanise(look.effect);

/** The effects of later looks unlike the first's: a "White" scene with a palette on the TV. */
const otherKinds = (looks: readonly Look[], effectOf: EffectOf): string[] => {
  const first = kind(looks[0], effectOf);
  return [...new Set(looks.slice(1).map((look) => kind(look, effectOf)))].filter((name) => name !== first);
};

/** The tile's "what it is": the first look's effect and first fact, and what other parts do. */
export const tileLine = (scene: StoredScene, partOf: PartOf, effectOf: EffectOf): string => {
  const looks = sceneLooks(scene, partOf);
  if (!looks.length) return "";
  const facts = layerLook(effectOf(looks[0].effect), looks[0]);
  const others = otherKinds(looks, effectOf);
  if (looks.length > 1 && !others.length) facts.push(plural(looks.length, "part"));
  return [facts.slice(0, 2).join(" · "), ...others].join(" + ");
};

/** A search by name; an empty query matches everything. */
export const matchesQuery = (name: string, query: string): boolean => {
  const q = query.trim().toLowerCase();
  return !q || name.toLowerCase().includes(q);
};

// ---- the home Parts tab

export interface PartEntry {
  part: PartInfo;
  /** The scenes of existing rooms that play it. */
  uses: SceneInfo[];
  /** What a light switched on in an idle room plays (settings). */
  isDefault: boolean;
}

/** Every part once: the built-ins in catalogue order, then yours. */
export const partEntries = (store: MlStore): PartEntry[] =>
  store.parts().map((part) => ({
    part,
    uses: store.usesOf(part.id),
    isDefault: store.config.settings.default_scene === part.id,
  }));

export interface PartFold {
  key: PartGroupKey;
  title: string;
  entries: PartEntry[];
}

export interface PartSections {
  yours: PartEntry[];
  /** Built-ins a scene uses, that you edited, or the switch-on default. */
  builtins: PartEntry[];
  /** The rest of the catalogue, one fold per category. */
  more: PartFold[];
}

export const partSections = (entries: readonly PartEntry[]): PartSections => {
  const yours: PartEntry[] = [];
  const builtins: PartEntry[] = [];
  const rest: PartEntry[] = [];
  for (const entry of entries) {
    if (!entry.part.builtin) yours.push(entry);
    else if (entry.uses.length || entry.part.edited || entry.isDefault) builtins.push(entry);
    else rest.push(entry);
  }
  const more = groupParts(rest.map((e) => e.part)).map(({ key, title, parts }) => ({
    key,
    title,
    entries: rest.filter((e) => parts.includes(e.part)),
  }));
  return { yours, builtins, more };
};

/** What keeps a part from being deleted: the scenes that play it and the switch-on default. */
export const partHolders = (store: MlStore, partId: string): string[] => [
  ...store.usesOf(partId).map((scene) => store.sceneLabel(scene)),
  ...(store.config.settings.default_scene === partId ? ["the switch-on default"] : []),
];

/** A menu with `sceneId` moved `by` places, kept inside the list. */
export const movedBy = (ids: readonly string[], sceneId: string, by: number): string[] => {
  const from = ids.indexOf(sceneId);
  if (from < 0) return [...ids];
  const to = Math.max(0, Math.min(ids.length - 1, from + by));
  const next = ids.filter((id) => id !== sceneId);
  next.splice(to, 0, sceneId);
  return next;
};
