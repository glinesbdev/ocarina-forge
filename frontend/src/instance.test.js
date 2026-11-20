import assert from "node:assert/strict";
import test from "node:test";

import { actorLocked, actorShown, batchKey, canGizmo, canPick } from "./instance.js";

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

test("copies that share an albedo share a batch key", () => {
  const house = { image: "roof", alpha: "opaque", side: 0, tint: "ffffff", indexed: "idx", attributes: "position,uv" };
  assert.equal(batchKey(house), batchKey({ ...house }));
  assert.notEqual(batchKey(house), batchKey({ ...house, image: "wall" }));
});
