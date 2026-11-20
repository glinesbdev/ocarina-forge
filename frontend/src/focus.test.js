import assert from "node:assert/strict";
import test from "node:test";

import { canHoverFocus } from "./focus.js";

test("hover focuses the view unless a field or a dialog has focus", () => {
  assert.equal(canHoverFocus(null, false), true);
  assert.equal(canHoverFocus({ tagName: "BUTTON" }, false), true);
  assert.equal(canHoverFocus({ tagName: "CANVAS" }, false), true);
  assert.equal(canHoverFocus({ tagName: "INPUT" }, false), false);
  assert.equal(canHoverFocus({ tagName: "INPUT", type: "search" }, false), false);
  assert.equal(canHoverFocus({ tagName: "INPUT", type: "number" }, false), false);
  assert.equal(canHoverFocus({ tagName: "INPUT", type: "checkbox" }, false), true);
  assert.equal(canHoverFocus({ tagName: "TEXTAREA" }, false), false);
  assert.equal(canHoverFocus({ tagName: "SELECT" }, false), false);
  assert.equal(canHoverFocus({ tagName: "DIV", isContentEditable: true }, false), false);
  assert.equal(canHoverFocus({ tagName: "BUTTON" }, true), false);
  assert.equal(canHoverFocus(null, true), false);
});
