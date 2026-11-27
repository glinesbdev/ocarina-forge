import assert from "node:assert/strict";
import test from "node:test";

import { budgetAssetBytes, bc7GpuBytes, collectMemoryReport, residentModelBytes } from "./memory-report.js";

test("the memory report counts actors, instanced slots, and compressed textures", () => {
  const geometry = {
    attributes: { position: { array: new Float32Array(24) } },
    index: { array: new Uint16Array(6) },
  };
  const texture = {
    isCompressedTexture: true,
    format: 36492,
    mipmaps: [{ width: 4, height: 4, data: new Uint8Array(16) }],
    uuid: "tex-1",
  };
  const report = collectMemoryReport({
    entries: new Map([
      ["a", { gyaml: "Tree", slots: [1], parts: [{ geometry, material: { map: texture } }] }],
      ["b", { gyaml: "Tree", slots: [1], parts: [{ geometry, material: { map: texture } }] }],
      ["c", { gyaml: "Rock", slots: [], parts: [] }],
    ]),
    batches: [{ mesh: { geometry, material: { map: texture }, maxInstanceCount: 8 } }],
    stats: {
      fps: 60,
      frameMs: 16.6,
      draws: 12,
      instances: 2,
      culled: 1,
      cpuMs: 8,
      updateMs: 1,
      cullMs: 3,
      submitMs: 4,
      gpuMs: 6,
      objects: 2,
      batchCount: 1,
      subDraws: 4,
      profile: { enabled: true, gpuTiming: "webgpu" },
    },
    renderer: { info: { render: { drawCalls: 12, triangles: 30 }, memory: { geometries: 4, textures: 2 } } },
  });
  assert.equal(report.actors.total, 3);
  assert.equal(report.actors.instanced, 2);
  assert.equal(report.actors.uniqueModels, 2);
  assert.equal(report.textures.count, 1);
  assert.equal(report.textures.compressed, 1);
  assert.equal(report.textures.byFormat.BC7.count, 1);
  assert.equal(report.frame.draws, 12);
  assert.equal(report.frame.cpuMs, 8);
  assert.equal(report.frame.cullMs, 3);
  assert.equal(report.frame.submitMs, 4);
  assert.equal(report.frame.gpuMs, 6);
  assert.equal(report.frame.objects, 2);
  assert.equal(report.frame.batches, 1);
  assert.equal(report.frame.subDraws, 4);
  assert.equal(report.frame.batchKeys, null);
  assert.equal(report.frame.pickUploads, null);
  assert.equal(report.profile.gpuTiming, "webgpu");
  assert.ok(report.textures.gpuBytes > 0);
  assert.deepEqual(report.gpu, { geometries: 4, textures: 2 });
  const budget = budgetAssetBytes(report);
  assert.equal(budget.geometry, report.geometries.cpuBytes);
  assert.equal(budget.bc7, report.textures.byFormat.BC7.gpu);
  assert.equal(budget.total, budget.geometry + budget.bc7);
  const perModel = residentModelBytes(new Map([
    ["a", { gyaml: "Tree", parts: [{ geometry, material: { map: texture } }] }],
    ["b", { gyaml: "Tree", parts: [{ geometry, material: { map: texture } }] }],
  ]));
  assert.equal(perModel.size, 1);
  assert.ok(perModel.get("Tree") > 0);
});

test("a packed 2D image is not counted beside its array, and the array counts every layer", () => {
  const source = {
    isCompressedTexture: true,
    format: 36492,
    mipmaps: [{ width: 4, height: 4, data: new Uint8Array(16) }],
    uuid: "src",
    userData: { forgeInArray: true },
  };
  const array = {
    isTexture: true,
    isCompressedTexture: true,
    isCompressedArrayTexture: true,
    format: 36492,
    mipmaps: [{ width: 4, height: 4, data: new Uint8Array(64) }],
    image: { width: 4, height: 4, depth: 4 },
    uuid: "arr",
  };
  assert.equal(bc7GpuBytes(source), 0);
  assert.equal(bc7GpuBytes(array), 64);
  const report = collectMemoryReport({
    entries: new Map([
      ["a", { gyaml: "Grass", parts: [{ material: { map: source, userData: { blendMap: source } } }] }],
    ]),
    batches: [{ mesh: { material: { userData: { forgeArrayAlbedo: array } } } }],
  });
  assert.equal(report.textures.byFormat.BC7.gpu, 64);
});
