import init, { WasmMeshIndex, decodeSwizzle } from "../pkg/ocarina_forge_native.js";
import { MeshIndex } from "./surface.js";
import { parseSurfaceFixture, parseTextureFixture } from "./fixture.js";

const status = document.getElementById("status");
const table = document.getElementById("results");
const isolated = document.getElementById("isolated");
const logEl = document.getElementById("log");

const results = {
  when: new Date().toISOString(),
  crossOriginIsolated: Boolean(globalThis.crossOriginIsolated),
  sharedArrayBuffer: typeof SharedArrayBuffer === "function",
  hardwareConcurrency: navigator.hardwareConcurrency || 1,
  userAgent: navigator.userAgent,
  rows: [],
  mismatches: {},
  memory: {},
  errors: [],
};

function log(line) {
  logEl.textContent += line + "\n";
}

function heap() {
  const mem = performance.memory;
  if (!mem) return null;
  return {
    usedMB: mem.usedJSHeapSize / 1048576,
    totalMB: mem.totalJSHeapSize / 1048576,
    limitMB: mem.jsHeapSizeLimit / 1048576,
  };
}

function setStatus(text) {
  status.textContent = text;
}

function addRow(row) {
  results.rows.push(row);
  const tr = document.createElement("tr");
  for (const key of ["name", "ms", "pointsPerSec", "maxAbsError", "notes"]) {
    const td = document.createElement("td");
    let value = row[key];
    if (key === "ms" && typeof value === "number") value = value.toFixed(2);
    if (key === "pointsPerSec" && typeof value === "number") value = Math.round(value).toLocaleString();
    if (key === "maxAbsError" && typeof value === "number") value = value.toExponential(2);
    td.textContent = value == null ? "—" : value;
    tr.appendChild(td);
  }
  table.querySelector("tbody").appendChild(tr);
}

function maxError(got, expected) {
  let max = 0;
  let compared = 0;
  let missing = 0;
  for (let i = 0; i < expected.length; i++) {
    const e = expected[i];
    const g = got[i];
    const eHit = Number.isFinite(e);
    const gHit = Number.isFinite(g);
    if (eHit !== gHit) {
      missing += 1;
      max = Math.max(max, 1);
      continue;
    }
    if (!eHit) continue;
    compared += 1;
    max = Math.max(max, Math.abs(g - e));
  }
  return { max, compared, missing };
}

async function loadJson(url) {
  const response = await fetch(url);
  if (!response.ok) return null;
  return response.json();
}

