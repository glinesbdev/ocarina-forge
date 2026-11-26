import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { backendLabel, compileObjectPipeline, createBackendRenderer, dismissWebgpuNotice, drawCount, drawTotal, guardWebgpuSetPipeline, initFailureReason, inspectWebGPU, patchWebgpuBc7Format, PIPELINE_COMPILE_LIMIT, pipelineCompileInFlight, rendererChoice, rendererSearch, repairCompileAsyncTransparents, stripLegacyGpu, warmWebGLPrograms, webgpuFallbackNote, webgpuNoticeVisible } from "./backend.js";
import { buildNoticeId, buildNoticeVisible, describeBuildMismatch, dismissBuildNotice, shortRevision } from "./build-info.js";
import { formatStatusLabel } from "./frame.js";

test("WebGPU is the default and WebGL2 can be forced", () => {
  assert.equal(rendererChoice(""), "webgpu");
  assert.equal(rendererChoice("?gpu=1"), "webgpu");
  assert.equal(rendererChoice("?webgpu=1"), "webgpu");
  assert.equal(rendererChoice("?renderer=webgpu"), "webgpu");
  assert.equal(rendererChoice("?gpu=0"), "webgl");
  assert.equal(rendererChoice("?webgpu=0"), "webgl");
  assert.equal(rendererChoice("?renderer=webgl&webgpu=1"), "webgl");
  assert.equal(rendererChoice("?renderer=webgpu-strict"), "webgpu-strict");
  assert.equal(rendererChoice("?renderer=WebGPU-Strict"), "webgpu-strict");
  assert.equal(rendererChoice("?renderer=webgl&renderer=webgpu-strict"), "webgl");
  assert.equal(rendererSearch("?scene=kokiri", "webgpu"), "?scene=kokiri&webgpu=1");
  assert.equal(rendererSearch("?gpu=1&scene=kokiri", "webgl"), "?scene=kokiri&renderer=webgl");
  assert.equal(rendererSearch("?webgpu=1&scene=kokiri", "webgl"), "?scene=kokiri&renderer=webgl");
  assert.equal(stripLegacyGpu("?gpu=1"), "");
  assert.equal(stripLegacyGpu("?scene=kokiri&gpu=1"), "?scene=kokiri");
  assert.equal(stripLegacyGpu("?webgpu=1"), null);
  assert.equal(stripLegacyGpu(""), null);
});

test("terrain warm-up never publishes a pending pipeline in the live WebGPU renderer", async () => {
  const args = [{}, {}, {}];
  const compileAsync = (...received) => calls.push(received);
  const calls = [];
  await warmWebGLPrograms({ isWebGPURenderer: true, compileAsync }, ...args);
  assert.deepEqual(calls, []);
  await warmWebGLPrograms({ isWebGLRenderer: true, compileAsync }, ...args);
  assert.deepEqual(calls, [args]);
});

test("WebGPU compiles a batch before attach and caps how many run at once", async () => {
  const calls = [];
  let inFlight = 0;
  let peak = 0;
  const renderer = {
    isWebGPURenderer: true,
    backend: { isWebGPUBackend: true },
    compileAsync(object, camera, scene) {
      calls.push([object, camera, scene]);
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      return Promise.resolve().then(() => {
        inFlight -= 1;
      });
    },
  };
  const mesh = { name: "batch" };
  const camera = {};
  const scene = {};
  const pending = [];
  for (let i = 0; i < PIPELINE_COMPILE_LIMIT + 3; i += 1) {
    pending.push(compileObjectPipeline(renderer, mesh, camera, scene));
  }
  await Promise.all(pending);
  assert.equal(peak, PIPELINE_COMPILE_LIMIT);
  assert.equal(calls.length, PIPELINE_COMPILE_LIMIT + 3);
  assert.equal(pipelineCompileInFlight(), 0);
  assert.deepEqual(calls[0], [mesh, camera, scene]);
  const webglCalls = [];
  await compileObjectPipeline({
    isWebGLRenderer: true,
    compileAsync(...received) { webglCalls.push(received); },
  }, mesh, camera, scene);
  assert.deepEqual(webglCalls, []);
});

