import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { SHORTCUT_GROUPS, shortcutText } from "./shortcuts.js";

test("the controls doc is the shortcut table", () => {
  const doc = readFileSync(new URL("../../docs/controls.md", import.meta.url), "utf8");
  assert.equal(doc.trim(), shortcutText().trim());
});

test("shortcuts stay in the groups the editor shows", () => {
  assert.deepEqual(
    SHORTCUT_GROUPS.map((group) => group.label),
    ["Camera", "Selection and editing", "Gizmo", "Undo and redo", "Snap", "Search", "View toggles"],
  );
  const keys = SHORTCUT_GROUPS.flatMap((group) => group.items.map((item) => item.keys));
  for (const binding of ["Hover the view", "Right-drag", "Alt+left-drag", "Middle-drag", "Ctrl+K or Cmd+K", "?", "V", "G", "R"]) {
    assert.equal(keys.includes(binding), true, binding);
  }
});
