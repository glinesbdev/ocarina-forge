import assert from "node:assert/strict";
import test from "node:test";

import { chooseSnap, groundReplyApplies, isMovedTerrainActor, nearestGroundSample, shouldApplyGroundReply } from "./snap.js";

test("the highest surface under the actor wins, including a low curb", () => {
  assert.equal(chooseSnap(3, [{ y: 0 }, { y: 1.2 }]), 1.2);
  assert.equal(chooseSnap(2, [{ y: 0 }, { y: 10 }]), 0);
  assert.equal(chooseSnap(2, [{ y: 0 }, { y: 3.2 }]), 3.2);
});

test("a pivot under the floor snaps up to the nearest surface", () => {
  assert.equal(chooseSnap(-2, [{ y: 0 }, { y: 4 }]), 0);
});

test("no hit leaves the actor where it is", () => {
  assert.equal(chooseSnap(1, []), null);
  assert.equal(chooseSnap(Number.NaN, [{ y: 0 }]), null);
});

test("chooseSnap prefers the heightmap under the actor when a higher island is also a hit", () => {
  // This is why Snap must not mix the visible heightmap with /api/ground: once
  // Y drops to the lake-adjacent slope, the island (18 m up) is no longer a curb.
  assert.equal(chooseSnap(131.6071, [{ y: 113.3268 }, { y: 131.6071 }]), 131.6071);
  assert.equal(chooseSnap(113.3268, [{ y: 113.3268 }, { y: 131.6071 }]), 113.3268);
});

test("a cached ground sample from 1.4 m away is a different column", () => {
  const samples = [
    { x: -195.2242, z: 517.8778, y: 113.3268 },
    { x: -195.2242, z: 519.2956, y: 131.6071 },
  ];
  assert.equal(nearestGroundSample(samples, -195.2242, 519.2956).y, 131.6071);
  assert.equal(nearestGroundSample(samples, -195.2242, 519.2956, 1.5).y, 131.6071);
  assert.equal(nearestGroundSample([{ x: -195.2242, z: 517.8778, y: 113.3268 }], -195.2242, 519.2956), null);
  assert.equal(nearestGroundSample([{ x: -195.2242, z: 517.8778, y: 113.3268 }], -195.2242, 519.2956, 1.5).y, 113.3268);
});

test("a delayed ground reply is ignored after the pivot has moved to a new column", () => {
  assert.equal(groundReplyApplies(-195.2242, 517.8778, -195.2242, 519.2956), false);
  assert.equal(groundReplyApplies(-195.2242, 519.2956, -195.2242, 519.30), true);
  assert.equal(groundReplyApplies(-195.2242, 519.2956, -195.2242, 519.2956), true);
});

test("Land_ actors are excluded from their own snap query", () => {
  assert.equal(isMovedTerrainActor("Land_LakeHrl_CliffEdge_A_02"), true);
  assert.equal(isMovedTerrainActor("Land_LakeHrl_Island_E_05"), true);
  assert.equal(isMovedTerrainActor("Obj_Tree_A_01"), false);
  assert.equal(isMovedTerrainActor(""), false);
});

test("a ground reply applies to the latest requested column or the current pivot", () => {
  // Still on the requested column.
  assert.equal(shouldApplyGroundReply(-195.2242, 519.2956, -195.2242, 519.30, -195.2242, 519.2956), true);
  // Pivot stayed put; a newer request has not been issued.
  assert.equal(shouldApplyGroundReply(-195.2242, 519.2956, -195.2242, 519.2956, null, null), true);
  // Pivot already moved 1.4 m to a later column — this stale sample must not apply,
  // even if it was the latest request when it was sent.
  assert.equal(shouldApplyGroundReply(-195.2242, 517.8778, -195.2242, 519.2956, -195.2242, 517.8778), false);
  assert.equal(shouldApplyGroundReply(-195.2242, 517.8778, -195.2242, 519.2956, -195.2242, 519.2956), false);
});

