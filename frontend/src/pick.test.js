import assert from "node:assert/strict";
import test from "node:test";

import { chooseHit } from "./pick.js";

test("a solid inside a volume is preferred", () => {
  assert.equal(chooseHit({ solids: ["fado"], volumes: ["area"], edges: [] }), "fado");
});

test("a volume face is selected only when nothing else is hit", () => {
  assert.equal(chooseHit({ solids: [], volumes: ["area"], edges: [] }), "area");
});

test("an edge click keeps the solid first and cycles to the volume", () => {
  const first = chooseHit({ solids: ["fado"], volumes: ["area"], edges: ["area"], selected: null });
  assert.equal(first, "fado");
  const next = chooseHit({
    solids: ["fado"],
    volumes: ["area"],
    edges: ["area"],
    selected: "fado",
    alt: true,
  });
  assert.equal(next, "area");
});

test("a repeated click cycles and then wraps", () => {
  const stack = { solids: ["tree", "sign"], volumes: [], edges: [], selected: "tree", repeat: true };
  assert.equal(chooseHit(stack), "sign");
  assert.equal(chooseHit({ ...stack, selected: "sign" }), "tree");
});

test("an empty ray selects nothing", () => {
  assert.equal(chooseHit({ solids: [], volumes: [], edges: [] }), null);
});
