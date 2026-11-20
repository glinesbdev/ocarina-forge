import assert from "node:assert/strict";
import test from "node:test";

import { LAYOUT_KEY, LAYOUT_VERSION, PANELS, panelIdForTab, readSavedLayout, tabForPanel } from "./dock.js";

test("the layout is stored under ocarinaforge.layout", () => {
  assert.equal(LAYOUT_KEY, "ocarinaforge.layout");
  assert.equal(LAYOUT_VERSION, 1);
});

test("a saved layout must be this version", () => {
  assert.equal(readSavedLayout(null), null);
  assert.equal(readSavedLayout(""), null);
  assert.equal(readSavedLayout("{"), null);
  assert.equal(readSavedLayout(JSON.stringify({ version: 2, dock: { grid: {} } })), null);
  assert.equal(readSavedLayout(JSON.stringify({ version: 1 })), null);
  const dock = { grid: { root: { type: "branch" } } };
  assert.deepEqual(readSavedLayout(JSON.stringify({ version: 1, dock })), dock);
});

test("the old side tabs open the docked panels", () => {
  assert.equal(panelIdForTab("outliner"), "actors");
  assert.equal(panelIdForTab("props"), "inspector");
  assert.equal(panelIdForTab("changes"), "edits");
  assert.equal(panelIdForTab("behavior"), "behavior");
  assert.equal(panelIdForTab("problems"), "problems");
  assert.equal(tabForPanel("actors"), "outliner");
  assert.equal(tabForPanel("inspector"), "props");
  assert.equal(tabForPanel("edits"), "changes");
  assert.equal(tabForPanel("behavior"), "behavior");
});

test("the default panels cover the workbench", () => {
  const ids = PANELS.map((panel) => panel.id);
  for (const id of ["viewport", "inspector", "behavior", "edits", "problems", "scenes", "actors", "stats"]) {
    assert.ok(ids.includes(id), id);
  }
});
