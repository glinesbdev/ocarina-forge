import assert from "node:assert/strict";
import test from "node:test";

import { formatProfileDetail, formatStatusLabel, keepRendering } from "./frame.js";

test("the view only redraws continuously during interaction", () => {
  assert.equal(keepRendering({}), false);
  assert.equal(keepRendering({ flying: true }), true);
  assert.equal(keepRendering({ damped: true }), true);
  assert.equal(keepRendering({ looking: true }), true);
  assert.equal(keepRendering({ dragging: true }), true);
  assert.equal(keepRendering({ loading: true }), false);
  assert.equal(keepRendering({ flying: false, loading: false }), false);
});

test("the status label keeps the backend, the revision, and the draw count", () => {
  assert.equal(
    formatStatusLabel({ backend: "WebGPU", build: "abcdef1", draws: 5600 }),
    "WebGPU · abcdef1 · 5600 draws",
  );
  assert.equal(
    formatStatusLabel({
      backend: "WebGL (WebGPU unavailable: adapter is null)",
      build: "abcdef1",
      draws: 10,
    }),
    "WebGL (WebGPU unavailable: adapter is null) · abcdef1 · 10 draws",
  );
  assert.equal(
    formatStatusLabel({ backend: "WebGPU", draws: 240, queued: 40, loading: 8, background: 3 }),
    "WebGPU · 240 draws · 40 queued · 8 loading · 3 background",
  );
  assert.equal(formatStatusLabel({ draws: 0 }), "0 draws");
});

test("the profile detail keeps the counters that used to sit on the stats line", () => {
  assert.deepEqual(
    formatProfileDetail({ rendering: false, fps: 60, instances: null, separate: 10553 }),
    ["idle", "10553 separate"],
  );
  assert.deepEqual(
    formatProfileDetail({ rendering: true, fps: 0, separate: 12 }),
    ["…", "12 separate"],
  );
  assert.deepEqual(
    formatProfileDetail({ rendering: true, fps: 58, instances: 4000, culled: 900, separate: 10553 }),
    ["58 fps", "4000 inst · 900 culled · 10553 separate"],
  );
  assert.deepEqual(
    formatProfileDetail({
      rendering: false,
      queued: 40,
      loading: 8,
      loaded: 12,
      convertMs: 1200.2,
      textureMs: 800,
      downloadMs: 40.4,
      uploadMs: 90,
      warmDone: 3,
      warmTotal: 11,
      server: "server 1 converted",
    }),
    [
      "idle",
      "40 queued · 8 loading · 12 loaded",
      "conv 1200.2 ms · tex 800 ms · dl 40.4 ms · up 90 ms",
      "warm 3/11",
      "server 1 converted",
    ],
  );
  assert.deepEqual(
    formatProfileDetail({
      rendering: false,
      queued: 0,
      loading: 0,
      loaded: 5,
      deferred: 2,
      backgroundLoading: 1,
    }),
    ["idle", "0 queued · 0 loading · 5 loaded · 3 background · 1 background loading"],
  );
});

test("unloaded layers are named on the status label", () => {
  assert.equal(
    formatStatusLabel({ backend: "WebGPU", draws: 2, layersOff: ["effects", "behavior"] }),
    "WebGPU · 2 draws · off: effects, behavior",
  );
  assert.equal(formatStatusLabel({ draws: 12, layersOff: [] }), "12 draws");
  assert.equal(
    formatStatusLabel({
      backend: "WebGPU",
      draws: 240,
      queued: 40,
      loading: 8,
      texturesDone: 120,
      texturesTotal: 450,
      layersOff: ["effects"],
    }),
    "WebGPU · 240 draws · 40 queued · 8 loading · textures 120/450 · off: effects",
  );
  assert.equal(formatStatusLabel({ draws: 12, texturesDone: 450, texturesTotal: 450 }), "12 draws");
  assert.equal(formatStatusLabel({ draws: 12, texturesDone: 0, texturesTotal: 0 }), "12 draws");
});
