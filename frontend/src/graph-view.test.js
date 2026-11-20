import assert from "node:assert/strict";
import test from "node:test";

import {
  boundsOf,
  categoryColor,
  connectionSpotlight,
  fitCamera,
  frameCamera,
  minimapLayout,
  nodeCategory,
  pinColor,
  pinKind,
  wirePath,
  zoomAt,
} from "./graph-view.js";

test("headers follow the node category", () => {
  assert.equal(nodeCategory({ type: "Command", name: "Command Root", id: "ainb:a#command:Root", entry: true }), "command");
  assert.equal(nodeCategory({ type: "Entry", entry: true, raw: "OnPlayerTalk", kind: "event" }), "entry");
  assert.equal(nodeCategory({ type: "Element_Selector", raw: "Element_Selector" }), "selector");
  assert.equal(nodeCategory({ type: "Element_Simultaneous", raw: "Element_Simultaneous" }), "sequence");
  assert.equal(nodeCategory({ type: "Element_Sequential", raw: "Element_Sequential" }), "sequence");
  assert.equal(nodeCategory({ raw: "Element_ModuleIF_Input_Bool", type: "Element_ModuleIF_Input_Bool" }), "subflow");
  assert.equal(nodeCategory({ kind: "event", type: "Action", raw: "Demo_Talk" }), "event");
  assert.equal(nodeCategory({ kind: "schedule", type: "builtin", raw: "game__aischedule__AIScheduleMoveTo" }), "schedule");
  assert.equal(nodeCategory({ type: "ASPlay", raw: "ASPlay", kind: "ainb" }), "action");
  assert.ok(categoryColor({ type: "Element_Selector" }).startsWith("#"));
});

test("pins keep a color for each type", () => {
  assert.equal(pinKind({ type: "bool" }), "bool");
  assert.equal(pinKind({ type: "int" }), "int");
  assert.equal(pinKind({ type: "float" }), "float");
  assert.equal(pinKind({ type: "string" }), "string");
  assert.equal(pinKind({ type: "vec3f" }), "vec3");
  assert.equal(pinKind({ type: "ptr" }), "pointer");
  assert.equal(pinKind({ type: "flow" }), "flow");
  assert.notEqual(pinColor({ type: "bool" }), pinColor({ type: "float" }));
  assert.notEqual(pinColor({ type: "flow" }), pinColor({ type: "string" }));
});

test("a hovered node lights its wires and a hovered wire lights both ends", () => {
  const edges = [
    { id: "e1", from: "a:out", to: "b:in", fromNode: "a", toNode: "b" },
    { id: "e2", from: "b:out", to: "c:in", fromNode: "b", toNode: "c" },
    { id: "e3", from: "d:out", to: "e:in", fromNode: "d", toNode: "e" },
  ];
  const node = connectionSpotlight(edges, { type: "node", id: "b" });
  assert.deepEqual(node.hot, ["e1", "e2"]);
  assert.deepEqual(node.inputs, ["a"]);
  assert.deepEqual(node.outputs, ["c"]);
  assert.equal(node.focus, "b");
  const wire = connectionSpotlight(edges, { type: "edge", id: "e1" });
  assert.deepEqual(wire.hot, ["e1"]);
  assert.deepEqual(wire.inputs, ["a"]);
  assert.deepEqual(wire.outputs, ["b"]);
  const pin = connectionSpotlight(edges, { type: "pin", id: "b:out", node: "b" });
  assert.deepEqual(pin.hot, ["e2"]);
  assert.deepEqual(pin.outputs, ["c"]);
  const shot = frameCamera({ x: 0, y: 0, zoom: 1 }, { width: 800, height: 600 }, { x: 100, y: 40, w: 200, h: 80 });
  assert.equal(shot.x, 800 / 2 - 200);
  assert.equal(shot.y, 600 / 2 - 80);
});

test("a wire is a cubic bezier", () => {
  const path = wirePath({ x: 10, y: 20 }, { x: 200, y: 80 });
  assert.match(path, /^M 10 20 C /);
  assert.match(path, /200 80$/);
});

test("zoom stays on the cursor", () => {
  const camera = { x: 10, y: 20, zoom: 1 };
  const next = zoomAt(camera, 100, 80, 2);
  const world = (point, shot) => ({ x: (point.x - shot.x) / shot.zoom, y: (point.y - shot.y) / shot.zoom });
  const before = world({ x: 100, y: 80 }, camera);
  const after = world({ x: 100, y: 80 }, next);
  assert.ok(Math.abs(before.x - after.x) < 1e-6);
  assert.ok(Math.abs(before.y - after.y) < 1e-6);
  assert.ok(next.zoom > camera.zoom);
});

test("fit centers a two-node graph in a large panel", () => {
  const positions = {
    a: { x: 8, y: 8, w: 248, h: 60 },
    b: { x: 368, y: 8, w: 248, h: 60 },
  };
  const bounds = boundsOf(positions);
  const view = { width: 1200, height: 700 };
  const camera = fitCamera(bounds, view);
  const left = bounds.x * camera.zoom + camera.x;
  const right = (bounds.x + bounds.width) * camera.zoom + camera.x;
  const top = bounds.y * camera.zoom + camera.y;
  const bottom = (bounds.y + bounds.height) * camera.zoom + camera.y;
  assert.ok(left > 40, `left ${left}`);
  assert.ok(view.width - right > 40, `right ${right}`);
  assert.ok(top > 40, `top ${top}`);
  assert.ok(view.height - bottom > 40, `bottom ${bottom}`);
  assert.ok(camera.zoom > 1);
  const map = minimapLayout(bounds, camera, view, { width: 168, height: 112 });
  assert.ok(map.frame.w > 0);
  assert.ok(map.frame.h > 0);
});
