import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CULL_HEAVY_MS,
  CULL_HEAVY_SHARE,
  CPU_OVER_GPU,
  OBJECTS_HEAVY,
  PROFILE_READY_FRAMES,
  average,
  buildCapture,
  countTextures,
  createProfileWindow,
  formatProfileHud,
  percentile,
  recommendNext,
  summarize,
} from "./frame-profile.js";

test("percentile uses nearest rank and skips an empty list", () => {
  assert.equal(percentile([], 95), null);
  assert.equal(average([]), null);
  assert.equal(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10);
  assert.equal(percentile([4], 95), 4);
  assert.equal(average([10, 30]), 20);
});

test("rolling averages ignore null gpu samples and keep a fixed window", () => {
  const window = createProfileWindow(3);
  window.push({ cpuMs: 10, gpuMs: null, cullMs: 1 });
  window.push({ cpuMs: 30, gpuMs: 5, cullMs: 2 });
  window.push({ cpuMs: 20, gpuMs: null, cullMs: 3 });
  window.push({ cpuMs: 40, gpuMs: 9, cullMs: 4 });
  const summary = window.summary();
  assert.equal(summary.frames, 3);
  assert.equal(summary.cpuMs.avg, 30);
  assert.equal(summary.cpuMs.n, 3);
  assert.equal(summary.gpuMs.n, 2);
  assert.equal(summary.gpuMs.avg, 7);
  assert.equal(summary.gpuMs.p95, 9);
  assert.equal(window.latest().cpuMs, 40);
  window.clear();
  assert.equal(window.summary().frames, 0);
  assert.equal(summarize([{ gpuMs: null }]).gpuMs.avg, null);
});

function samples(count, fields) {
  return Array.from({ length: count }, () => ({ ...fields }));
}

test("the next step follows the cpu, gpu, and cull split", () => {
  const ready = PROFILE_READY_FRAMES;
  const gpuBound = recommendNext(summarize(samples(ready, {
    cpuMs: 10, gpuMs: 10 * CPU_OVER_GPU, cullMs: 1, objects: 10, batches: 0,
  })), { gpuState: "ready" });
  assert.match(gpuBound, /overdraw/);
  assert.match(gpuBound, /far proxies/);

  const cull = Math.max(CULL_HEAVY_MS, 20 * CULL_HEAVY_SHARE);
  const cullBound = recommendNext(summarize(samples(ready, {
    cpuMs: 20, updateMs: 20 - cull, cullMs: cull, submitMs: 0, gpuMs: 4, objects: 10, batches: 10,
  })), { gpuState: "ready" });
  assert.match(cullBound, /spatial chunk/);

  const cpuBound = recommendNext(summarize(samples(ready, {
    cpuMs: 30, updateMs: 28, cullMs: 1, submitMs: 1, gpuMs: 10, objects: OBJECTS_HEAVY, batches: 0,
  })), { gpuState: "ready" });
  assert.match(cpuBound, /texture arrays/);

  const close = recommendNext(summarize(samples(ready, {
    cpuMs: 10, gpuMs: 10, cullMs: 1, objects: 4, batches: 4,
  })), { gpuState: "ready" });
  assert.match(close, /CPU and GPU are close/);

  const missing = recommendNext(summarize(samples(ready, { cpuMs: 10, cullMs: 1 })), { gpuState: "unsupported" });
  assert.match(missing, /unavailable/);
  const waiting = recommendNext(summarize(samples(ready, { cpuMs: 10 })), { gpuState: "pending" });
  assert.match(waiting, /Waiting for the first GPU timestamp/);
  const early = recommendNext(summarize(samples(3, { cpuMs: 10, gpuMs: 10 })), { gpuState: "ready" });
  assert.match(early, /Early read \(3 frames\)/);
});

test("the hud prints n/a when timestamps are unsupported", () => {
  const summary = summarize([{
    cpuMs: 12.24, updateMs: 2, cullMs: 4.04, submitMs: 6.2, frameMs: 50,
    objects: 40, batches: 12, subDraws: 100, draws: 140, triangles: 1600,
    meshes: 8, textures: 3, heapBytes: 512 * 1048576,
  }]);
  const text = formatProfileHud({
    backend: "WebGPU",
    mode: "none",
    gpuState: "unsupported",
    latest: summary.frames ? {
      cpuMs: 12.24, updateMs: 2, cullMs: 4.04, submitMs: 6.2, frameMs: 50,
      objects: 40, batches: 12, subDraws: 100, draws: 140, triangles: 1600,
      meshes: 8, textures: 3, heapBytes: 512 * 1048576,
    } : {},
    summary,
    recommendation: "GPU timestamps are unavailable on this adapter.",
  });
  assert.match(text, /profile · WebGPU · timestamps unsupported · 1 frames/);
  assert.match(text, /cpu 12\.2 ms · avg 12\.2 · p95 12\.2/);
  assert.match(text, /update 2\.0 · cull 4\.0 · submit 6\.2/);
  assert.match(text, /^gpu n\/a$/m);
  assert.match(text, /obj 40 · batches 12 · sub 100 · draws 140/);
  assert.match(text, /tris 1\.6k · meshes 8 · tex 3 · heap 512\.0 MB/);
  assert.match(text, /unavailable/);
});

