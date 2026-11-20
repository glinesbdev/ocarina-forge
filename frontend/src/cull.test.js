import assert from "node:assert/strict";
import test from "node:test";

import { chooseLod, instanceLod, isTerrainProxy } from "./cull.js";

test("a selected actor keeps the full mesh", () => {
  assert.equal(chooseLod({ distance: 400, radius: 0.4, pixels: 1, hasFar: true, selected: true }), 0);
});

test("small props drop out before trees do", () => {
  assert.equal(chooseLod({ distance: 40, radius: 0.8, pixels: 12, hasFar: false, selected: false }), 0);
  assert.equal(chooseLod({ distance: 60, radius: 0.8, pixels: 12, hasFar: false, selected: false }), null);
  assert.equal(chooseLod({ distance: 100, radius: 2.4, pixels: 20, hasFar: true, selected: false }), null);
  assert.equal(chooseLod({ distance: 200, radius: 6, pixels: 30, hasFar: true, selected: false }), null);
});

test("the coarse mesh starts just past the actor", () => {
  assert.equal(chooseLod({ distance: 30, radius: 8, pixels: 80, hasFar: true, selected: false }), 0);
  assert.equal(chooseLod({ distance: 50, radius: 8, pixels: 40, hasFar: true, selected: false }), 1);
  assert.equal(chooseLod({ distance: 50, radius: 8, pixels: 40, hasFar: false, selected: false }), 0);
});

test("a tiny screen size hides a modest prop and keeps a huge one", () => {
  assert.equal(chooseLod({ distance: 12, radius: 4, pixels: 1, hasFar: false, selected: false }), null);
  assert.equal(chooseLod({ distance: 80, radius: 40, pixels: 1, hasFar: true, selected: false }), 1);
});

test("an instance outside the frustum or past the draw distance is dropped", () => {
  const near = { distance: 30, radius: 8, pixels: 80, hasFar: true, selected: false, inFrustum: true, maxDistance: 0 };
  assert.equal(instanceLod(near), 0);
  assert.equal(instanceLod({ ...near, inFrustum: false }), null);
  assert.equal(instanceLod({ ...near, distance: 500, maxDistance: 400 }), null);
  assert.equal(instanceLod({ ...near, distance: 500, maxDistance: 400, selected: true }), 0);
  assert.equal(instanceLod({ ...near, distance: 50 }), 1);
});

test("the terrain proxy is the untextured static map", () => {
  assert.equal(isTerrainProxy({ reason: "terrain proxy", status: "mesh" }), true);
  assert.equal(isTerrainProxy({ reason: "Flat colour. terrain proxy", textures: 0 }), true);
  assert.equal(isTerrainProxy({ reason: "", textures: 0, status: "mesh" }), false);
  assert.equal(isTerrainProxy({ reason: "a real albedo" }), false);
  assert.equal(isTerrainProxy({ reason: "terrain heightmap. UV repeats every 8 m (a guess)", status: "mesh" }), false);
  assert.equal(isTerrainProxy(null), false);
});
