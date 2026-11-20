import assert from "node:assert/strict";
import test from "node:test";

import { displayName, layoutGraph, sharedFileLabel, visibleNodes } from "./behavior.js";

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
  assert.ok(laid.positions[spawn.id].x > laid.positions[flag.id].x);
  assert.ok(laid.positions[blank.id]);
  const again = layoutGraph(graph);
  assert.deepEqual(Object.keys(laid.positions), Object.keys(again.positions));
  assert.equal(laid.positions[flag.id].x, again.positions[flag.id].x);
  assert.equal(graph.nodes[0].id, "ainb:AI/Fado.ainb#0:HoldGameDataGetBool");
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
