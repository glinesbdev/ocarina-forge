import assert from "node:assert/strict";
import test from "node:test";
import {
  RESIDENT_BYTE_HARD,
  RESIDENT_BYTE_SOFT,
  RESIDENT_BYTE_TARGET,
  RESIDENT_HEAP_HARD,
  RESIDENT_MODEL_SOFT_CAP,
  RESIDENT_UNLOAD_COOLDOWN_FRAMES,
  cameraNeedsPriorityUpdate,
  isWorsePriority,
  modelsToEvict,
  overResidentHardCap,
  overResidentSoftCap,
  planEvictions,
  simulateFieldOrbit,
  uniqueResidentNames,
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

test("the soft cap is geometry plus BC7 bytes, with the mesh count as a safety cap", () => {
  assert.equal(overResidentSoftCap(10, 0, 0), false);
  assert.equal(overResidentSoftCap(10, 0, RESIDENT_BYTE_SOFT), true);
  assert.equal(overResidentSoftCap(RESIDENT_MODEL_SOFT_CAP, 0, 0), true);
  assert.equal(overResidentHardCap(10, 0, RESIDENT_BYTE_SOFT), false);
  assert.equal(overResidentHardCap(10, 0, RESIDENT_BYTE_HARD), true);
});

test("models behind the camera rank worse than the same distance ahead", () => {
  assert.equal(
    isWorsePriority({ tier: 1, distance: 100, behind: true }, { tier: 1, distance: 120, behind: false }),
    true,
  );
  assert.equal(
    isWorsePriority({ tier: 1, distance: 100, behind: false }, { tier: 1, distance: 140, behind: true }),
    false,
  );
  const evict = modelsToEvict(
    [
      { name: "Ahead", distance: 200, behind: false },
      { name: "Behind", distance: 150, behind: true },
    ],
    { count: 2, targetCount: 1, heapBytes: 0, assetBytes: 0 },
  );
  assert.deepEqual(evict, ["Behind"]);
});

test("a byte overrun evicts far geometry until the byte target is met", () => {
  const big = 800 * 1024 * 1024;
  const evict = modelsToEvict(
    [
      { name: "BigFar", distance: 500, bytes: big },
      { name: "SmallNear", distance: 10, bytes: 1024 * 1024 },
    ],
    {
      count: 10,
      heapBytes: 0,
      assetBytes: RESIDENT_BYTE_TARGET + big,
      targetCount: 240,
      targetBytes: RESIDENT_BYTE_TARGET,
    },
  );
  assert.deepEqual(evict, ["BigFar"]);
});

test("unload cooldown arms a model before it can leave, and a swing back can cancel it", () => {
  const candidates = [{ name: "Tree", distance: 400, bytes: 1024 * 1024 }];
  const options = {
    count: 250,
    heapBytes: 0,
    assetBytes: 0,
    targetCount: 200,
    cooldown: true,
    now: 10,
  };
  const armed = planEvictions(candidates, options);
  assert.deepEqual(armed.evict, []);
  assert.deepEqual(armed.arm, ["Tree"]);
  const cooling = planEvictions(
    [{ ...candidates[0], cooldownUntil: 10 + RESIDENT_UNLOAD_COOLDOWN_FRAMES }],
    { ...options, now: 10 + RESIDENT_UNLOAD_COOLDOWN_FRAMES - 1 },
  );
  assert.deepEqual(cooling.evict, []);
  const ready = planEvictions(
    [{ ...candidates[0], cooldownUntil: 10 }],
    { ...options, now: 10 },
  );
  assert.deepEqual(ready.evict, ["Tree"]);
  const emergency = planEvictions(candidates, { ...options, ignoreCooldown: true });
  assert.deepEqual(emergency.evict, ["Tree"]);
});

test("priority is recomputed after more than 2 m or 30 degrees", () => {
  const origin = { position: [0, 0, 0], direction: [0, 0, -1] };
  assert.equal(cameraNeedsPriorityUpdate(null, origin), true);
  assert.equal(cameraNeedsPriorityUpdate(origin, { position: [1.9, 0, 0], direction: [0, 0, -1] }), false);
  assert.equal(cameraNeedsPriorityUpdate(origin, { position: [2, 0, 0], direction: [0, 0, -1] }), false);
  assert.equal(cameraNeedsPriorityUpdate(origin, { position: [2.1, 0, 0], direction: [0, 0, -1] }), true);
  const turn20 = { position: [0, 0, 0], direction: [Math.sin((20 * Math.PI) / 180), 0, -Math.cos((20 * Math.PI) / 180)] };
  const turn31 = { position: [0, 0, 0], direction: [Math.sin((31 * Math.PI) / 180), 0, -Math.cos((31 * Math.PI) / 180)] };
  assert.equal(cameraNeedsPriorityUpdate(origin, turn20), false);
  assert.equal(cameraNeedsPriorityUpdate(origin, turn31), true);
});

test("orbiting a Field-sized set does not evict and reload the same models", () => {
  const orbit = simulateFieldOrbit();
  assert.ok(orbit.before.rapidReloads > orbit.after.rapidReloads);
  assert.equal(orbit.after.rapidReloads, 0);
  assert.ok(orbit.after.reloads < orbit.before.reloads);
  assert.ok(orbit.after.evictions < orbit.before.evictions);
  assert.ok(orbit.after.recomputes < orbit.before.recomputes);
  assert.ok(orbit.after.peakBytes < orbit.before.peakBytes);
  assert.ok(orbit.after.peakBytes <= RESIDENT_BYTE_SOFT);
  assert.ok(orbit.after.peakCount <= RESIDENT_MODEL_SOFT_CAP);
});