test("keys and pick uploads stay off the draws line until a sample has them", () => {
  const summary = summarize([]);
  const withCounts = formatProfileHud({
    backend: "WebGPU",
    mode: "none",
    gpuState: "unsupported",
    latest: {
      objects: 40, batches: 12, subDraws: 100, draws: 140,
      batchKeys: 9, pickUploads: 0,
    },
    summary,
  });
  assert.match(withCounts, /obj 40 · batches 12 · sub 100 · draws 140 · keys 9 · pick 0/);
  const plain = formatProfileHud({
    backend: "WebGPU",
    mode: "none",
    gpuState: "unsupported",
    latest: { objects: 40, batches: 12, subDraws: 100, draws: 140 },
    summary,
  });
  assert.match(plain, /obj 40 · batches 12 · sub 100 · draws 140$/m);
  assert.doesNotMatch(plain, /keys /);
});

test("a capture is json and counts resident textures once per source", () => {
  const capture = buildCapture({
    backend: "WebGL",
    gpuMode: "webgl",
    gpuState: "ready",
    samples: [{ cpuMs: 4, gpuMs: 6, objects: 2, batches: 1 }],
    at: "2026-11-25T00:00:00.000Z",
  });
  assert.equal(capture.version, 1);
  assert.equal(capture.capturedAt, "2026-11-25T00:00:00.000Z");
  assert.equal(capture.gpuTiming, "webgl");
  assert.equal(JSON.parse(JSON.stringify(capture)).summary.gpuMs.avg, 6);
  const shared = { isTexture: true, source: { uuid: "src" }, uuid: "a" };
  const other = { isTexture: true, uuid: "b" };
  assert.equal(countTextures([
    { map: shared, userData: { mossMap: shared } },
    { map: other, color: 1 },
    null,
  ]), 2);
});

test("the hud carries the revision and the counters that used to be the stats line", () => {
  const text = formatProfileHud({
    backend: "WebGL (WebGPU unavailable: adapter is null)",
    revision: "abcdef1",
    mode: "webgl",
    gpuState: "ready",
    latest: { cpuMs: 4, gpuMs: 6, draws: 10, triangles: 1000 },
    summary: summarize([{ cpuMs: 4, gpuMs: 6, draws: 10, triangles: 1000 }]),
    detail: {
      rendering: true,
      fps: 19,
      instances: 9000,
      culled: 100,
      separate: 12,
      queued: 0,
      loading: 1,
      loaded: 8,
    },
    recommendation: "CPU and GPU are close.",
  });
  assert.match(text, /^profile · WebGL \(WebGPU unavailable: adapter is null\)/);
  assert.match(text, /^abcdef1$/m);
  assert.match(text, /^19 fps$/m);
  assert.match(text, /9000 inst · 100 culled · 12 separate/);
  assert.match(text, /0 queued · 1 loading · 8 loaded/);
});

test("the viewport hud and the memory report share the profile", () => {
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const profile = readFileSync(new URL("./frame-profile.js", import.meta.url), "utf8");
  const memory = readFileSync(new URL("./memory-report.js", import.meta.url), "utf8");
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(viewport, /createFrameTimer/);
  assert.match(viewport, /formatStatusLabel/);
  assert.match(viewport, /setShowProfile/);
  assert.doesNotMatch(viewport, /setShowStats/);
  assert.match(viewport, /__forgeProfile/);
  assert.match(viewport, /syncBatchVisibility\(\)/);
  assert.match(profile, /gpu n\/a/);
  assert.match(memory, /subDraws/);
  assert.match(main, /setShowProfile/);
  assert.doesNotMatch(main, /ocarinaforge\.stats/);
  assert.match(html, /id="show-profile"/);
  assert.equal(html.includes(`id="${["show", "stats"].join("-")}"`), false);
  assert.doesNotMatch(html, /id="show-profile"[^>]*checked/);
});
