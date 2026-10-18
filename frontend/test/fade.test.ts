import assert from "node:assert/strict";
import { test } from "node:test";
import { FADE_DEFAULT, fadeChoices, fadeLabel, fadeSeconds, fadeValue } from "../src/panel/fade";

test("a stored step shows as that step, anything else as custom", () => {
  assert.equal(fadeLabel(1.0), "Smooth 1 s", "the user's stored 1.0");
  assert.equal(fadeLabel(0), "Instant");
  assert.equal(fadeLabel(2.5), "Custom 2.5 s");
  assert.equal(fadeValue(1.0), "1");
  assert.equal(fadeValue(0.4), "0.4");
  assert.equal(fadeValue(null), FADE_DEFAULT);
  assert.deepEqual(
    fadeChoices(1).map((c) => c.label),
    ["Instant", "Quick 0.4 s", "Smooth 1 s", "Slow 3 s"],
  );
  assert.deepEqual(fadeChoices(2.5).at(-1), { value: "2.5", label: "Custom 2.5 s" });
});

test("a scene's fade offers Default, which follows the setting", () => {
  const choices = fadeChoices(null, 0.4);
  assert.deepEqual(choices[0], { value: FADE_DEFAULT, label: "Default 0.4 s" });
  assert.equal(choices.length, 5);
  assert.equal(fadeSeconds(FADE_DEFAULT), null);
  assert.equal(fadeSeconds("0.4"), 0.4);
  assert.equal(fadeSeconds(fadeValue(3)), 3);
});
