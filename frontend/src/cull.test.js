import assert from "node:assert/strict";
import test from "node:test";

import { boundsGap, chooseLod, instanceLod, isTerrainProxy, resolveLod, transformSphere } from "./cull.js";

test("a selected actor keeps the full mesh", () => {
  assert.equal(chooseLod({ distance: 400, radius: 0.4, pixels: 1, hasFar: true, selected: true }), 0);
});

test("small props drop out before trees do", () => {
  assert.equal(chooseLod({ distance: 40, radius: 0.8, pixels: 12, hasFar: false, selected: false }), 0);
  assert.equal(chooseLod({ distance: 60, radius: 0.8, pixels: 12, hasFar: false, selected: false }), null);
  assert.equal(chooseLod({ distance: 100, radius: 2.4, pixels: 20, hasFar: false, selected: false }), null);
  assert.equal(chooseLod({ distance: 200, radius: 6, pixels: 30, hasFar: false, selected: false }), null);
});

test("a far mesh stays visible when the full mesh would have been hidden", () => {
  assert.equal(chooseLod({ distance: 100, radius: 2.4, pixels: 20, hasFar: true, selected: false }), 1);
  assert.equal(chooseLod({ distance: 200, radius: 6, pixels: 30, hasFar: true, selected: false }), 1);
  assert.equal(chooseLod({ distance: 800, radius: 6, pixels: 1, hasFar: true, selected: false }), 1);
  assert.equal(chooseLod({ distance: 800, radius: 6, pixels: 1, hasFar: false, selected: false }), null);
  assert.equal(instanceLod({
    distance: 500, radius: 8, pixels: 4, hasFar: true, selected: false, inFrustum: true, maxDistance: 0,
  }), 1);
  assert.equal(instanceLod({
    distance: 500, radius: 8, pixels: 4, hasFar: true, selected: false, inFrustum: true, maxDistance: 400,
  }), null);
  assert.equal(resolveLod(1, [0, 1]), 1);
  assert.equal(resolveLod(1, [0]), 0);
  assert.equal(resolveLod(null, [0, 1]), null);
});

test("the coarse mesh follows screen size", () => {
  assert.equal(chooseLod({ distance: 30, radius: 8, pixels: 80, hasFar: true, selected: false }), 0);
  assert.equal(chooseLod({ distance: 50, radius: 8, pixels: 80, hasFar: true, selected: false }), 0);
  assert.equal(chooseLod({ distance: 20, radius: 8, pixels: 40, hasFar: true, selected: false }), 1);
  assert.equal(chooseLod({ distance: 50, radius: 8, pixels: 40, hasFar: false, selected: false }), 0);
  assert.equal(chooseLod({ distance: 50, radius: 8, pixels: 999, hasFar: true, selected: false }), 1);
  assert.equal(chooseLod({ distance: 20, radius: 8, pixels: 999, hasFar: true, selected: false }), 0);
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
  assert.equal(instanceLod({ ...near, distance: 50, pixels: 40 }), 1);
  assert.equal(instanceLod({ ...near, distance: 50, pixels: 80 }), 0);
});

test("distance and frustum use the instance sphere, not the world origin", () => {
  assert.equal(boundsGap(210, 30), 180);
  assert.equal(boundsGap(12, 40), 0);
  assert.equal(boundsGap(0, 0), 0);
  const world = transformSphere([0, 0, 0], 2, [
    2, 0, 0, 0,
    0, 2, 0, 0,
    0, 0, 2, 0,
    10, 0, 4, 1,
  ]);
  assert.deepEqual(world.center, [10, 0, 4]);
  assert.equal(world.radius, 4);
  const shifted = transformSphere([1, 0, 0], 1, [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ]);
  assert.deepEqual(shifted.center, [1, 0, 0]);
  assert.notDeepEqual(shifted.center, [0, 0, 0]);
});

test("the terrain proxy is the untextured static map", () => {
  assert.equal(isTerrainProxy({ reason: "terrain proxy", status: "mesh" }), true);
  assert.equal(isTerrainProxy({ reason: "Flat colour. terrain proxy", textures: 0 }), true);
  assert.equal(isTerrainProxy({ reason: "", textures: 0, status: "mesh" }), false);
  assert.equal(isTerrainProxy({ reason: "a real albedo" }), false);
  assert.equal(isTerrainProxy({ reason: "terrain heightmap. UV repeats every 8 m (a guess)", status: "mesh" }), false);
  assert.equal(isTerrainProxy(null), false);
});
