// Port of ha-form's field visibility (fe-2026.8 src/components/ha-form/conditions.ts).

import type { HaFormCondition, HaFormFieldCondition, HaFormSchema } from "./types";

type Data = Record<string, unknown> | undefined;

const isEmpty = (value: unknown): boolean =>
  value === undefined || value === null || value === "";

const matchFieldCondition = (condition: HaFormFieldCondition, data: Data): boolean => {
  const actual = data?.[condition.field];
  switch (condition.operator ?? "eq") {
    case "eq":
      return actual === condition.value;
    case "not_eq":
      return actual !== condition.value;
    case "in":
      return Array.isArray(condition.value) && condition.value.includes(actual);
    case "not_in":
      return Array.isArray(condition.value) && !condition.value.includes(actual);
    case "exists":
      return !isEmpty(actual);
    case "not_exists":
      return isEmpty(actual);
    default:
      return false;
  }
};

export const evaluateCondition = (condition: HaFormCondition, data: Data): boolean => {
  if ("condition" in condition) {
    switch (condition.condition) {
      case "and":
        return condition.conditions.every((c) => evaluateCondition(c, data));
      case "or":
        return condition.conditions.some((c) => evaluateCondition(c, data));
      case "not":
        return !condition.conditions.some((c) => evaluateCondition(c, data));
      default:
        return false;
    }
  }
  return matchFieldCondition(condition, data);
};

export const isFieldVisible = (schema: HaFormSchema, data: Data): boolean => {
  const { visible } = schema;
  if (visible === undefined || visible === true) return true;
  if (visible === false) return false;
  const conditions = Array.isArray(visible) ? visible : [visible];
  return conditions.every((condition) => evaluateCondition(condition, data));
};

// A hidden field counts as empty in the other fields' conditions, as in ha-form,
// which can hide more of them, so resolve the set to a fixpoint.
export const getHiddenFields = (schema: readonly HaFormSchema[], data: Data): Set<string> => {
  const hidden = new Set<string>();
  const evalData: Record<string, unknown> = { ...(data ?? {}) };
  let changed = true;
  while (changed) {
    changed = false;
    for (const field of schema) {
      if (hidden.has(field.name) || isFieldVisible(field, evalData)) continue;
      hidden.add(field.name);
      delete evalData[field.name];
      changed = true;
    }
  }
  return hidden;
};
