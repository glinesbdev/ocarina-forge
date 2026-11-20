import assert from "node:assert/strict";
import test from "node:test";

import { displayName, layoutGraph } from "./behavior.js";

test("the raw layer keeps the file name and the curated layer uses the friendly one", () => {
  const node = { id: "ainb:a#0:HoldGameDataGetBool", raw: "HoldGameDataGetBool", name: "Read flag", decoded: true };
  assert.equal(displayName(node, "raw"), "HoldGameDataGetBool");
  assert.equal(displayName(node, "curated"), "Read flag");
  assert.equal(displayName({ id: "x", raw: "node 3", name: "Undecoded", decoded: false }, "raw"), "node 3");
});

test("a connected node sits to the right and a second layout keeps the same ids", () => {
  const flag = {
    id: "ainb:AI/Fado.ainb#0:HoldGameDataGetBool",
    source: "src:ai",
    index: 0,
    pins: [{ id: "ainb:AI/Fado.ainb#0:HoldGameDataGetBool:out:bool:0:Bool", dir: "out", name: "Bool" }],
  };
  const spawn = {
    id: "ainb:AI/Fado.ainb#1:ActorLogicSimpleActor",
    source: "src:ai",
    index: 1,
    pins: [{ id: "ainb:AI/Fado.ainb#1:ActorLogicSimpleActor:in:bool:0:Logic_Create", dir: "in", name: "Logic_Create" }],
  };
  const blank = { id: "ainb:AI/Fado.ainb#3:undecoded", source: "src:ai", index: 3, raw: "node 3", decoded: false, pins: [] };
  const graph = {
    sources: [{ id: "src:ai", title: "AI graph", path: "AI/Fado.ainb" }],
    nodes: [flag, spawn, blank],
    edges: [{ id: "e", from: flag.pins[0].id, to: spawn.pins[0].id }],
  };
  const laid = layoutGraph(graph);
  assert.ok(laid.positions[spawn.id].x > laid.positions[flag.id].x);
  assert.ok(laid.positions[blank.id]);
  const again = layoutGraph(graph);
  assert.deepEqual(Object.keys(laid.positions), Object.keys(again.positions));
  assert.equal(laid.positions[flag.id].x, again.positions[flag.id].x);
  assert.equal(graph.nodes[0].id, "ainb:AI/Fado.ainb#0:HoldGameDataGetBool");
});
