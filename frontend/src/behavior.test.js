import assert from "node:assert/strict";
import test from "node:test";

import { displayName, graphHistory, historyStep, layoutGraph, nodeConnections, sharedFileLabel, visibleNodes } from "./behavior.js";
import { COL, NODE_W } from "./graph-view.js";

test("a shared schedule file is labeled with the pack it lives in", () => {
  const label = sharedFileLabel(
    { file: "NpcSecurityGuard.game__component__AIScheduleParam.bgyml", sharedFrom: "NpcSecurityGuard" },
    5,
  );
  assert.equal(label, "NpcSecurityGuard.game__component__AIScheduleParam.bgyml · 5 · shared from NpcSecurityGuard");
  assert.equal(sharedFileLabel({ file: "Fado.root.ainb" }, 3), "Fado.root.ainb · 3");
});

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
  assert.equal(laid.positions[spawn.id].x - laid.positions[flag.id].x, COL);
  assert.ok(COL - NODE_W >= 80);
  assert.ok(laid.positions[blank.id]);
  const again = layoutGraph(graph);
  assert.deepEqual(Object.keys(laid.positions), Object.keys(again.positions));
  assert.equal(laid.positions[flag.id].x, again.positions[flag.id].x);
  assert.equal(graph.nodes[0].id, "ainb:AI/Fado.ainb#0:HoldGameDataGetBool");
});

test("connections group each pin and mark a wire that leaves the view", () => {
  const read = { id: "a", name: "Read flag", pins: [{ id: "a:out", dir: "out", name: "Bool", type: "bool" }] };
  const gate = {
    id: "b",
    name: "Gate",
    pins: [
      { id: "b:in", dir: "in", name: "Logic", type: "bool" },
      { id: "b:out", dir: "out", name: "Then", type: "flow" },
    ],
  };
  const spawn = { id: "c", name: "Spawn", pins: [{ id: "c:in", dir: "in", name: "In", type: "flow" }] };
  const graph = {
    nodes: [read, gate, spawn],
    edges: [
      { id: "e1", from: "a:out", to: "b:in" },
      { id: "e2", from: "b:out", to: "c:in" },
    ],
  };
  const outside = nodeConnections(gate, graph, new Set(["b"]));
  assert.equal(outside.inputs[0].pin.name, "Logic");
  assert.equal(outside.inputs[0].rows[0].otherName, "Read flag");
  assert.equal(outside.inputs[0].rows[0].otherPin, "Bool");
  assert.equal(outside.inputs[0].rows[0].outside, true);
  assert.equal(outside.outputs[0].rows[0].outside, true);
  const inside = nodeConnections(gate, graph, new Set(["a", "b", "c"]));
  assert.equal(inside.outputs[0].rows[0].outside, false);
  assert.equal(inside.outputs[0].rows[0].edgeId, "e2");
});

test("connection jumps keep a back and forward history", () => {
  let history = { trail: [], ahead: [] };
  let current = { node: "a", entry: "a", file: "ai" };
  history = graphHistory(history, current, { node: "b", entry: "a", file: "ai" });
  current = { node: "b", entry: "a", file: "ai" };
  history = graphHistory(history, current, { node: "c", entry: "c", file: "ai" });
  assert.deepEqual(history.trail.map((point) => point.node), ["a", "b"]);
  assert.equal(history.ahead.length, 0);
  const back = historyStep(history, { node: "c", entry: "c", file: "ai" }, -1);
  assert.equal(back.current.node, "b");
  assert.equal(back.current.entry, "a");
  const forward = historyStep(back, back.current, 1);
  assert.equal(forward.current.node, "c");
  const same = graphHistory(forward, forward.current, forward.current);
  assert.equal(same.trail.length, forward.trail.length);
});

test("a file opens on its entry and search stays inside that file", () => {
  const root = { id: "cmd", source: "src:ai", entry: true, name: "Command Root", raw: "Root", pins: [] };
  const spawn = { id: "spawn", source: "src:ai", name: "Spawn actor", raw: "ActorLogicSimpleActor", pins: [] };
  const blank = { id: "blank", source: "src:ai", name: "Undecoded", raw: "node 3", decoded: false, pins: [] };
  const talk = { id: "talk", source: "src:event", name: "Talk", raw: "OnPlayerTalk", pins: [] };
  const graph = {
    open: "src:ai",
    sources: [
      { id: "src:ai", file: "Fado.root.ainb", count: 3, openEntry: "cmd", entries: [{ id: "cmd", name: "Command Root", raw: "Root" }] },
      { id: "src:event", file: "Fado.bfevfl", count: 1, openEntry: "talk", entries: [{ id: "talk", name: "Talk", raw: "OnPlayerTalk" }] },
    ],
    nodes: [root, spawn, blank, talk],
    edges: [{ from: "cmd", to: "spawn" }],
  };
  const opened = visibleNodes(graph, {});
  assert.equal(opened.source.id, "src:ai");
  assert.deepEqual(opened.nodes.map((node) => node.id).sort(), ["cmd", "spawn"]);
  const searched = visibleNodes(graph, { query: "talk" });
  assert.equal(searched.nodes.length, 0);
  assert.equal(searched.elsewhere.file, "Fado.bfevfl");
  const event = visibleNodes(graph, { file: "src:event" });
  assert.equal(event.nodes[0].name, "Talk");
});
