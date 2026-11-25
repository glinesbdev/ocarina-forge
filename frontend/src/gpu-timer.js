/**
 * GPU frame timer that stays idle while the profile HUD is hidden.
 *
 * WebGPU: a `timestamp-query` set on the render pass, resolved into a buffer
 * and read back with `mapAsync`. WebGL (and the WebGPU renderer's WebGL2
 * fallback): `EXT_disjoint_timer_query_webgl2`.
 *
 * three r170 can do this itself with `trackTimestamp: true`, but that writes
 * queries on every pass for the life of the renderer (the cached pass
 * descriptor keeps `timestampWrites`, and the backend copies them out on
 * every submit). This timer arms a query only while the HUD is open and
 * puts the encoder hook back when the HUD closes. It forces
 * `backend.trackTimestamp` off so the two paths do not write the same pass.
 */

const PASS_LIMIT = 16;
const QUERY_COUNT = PASS_LIMIT * 2;
const RING = 3;
const BUFFER_MAP_READ = 0x0001;
const BUFFER_COPY_SRC = 0x0004;
const BUFFER_COPY_DST = 0x0008;
const BUFFER_QUERY_RESOLVE = 0x0200;
const MAP_READ = 0x0001;

function createNullTimer() {
  return {
    mode: "none",
    supported: false,
    setEnabled() {},
    beginFrame() {},
    endFrame() { return Promise.resolve(null); },
    poll() { return null; },
    latest() { return null; },
    disjoint() { return false; },
    onResult() {},
    dispose() {},
  };
}

function webglContext(renderer) {
  const backend = renderer && renderer.backend;
  if (backend && backend.isWebGPUBackend === true) return null;
  const candidates = [];
  if (typeof renderer.getContext === "function") {
    try { candidates.push(renderer.getContext()); } catch { /* a WebGPU canvas context can throw */ }
  }
  if (backend && backend.gl) candidates.push(backend.gl);
  for (const gl of candidates) {
    if (gl && typeof gl.getExtension === "function") return gl;
  }
  return null;
}

function detect(renderer) {
  const backend = renderer && renderer.backend;
  if (backend && backend.isWebGPUBackend === true) {
    const device = backend.device;
    const features = device && device.features;
    const listed = features && typeof features.has === "function" && features.has("timestamp-query");
    const probed = !listed && device && typeof backend.hasFeature === "function" && backend.hasFeature("timestamp-query");
    if (device && typeof device.createCommandEncoder === "function" && (listed || probed)) {
      return { mode: "webgpu", device };
    }
    return { mode: "none" };
  }
  const gl = webglContext(renderer);
  if (!gl) return { mode: "none" };
  let ext = null;
  try { ext = gl.getExtension("EXT_disjoint_timer_query_webgl2"); } catch { ext = null; }
  if (!ext || ext.TIME_ELAPSED_EXT == null) return { mode: "none" };
  return { mode: "webgl", gl, ext };
}

