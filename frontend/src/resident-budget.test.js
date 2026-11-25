import assert from "node:assert/strict";
import test from "node:test";
import {
  RESIDENT_HEAP_HARD,
  RESIDENT_MODEL_SOFT_CAP,
  modelsToEvict,
  overResidentHardCap,
  overResidentSoftCap,
  uniqueResidentNames,
  isWorsePriority,
} from "./resident-budget.js";

test("uniqueResidentNames counts gyamls that still have mesh parts", () => {
  const names = uniqueResidentNames([
    { gyaml: "Tree", parts: [{ }] },
    { gyaml: "Tree", parts: [{ }] },
    { gyaml: "Rock", parts: [] },
    { gyaml: "Bush", parts: [{ }] },
    { gyaml: "", parts: [{ }] },
  ]);
  assert.deepEqual([...names].sort(), ["Bush", "Tree"]);
});

test("soft and hard caps trip on unique count or JS heap", () => {
  assert.equal(overResidentSoftCap(RESIDENT_MODEL_SOFT_CAP, 0), true);
  assert.equal(overResidentSoftCap(10, 0), false);
  assert.equal(overResidentHardCap(RESIDENT_MODEL_SOFT_CAP, 0), false);
  assert.equal(overResidentHardCap(10, RESIDENT_HEAP_HARD), true);
});

test("modelsToEvict drops the farthest names not in the keep set", () => {
  const evict = modelsToEvict(
    [
      { name: "Near", distance: 10 },
      { name: "Far", distance: 900 },
      { name: "Mid", distance: 400 },
      { name: "Kept", distance: 8000 },
    ],
    { keep: new Set(["Kept"]), count: 250, heapBytes: 0, targetCount: 248, maxEvict: 8 },
  );
  assert.deepEqual(evict, ["Far", "Mid"]);
});

test("modelsToEvict stops when the keep set covers everyone", () => {
  const evict = modelsToEvict(
    [{ name: "A", distance: 100 }, { name: "B", distance: 200 }],
    { keep: new Set(["A", "B"]), count: 400, heapBytes: RESIDENT_HEAP_HARD },
  );
  assert.deepEqual(evict, []);
});

test("isWorsePriority prefers a closer or lower-tier model", () => {
  assert.equal(isWorsePriority({ tier: 5, distance: 10 }, { tier: 1, distance: 100 }), true);
  assert.equal(isWorsePriority({ tier: 1, distance: 10 }, { tier: 1, distance: 100 }), false);
  assert.equal(isWorsePriority({ tier: 1, distance: 200 }, { tier: 1, distance: 100 }), true);
});
