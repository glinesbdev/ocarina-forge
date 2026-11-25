import assert from "node:assert/strict";
import test from "node:test";

import { formatStats, keepRendering } from "./frame.js";

test("the view only redraws continuously during interaction", () => {
  assert.equal(keepRendering({}), false);
  assert.equal(keepRendering({ flying: true }), true);
  assert.equal(keepRendering({ damped: true }), true);
  assert.equal(keepRendering({ looking: true }), true);
  assert.equal(keepRendering({ dragging: true }), true);
  assert.equal(keepRendering({ loading: true }), false);
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
  assert.equal(
    formatStats({
      rendering: false,
      fps: 60,
      draws: 240,
      separate: 12,
      tris: 800,
      convertMs: 1200.2,
      textureMs: 800,
      downloadMs: 40.4,
      uploadMs: 90,
      warmDone: 3,
      warmTotal: 11,
    }),
    "idle · 240 draws · 12 separate · 800 tris · conv 1200.2 ms · tex 800 ms · dl 40.4 ms · up 90 ms · warm 3/11"
  );
});

test("the profile split is added to the stats line while the hud is open", () => {
  assert.equal(
    formatStats({
      backend: "WebGPU",
      rendering: true,
      fps: 19,
      frameMs: 50.3,
      cpuMs: 18.2,
      gpuMs: 31,
      gpuState: "ready",
      cullMs: 6.1,
      objects: 40,
      batchCount: 380,
      subDraws: 5200,
      draws: 5600,
      instances: 9000,
      culled: 100,
      separate: 12,
      tris: 80000,
    }),
    "WebGPU · 19 fps · 50.3 ms · cpu 18.2 · gpu 31 · cull 6.1 · 40 obj · 380 batches · 5200 sub · 5600 draws · 9000 inst · 100 culled · 12 separate · 80k tris",
  );
  assert.match(
    formatStats({ rendering: true, fps: 10, draws: 1, separate: 1, tris: 1, gpuState: "unsupported", cpuMs: 4 }),
    /cpu 4 · gpu n\/a · 1 draws/,
  );
  assert.match(
    formatStats({ rendering: false, draws: 1, separate: 1, tris: 1, gpuState: "pending" }),
    /gpu …/,
  );
});

test("background models are counted separately from active view loads", () => {
  assert.equal(
    formatStats({ draws: 2, separate: 0, tris: 20, queued: 0, loading: 0, loaded: 5, deferred: 2, backgroundLoading: 1 }),
    "idle · 2 draws · 0 separate · 20 tris · 0 queued · 0 loading · 5 loaded · 3 background remaining · 1 background loading"
  );
});
