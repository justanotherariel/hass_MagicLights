// A room's live facts in words: the home tile's status line and the room page's hero.
// Pure, tested without a DOM; the callers pass names, times and colour temperatures in.

import { elsewhereOwners, ownerScene, statusLine, type StatusNames, type TargetStatusView } from "../shared/status";
import type { SelectAttributes } from "../shared/types";
import { plural } from "../shared/util";

/** "4 lights", or "3 of 4 lights" while the scene covers only some of them. */
export const lightsCount = (attrs: SelectAttributes | undefined, total: number): string => {
  if (!attrs?.partial) return plural(total, "light");
  const running = Object.values(attrs.lights).filter((status) => status === "scene").length;
  return `${running} of ${plural(total, "light")}`;
};

/** "Hell · 80% · 4 lights", "Gedimmt · 30% · 1 light manual", "Off · 6 lights". */
export const roomLine = (view: TargetStatusView | undefined, names: StatusNames, lights: number): string => {
  const a = view?.attrs;
  if (!a || (a.status !== "scene" && a.status !== "off")) return statusLine(view, names);
  if (a.status === "off") return `Off · ${plural(lights, "light")}`;
  const parts = [a.scene_id === null ? "Trying a draft" : (a.scene_name ?? a.scene_id)];
  if (a.via) parts.push(`via ${names.target(a.via)}`);
  for (const owner of elsewhereOwners(a)) parts.push(`${names.target(owner.target_id)}: ${ownerScene(owner, names)}`);
  if (a.brightness != null) parts.push(`${a.brightness}%`);
  parts.push(a.manual.length ? `${plural(a.manual.length, "light")} manual` : lightsCount(a, lights));
  return parts.join(" · ");
};

export interface HeroContext {
  names: StatusNames;
  lights: number;
  lightName(entityId: string): string;
  /** "18:42" for an ISO time. */
  time(iso: string): string;
  /** The colour temperature the running scene shows now, when it follows the sun. */
  kelvinNow?: number;
}

export interface HeroText {
  title: string;
  facts: string[];
}

/** The manual lights by name while there is one, else their count. */
const manualText = (a: SelectAttributes, ctx: HeroContext): string =>
  a.manual.length === 1 ? `${ctx.lightName(a.manual[0])} manual` : `${plural(a.manual.length, "light")} manual`;

/** The hero's title and its one line of facts, for every status a room can be in. */
export const heroText = (view: TargetStatusView | undefined, ctx: HeroContext): HeroText => {
  const a = view?.attrs;
  if (!a) return { title: statusLine(view, ctx.names), facts: [] };
  const manual = a.manual.length ? [manualText(a, ctx)] : [];
  switch (a.status) {
    case "off":
      return { title: "Off", facts: [plural(ctx.lights, "light")] };
    case "manual": {
      const on = Object.values(a.lights).filter((status) => status === "on").length;
      return { title: "Manual", facts: [`${on} of ${plural(ctx.lights, "light")} on`, ...manual] };
    }
    case "mixed":
      return {
        title: "Several scenes",
        facts: [
          ...a.parts.map((part) => `${ctx.names.target(part.target_id)}: ${part.scene_name}`),
          lightsCount(a, ctx.lights),
          ...manual,
        ],
      };
    case "scene": {
      const facts: string[] = [];
      if (a.since) facts.push(`${a.motion ? "Playing" : "Running"} since ${ctx.time(a.since)}`);
      if (a.via) facts.push(`via ${ctx.names.target(a.via)}`);
      for (const owner of elsewhereOwners(a)) {
        facts.push(`${ctx.names.target(owner.target_id)}: ${ownerScene(owner, ctx.names)}`);
      }
      if (ctx.kelvinNow) facts.push(`now ${ctx.kelvinNow} K`);
      const count = lightsCount(a, ctx.lights);
      facts.push(...(manual.length ? [count, ...manual] : [`${count}, all following`]));
      return { title: a.scene_id === null ? "Trying a draft" : (a.scene_name ?? a.scene_id), facts };
    }
  }
};
