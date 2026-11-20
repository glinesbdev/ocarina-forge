import assert from "node:assert/strict";
import test from "node:test";

import { actorLocked, actorShown, applyActorMatrix, batchKey, canGizmo, canPick } from "./instance.js";

const open = { area: "show", system: "show", object: "show" };

test("a repeated model stays pickable until its category is hidden or locked", () => {
  const shown = actorShown({
    status: "same",
    category: "object",
    volume: false,
    showHidden: false,
    categories: open,
    hideVolumes: false,
  });
  assert.equal(shown, true);
  assert.equal(canPick({ shown, locked: false }), true);
  assert.equal(canGizmo({ id: "house", shown, locked: false }), true);

  const hidden = actorShown({
    status: "same",
    category: "object",
    volume: false,
    showHidden: false,
    categories: { ...open, object: "hide" },
    hideVolumes: false,
  });
  assert.equal(hidden, false);
  assert.equal(canPick({ shown: hidden, locked: false }), false);
  assert.equal(canGizmo({ id: "house", shown: hidden, locked: false }), false);

  const locked = actorLocked({ category: "object", categories: { ...open, object: "lock" }, companion: false, pickable: true });
  assert.equal(actorShown({
    status: "same",
    category: "object",
    volume: false,
    showHidden: false,
    categories: { ...open, object: "lock" },
    hideVolumes: false,
  }), true);
  assert.equal(canPick({ shown: true, locked }), false);
  assert.equal(canGizmo({ id: "house", shown: true, locked }), false);
});

test("hidden actors and volumes follow the stage-bar switches", () => {
  assert.equal(actorShown({
    status: "hidden",
    category: "npc",
    volume: false,
    showHidden: false,
    categories: open,
    hideVolumes: false,
  }), false);
  assert.equal(actorShown({
    status: "hidden",
    category: "npc",
    volume: false,
    showHidden: true,
    categories: open,
    hideVolumes: false,
  }), true);
  assert.equal(actorShown({
    status: "same",
    category: "area",
    volume: true,
    showHidden: false,
    categories: open,
    hideVolumes: true,
  }), false);
});

test("a companion that cannot be picked is locked, and a locked actor has no gizmo", () => {
  assert.equal(actorLocked({ category: "object", categories: open, companion: true, pickable: false }), true);
  assert.equal(actorLocked({ category: "object", categories: open, companion: true, pickable: true }), false);
  assert.equal(canGizmo({ id: "", shown: true, locked: false }), false);
});

test("dragging one copy writes that actor's matrix only", () => {
  const writes = [];
  const mesh = {
    setMatrixAt(index, matrix) {
      writes.push([index, matrix.slice()]);
    },
  };
  const other = {
    setMatrixAt(index, matrix) {
      writes.push(["other", index, matrix.slice()]);
    },
  };
  const moved = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 4, 0, 2, 1];
  const kept = [9];
  const slots = [
    { actor: "grass-a", index: 0, mesh, matrix: { copy(next) { this.value = next.slice(); } } },
    { actor: "grass-b", index: 1, mesh, matrix: { copy() { throw new Error("the other copy was written"); }, value: kept } },
    { actor: "grass-a", index: 3, mesh: other },
  ];
  assert.equal(applyActorMatrix(slots, "grass-a", moved), 2);
  assert.deepEqual(writes, [[0, moved], ["other", 3, moved]]);
  assert.deepEqual(slots[0].matrix.value, moved);
  assert.deepEqual(slots[1].matrix.value, kept);
  assert.equal(applyActorMatrix(slots, "grass-a", moved.concat(0).slice(0, 16).map((value, index) => index === 12 ? 8 : value)), 2);
  assert.equal(writes[2][0], 0);
  assert.equal(writes[2][1][12], 8);
  assert.equal(writes.some((write) => write[0] === 1), false);
});

test("a batched mesh has no instanceMatrix and the drag must not touch it", () => {
  const mesh = {
    setMatrixAt() {},
  };
  assert.equal(mesh.instanceMatrix, undefined);
  assert.doesNotThrow(() => applyActorMatrix([{ actor: "grass-a", index: 2, mesh }], "grass-a", [1]));
  mesh.instanceMatrix = { needsUpdate: false };
  applyActorMatrix([{ actor: "grass-a", index: 2, mesh }], "grass-a", [1]);
  assert.equal(mesh.instanceMatrix.needsUpdate, true);
});

test("copies that share an albedo share a batch key", () => {
  const house = { image: "roof", alpha: "opaque", side: 0, tint: "ffffff", indexed: "idx", attributes: "position,uv" };
  assert.equal(batchKey(house), batchKey({ ...house }));
  assert.notEqual(batchKey(house), batchKey({ ...house, image: "wall" }));
});
