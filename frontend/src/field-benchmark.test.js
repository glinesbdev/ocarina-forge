import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { FIELD_FLIGHT, createFlight, streamSettled, summarizeBenchmark } from "./field-benchmark.js";

test("the field loop uses 160 m sides and closes", () => {
  const points = FIELD_FLIGHT.points;
  assert.equal(points[0][0], 0);
  assert.equal(points[0][1], 0);
  assert.deepEqual(points[points.length - 1], [0, 0]);
  for (let index = 0; index < points.length - 2; index += 1) {
    const a = points[index];
    const b = points[index + 1];
    assert.equal(Math.hypot(b[0] - a[0], b[1] - a[1]), 160);
  }
  const flight = createFlight();
  let steps = 0;
  let pose = null;
  while (!flight.done && steps < 5000) {
    pose = flight.sample(1 / 60);
    steps += 1;
  }
  assert.equal(pose.done, true);
  assert.equal(pose.x, 0);
  assert.equal(pose.z, 0);
  assert.equal(pose.y, FIELD_FLIGHT.eyeY);
  assert.ok(steps > 60);
});

test("a settled stream is idle and a pending load is not", () => {
  assert.equal(streamSettled({ queued: 0, loading: 0, background: 0, pending: 0 }), true);
  assert.equal(streamSettled({ queued: 1, loading: 0, background: 0, pending: 0 }), false);
});

test("the summary reports avg and p95 and skips null gpu samples", () => {
  const summary = summarizeBenchmark([
    { submitMs: 10, updateMs: 4, draws: 20, batches: 8, heapBytes: 100, gpuMs: null, writeTex: 1 },
    { submitMs: 30, updateMs: 6, draws: 22, batches: 8, heapBytes: 300, gpuMs: 5, writeTex: 3 },
  ]);
  assert.equal(summary.frames, 2);
  assert.equal(summary.submitMs.avg, 20);
  assert.equal(summary.submitMs.p95, 30);
  assert.equal(summary.gpuMs.n, 1);
  assert.equal(summary.gpuMs.avg, 5);
  assert.equal(summary.heapBytes.avg, 200);
  assert.equal(summary.draws.avg, 21);
  assert.equal(summary.batches.avg, 8);
  assert.equal(summary.writeTex.avg, 2);
});

test("the benchmark stays off unless Profile or the query flag starts it", () => {
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(viewport, /Field benchmark/);
  assert.match(viewport, /field-loop-160m/);
  assert.match(viewport, /benchmarkRun \? stepBenchmark/);
  assert.doesNotMatch(viewport, /BundleGroup/);
  assert.doesNotMatch(viewport, /render-bundles/);
  assert.doesNotMatch(viewport, /blend-array/);
  assert.match(main, /get\("benchmark"\) === "1"/);
  assert.doesNotMatch(html, /id="show-profile"[^>]*checked/);
  assert.doesNotMatch(main, /ocarinaforge\.benchmark/);
});
