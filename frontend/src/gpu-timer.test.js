import assert from "node:assert/strict";
import test from "node:test";

import { createFrameTimer } from "./gpu-timer.js";

function timestampBuffer() {
  return {
    mapState: "unmapped",
    mapAsync() {
      this.mapState = "pending";
      return Promise.resolve().then(() => {
        this.mapState = "mapped";
      });
    },
    getMappedRange() {
      const times = new BigUint64Array(32);
      times[0] = 1_000_000n;
      times[1] = 6_000_000n;
      times[2] = 0n;
      times[3] = 2_000_000n;
      return times.buffer;
    },
    unmap() { this.mapState = "unmapped"; },
    destroy() { this.destroyed = true; },
  };
}

function webgpuDevice() {
  const seen = [];
  const resolved = [];
  const device = {
    features: { has(name) { return name === "timestamp-query"; } },
    createQuerySet(desc) { return { ...desc, destroyed: false, destroy() { this.destroyed = true; } }; },
    createBuffer() { return timestampBuffer(); },
    createCommandEncoder() {
      return {
        beginRenderPass(desc) {
          seen.push(desc && desc.timestampWrites ? { ...desc.timestampWrites } : null);
          return {};
        },
        resolveQuerySet(...args) { resolved.push(args); },
        copyBufferToBuffer() {},
        finish() { return { ok: true }; },
      };
    },
  };
  return { device, seen, resolved };
}

function webgpuRenderer(device, extra = {}) {
  return {
    backend: {
      isWebGPUBackend: true,
      trackTimestamp: true,
      device,
      hasFeature(name) { return device.features.has(name); },
      ...extra,
    },
    getContext() { throw new Error("WebGPU canvas context"); },
  };
}

test("webgpu timestamps resolve to milliseconds and stay off while the hud is hidden", async () => {
  const { device, seen, resolved } = webgpuDevice();
  const renderer = webgpuRenderer(device);
  const timer = createFrameTimer(renderer);
  assert.equal(timer.mode, "webgpu");
  assert.equal(timer.supported, true);
  assert.equal(renderer.backend.trackTimestamp, false);
  timer.beginFrame();
  const idle = device.createCommandEncoder();
  const untouched = {};
  idle.beginRenderPass(untouched);
  assert.equal(untouched.timestampWrites, undefined);
  assert.deepEqual(seen, [null]);

  timer.setEnabled(true);
  timer.beginFrame();
  const encoder = device.createCommandEncoder();
  const desc = { colorAttachments: [{}] };
  encoder.beginRenderPass(desc);
  encoder.beginRenderPass(desc);
  assert.equal(desc.timestampWrites, undefined);
  assert.equal(seen[1].beginningOfPassWriteIndex, 0);
  assert.equal(seen[1].endOfPassWriteIndex, 1);
  assert.equal(seen[2].beginningOfPassWriteIndex, 2);
  encoder.finish();
  assert.equal(resolved.length, 1);
  assert.equal(resolved[0][1], 0);
  assert.equal(resolved[0][2], 4);
  const landed = [];
  timer.onResult((ms) => landed.push(ms));
  assert.equal(await timer.endFrame(), 7);
  assert.equal(timer.latest(), 7);
  assert.deepEqual(landed, [7]);

  timer.setEnabled(false);
  seen.length = 0;
  timer.beginFrame();
  const after = device.createCommandEncoder();
  const quiet = {};
  after.beginRenderPass(quiet);
  assert.equal(quiet.timestampWrites, undefined);
  assert.deepEqual(seen, [null]);
  timer.dispose();
});

