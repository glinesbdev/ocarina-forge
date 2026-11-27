import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { planActorRefresh } from "./actors.js";

function actor(id, gyaml, translate) {
  return {
    id,
    gyaml,
    translate,
    rotate: [0, 0, 0],
    scale: [1, 1, 1],
    status: "stock",
    category: "object",
    name: id,
    pickable: true,
    companion: false,
    volume: false,
  };
}

test("moving one copy patches that actor and leaves the other copy alone", () => {
  const before = [actor("grass-a", "Obj_Grass", [0, 0, 0]), actor("grass-b", "Obj_Grass", [4, 0, 0]), actor("tree", "Obj_Tree", [2, 0, 1])];
  const after = [actor("grass-a", "Obj_Grass", [9, 2, 1]), actor("grass-b", "Obj_Grass", [4, 0, 0]), actor("tree", "Obj_Tree", [2, 0, 1])];
  const plan = planActorRefresh(before, after);
  assert.equal(plan.mode, "patch");
  assert.deepEqual(plan.actors.map((item) => item.id), ["grass-a"]);
});

test("undo can patch every moved actor without rebuilding an untouched copy", () => {
  const before = [actor("grass-a", "Obj_Grass", [9, 2, 1]), actor("rock", "Obj_Rock", [1, 0, 1]), actor("grass-b", "Obj_Grass", [4, 0, 0])];
  const after = [actor("grass-a", "Obj_Grass", [0, 0, 0]), actor("rock", "Obj_Rock", [3, 1, 0]), actor("grass-b", "Obj_Grass", [4, 0, 0])];
  const plan = planActorRefresh(before, after);
  assert.equal(plan.mode, "patch");
  assert.deepEqual(plan.actors.map((item) => item.id), ["grass-a", "rock"]);
});

test("a logic-only actor becoming a model rebuilds so the mesh is fetched", () => {
  const before = [{ ...actor("npc", "Npc_Kokiri_F_01", [0, 0, 0]), logicOnly: true, fetchModel: false, layer: "behavior" }];
  const after = [{ ...actor("npc", "Npc_Kokiri_F_01", [0, 0, 0]), logicOnly: false, fetchModel: true, layer: "models" }];
  assert.equal(planActorRefresh(before, after).mode, "rebuild");
});

test("an identical refresh keeps the batches", () => {
  const actors = [actor("tree", "Obj_Tree", [2, 0, 1])];
  assert.deepEqual(planActorRefresh(actors, actors.map((item) => ({ ...item }))), { mode: "keep" });
});

test("adding, removing, or swapping a model rebuilds", () => {
  const before = [actor("tree", "Obj_Tree", [0, 0, 0]), actor("rock", "Obj_Rock", [1, 0, 0])];
  assert.equal(planActorRefresh(before, before.slice(0, 1)).mode, "rebuild");
  assert.equal(planActorRefresh(before.slice(0, 1), before).mode, "rebuild");
  const swapped = [actor("tree", "Obj_Tree_B", [0, 0, 0]), actor("rock", "Obj_Rock", [1, 0, 0])];
  assert.equal(planActorRefresh(before, swapped).mode, "rebuild");
  assert.equal(planActorRefresh(null, before).mode, "rebuild");
});

test("a saved transform does not clear the actor batches", () => {
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  assert.match(main, /planActorRefresh\(previousActors, composed\.actors\)/);
  assert.match(main, /viewport\.patchActors\(plan\.actors\)/);
  const clear = viewport.slice(viewport.indexOf("function clearActors"), viewport.indexOf("function helperVisible"));
  assert.match(clear, /keepBatches/);
  assert.match(viewport, /clearActors\(\{ keepBatches: !reloadTerrain \}\)/);
  const rebuild = viewport.slice(viewport.indexOf("function rebuildBatches"), viewport.indexOf("function queueRebuild"));
  assert.doesNotMatch(rebuild, /clearBatches\(\)/);
  const attached = rebuild.indexOf("placeBatchMesh(mesh, batch.key)");
  assert.ok(attached >= 0 && attached < rebuild.lastIndexOf("removeFromParent"));
  const patch = viewport.slice(viewport.indexOf("function applyActorRecord"), viewport.indexOf("function fitBounds"));
  assert.match(patch, /function patchActors/);
  assert.match(patch, /syncInstanceMatrix\(entry\)/);
  assert.doesNotMatch(patch, /clearBatches\(/);
  assert.doesNotMatch(patch, /loadModels\(/);
  const reconcile = viewport.slice(viewport.indexOf("function reconcileActors"), viewport.indexOf("function report"));
  assert.doesNotMatch(reconcile, /clearActors\(/);
  assert.doesNotMatch(reconcile, /clearBatches\(/);
  const undo = main.slice(main.indexOf("function undo("), main.indexOf("function redo("));
  const redo = main.slice(main.indexOf("function redo("), main.indexOf("function removeRecipe"));
  assert.doesNotMatch(undo, /api\.scene/);
  assert.doesNotMatch(redo, /api\.scene/);
  assert.match(undo, /applyLocal\(/);
  assert.match(main, /viewport\.reconcileActors\(/);
});
