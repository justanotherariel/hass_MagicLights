// The number handling of the value and range bars, kept apart from their DOM.

export type Handle = 0 | 1;

/** A value rounded to the step's decimals: 0.1 + 0.2 reads "0.3". */
export const formatStep = (value: number, step: number): string => {
  const decimals = Math.min(4, (String(step).split(".")[1] ?? "").length);
  return String(Number(value.toFixed(decimals)));
};

/** `values` with handle `which` moved to `to`, snapped to `step` and kept `step` from the other. */
export const moveHandle = (
  values: readonly [number, number],
  which: Handle,
  to: number,
  { min, max, step }: { min: number; max: number; step: number },
): [number, number] => {
  const snapped = Math.round((to - min) / step) * step + min;
  const next: [number, number] = [values[0], values[1]];
  next[which] =
    which === 0
      ? Math.max(min, Math.min(snapped, values[1] - step))
      : Math.min(max, Math.max(snapped, values[0] + step));
  return next;
};
