import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CELL_METERS,
  aabbIntersectsFrustum,
  bundleEligible,
  cellFromBatchKey,
  cellOfPosition,
  createFlight,
  emptyBox,
  expandBox,
  groupedBatchKey,
  bundleBindingStamp,
  materialBatchKey,
  nominalCellBox,
  streamSettled,
  summarizeBenchmark,
} from "./render-bundles.js";

test("a 64 m cell is the floor of world XZ", () => {
  assert.equal(CELL_METERS, 64);
  assert.equal(cellOfPosition(0, 0), "0,0");
  assert.equal(cellOfPosition(63.9, -0.1), "0,-1");
  assert.equal(cellOfPosition(-192.39, 506.95), "-4,7");
  assert.equal(cellOfPosition(Number.NaN, 0), null);
});

test("static scenery is grouped per cell and a selection is loose", () => {
  const base = "arr:512|opaque|s0|cffffff|idx|position:3u";
  assert.equal(bundleEligible(base), true);
  assert.equal(bundleEligible(`${base}|water`), false);
  assert.equal(bundleEligible(`${base}|terrain`), false);
  assert.equal(bundleEligible(`${base}|ground`), false);
  assert.equal(bundleEligible(`${base}|h:box`), false);
  assert.equal(groupedBatchKey(base, { enabled: false, x: 70, z: 10 }), base);
  assert.equal(groupedBatchKey(base, { enabled: true, x: 70, z: 10 }), `${base}|cell:1,0`);
  assert.equal(groupedBatchKey(`${base}|water`, { enabled: true, x: 70, z: 10 }), `${base}|water`);
  const loose = groupedBatchKey(base, { enabled: true, x: 70, z: 10, loose: true, actor: "hash" });
  assert.equal(loose, `${base}|loose:hash`);
  assert.equal(bundleEligible(loose), false);
  assert.equal(cellFromBatchKey(`${base}|cell:1,0`), "1,0");
  assert.equal(cellFromBatchKey(`${base}|cell:1,0\u00001`), "1,0");
  assert.equal(materialBatchKey(`${base}|cell:-2,4`), base);
  assert.equal(materialBatchKey(loose), base);
});

test("a cell box is hidden only when it is fully outside a plane", () => {
  const box = nominalCellBox("0,0");
  assert.equal(box.minX, 0);
  assert.equal(box.maxX, 64);
  const lookingDownX = [{ nx: 1, ny: 0, nz: 0, constant: -100 }];
  assert.equal(aabbIntersectsFrustum(box, lookingDownX), false);
  const lookingAtOrigin = [{ nx: 1, ny: 0, nz: 0, constant: 10 }];
  assert.equal(aabbIntersectsFrustum(box, lookingAtOrigin), true);
  const grown = expandBox(emptyBox(), 10, 20, 30, 4);
  assert.equal(grown.minX, 6);
  assert.equal(grown.maxY, 24);
});

test("the field flight is deterministic and finishes the loop", () => {
  const flight = createFlight();
  const first = flight.sample(0);
  assert.equal(first.x, 0);
  assert.equal(first.z, 0);
  assert.equal(first.y, 190);
  const again = createFlight();
  assert.deepEqual(again.sample(1), flight.sample(1));
  let steps = 0;
  let done = flight.done;
  while (!done && steps < 400) {
    done = flight.sample(0.5).done;
    steps += 1;
  }
  assert.equal(done, true);
  assert.ok(steps > 10 && steps < 400);
});

test("streaming is settled only when every queue is empty", () => {
  assert.equal(streamSettled({ queued: 0, loading: 0, background: 0, pending: 0 }), true);
  assert.equal(streamSettled({ queued: 1, loading: 0, background: 0, pending: 0 }), false);
  assert.equal(streamSettled({ queued: 0, loading: 0, background: 2, pending: 0 }), false);
});

test("the viewport toggle defaults off and the benchmark is on the profile panel", () => {
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /id="render-bundles"/);
  assert.doesNotMatch(html, /id="render-bundles"[^>]*checked/);
  assert.match(viewport, /setRenderBundles/);
  assert.match(viewport, /Field benchmark/);
  assert.match(viewport, /new THREE\.BundleGroup/);
  assert.match(viewport, /summarizeBenchmark/);
  assert.match(main, /ocarinaforge\.renderBundles/);
  assert.match(main, /setRenderBundles/);
  assert.match(viewport, /bundlesSupported\(\)/);
  assert.match(viewport, /group\.static = false/);
  assert.match(viewport, /refreshBundleBindings/);
});

test("a bundle re-records when its texture or visibility changes", () => {
  const mesh = {
    visible: true,
    _matricesTexture: { uuid: "matrices-a" },
    _indirectTexture: { uuid: "indirect-a" },
    _colorsTexture: { uuid: "colors-a" },
  };
  const first = bundleBindingStamp(mesh);
  mesh._matricesTexture = { uuid: "matrices-b" };
  assert.notEqual(bundleBindingStamp(mesh), first);
  mesh._matricesTexture = { uuid: "matrices-a" };
  assert.equal(bundleBindingStamp(mesh), first);
  mesh.visible = false;
  assert.notEqual(bundleBindingStamp(mesh), first);
});

test("benchmark summary reports avg and p95 and skips null gpu samples", () => {
  const summary = summarizeBenchmark([
    { submitMs: 10, gpuMs: null, bundleRecords: 2, gpuDraws: 100 },
    { submitMs: 30, gpuMs: 5, bundleRecords: 0, gpuDraws: 80 },
    { submitMs: 20, gpuMs: 7, bundleRecords: 0, gpuDraws: 80 },
  ]);
  assert.equal(summary.frames, 3);
  assert.equal(summary.submitMs.avg, 20);
  assert.equal(summary.submitMs.p95, 30);
  assert.equal(summary.gpuMs.n, 2);
  assert.equal(summary.gpuMs.avg, 6);
  assert.equal(summary.bundleRecords.sum, 2);
});