test("a transparent batch survives r170 compileAsync's missing double-pass list", () => {
  const camera = { isCamera: true };
  const scene = { isScene: true, overrideMaterial: null };
  const lights = {};
  const blend = { transparent: true, side: 2, forceSinglePass: false, name: "alpha" };
  const list = [{ object: { material: blend }, material: blend }];
  const seen = [];
  const renderer = {
    _renderObjects(renderList, cameraArg, sceneArg) {
      for (const item of renderList) {
        if (sceneArg.overrideMaterial !== null) {
          const overrideMaterial = sceneArg.overrideMaterial;
          void overrideMaterial.isShadowNodeMaterial;
        }
        seen.push({ camera: cameraArg, scene: sceneArg, material: item.material });
      }
    },
    _renderTransparents(renderList, doublePassList, cameraArg, sceneArg, lightsNode) {
      if (doublePassList.length > 0) {
        this._renderObjects(doublePassList, cameraArg, sceneArg, lightsNode, "backSide");
        this._renderObjects(renderList, cameraArg, sceneArg, lightsNode);
      } else {
        this._renderObjects(renderList, cameraArg, sceneArg, lightsNode);
      }
    },
  };
  assert.throws(
    () => renderer._renderTransparents(list, camera, scene, lights),
    (error) => error instanceof TypeError && /isShadowNodeMaterial/.test(error.message),
  );
  assert.equal(repairCompileAsyncTransparents(renderer), true);
  assert.equal(repairCompileAsyncTransparents(renderer), false);
  renderer._renderTransparents(list, camera, scene, lights);
  assert.deepEqual(seen, [{ camera, scene, material: blend }]);

  const passed = [];
  renderer._renderObjects = (renderList, cameraArg, sceneArg, lightsArg) => {
    passed.push({ renderList, camera: cameraArg, scene: sceneArg, lights: lightsArg });
  };
  const doublePass = [{ material: blend }];
  renderer._renderTransparents(list, doublePass, camera, scene, lights);
  assert.equal(passed.length, 2);
  assert.equal(passed[0].renderList, doublePass);
  assert.equal(passed[0].camera, camera);
  assert.equal(passed[0].scene, scene);
  assert.equal(passed[0].lights, lights);
  assert.equal(passed[1].renderList, list);
  assert.equal(passed[1].camera, camera);
  assert.equal(passed[1].scene, scene);
});

test("a compileAsync failure is logged once and the batch promise still resolves", async () => {
  const direct = function renderDirect() {};
  let compiles = 0;
  const renderer = {
    isWebGPURenderer: true,
    backend: { isWebGPUBackend: true },
    _renderObjectDirect: direct,
    _handleObjectFunction: direct,
    _compilationPromises: null,
    compileAsync() {
      compiles += 1;
      this._handleObjectFunction = function compiling() {};
      this._compilationPromises = [];
      throw new TypeError("Cannot read properties of undefined (reading 'isShadowNodeMaterial')");
    },
  };
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args);
  try {
    await compileObjectPipeline(renderer, { name: "blend", material: { transparent: true } }, { isCamera: true }, { isScene: true });
    await compileObjectPipeline(renderer, { name: "water", material: { transparent: true } }, { isCamera: true }, { isScene: true });
  } finally {
    console.error = original;
  }
  assert.equal(compiles, 2);
  assert.equal(errors.length, 1);
  assert.match(errors[0][0], /attaching the batch anyway/);
  assert.match(errors[0][1].message, /isShadowNodeMaterial/);
  assert.equal(renderer._handleObjectFunction, direct);
  assert.equal(renderer._compilationPromises, null);
  assert.equal(pipelineCompileInFlight(), 0);
});

