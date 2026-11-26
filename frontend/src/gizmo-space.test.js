import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeSpace,
  readStoredSpace,
  spaceForMode,
  toggleSpace,
  writeStoredSpace,
  GIZMO_SPACE_KEY,
} from "./gizmo-space.js";

function memory() {
  const data = new Map();
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(String(key), String(value));
    },
  };
}

test("move and rotate use the chosen space, and scale stays local", () => {
  assert.equal(spaceForMode("translate", "world"), "world");
  assert.equal(spaceForMode("rotate", "world"), "world");
  assert.equal(spaceForMode("translate", "local"), "local");
  assert.equal(spaceForMode("rotate", "local"), "local");
  assert.equal(spaceForMode("scale", "world"), "local");
  assert.equal(spaceForMode("scale", "local"), "local");
  assert.equal(spaceForMode("translate", "nope"), "world");
});

test("the space choice round-trips through storage", () => {
  const storage = memory();
  assert.equal(readStoredSpace(storage), "world");
  assert.equal(toggleSpace("world"), "local");
  writeStoredSpace(storage, toggleSpace("world"));
  assert.equal(storage.getItem(GIZMO_SPACE_KEY), "local");
  assert.equal(readStoredSpace(storage), "local");
  assert.equal(normalizeSpace("local"), "local");
  writeStoredSpace(null, "local");
});
