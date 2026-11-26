import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  chooseSnap,
  dragPlanePoint,
  groundReplyApplies,
  isExplicitLift,
  isMovedTerrainActor,
  isSameActor,
  isSnapSelfHit,
  nearestGroundSample,
  shouldApplyGroundReply,
  shouldSnapTranslate,
  snapExcludeId,
  translateAfterSnap,
} from "./snap.js";

test("the nearest surface under the actor wins, and a shelf above does not beat a floor below", () => {
  assert.equal(chooseSnap(3, [{ y: 0 }, { y: 1.2 }]), 1.2);
  assert.equal(chooseSnap(2, [{ y: 0 }, { y: 10 }]), 0);
  assert.equal(chooseSnap(2, [{ y: 0 }, { y: 3.2 }]), 0);
});

test("a pivot under the floor snaps up to the nearest surface", () => {
  assert.equal(chooseSnap(-2, [{ y: 0 }, { y: 4 }]), 0);
});

test("a ceiling far above the actor is not a snap, and a raised drag plane moves X and Z", () => {
  assert.equal(chooseSnap(20, [{ y: 2.5 }, { y: 95.0245 }]), 2.5);
  assert.equal(chooseSnap(20, [{ y: 95.0245 }]), null);
  assert.equal(chooseSnap(94, [{ y: 95.0245 }]), 95.0245);
  const drag = [6, 20, -4];
  const locked = [0, 95.0245, -14.9636];
  const direction = [locked[0] - drag[0], locked[1] - drag[1], locked[2] - drag[2]];
  const onDrag = dragPlanePoint(drag, direction, drag[1]);
  const onCeiling = dragPlanePoint(drag, direction, locked[1]);
  assert.ok(onDrag.every((value, index) => Math.abs(value - drag[index]) < 1e-6));
  assert.ok(onCeiling.every((value, index) => Math.abs(value - locked[index]) < 1e-6));
  const sideways = translateAfterSnap([6, 20, -4], 2.5, { axis: "XZ", phase: "move", snapOn: true });
  assert.deepEqual(sideways.translate, [6, 2.5, -4]);
});

