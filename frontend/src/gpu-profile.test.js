import assert from "node:assert/strict";
import test from "node:test";

import { createGpuProfile, sampleGpuFrame } from "./gpu-profile.js";

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
  assert.equal(state.last.writeTexture, 0);
  assert.equal(state.last.bindGroups, 0);
  assert.equal(state.last.pipelines, 1);
});

test("writeTexture and bind group calls are counted on the settled frame", () => {
  const renderer = {
    backend: {
      device: {
        queue: {
          writeTexture() {},
        },
        createBindGroup() {
          return {};
        },
        createRenderPipeline() {
          return {};
        },
      },
    },
  };
  const state = createGpuProfile(renderer);
  renderer.backend.device.queue.writeTexture({}, new Uint8Array(32), {}, {});
  renderer.backend.device.createBindGroup({});
  const info = console.info;
  console.info = () => {};
  try {
    sampleGpuFrame(state, { calls: 1, drawCalls: 12, materials: [{ version: 1 }], loading: true });
  } finally {
    console.info = info;
  }
  assert.equal(state.last.drawCalls, 12);
  assert.equal(state.last.writeTexture, 1);
  assert.equal(state.last.writeBytes, 32);
  assert.equal(state.last.bindGroups, 1);
  assert.equal(state.last.pipelines, 0);
});
