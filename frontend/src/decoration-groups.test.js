import assert from "node:assert/strict";
import test from "node:test";

import {
  followDecoration,
  followersFor,
  resolveDecorationGroups,
  transformPoint,
} from "./decoration-groups.js";

const HOUSE = "0x10";
const GROUND = "0x20";
const IVY = "0x30";
const ROOF = "0x31";
const FIELD = "0x32";
const FERN = "0x33";
const MUSHROOM = "0x34";
const CLIFF = "0x40";

const HOUSE_BOUNDS = { min: [-4, -1, -4], max: [4, 5, 4] };
const GROUND_BOUNDS = { min: [-50, -1, -50], max: [50, 1, 50] };
const CLIFF_BOUNDS = { min: [-4, -1, -4], max: [4, 5, 4] };

function actor(id, gyaml, translate, extra = {}) {
  return {
    id,
    gyaml,
    translate,
    rotate: extra.rotate || [0, 0, 0],
    scale: extra.scale || [1, 1, 1],
    status: "vanilla",
    layer: extra.layer,
    volume: Boolean(extra.volume),
    region: Boolean(extra.region),
  };
}

function scene() {
  return [
    actor(HOUSE, "Land_House_A_01", [10, 0, 10]),
    actor(GROUND, "Land_Kokiri_Ground", [0, 0, 0]),
    actor(IVY, "Land_IvyClimbable_A_01", [12, 3, 10], { rotate: [1.5708, -1.29154, 0] }),
    actor(ROOF, "Obj_Grass_A", [10, 0.2, 10]),
    actor(FIELD, "Obj_Grass_B", [40, 0, 40]),
    actor(FERN, "Obj_Fern_A", [0, 0, 0]),
    actor(MUSHROOM, "Obj_Mushroom_A", [200, 0, 200]),
    actor(CLIFF, "Land_Kokiri_Shrine", [80, 0, 80]),
  ];
}

function boundsFor(row) {
  if (row.id === HOUSE) return HOUSE_BOUNDS;
  if (row.id === GROUND) return GROUND_BOUNDS;
  if (row.id === CLIFF) return CLIFF_BOUNDS;
  return null;
}

test("ivy tip hangs down, matching the banc rotate", () => {
  const tip = transformPoint([0, 0, 3], {
    translate: [29.5, 109, -21.75],
    rotate: [1.5708, -1.29154, 0],
    scale: [1, 1, 1],
  });
  assert.ok(Math.abs(tip[0] - 29.5) < 0.02, `x ${tip[0]}`);
  assert.ok(Math.abs(tip[1] - 106) < 0.02, `y ${tip[1]}`);
  assert.ok(Math.abs(tip[2] - -21.75) < 0.02, `z ${tip[2]}`);
});

test("smallest box wins and a shared fern name does not", () => {
  const resolved = resolveDecorationGroups(scene(), { boundsFor });
  assert.equal(resolved.childOf.get(IVY), HOUSE);
  assert.equal(resolved.childOf.get(ROOF), HOUSE);
  assert.equal(resolved.childOf.get(FIELD), GROUND);
  assert.equal(resolved.childOf.get(FERN), GROUND);
  assert.equal(resolved.reason.get(MUSHROOM), "standalone");
  assert.deepEqual(followersFor(HOUSE, resolved).sort(), [IVY, ROOF].sort());
  assert.deepEqual(followersFor(GROUND, resolved).sort(), [FIELD, FERN].sort());
  assert.deepEqual(followersFor(FERN, resolved), []);
  assert.deepEqual(followersFor(CLIFF, resolved), []);
});

test("include wins and exclude drops the automatic group", () => {
  const resolved = resolveDecorationGroups(scene(), {
    boundsFor,
    include: { [IVY]: CLIFF },
    exclude: [ROOF],
  });
  assert.equal(resolved.childOf.get(IVY), CLIFF);
  assert.equal(resolved.reason.get(IVY), "override");
  assert.equal(resolved.reason.get(ROOF), "excluded");
  assert.ok(!resolved.childOf.has(ROOF));
});

test("a volume is not a parent until its mesh bounds exist", () => {
  const rows = [
    actor("town", "Land_Town", [0, 0, 0], { scale: [20, 1, 20], volume: true }),
    actor("flower", "Obj_Flower_A", [0, 0, 0]),
  ];
  const bare = resolveDecorationGroups(rows);
  assert.equal(bare.reason.get("flower"), "standalone");
  const bounded = resolveDecorationGroups(rows, {
    boundsFor: (row) => (row.id === "town" ? { min: [-2, -1, -2], max: [2, 1, 2] } : null),
  });
  assert.equal(bounded.childOf.get("flower"), "town");
});

test("a parent yaw moves the decoration and leaves its scale", () => {
  const beforeParent = { translate: [0, 0, 0], rotate: [0, 0, 0], scale: [1, 1, 1] };
  const afterParent = { translate: [1, 0, 0], rotate: [0, Math.PI / 2, 0], scale: [4, 4, 4] };
  const beforeChild = { translate: [2, 0, 0], rotate: [0, 0, 0], scale: [1, 2, 1] };
  const next = followDecoration(beforeParent, afterParent, beforeChild);
  assert.ok(Math.abs(next.translate[0] - 1) < 1e-6, `x ${next.translate[0]}`);
  assert.ok(Math.abs(next.translate[1]) < 1e-6, `y ${next.translate[1]}`);
  assert.ok(Math.abs(next.translate[2] - -2) < 1e-6, `z ${next.translate[2]}`);
  assert.ok(Math.abs(next.rotate[1] - Math.PI / 2) < 1e-6, `yaw ${next.rotate[1]}`);
  assert.deepEqual(next.scale, [1, 2, 1]);
});

test("a plain slide keeps the decoration's rotation", () => {
  const next = followDecoration(
    { translate: [10, 0, 10], rotate: [0, 0.4, 0], scale: [1, 1, 1] },
    { translate: [12, 1, 9], rotate: [0, 0.4, 0], scale: [1, 1, 1] },
    { translate: [12, 3, 10], rotate: [1.5708, -1.29154, 0], scale: [1, 1, 1] },
  );
  assert.ok(Math.abs(next.translate[0] - 14) < 1e-6);
  assert.ok(Math.abs(next.translate[1] - 4) < 1e-6);
  assert.ok(Math.abs(next.translate[2] - 9) < 1e-6);
  assert.ok(Math.abs(next.rotate[0] - 1.5708) < 1e-6);
  assert.ok(Math.abs(next.rotate[1] - -1.29154) < 1e-6);
});
