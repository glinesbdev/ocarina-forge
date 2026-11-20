import assert from "node:assert/strict";
import test from "node:test";

import { chooseSnap } from "./snap.js";

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