function createWebglTimer(gl, ext) {
  let enabled = false;
  let latestMs = null;
  let onResult = null;
  let active = null;
  const pending = [];
  const TIME = ext.TIME_ELAPSED_EXT;
  const AVAILABLE = gl.QUERY_RESULT_AVAILABLE;
  const RESULT = gl.QUERY_RESULT;
  const DISJOINT = ext.GPU_DISJOINT_EXT;

  function disjoint() {
    try { return Boolean(gl.getParameter(DISJOINT)); } catch { return false; }
  }

  function drop(query) {
    try { gl.deleteQuery(query); } catch { /* already deleted */ }
  }

  function drain() {
    if (disjoint()) {
      for (const query of pending) drop(query);
      pending.length = 0;
      if (active) {
        try { gl.endQuery(TIME); } catch { /* query was not begun */ }
        drop(active);
        active = null;
      }
      return;
    }
    let index = 0;
    while (index < pending.length) {
      const query = pending[index];
      let available = false;
      try { available = Boolean(gl.getQueryParameter(query, AVAILABLE)); } catch { available = false; }
      if (!available) {
        index += 1;
        continue;
      }
      let ns = null;
      try { ns = Number(gl.getQueryParameter(query, RESULT)); } catch { ns = null; }
      drop(query);
      pending.splice(index, 1);
      if (ns != null && Number.isFinite(ns)) {
        latestMs = ns / 1e6;
        if (onResult) onResult(latestMs);
      }
    }
  }

  return {
    mode: "webgl",
    supported: true,
    setEnabled(on) {
      enabled = Boolean(on);
      if (!enabled && active) {
        try { gl.endQuery(TIME); } catch { /* not begun */ }
        drop(active);
        active = null;
      }
    },
    beginFrame() {
      if (!enabled || active) return;
      drain();
      if (disjoint()) return;
      const query = gl.createQuery();
      if (!query) return;
      try {
        gl.beginQuery(TIME, query);
        active = query;
      } catch {
        drop(query);
        active = null;
      }
    },
    endFrame() {
      if (!active) return Promise.resolve(null);
      try { gl.endQuery(TIME); } catch { /* ignore a query the context already closed */ }
      pending.push(active);
      active = null;
      while (pending.length > 8) drop(pending.shift());
      drain();
      return Promise.resolve(latestMs);
    },
    poll() {
      drain();
      return latestMs;
    },
    latest() { return latestMs; },
    disjoint,
    onResult(fn) { onResult = fn; },
    dispose() {
      this.setEnabled(false);
      for (const query of pending) drop(query);
      pending.length = 0;
    },
  };
}