test("a companion banc id is the same actor as the bare hash", () => {
  const hash = "0x1000000000000032";
  const qualified = `Banc/BossRush/BossRush_InternalRoom_Static.bcett.byml.zs::${hash}`;
  assert.equal(isSameActor(qualified, hash), true);
  assert.equal(isSameActor(hash, qualified), true);
  assert.equal(isSnapSelfHit(qualified, hash), true);
  assert.equal(isSameActor(hash, "0x1000000000000033"), false);
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

test("an explicit Y lift keeps the gizmo, the model, and the saved height together", () => {
  const lifted = [2, 18, -4];
  const ground = 3.5;
  assert.equal(isExplicitLift("Y"), true);
  assert.equal(shouldSnapTranslate("Y", "move"), false);
  assert.equal(shouldSnapTranslate("Y", "release"), false);
  const during = translateAfterSnap(lifted, ground, { axis: "Y", phase: "move", snapOn: true });
  assert.equal(during.snapped, false);
  assert.equal(during.gizmo, during.visible);
  assert.deepEqual(during.visible, lifted);
  assert.equal(during.stored, null);
  const released = translateAfterSnap(lifted, ground, { axis: "Y", phase: "release", snapOn: true });
  assert.equal(released.gizmo, released.visible);
  assert.equal(released.visible, released.stored);
  assert.deepEqual(released.stored, lifted);
  assert.equal(released.snapped, false);
});

test("a sideways snap writes the ground into the gizmo and the model together", () => {
  const start = [2, 18, -4];
  for (const axis of ["X", "Z", "XZ"]) {
    assert.equal(shouldSnapTranslate(axis, "move"), true);
    const during = translateAfterSnap(start, 6, { axis, phase: "move", snapOn: true });
    assert.equal(during.snapped, true);
    assert.equal(during.gizmo, during.visible);
    assert.deepEqual(during.visible, [2, 6, -4]);
    assert.equal(during.stored, null);
  }
  for (const axis of ["X", "Z", "XZ", "XY", "YZ", "XYZ"]) {
    assert.equal(shouldSnapTranslate(axis, "release"), true);
    const released = translateAfterSnap(start, 6, { axis, phase: "release", snapOn: true });
    assert.equal(released.visible, released.stored);
    assert.equal(released.gizmo, released.visible);
    assert.deepEqual(released.stored, [2, 6, -4]);
  }
  const held = translateAfterSnap(start, 6, { axis: "XZ", phase: "move", snapOn: false });
  assert.deepEqual(held.visible, start);
  const missed = translateAfterSnap(start, null, { axis: "XZ", phase: "release", snapOn: true });
  assert.equal(missed.gizmo, missed.visible);
  assert.equal(missed.stored, missed.visible);
  assert.deepEqual(missed.stored, start);
});

test("the dragged actor is excluded from its own snap, including Land_ pieces", () => {
  assert.equal(snapExcludeId({ id: "0x10", gyaml: "Land_LakeHrl_Island_E_05" }), "0x10");
  assert.equal(snapExcludeId({ id: "0x11", gyaml: "Obj_Tree_A_01" }), "0x11");
  assert.equal(snapExcludeId({ id: "0x12", gyaml: "AirWall_Collision" }), "0x12");
  assert.equal(isMovedTerrainActor("Land_LakeHrl_Island_E_05"), true);
  assert.equal(snapExcludeId({ gyaml: "Land_Kokiri_Ground" }), "");
  assert.equal(snapExcludeId(null), "");
  assert.equal(isSnapSelfHit("0x10", "0x10"), true);
  assert.equal(isSnapSelfHit(16, "16"), true);
  assert.equal(isSnapSelfHit("0x11", "0x10"), false);
  assert.equal(isSnapSelfHit("0x10", ""), false);
  assert.equal(isSnapSelfHit("", "0x10"), false);
});

test("the viewport publishes one pose for the gizmo, the mesh, and the save", () => {
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const apply = viewport.slice(
    viewport.indexOf("function applySelectedActorTransform"),
    viewport.indexOf("function applyComposedSnap"),
  );
  assert.match(apply, /syncPreviewPlacement\(entry\)/);
  assert.match(apply, /syncInstanceMatrix\(entry\)/);
  assert.ok(apply.indexOf("syncInstanceMatrix(entry)") < apply.indexOf("hooks.onPreview"));
  assert.match(apply, /hooks\.onTransform/);
  assert.match(apply, /actorTransformFollowers/);
  const move = viewport.slice(
    viewport.indexOf('transform.addEventListener("objectChange"'),
    viewport.indexOf('transform.addEventListener("dragging-changed"'),
  );
  assert.match(move, /maybeSnap\(entry, "move"\)/);
  assert.match(move, /applySelectedActorTransform\(entry, \{ commit: false \}\)/);
  assert.ok(move.indexOf('maybeSnap(entry, "move")') < move.indexOf("applySelectedActorTransform"));
  const release = viewport.slice(
    viewport.indexOf('transform.addEventListener("dragging-changed"'),
    viewport.indexOf("function onFlyKey"),
  );
  assert.match(release, /maybeSnap\(entry, "release", axis\)/);
  assert.match(release, /applySelectedActorTransform\(entry, \{ commit: true \}\)/);
  assert.ok(release.indexOf('maybeSnap(entry, "release", axis)') < release.indexOf("applySelectedActorTransform"));
  assert.match(viewport, /snapExcludeId\(entry\)/);
  assert.match(viewport, /isSnapSelfHit\(candidate\.id, ignoreId\)/);
  assert.match(viewport, /commitTranslateDrag\(/);
  assert.match(viewport, /followActorTransform\(follow\)/);
  assert.match(viewport, /verticalHold/);
  const drag = readFileSync(new URL("./drag-commit.js", import.meta.url), "utf8");
  assert.match(drag, /translateAfterSnap\(/);
  assert.ok(drag.indexOf("position.y = posed.translate[1]") < drag.indexOf("write()"));
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

