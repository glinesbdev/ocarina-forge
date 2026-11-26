import assert from "node:assert/strict";
import test from "node:test";

import { followDecoration } from "./decoration-groups.js";
import { groupToggle, marqueeIds, normalizeRect, toggleId } from "./selection.js";

test("shift toggles an id and keeps the others", () => {
  assert.deepEqual(toggleId(["house"], "ivy"), ["house", "ivy"]);
  assert.deepEqual(toggleId(["house", "ivy"], "ivy"), ["house"]);
  assert.deepEqual(toggleId(["house", "ivy"], "house"), ["ivy"]);
});

test("a marquee keeps actors whose screen box meets the rectangle", () => {
  const rect = normalizeRect(10, 10, 40, 40);
  const items = [
    { id: "inside", box: { minX: 12, minY: 12, maxX: 20, maxY: 20 } },
    { id: "overlap", box: { minX: 30, minY: 30, maxX: 80, maxY: 80 } },
    { id: "outside", box: { minX: 50, minY: 50, maxX: 60, maxY: 60 } },
    { id: "hidden", box: null },
  ];
  assert.deepEqual(marqueeIds(items, rect), ["inside", "overlap"]);
  assert.deepEqual(marqueeIds(items, rect, { additive: true, existing: ["kept", "inside"] }), ["kept", "inside", "overlap"]);
});

test("ctrl-click toggles decoration membership and leaves the parent selected", () => {
  const parent = { id: "house", gyaml: "Land_House_A_01", status: "vanilla" };
  const ivy = { id: "ivy", gyaml: "Land_IvyClimbable_A_01", status: "vanilla" };
  const tree = { id: "tree", gyaml: "Obj_Tree", status: "vanilla" };
  const resolved = { childOf: new Map([["ivy", "house"]]) };
  assert.deepEqual(groupToggle(parent, ivy, resolved), { childId: "ivy", parentId: null });
  assert.deepEqual(groupToggle(parent, ivy, { childOf: new Map() }), { childId: "ivy", parentId: "house" });
  assert.equal(groupToggle(parent, tree, resolved), undefined);
  assert.equal(groupToggle(ivy, parent, resolved), undefined);
});

test("a shared pivot yaw moves every selected actor around the center", () => {
  const before = { translate: [1, 0, 0], rotate: [0, 0, 0], scale: [1, 1, 1] };
  const after = { translate: [1, 0, 0], rotate: [0, Math.PI / 2, 0], scale: [1, 1, 1] };
  const left = followDecoration(before, after, { translate: [0, 0, 0], rotate: [0, 0, 0], scale: [1, 1, 1] });
  const right = followDecoration(before, after, { translate: [2, 0, 0], rotate: [0, 0.2, 0], scale: [2, 2, 2] });
  assert.ok(Math.abs(left.translate[0] - 1) < 1e-6);
  assert.ok(Math.abs(left.translate[2] - 1) < 1e-6);
  assert.ok(Math.abs(right.translate[0] - 1) < 1e-6);
  assert.ok(Math.abs(right.translate[2] - -1) < 1e-6);
  assert.deepEqual(right.scale, [2, 2, 2]);
});
