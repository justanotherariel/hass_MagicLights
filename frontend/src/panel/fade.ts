// How long switching scenes takes, as a few named steps. A stored value that is a step
// shows as that step; any other shows as "Custom".

import type { Choice } from "./editor/form-rules";

export interface FadeStep {
  seconds: number;
  label: string;
  icon?: string;
}

export const FADE_STEPS: readonly FadeStep[] = [
  { seconds: 0, label: "Instant", icon: "mdi:flash" },
  { seconds: 0.4, label: "Quick 0.4 s" },
  { seconds: 1, label: "Smooth 1 s" },
  { seconds: 3, label: "Slow 3 s" },
];

/** The control value standing for "follow the setting" (a scene's null). */
export const FADE_DEFAULT = "default";

const stepOf = (seconds: number): FadeStep | undefined =>
  FADE_STEPS.find((step) => Math.abs(step.seconds - seconds) < 1e-6);

const format = (seconds: number): string => String(Math.round(seconds * 100) / 100);

/** "Quick 0.4 s", or "Custom 2.5 s". */
export const fadeLabel = (seconds: number): string => stepOf(seconds)?.label ?? `Custom ${format(seconds)} s`;

/** The control value of a stored fade. */
export const fadeValue = (seconds: number | null): string =>
  seconds === null ? FADE_DEFAULT : String(stepOf(seconds)?.seconds ?? seconds);

/** Segments for `seconds`; with `fallback` (the setting) a scene's "Default 1 s" comes first. */
export const fadeChoices = (seconds: number | null, fallback?: number): (Choice & { icon?: string })[] => {
  const choices: (Choice & { icon?: string })[] = FADE_STEPS.map((step) => ({
    value: String(step.seconds),
    label: step.label,
    icon: step.icon,
  }));
  if (seconds !== null && !stepOf(seconds)) choices.push({ value: String(seconds), label: fadeLabel(seconds) });
  if (fallback !== undefined) choices.unshift({ value: FADE_DEFAULT, label: `Default ${format(fallback)} s` });
  return choices;
};

/** The stored fade of a control value: null follows the setting. */
export const fadeSeconds = (value: string): number | null => (value === FADE_DEFAULT ? null : Number(value));
