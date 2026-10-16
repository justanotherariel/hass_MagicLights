import assert from "node:assert/strict";
import { test } from "node:test";
import { evaluateCondition, getHiddenFields, isFieldVisible } from "../src/shared/conditions";
import type { HaFormSchema } from "../src/shared/types";

test("field operators", () => {
  const data = { motion: "dynamic", speed: 0.5, empty: "" };
  assert.equal(evaluateCondition({ field: "motion", value: "dynamic" }, data), true);
  assert.equal(evaluateCondition({ field: "motion", operator: "not_eq", value: "dynamic" }, data), false);
  assert.equal(evaluateCondition({ field: "motion", operator: "in", value: ["static", "dynamic"] }, data), true);
  assert.equal(evaluateCondition({ field: "motion", operator: "not_in", value: ["static"] }, data), true);
  assert.equal(evaluateCondition({ field: "motion", operator: "in", value: "dynamic" }, data), false);
  assert.equal(evaluateCondition({ field: "speed", operator: "exists" }, data), true);
  assert.equal(evaluateCondition({ field: "empty", operator: "exists" }, data), false);
  assert.equal(evaluateCondition({ field: "missing", operator: "not_exists" }, data), true);
});

test("and / or / not", () => {
  const data = { a: 1, b: 2 };
  const a = { field: "a", value: 1 };
  const b = { field: "b", value: 3 };
  assert.equal(evaluateCondition({ condition: "and", conditions: [a, b] }, data), false);
  assert.equal(evaluateCondition({ condition: "or", conditions: [a, b] }, data), true);
  assert.equal(evaluateCondition({ condition: "not", conditions: [b] }, data), true);
  assert.equal(evaluateCondition({ condition: "not", conditions: [a, b] }, data), false);
});

test("visible: undefined, booleans and condition lists", () => {
  assert.equal(isFieldVisible({ name: "x" }, {}), true);
  assert.equal(isFieldVisible({ name: "x", visible: false }, {}), false);
  const both: HaFormSchema = { name: "x", visible: [{ field: "a", value: 1 }, { field: "b", value: 2 }] };
  assert.equal(isFieldVisible(both, { a: 1, b: 2 }), true);
  assert.equal(isFieldVisible(both, { a: 1 }), false);
});

test("hidden fields resolve to a fixpoint", () => {
  const schema: HaFormSchema[] = [
    { name: "motion" },
    { name: "speed", visible: { field: "motion", value: "dynamic" } },
    { name: "jitter", visible: { field: "speed", operator: "exists" } },
  ];
  assert.deepEqual([...getHiddenFields(schema, { motion: "static", speed: 0.6, jitter: 1 })], ["speed", "jitter"]);
  assert.equal(getHiddenFields(schema, { motion: "dynamic", speed: 0.6 }).size, 0);
});