function createWebgpuTimer(device) {
  let enabled = false;
  let failed = false;
  let patched = false;
  let latestMs = null;
  let onResult = null;
  let generation = 0;
  let cursor = 0;
  let armed = null;
  let slots = null;
  const originalCreate = device.createCommandEncoder.bind(device);

  function unpatch() {
    if (!patched) return;
    device.createCommandEncoder = originalCreate;
    patched = false;
  }

  function install() {
    if (patched || failed) return;
    device.createCommandEncoder = (descriptor) => {
      const encoder = originalCreate(descriptor);
      const slot = armed;
      if (!slot) return encoder;
      const originalBegin = encoder.beginRenderPass.bind(encoder);
      const originalCompute = typeof encoder.beginComputePass === "function" ? encoder.beginComputePass.bind(encoder) : null;
      const originalFinish = encoder.finish.bind(encoder);
      const stamp = (descriptorIn) => {
        const desc = descriptorIn || {};
        if (slot.passes >= PASS_LIMIT) return { desc, skip: true };
        const previous = desc.timestampWrites;
        const index = slot.passes * 2;
        slot.passes += 1;
        desc.timestampWrites = {
          querySet: slot.querySet,
          beginningOfPassWriteIndex: index,
          endOfPassWriteIndex: index + 1,
        };
        return { desc, previous, skip: false };
      };
      const restore = (desc, previous) => {
        if (previous === undefined) delete desc.timestampWrites;
        else desc.timestampWrites = previous;
      };
      const runPass = (original, descriptorIn) => {
        const stamped = stamp(descriptorIn);
        if (stamped.skip) return original(stamped.desc);
        try { return original(stamped.desc); }
        catch (error) {
          slot.passes = Math.max(0, slot.passes - 1);
          throw error;
        } finally {
          restore(stamped.desc, stamped.previous);
        }
      };
      encoder.beginRenderPass = (descriptorIn) => runPass(originalBegin, descriptorIn);
      if (originalCompute) {
        encoder.beginComputePass = (descriptorIn) => runPass(originalCompute, descriptorIn);
      }
      encoder.finish = (...args) => {
        if (slot.passes > 0) {
          try {
            encoder.resolveQuerySet(slot.querySet, 0, slot.passes * 2, slot.resolve, 0);
            encoder.copyBufferToBuffer(slot.resolve, 0, slot.read, 0, slot.passes * 2 * 8);
            slot.copied = true;
          } catch { slot.copied = false; }
        }
        return originalFinish(...args);
      };
      return encoder;
    };
    patched = true;
  }

  function ensure() {
    if (slots) return true;
    if (failed) return false;
    try {
      slots = [];
      for (let index = 0; index < RING; index += 1) {
        slots.push({
          querySet: device.createQuerySet({ type: "timestamp", count: QUERY_COUNT, label: `forge-frame-${index}` }),
          resolve: device.createBuffer({
            label: `forge-timestamp-resolve-${index}`,
            size: QUERY_COUNT * 8,
            usage: BUFFER_QUERY_RESOLVE | BUFFER_COPY_SRC,
          }),
          read: device.createBuffer({
            label: `forge-timestamp-read-${index}`,
            size: QUERY_COUNT * 8,
            usage: BUFFER_COPY_DST | BUFFER_MAP_READ,
          }),
          pending: false,
          passes: 0,
          copied: false,
        });
      }
      return true;
    } catch {
      failed = true;
      slots = null;
      unpatch();
      return false;
    }
  }

  function readSlot(slot, passes, stamp) {
    const read = slot.read;
    let mapped;
    try {
      mapped = read.mapAsync(MAP_READ);
    } catch {
      slot.pending = false;
      return Promise.resolve(null);
    }
    slot.pending = true;
    return Promise.resolve(mapped).then(() => {
      let ms = null;
      try {
        const times = new BigUint64Array(read.getMappedRange());
        let ns = 0;
        for (let index = 0; index < passes; index += 1) {
          const start = times[index * 2];
          const end = times[index * 2 + 1];
          if (end > start) ns += Number(end - start);
        }
        ms = ns / 1e6;
      } finally {
        try { read.unmap(); } catch { /* already unmapped */ }
        slot.pending = false;
      }
      if (stamp !== generation || ms == null || !Number.isFinite(ms)) return null;
      latestMs = ms;
      if (onResult) onResult(ms);
      return ms;
    }).catch(() => {
      slot.pending = false;
      try { read.unmap(); } catch { /* not mapped */ }
      return null;
    });
  }

  return {
    mode: "webgpu",
    get supported() { return !failed; },
    setEnabled(on) {
      const next = Boolean(on);
      if (enabled && !next) generation += 1;
      enabled = next;
      if (!enabled) {
        armed = null;
        unpatch();
        return;
      }
      if (!ensure()) return;
      install();
    },
    beginFrame() {
      armed = null;
      if (!enabled || !ensure()) return;
      install();
      for (let index = 0; index < slots.length; index += 1) {
        const slot = slots[(cursor + index) % slots.length];
        if (slot.pending) continue;
        slot.passes = 0;
        slot.copied = false;
        armed = slot;
        cursor = (cursor + index + 1) % slots.length;
        return;
      }
    },
    endFrame() {
      const slot = armed;
      armed = null;
      if (!slot || !slot.copied || slot.passes === 0) return Promise.resolve(null);
      const passes = slot.passes;
      const stamp = generation;
      return readSlot(slot, passes, stamp);
    },
    poll() { return latestMs; },
    latest() { return latestMs; },
    disjoint() { return false; },
    onResult(fn) { onResult = fn; },
    dispose() {
      generation += 1;
      enabled = false;
      armed = null;
      unpatch();
      for (const slot of slots || []) {
        try { if (slot.read.mapState === "mapped") slot.read.unmap(); } catch { /* ignore */ }
        try { slot.querySet.destroy(); } catch { /* ignore */ }
        try { slot.resolve.destroy(); } catch { /* ignore */ }
        try { slot.read.destroy(); } catch { /* ignore */ }
      }
      slots = null;
    },
  };
}

export function createFrameTimer(renderer) {
  const detected = detect(renderer);
  if (detected.mode === "none") return createNullTimer();
  if (renderer && renderer.backend) renderer.backend.trackTimestamp = false;
  if (detected.mode === "webgpu") return createWebgpuTimer(detected.device);
  return createWebglTimer(detected.gl, detected.ext);
}
