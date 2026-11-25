import assert from "node:assert/strict";
import test from "node:test";

import { guardPointerCapture } from "./pointer-capture.js";

test("viewport capture and release still call the browser with the correct receiver", () => {
  const calls = [];
  const canvas = {
    setPointerCapture(id) { calls.push([this, "capture", id]); },
    releasePointerCapture(id) { calls.push([this, "release", id]); },
  };
  guardPointerCapture(canvas);
  canvas.setPointerCapture(1);
  canvas.releasePointerCapture(1);
  assert.deepEqual(calls, [[canvas, "capture", 1], [canvas, "release", 1]]);
});

test("controls can keep handling pointer events when capture is unavailable", () => {
  const canvas = {
    setPointerCapture(id) {
      if (id === 1) throw new DOMException("Pointer capture is unavailable", "InvalidStateError");
      throw new TypeError("Unexpected input");
    },
    releasePointerCapture() { throw new DOMException("The pointer ended", "NotFoundError"); },
  };
  guardPointerCapture(canvas);
  assert.doesNotThrow(() => canvas.setPointerCapture(1));
  assert.doesNotThrow(() => canvas.releasePointerCapture(1));
  assert.throws(() => canvas.setPointerCapture(2), TypeError);
});
