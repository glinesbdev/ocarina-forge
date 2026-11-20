import assert from "node:assert/strict";
import test from "node:test";

import { formatStats, keepRendering } from "./frame.js";

test("the view keeps drawing only while something is moving or loading", () => {
  assert.equal(keepRendering({}), false);
  assert.equal(keepRendering({ flying: true }), true);
  assert.equal(keepRendering({ damped: true }), true);
  assert.equal(keepRendering({ looking: true }), true);
  assert.equal(keepRendering({ dragging: true }), true);
  assert.equal(keepRendering({ loading: true }), true);
  assert.equal(keepRendering({ flying: false, loading: false }), false);
});

test("a stopped view says idle and keeps the last counts", () => {
  assert.equal(
    formatStats({ rendering: false, fps: 60, draws: 240, separate: 10553, tris: 1600000 }),
    "idle · 240 draws · 10553 separate · 1.6M tris"
  );
  assert.equal(
    formatStats({ rendering: true, fps: 0, draws: 12, separate: 12, tris: 800 }),
    "… · 12 draws · 12 separate · 800 tris"
  );
  assert.equal(
    formatStats({ rendering: true, fps: 58, draws: 220, instances: 4000, culled: 900, separate: 10553, tris: 1400000 }),
    "58 fps · 220 draws · 4000 inst · 900 culled · 10553 separate · 1.4M tris"
  );
  assert.equal(
    formatStats({ rendering: false, fps: 60, draws: 240, separate: 10553, tris: 1600000, queued: 40, loading: 8, loaded: 12 }),
    "idle · 240 draws · 10553 separate · 1.6M tris · 40 queued · 8 loading · 12 loaded"
  );
});