async function loadBuffer(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} (${response.status})`);
  return response.arrayBuffer();
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

async function timeIt(name, rounds, fn) {
  const times = [];
  let last = null;
  for (let i = 0; i < rounds; i++) {
    const t0 = performance.now();
    last = await fn();
    times.push(performance.now() - t0);
  }
  const ms = median(times);
  return { name, ms, times, last };
}

function workerCount() {
  return Math.max(1, Math.min(8, navigator.hardwareConcurrency || 4));
}

async function makeWorkers(count, step, triangles, wasmUrl) {
  const workers = [];
  const ready = [];
  const copy = triangles.slice().buffer;
  for (let i = 0; i < count; i++) {
    const worker = new Worker(new URL("./surface-worker.js", import.meta.url), { type: "module" });
    const wait = new Promise((resolve, reject) => {
      worker.onmessage = (event) => {
        if (event.data.type === "ready") resolve(event.data);
        else if (event.data.type === "error") reject(new Error(event.data.message));
      };
      worker.onerror = (event) => reject(event.error || new Error(event.message));
    });
    worker.postMessage({ type: "init", step, wasmUrl, triangles: copy });
    workers.push(worker);
    ready.push(wait);
  }
  await Promise.all(ready);
  return workers;
}

function closeWorkers(workers) {
  for (const worker of workers) worker.terminate();
}

async function sampleWithSab(workers, points, count) {
  const pointBytes = points.byteLength;
  const resultBytes = count * 8;
  const controlBytes = 16;
  const sab = new SharedArrayBuffer(pointBytes + resultBytes + controlBytes);
  const sabPoints = new Float64Array(sab, 0, points.length);
  sabPoints.set(points);
  const sabResults = new Float64Array(sab, pointBytes, count);
  const control = new Int32Array(sab, pointBytes + resultBytes, 4);
  Atomics.store(control, 0, 0);
  Atomics.store(control, 1, 0);
  const offsets = { points: 0, pointFloats: points.length, results: pointBytes, resultFloats: count, control: pointBytes + resultBytes };
  const done = workers.map((worker) => new Promise((resolve, reject) => {
    const onMessage = (event) => {
      if (event.data.type === "done") {
        worker.removeEventListener("message", onMessage);
        resolve();
      } else if (event.data.type === "error") {
        worker.removeEventListener("message", onMessage);
        reject(new Error(event.data.message));
      }
    };
    worker.addEventListener("message", onMessage);
    worker.postMessage({ type: "sample-sab", sab, offsets, count, chunk: 4096 });
  }));
  await Promise.all(done);
  return sabResults.slice();
}

async function benchSurface(name, fixtureUrl, meta, wasmUrl) {
  setStatus(`Loading ${name}…`);
  const buffer = await loadBuffer(fixtureUrl);
  const fixture = parseSurfaceFixture(buffer);
  const { step, triangles, points, expected, pointCount, triCount } = fixture;
  log(`${name}: ${triCount.toLocaleString()} triangles, ${pointCount.toLocaleString()} points, step ${step}.`);

  setStatus(`${name}: building JS index…`);
  const jsBuilt = await timeIt(`${name} JS index build`, 3, async () => new MeshIndex(step, triangles));
  const jsIndex = jsBuilt.last;
  addRow({ name: jsBuilt.name, ms: jsBuilt.ms, pointsPerSec: null, maxAbsError: null, notes: `${triCount} triangles` });

  setStatus(`${name}: JS sample…`);
  const jsOut = new Float64Array(pointCount);
  const jsSample = await timeIt(`${name} JS sample`, 3, async () => {
    jsIndex.sampleMany(points, jsOut);
    return jsOut;
  });
  const jsErr = maxError(jsOut, expected);
  addRow({
    name: jsSample.name,
    ms: jsSample.ms,
    pointsPerSec: pointCount / (jsSample.ms / 1000),
    maxAbsError: jsErr.max,
    notes: jsErr.missing ? `${jsErr.missing} hit mismatches` : "matches Python",
  });
  results.mismatches[`${name}-js`] = jsErr;

  setStatus(`${name}: building wasm index…`);
  const wasmBuilt = await timeIt(`${name} wasm index build`, 3, async () => new WasmMeshIndex(step, triangles));
  const wasmIndex = wasmBuilt.last;
  addRow({ name: wasmBuilt.name, ms: wasmBuilt.ms, pointsPerSec: null, maxAbsError: null, notes: `${wasmIndex.triangleCount} triangles` });

  setStatus(`${name}: wasm single-thread sample…`);
  let wasmOut;
  const wasmSample = await timeIt(`${name} wasm single-thread sample`, 3, async () => {
    wasmOut = wasmIndex.sampleMany(points);
    return wasmOut;
  });
  const wasmErr = maxError(wasmOut, expected);
  addRow({
    name: wasmSample.name,
    ms: wasmSample.ms,
    pointsPerSec: pointCount / (wasmSample.ms / 1000),
    maxAbsError: wasmErr.max,
    notes: wasmErr.missing ? `${wasmErr.missing} hit mismatches` : "matches Python",
  });
  results.mismatches[`${name}-wasm`] = wasmErr;

  if (meta && typeof meta.pythonSampleMs === "number") {
    addRow({
      name: `${name} Python sample (export host)`,
      ms: meta.pythonSampleMs,
      pointsPerSec: pointCount / (meta.pythonSampleMs / 1000),
      maxAbsError: 0,
      notes: meta.pythonIndexMs != null ? `index build ${meta.pythonIndexMs.toFixed(2)} ms` : "baseline from export",
    });
  }

  if (!globalThis.crossOriginIsolated || typeof SharedArrayBuffer !== "function") {
    addRow({
      name: `${name} wasm multi-thread (SAB)`,
      ms: null,
      pointsPerSec: null,
      maxAbsError: null,
      notes: "blocked: page is not crossOriginIsolated",
    });
    return;
  }

  const threads = workerCount();
  setStatus(`${name}: starting ${threads} wasm workers…`);
  const workers = await makeWorkers(threads, step, triangles, wasmUrl);
  try {
    // Warm one pass so setup is not in the timed loop.
    await sampleWithSab(workers, points, pointCount);
    const mt = await timeIt(`${name} wasm ${threads}-thread SAB sample`, 3, async () => sampleWithSab(workers, points, pointCount));
    const mtErr = maxError(mt.last, expected);
    addRow({
      name: mt.name,
      ms: mt.ms,
      pointsPerSec: pointCount / (mt.ms / 1000),
      maxAbsError: mtErr.max,
      notes: `${threads} workers, SharedArrayBuffer + Atomics`,
    });
    results.mismatches[`${name}-wasm-mt`] = mtErr;
  } finally {
    closeWorkers(workers);
  }
}

async function benchTexture(url, meta) {
  setStatus("Loading texture fixture…");
  const buffer = await loadBuffer(url);
  const tex = parseTextureFixture(buffer);
  log(`Texture: ${tex.codec} ${tex.width}x${tex.height}, ${tex.raw.byteLength} swizzled bytes.`);
  const rounds = 5;
  const decoded = await timeIt(`wasm decodeSwizzle ${tex.codec} ${tex.width}x${tex.height}`, rounds, async () => {
    return decodeSwizzle(tex.codec, tex.width, tex.height, tex.blockW, tex.blockH, tex.bytesPer, tex.tile, tex.log2, tex.raw, new Uint8Array());
  });
  let max = 0;
  if (decoded.last && tex.rgba && tex.rgba.length) {
    const got = decoded.last;
    const n = Math.min(got.length, tex.rgba.length);
    for (let i = 0; i < n; i++) max = Math.max(max, Math.abs(got[i] - tex.rgba[i]));
  }
  addRow({
    name: decoded.name,
    ms: decoded.ms,
    pointsPerSec: null,
    maxAbsError: max,
    notes: meta && meta.pythonDecodeMs != null ? `Python ${meta.pythonDecodeMs.toFixed(2)} ms` : `${tex.raw.byteLength} input bytes`,
  });
  if (meta && typeof meta.pythonDecodeMs === "number") {
    addRow({
      name: `Python decodeSwizzle ${tex.codec} ${tex.width}x${tex.height}`,
      ms: meta.pythonDecodeMs,
      pointsPerSec: null,
      maxAbsError: 0,
      notes: "baseline from export",
    });
  }
}

async function run() {
  isolated.textContent = results.crossOriginIsolated
    ? "crossOriginIsolated is true. SharedArrayBuffer is available."
    : "crossOriginIsolated is false. Multi-thread SAB will not run.";
  isolated.className = results.crossOriginIsolated ? "ok" : "warn";
  results.memory.before = heap();

  try {
    setStatus("Loading wasm module…");
    const wasmUrl = new URL("../pkg/ocarina_forge_native_bg.wasm", import.meta.url).href;
    await init({ module_or_path: wasmUrl });

    const lakeMeta = await loadJson("./data/hyrule-field-child.json");
    const kokiriMeta = await loadJson("./data/kokiri-forest-child.json");
    const texMeta = await loadJson("./data/texture.json");

    await benchSurface("Hyrule Field (Child) / Lake Hylia", "./data/hyrule-field-child.bin", lakeMeta, wasmUrl);
    await benchSurface("Kokiri Forest (Child)", "./data/kokiri-forest-child.bin", kokiriMeta, wasmUrl);
    if (texMeta || true) {
      try {
        await benchTexture("./data/texture.bin", texMeta);
      } catch (error) {
        log(`Texture bench skipped: ${error}`);
        results.errors.push(String(error));
      }
    }
  } catch (error) {
    log(String(error && error.stack ? error.stack : error));
    results.errors.push(String(error));
    setStatus("Bench failed. See the log.");
    window.__BENCH_RESULTS = results;
    window.__BENCH_DONE = true;
    throw error;
  }

  results.memory.after = heap();
  results.crossOriginIsolated = Boolean(globalThis.crossOriginIsolated);
  window.__BENCH_RESULTS = results;
  window.__BENCH_DONE = true;
  setStatus("Bench finished.");
  log(JSON.stringify(results, null, 2));
}

run().catch((error) => {
  console.error(error);
});
