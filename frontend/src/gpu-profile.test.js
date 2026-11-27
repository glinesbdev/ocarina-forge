import assert from "node:assert/strict";
import test from "node:test";

import { armFrameCounters, createGpuProfile, sampleGpuFrame } from "./gpu-profile.js";

test("pipeline creations and material version changes are counted per frame", () => {
  const created = [];
  const renderer = {
    backend: {
      device: {
        createRenderPipeline(descriptor) {
          created.push(descriptor);
          return { id: created.length };
        },
      },
    },
  };
  const state = createGpuProfile(renderer);
  renderer.backend.device.createRenderPipeline({ label: "grass" });
  renderer.backend.device.createRenderPipeline({ label: "trunk" });
  const material = { version: 1 };
  Object.defineProperty(material, "needsUpdate", {
    set(value) {
      if (value === true) this.version += 1;
    },
  });
  const lines = [];
  const info = console.info;
  console.info = (line) => lines.push(line);
  try {
    assert.equal(sampleGpuFrame(state, { calls: 1, materials: [material], loading: true }), "");
    assert.equal(sampleGpuFrame(state, { calls: 1, materials: [material], loading: false }), "");
    const settled = sampleGpuFrame(state, { calls: 1, materials: [material], loading: false });
    assert.match(settled, /render calls 1, pipelines this frame 0, material versions stable/);
    material.needsUpdate = true;
    const changed = sampleGpuFrame(state, { calls: 1, materials: [material], loading: false });
    assert.match(changed, /pipelines this frame 0, material version changes 1/);
    renderer.backend.device.createRenderPipeline({ label: "again" });
    const rebuilt = sampleGpuFrame(state, { calls: 2, materials: [material], loading: false });
    assert.match(rebuilt, /render calls 2, pipelines this frame 1/);
  } finally {
    console.info = info;
  }
  assert.equal(created.length, 3);
});

test("writeTexture and bind-group counters stay unwired until Profile arms them", () => {
  let writes = 0;
  let binds = 0;
  const device = {
    queue: {
      writeTexture() {
        writes += 1;
      },
    },
    createBindGroup() {
      binds += 1;
      return { id: binds };
    },
    createRenderPipeline() {
      return {};
    },
  };
  const state = createGpuProfile({ backend: { device } });
  device.queue.writeTexture();
  device.createBindGroup();
  sampleGpuFrame(state, { calls: 1, materials: [], loading: false });
  assert.equal(state.last, undefined);
  armFrameCounters(state);
  device.queue.writeTexture();
  device.queue.writeTexture();
  device.createBindGroup();
  sampleGpuFrame(state, { calls: 1, materials: [], loading: true });
  assert.equal(state.last.writeTex, 2);
  assert.equal(state.last.binds, 1);
  assert.equal(writes, 3);
  assert.equal(binds, 2);
});