test("a draw waits until the native pipeline exists", () => {
  const draws = [];
  const pipelines = new Map();
  const backend = {
    isWebGPUBackend: true,
    get(pipeline) { return pipelines.get(pipeline); },
    draw(renderObject) { draws.push(renderObject); },
  };
  const renderer = { backend };
  assert.equal(guardWebgpuSetPipeline(renderer), true);
  assert.equal(guardWebgpuSetPipeline(renderer), false);
  const pending = { id: "pending" };
  const ready = { id: "ready" };
  pipelines.set(pending, { pipeline: undefined });
  pipelines.set(ready, { pipeline: { gpu: true } });
  backend.draw({ pipeline: pending });
  backend.draw({ pipeline: ready });
  assert.deepEqual(draws, [{ pipeline: ready }]);
});

test("each module graph creates its own renderer", async () => {
  class WebGLRenderer {}
  class WebGPURenderer {
    isWebGPURenderer = true;
    backend = { isWebGPUBackend: true };
    async init() {}
  }
  const webgl = await createBackendRenderer({ WebGLRenderer });
  assert.ok(webgl.renderer instanceof WebGLRenderer);
  assert.equal(webgl.nodes, false);
  assert.equal(webgl.backend, "WebGL");
  assert.equal(webgl.webgpu, false);
  const webgpu = await createBackendRenderer({ WebGPURenderer });
  assert.ok(webgpu.renderer instanceof WebGPURenderer);
  assert.equal(webgpu.nodes, true);
  assert.equal(webgpu.backend, "WebGPU");
  assert.equal(webgpu.webgpu, true);
});

test("a missing adapter or failed initialization is returned to the boot module", async () => {
  let disposed = 0;
  class NoAdapter {
    isWebGPURenderer = true;
    backend = { isWebGLBackend: true };
    async init() {}
    dispose() { disposed++; }
  }
  class BrokenDevice extends NoAdapter {
    async init() { throw new Error("Device initialization rejected"); }
  }
  await assert.rejects(createBackendRenderer({ WebGPURenderer: NoAdapter }), /WebGL2 backend/);
  await assert.rejects(createBackendRenderer({ WebGPURenderer: BrokenDevice }), /Device initialization rejected/);
  assert.equal(disposed, 2);
  assert.equal(initFailureReason(new Error("WebGPU init used the WebGL2 backend")), "WebGPU init used the WebGL2 backend");
  assert.equal(initFailureReason(new Error("Device initialization rejected")), "init exception: Device initialization rejected");
  assert.equal(initFailureReason(new Error("WebGPUBackend: Unable to create WebGPU adapter.")), "adapter is null");
  assert.equal(initFailureReason(new Error("WebGPU init timed out")), "init exception: WebGPU init timed out");
});

test("draw counts come from the backend that actually records them", () => {
  assert.equal(drawCount({ calls: 220 }, false), 220);
  assert.equal(drawCount({ calls: 1, drawCalls: 180 }, true), 180);
  assert.equal(drawCount(null, true), 0);
  assert.equal(drawTotal({ drawCalls: 12 }, true, 40), 52);
  assert.equal(drawTotal({ calls: 20 }, false, 40), 20);
});

test("the WebGL2 fallback is not labeled as real WebGPU", () => {
  assert.equal(backendLabel({ isWebGPURenderer: true, backend: { isWebGPUBackend: true } }), "WebGPU");
  assert.equal(backendLabel({ isWebGPURenderer: true, backend: { isWebGLBackend: true } }), "WebGPU (WebGL2 fallback)");
  assert.equal(backendLabel({ isWebGPURenderer: true, backend: {} }), "WebGPU (WebGL2 fallback)");
  assert.equal(backendLabel({}), "WebGL");
  assert.equal(
    backendLabel({ isWebGLRenderer: true }, { requested: "webgpu", reason: "adapter is null" }),
    "WebGL (WebGPU unavailable: adapter is null)",
  );
  assert.equal(
    backendLabel({ isWebGPURenderer: true, backend: { isWebGLBackend: true } }, { reason: "requestDevice failed: denied" }),
    "WebGPU (WebGL2 fallback: requestDevice failed: denied)",
  );
  assert.equal(backendLabel({ isWebGLRenderer: true }, { requested: "webgl", reason: "adapter is null" }), "WebGL");
  assert.equal(webgpuFallbackNote("adapter is null"), "WebGPU is unavailable (adapter is null). Using WebGL.");
  assert.equal(
    formatStatusLabel({ backend: "WebGPU (WebGL2 fallback)", draws: 30 }),
    "WebGPU (WebGL2 fallback) · 30 draws",
  );
});

