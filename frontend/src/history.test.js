import assert from "node:assert/strict";
import test from "node:test";

import { blankHistory, editLabel, historyRows, popRedo, popUndo, pushEdit } from "./history.js";

function shot(id, y) {
  return { select: id, actors: {}, viewActors: [{ id, translate: [0, y, 0] }], recipes: [], companions: [] };
}

test("undo restores the previous shot and redo puts the edit back", () => {
  let history = blankHistory(shot("tree", 0));
  history = pushEdit(history, { label: "Move tree", shot: shot("tree", 2) });
  history = pushEdit(history, { label: "Hide tree", shot: shot("tree", 2) });
  const undone = popUndo(history);
  assert.equal(undone.entry.label, "Hide tree");
  assert.equal(undone.scene.viewActors[0].translate[1], 2);
  assert.equal(undone.history.undo.length, 1);
  assert.equal(undone.history.redo.length, 1);
  const again = popUndo(undone.history);
  assert.equal(again.scene.viewActors[0].translate[1], 0);
  const redone = popRedo(again.history);
  assert.equal(redone.entry.label, "Move tree");
  assert.equal(redone.entry.shot.viewActors[0].translate[1], 2);
  assert.equal(redone.scene.viewActors[0].translate[1], 0);
});

test("a lock sits on the stack and does not replace the scene shot", () => {
  let history = blankHistory(shot("tree", 0));
  history = pushEdit(history, { label: "Move tree", shot: shot("tree", 4) });
  history = pushEdit(history, { label: "Lock Areas", local: true, category: "area", before: "show", after: "lock" });
  const lock = popUndo(history);
  assert.equal(lock.entry.local, true);
  assert.equal(lock.scene, null);
  const move = popUndo(lock.history);
  assert.equal(move.scene.viewActors[0].translate[1], 0);
});

test("a new edit drops the redo tail", () => {
  let history = pushEdit(blankHistory(shot("tree", 0)), { label: "Move tree", shot: shot("tree", 1) });
  history = popUndo(history).history;
  history = pushEdit(history, { label: "Delete tree", shot: shot("tree", 0) });
  assert.equal(history.redo.length, 0);
  assert.equal(history.undo[0].label, "Delete tree");
});

test("the Changes list shows the latest edit first and then the redo tail", () => {
  let history = blankHistory(shot("tree", 0));
  history = pushEdit(history, { label: "Move tree", shot: shot("tree", 1) });
  history = pushEdit(history, { label: "Rotate tree", shot: shot("tree", 1) });
  history = popUndo(history).history;
  const rows = historyRows(history);
  assert.deepEqual(rows.map((row) => [row.place, row.entry.label, row.latest]), [
    ["done", "Move tree", true],
    ["redo", "Rotate tree", false],
  ]);
});

test("every edit kind gets a history label", () => {
  const actor = { name: "Fado", gyaml: "Npc_Kokiri_F_01", translate: [0, 0, 0], rotate: [0, 0, 0], scale: [1, 1, 1] };
  assert.equal(editLabel({ command: "transform", translate: [1, 0, 0], rotate: [0, 0, 0], scale: [1, 1, 1] }, actor), "Move Fado");
  assert.equal(editLabel({ command: "transform", translate: [0, 0, 0], rotate: [0, 1, 0], scale: [1, 1, 1] }, actor), "Rotate Fado");
  assert.equal(editLabel({ command: "transform", translate: [0, 0, 0], rotate: [0, 0, 0], scale: [2, 1, 1] }, actor), "Scale Fado");
  assert.equal(editLabel({ command: "transform", translate: [1, 0, 0], rotate: [0, 0, 0] }, actor, { gizmo: "rotate" }), "Rotate Fado");
  assert.equal(editLabel({ command: "set" }, actor), "Edit Fado");
  assert.equal(editLabel({ command: "set_name" }, actor), "Rename Fado");
  assert.equal(editLabel({ command: "set_gyaml" }, actor), "Change Fado");
  assert.equal(editLabel({ command: "add", actor: "Obj_Tree" }, null), "Add Obj_Tree");
  assert.equal(editLabel({ command: "duplicate" }, actor), "Duplicate Fado");
  assert.equal(editLabel({ command: "remove" }, actor), "Delete Fado");
  assert.equal(editLabel({ command: "hide" }, actor), "Hide Fado");
  assert.equal(editLabel({ command: "unhide" }, actor), "Show Fado");
  assert.equal(editLabel({ command: "revert" }, actor), "Revert Fado");
  assert.equal(editLabel({ command: "lock" }, null, { category: "Areas" }), "Lock Areas");
});