test("a late webgpu readback is dropped when the hud closes first", async () => {
  let release;
  const { device } = webgpuDevice();
  device.createBuffer = () => ({
    mapState: "unmapped",
    mapAsync() {
      return new Promise((resolve) => { release = resolve; });
    },
    getMappedRange() {
      const times = new BigUint64Array(4);
      times[1] = 3_000_000n;
      return times.buffer;
    },
    unmap() {},
    destroy() {},
  });
  const timer = createFrameTimer(webgpuRenderer(device));
  timer.setEnabled(true);
  timer.beginFrame();
  const encoder = device.createCommandEncoder();
  encoder.beginRenderPass({});
  encoder.finish();
  const pending = timer.endFrame();
  timer.setEnabled(false);
  release();
  assert.equal(await pending, null);
  assert.equal(timer.latest(), null);
  timer.dispose();
});

test("missing timestamp-query and a missing webgl extension are n/a", () => {
  const device = {
    features: { has() { return false; } },
    createCommandEncoder() { throw new Error("should not allocate"); },
  };
  const webgpu = createFrameTimer(webgpuRenderer(device));
  assert.equal(webgpu.mode, "none");
  assert.equal(webgpu.supported, false);
  webgpu.setEnabled(true);
  webgpu.beginFrame();
  assert.equal(webgpu.latest(), null);

  const gl = { getExtension() { return null; } };
  const webgl = createFrameTimer({ getContext() { return gl; } });
  assert.equal(webgl.mode, "none");
  webgl.beginFrame();
  webgl.endFrame();
});

function fakeGl({ available = true, disjoint = false, ns = 4_000_000 } = {}) {
  const ext = { TIME_ELAPSED_EXT: 0x88bf, GPU_DISJOINT_EXT: 0x8f90 };
  let created = 0;
  const gl = {
    QUERY_RESULT_AVAILABLE: 0x8867,
    QUERY_RESULT: 0x8866,
    getExtension(name) { return name === "EXT_disjoint_timer_query_webgl2" ? ext : null; },
    createQuery() { created += 1; return { id: created }; },
    beginQuery() {},
    endQuery() {},
    deleteQuery() {},
    getParameter(pname) { return pname === ext.GPU_DISJOINT_EXT ? disjoint : 0; },
    getQueryParameter(_query, pname) {
      if (pname === gl.QUERY_RESULT_AVAILABLE) return available;
      return ns;
    },
  };
  return { gl, created: () => created };
}

test("webgl timer queries report milliseconds and wait until the result is available", async () => {
  const state = { available: false, disjoint: false };
  const ext = { TIME_ELAPSED_EXT: 0x88bf, GPU_DISJOINT_EXT: 0x8f90 };
  const gl = {
    QUERY_RESULT_AVAILABLE: 0x8867,
    QUERY_RESULT: 0x8866,
    getExtension() { return ext; },
    createQuery() { return { id: 1 }; },
    beginQuery() {},
    endQuery() {},
    deleteQuery() {},
    getParameter(pname) { return pname === ext.GPU_DISJOINT_EXT ? state.disjoint : 0; },
    getQueryParameter(_query, pname) {
      if (pname === gl.QUERY_RESULT_AVAILABLE) return state.available;
      return 4_000_000;
    },
  };
  const renderer = { backend: { isWebGLBackend: true, trackTimestamp: true }, getContext() { return gl; } };
  const timer = createFrameTimer(renderer);
  assert.equal(timer.mode, "webgl");
  assert.equal(renderer.backend.trackTimestamp, false);
  timer.setEnabled(false);
  timer.beginFrame();
  await timer.endFrame();
  assert.equal(timer.latest(), null);

  timer.setEnabled(true);
  timer.beginFrame();
  await timer.endFrame();
  assert.equal(timer.latest(), null);
  state.available = true;
  assert.equal(timer.poll(), 4);
  assert.equal(timer.latest(), 4);
  timer.dispose();
});

test("a disjoint webgl gpu drops the sample", async () => {
  const { gl } = fakeGl({ disjoint: true });
  const timer = createFrameTimer({ getContext() { return gl; } });
  timer.setEnabled(true);
  timer.beginFrame();
  await timer.endFrame();
  assert.equal(timer.disjoint(), true);
  assert.equal(timer.latest(), null);
  timer.dispose();
});