test("the status label names the backend that actually started", () => {
  assert.equal(
    formatStatusLabel({ backend: "WebGPU", build: "abcdef1", draws: 40 }),
    "WebGPU · abcdef1 · 40 draws",
  );
  assert.equal(
    formatStatusLabel({ backend: "WebGL", draws: 240 }),
    "WebGL · 240 draws",
  );
});

test("the viewport keeps the WebGL2 graph and WebGPU shader hooks", () => {
  const backend = readFileSync(new URL("./backend.js", import.meta.url), "utf8");
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(backend, /new THREE\.WebGLRenderer\(\{ antialias: true \}\)/);
  assert.match(backend, /guardWebgpuSetPipeline\(gpuRenderer\)/);
  assert.match(backend, /repairCompileAsyncTransparents\(gpuRenderer\)/);
  assert.match(backend, /PIPELINE_COMPILE_LIMIT = 6/);
  assert.match(backend, /WebGPU \(WebGL2 fallback\)/);
  assert.match(backend, /backendLabel\(gpuRenderer\)/);
  assert.match(viewport, /attachTerrainColor\(material\)/);
  assert.match(viewport, /attachCutout\(material\)/);
  assert.match(viewport, /groupGeometryDraws\(mesh\)/);
  assert.match(viewport, /patchBatchedDraw\(renderer\.backend\)/);
  assert.doesNotMatch(viewport, /geometry\.uuid\}`/);
  assert.match(viewport, /!material\.userData\.terrainNode/);
  const cutout = readFileSync(new URL("./cutout-node.js", import.meta.url), "utf8");
  assert.match(cutout, /alphaTestNode/);
  assert.match(cutout, /hasAttribute\("uv"\)/);
  assert.match(cutout, /\.discard\(\)/);
  assert.match(cutout, /alphaToCoverage = false/);
  assert.match(viewport, /createBackendRenderer\(THREE/);
  assert.doesNotMatch(html, /id="use-webgpu"/);
  assert.doesNotMatch(html, /data-nav=/);
  assert.match(html, /id="shortcuts-open"/);
  assert.equal(html.includes(`id="${["show", "stats"].join("-")}"`), false);
  assert.match(html, /id="show-profile"/);
  assert.match(html, /id="renderer-notice"/);
  assert.match(html, /troubleshooting\.html/);
  const boot = readFileSync(new URL("./boot.js", import.meta.url), "utf8");
  assert.match(boot, /inspectWebGPU/);
  assert.match(boot, /webgpu-strict/);
  assert.match(boot, /this bundle was built in webgl mode/);
  assert.match(html, /src="\/src\/boot.js"/);
  const node = readFileSync(new URL("./terrain-node.js", import.meta.url), "utf8");
  assert.match(node, /attribute\("uv1", "vec2"\)/);
  assert.match(node, /attribute\("uv2", "vec2"\)/);
  assert.match(node, /attribute\("uv3", "vec2"\)/);
  assert.doesNotMatch(node, /\.discard\(\)/);
  assert.match(node, /material\.colorNode/);
  assert.match(node, /pow\(max\(abs\(normal\), vec3\(0\.0001\)\), vec3\(4\)\)/);
  assert.doesNotMatch(node, /(?<!\.)\bnormalize\s*\(/);
  assert.doesNotMatch(node, /attribute\("uv"/);
  assert.match(node, /material\.map = null/);
  assert.match(node, /hasAttribute\("uv1"\)/);
  const called = new Set();
  for (const match of node.matchAll(/(?<!\.)\b([A-Za-z_][A-Za-z0-9_]*)\s*\(/g)) called.add(match[1]);
  const imported = new Set([...node.matchAll(/^\s+([A-Za-z_][A-Za-z0-9_]*),?$/gm)].map((match) => match[1]));
  const locals = new Set(["terrainCell", "terrainSample", "terrainTri", "attachTerrainColor", "Fn", "if", "for", "while", "switch", "catch"]);
  for (const name of called) {
    if (locals.has(name)) continue;
    assert.equal(imported.has(name), true, `${name} is called but not imported from three/tsl`);
  }
});

test("patchWebgpuBc7Format maps BC7 before the r170 getFormat switch", () => {
  const calls = [];
  const utils = {
    getFormat(texture) {
      calls.push(texture);
      return "not-bc7";
    },
  };
  const renderer = { backend: { textureUtils: utils } };
  assert.equal(patchWebgpuBc7Format(renderer), true);
  assert.equal(utils.getFormat({ format: 36492, colorSpace: "srgb" }), "bc7-rgba-unorm-srgb");
  assert.equal(utils.getFormat({ format: 36492, colorSpace: "" }), "bc7-rgba-unorm");
  assert.equal(utils.getFormat({ format: 33779 }), "not-bc7");
  assert.equal(calls.length, 1);
  assert.equal(patchWebgpuBc7Format(renderer), true);
});

test("patchWebgpuBc7Format rewrites the r170 sRGB typo and hooks late textureUtils", () => {
  const utils = {
    getFormat(texture) {
      if (texture && texture.format === 36492) return "bc7-rgba-srgb";
      return "other";
    },
  };
  const backend = {};
  const renderer = { backend };
  assert.equal(patchWebgpuBc7Format(renderer), false);
  backend.textureUtils = utils;
  assert.equal(utils.getFormat({ format: 36492, colorSpace: "srgb" }), "bc7-rgba-unorm-srgb");
  assert.equal(utils.getFormat({ format: 1 }), "other");
});

test("a WebGPU choice that lands on classic WebGL names the reason", async () => {
  class WebGLRenderer {}
  const created = await createBackendRenderer(
    { WebGLRenderer },
    { requested: "webgpu", reason: "adapter is null" },
  );
  assert.equal(created.webgpu, false);
  assert.equal(created.backend, "WebGL (WebGPU unavailable: adapter is null)");
  const stale = await createBackendRenderer({ WebGLRenderer }, { requested: "webgpu" });
  assert.equal(stale.backend, "WebGL (WebGPU unavailable: this Three build has no WebGPURenderer)");
  await assert.rejects(
    () => createBackendRenderer({ WebGLRenderer }, { requested: "webgpu-strict", reason: "adapter is null" }),
    /adapter is null/,
  );
});

test("WebGPU is still chosen when the build also exports WebGLRenderer", async () => {
  class WebGLRenderer {
    constructor() {
      throw new Error("classic WebGLRenderer");
    }
  }
  class WebGPURenderer {
    isWebGPURenderer = true;
    backend = { isWebGPUBackend: true };
    async init() {}
  }
  const created = await createBackendRenderer(
    { WebGLRenderer, WebGPURenderer },
    { requested: "webgpu" },
  );
  assert.equal(created.webgpu, true);
  assert.equal(created.backend, "WebGPU");
});

test("inspectWebGPU records the specific reason and adapter info", async () => {
  assert.deepEqual(await inspectWebGPU(undefined), {
    ok: false,
    code: "no-navigator-gpu",
    reason: "no navigator.gpu",
    adapterInfo: null,
    limits: null,
  });
  assert.equal((await inspectWebGPU({ requestAdapter: async () => null })).reason, "adapter is null");
  const denied = await inspectWebGPU({
    requestAdapter: async () => {
      throw new Error("blocked by Shields");
    },
  });
  assert.equal(denied.reason, "requestAdapter failed: blocked by Shields");
  const device = await inspectWebGPU({
    requestAdapter: async () => ({
      features: new Set(),
      info: { vendor: "google", architecture: "gen-9", device: "dg2", description: "Intel" },
      limits: { maxTextureDimension2D: 8192, maxBufferSize: 1 },
      async requestDevice() {
        throw new Error("OperationError: device lost");
      },
    }),
  });
  assert.equal(device.code, "request-device");
  assert.equal(device.reason, "requestDevice failed: OperationError: device lost");
  assert.equal(device.adapterInfo.vendor, "google");
  assert.equal(device.adapterInfo.description, "Intel");
  assert.equal(device.limits.maxTextureDimension2D, 8192);
  const featureError = await inspectWebGPU({
    requestAdapter: async () => ({
      features: new Set(["texture-compression-bc"]),
      async requestDevice() {
        throw new Error("Unsupported feature: texture-compression-bc");
      },
    }),
  });
  assert.equal(featureError.code, "missing-feature");
  assert.match(featureError.reason, /missing required feature: Unsupported feature: texture-compression-bc/);
  const missing = await inspectWebGPU({
    requestAdapter: async () => ({
      features: {
        has(name) {
          return name === "texture-compression-bc";
        },
      },
      async requestDevice() {
        throw new Error("should not ask for a device");
      },
    }),
  }, { requiredFeatures: ["texture-compression-bc", "texture-compression-astc"] });
  assert.equal(missing.reason, "missing required feature: texture-compression-astc");
  const ok = await inspectWebGPU({
    requestAdapter: async () => ({
      features: new Set(["texture-compression-bc"]),
      requestAdapterInfo: async () => ({ vendor: "brave", description: "Vulkan" }),
      async requestDevice(descriptor) {
        assert.deepEqual(descriptor.requiredFeatures, ["texture-compression-bc"]);
        return { destroy() {} };
      },
    }),
  });
  assert.equal(ok.ok, true);
  assert.equal(ok.adapterInfo.vendor, "brave");
  assert.equal(ok.adapterInfo.description, "Vulkan");
});

test("the WebGPU notice is one reason at a time", () => {
  const storage = new Map();
  const box = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, value),
  };
  assert.equal(webgpuNoticeVisible(box, ""), false);
  assert.equal(webgpuNoticeVisible(box, "adapter is null"), true);
  dismissWebgpuNotice(box, "adapter is null");
  assert.equal(webgpuNoticeVisible(box, "adapter is null"), false);
  assert.equal(webgpuNoticeVisible(box, "no navigator.gpu"), true);
  assert.equal(webgpuNoticeVisible({ getItem() { throw new Error("private"); } }, "adapter is null"), true);
});

test("the stats line carries the page revision and a skew warning names the older side", () => {
  assert.equal(shortRevision(""), "");
  assert.equal(shortRevision("unknown"), "");
  assert.equal(shortRevision("abcdef1234567890"), "abcdef1");
  const page = { revision: "bbbbbbb222", committedAt: 200 };
  const olderServer = { revision: "aaaaaaa111", committedAt: 100 };
  const newerServer = { revision: "ccccccc333", committedAt: 300 };
  assert.equal(describeBuildMismatch(page, page), null);
  assert.equal(describeBuildMismatch({ revision: "unknown", committedAt: 1 }, olderServer), null);
  assert.match(describeBuildMismatch(page, olderServer).message, /Server build aaaaaaa is older than this page bbbbbbb/);
  assert.equal(describeBuildMismatch(page, olderServer).kind, "server-older");
  assert.match(describeBuildMismatch(page, newerServer).message, /older than the server/);
  assert.match(describeBuildMismatch(page, newerServer).message, /plain WebGL/);
  assert.match(describeBuildMismatch(page, {}).message, /did not report a build stamp/);
  const id = buildNoticeId(page, olderServer);
  const storage = new Map();
  const box = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, value),
  };
  assert.equal(buildNoticeVisible(box, id), true);
  dismissBuildNotice(box, id);
  assert.equal(buildNoticeVisible(box, id), false);
  assert.equal(
    formatStatusLabel({
      backend: "WebGL (WebGPU unavailable: adapter is null)",
      build: "abcdef1",
      draws: 10,
    }),
    "WebGL (WebGPU unavailable: adapter is null) · abcdef1 · 10 draws",
  );
});
