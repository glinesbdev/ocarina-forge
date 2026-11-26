export function rendererChoice(search) {
  const params = searchParams(search);
  const named = (params.get("renderer") || "").toLowerCase();
  if (named === "webgl" || params.get("webgpu") === "0" || params.get("gpu") === "0") return "webgl";
  if (named === "webgpu-strict") return "webgpu-strict";
  return "webgpu";
}

/** Search string for a reload. Other query keys are kept. `gpu` is the retired flag. */
export function rendererSearch(search, choice) {
  const params = searchParams(search);
  params.delete("renderer");
  params.delete("gpu");
  if (choice === "webgpu") params.set("webgpu", "1");
  else {
    params.delete("webgpu");
    params.set("renderer", "webgl");
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

/**
 * `?gpu=1` was the stage-bar checkbox. Drop it so a saved link uses the default renderer.
 * Returns null when the search does not carry that flag.
 */
export function stripLegacyGpu(search) {
  const params = searchParams(search);
  if (params.get("gpu") !== "1") return null;
  params.delete("gpu");
  const text = params.toString();
  return text ? `?${text}` : "";
}

function searchParams(search) {
  const raw = typeof search === "string" ? search : "";
  return new URLSearchParams(raw.charAt(0) === "?" ? raw.slice(1) : raw);
}

/**
 * WebGL counts draws in `calls`. WebGPU counts them in `drawCalls` and uses
 * `calls` for how many times render() ran. The WebGL2 fallback is still a
 * WebGPURenderer, so it uses the same counters.
 */
export function drawCount(info, webgpu) {
  if (!info) return 0;
  if (webgpu) return Number(info.drawCalls) || 0;
  return Number(info.calls) || 0;
}

/**
 * r170's WebGPU backend does not add BatchedMesh draws to `drawCalls`.
 * `extra` is the sum of those meshes' draw counts after the same-geometry fold.
 */
export function drawTotal(info, webgpu, extra = 0) {
  return drawCount(info, webgpu) + (webgpu ? Number(extra) || 0 : 0);
}

/**
 * Status label. A WebGPURenderer on the WebGL2 backend must not read as real WebGPU.
 * Plain "WebGL" is only a session that asked for WebGL. When WebGPU was the choice
 * and a classic WebGLRenderer is what started, name the reason.
 */
export function backendLabel(renderer, context = {}) {
  const reason = String((context && context.reason) || "").trim();
  const requested = (context && context.requested) || "";
  const wanted = requested === "webgpu" || requested === "webgpu-strict";
  let kind = "WebGL";
  if (renderer && renderer.isWebGPURenderer === true) {
    kind = renderer.backend && renderer.backend.isWebGPUBackend === true ? "WebGPU" : "WebGPU (WebGL2 fallback)";
  }
  if (kind === "WebGPU") return "WebGPU";
  if (kind === "WebGPU (WebGL2 fallback)") {
    return reason ? `WebGPU (WebGL2 fallback: ${reason})` : "WebGPU (WebGL2 fallback)";
  }
  if (wanted) return reason ? `WebGL (WebGPU unavailable: ${reason})` : "WebGL (WebGPU unavailable)";
  return "WebGL";
}

export function webgpuFallbackNote(reason) {
  const why = String(reason || "WebGPU did not start").trim();
  return `WebGPU is unavailable (${why}). Using WebGL.`;
}

/** Dismissing the notice remembers this reason. A different reason shows again. */
export const WEBGPU_NOTICE_KEY = "ocarinaforge.webgpuNotice";

export function webgpuNoticeVisible(storage, reason) {
  if (!reason) return false;
  try {
    return storage.getItem(WEBGPU_NOTICE_KEY) !== reason;
  } catch {
    return true;
  }
}

export function dismissWebgpuNotice(storage, reason) {
  try {
    storage.setItem(WEBGPU_NOTICE_KEY, reason);
  } catch {
    /* private mode */
  }
}

function messageOf(error) {
  if (!error) return "";
  if (typeof error === "string") return error;
  return String(error.message || error);
}

function infoFields(info) {
  if (!info) return null;
  return {
    vendor: info.vendor || "",
    architecture: info.architecture || "",
    device: info.device || "",
    description: info.description || "",
  };
}

async function readAdapterInfo(adapter) {
  try {
    if (typeof adapter.requestAdapterInfo === "function") {
      return infoFields(await adapter.requestAdapterInfo());
    }
  } catch {
    /* adapter info is optional */
  }
  if (adapter.info) return infoFields(adapter.info);
  return null;
}

function featureList(adapter) {
  const features = adapter && adapter.features;
  if (!features || typeof features[Symbol.iterator] !== "function") return [];
  return [...features];
}

function limitsSnapshot(adapter) {
  const limits = adapter && adapter.limits;
  if (!limits) return null;
  const keys = ["maxTextureDimension2D", "maxBufferSize", "maxStorageBufferBindingSize"];
  const out = {};
  for (const key of keys) {
    if (limits[key] != null) out[key] = limits[key];
  }
  return Object.keys(out).length ? out : null;
}

/**
 * Probe WebGPU before three's renderer is constructed. three r170 catches a
 * null adapter or a rejected requestDevice and swaps in its WebGL2 backend,
 * which drops the original message. The probe keeps that message.
 * `texture-compression-bc` is not required; a missing BC7 feature falls back
 * to RGBA later. Pass `requiredFeatures` to record a missing-feature reason.
 */
export async function inspectWebGPU(gpu, options = {}) {
  const required = Array.isArray(options.requiredFeatures) ? options.requiredFeatures : [];
  if (!gpu || typeof gpu.requestAdapter !== "function") {
    return { ok: false, code: "no-navigator-gpu", reason: "no navigator.gpu", adapterInfo: null, limits: null };
  }
  let adapter;
  try {
    adapter = await gpu.requestAdapter();
  } catch (error) {
    return {
      ok: false,
      code: "request-adapter",
      reason: `requestAdapter failed: ${messageOf(error)}`,
      adapterInfo: null,
      limits: null,
    };
  }
  if (!adapter) {
    return { ok: false, code: "adapter-null", reason: "adapter is null", adapterInfo: null, limits: null };
  }
  const adapterInfo = await readAdapterInfo(adapter);
  const limits = limitsSnapshot(adapter);
  const has = (name) => {
    if (adapter.features && typeof adapter.features.has === "function") return Boolean(adapter.features.has(name));
    return featureList(adapter).includes(name);
  };
  const missing = required.filter((name) => !has(name));
  if (missing.length) {
    return {
      ok: false,
      code: "missing-feature",
      reason: `missing required feature: ${missing.join(", ")}`,
      adapterInfo,
      limits,
      missing,
    };
  }
  const supported = featureList(adapter);
  try {
    const device = await adapter.requestDevice(supported.length ? { requiredFeatures: supported } : {});
    try {
      if (device && typeof device.destroy === "function") device.destroy();
    } catch {
      /* the device was created; releasing it is best-effort */
    }
  } catch (error) {
    const message = messageOf(error);
    const feature = /feature/i.test(message);
    return {
      ok: false,
      code: feature ? "missing-feature" : "request-device",
      reason: feature ? `missing required feature: ${message}` : `requestDevice failed: ${message}`,
      adapterInfo,
      limits,
    };
  }
  return { ok: true, code: "ok", reason: "", adapterInfo, limits };
}

const KNOWN_WEBGPU_REASONS = [
  /^no navigator\.gpu$/,
  /^adapter is null$/,
  /^requestAdapter failed:/,
  /^requestDevice failed:/,
  /^missing required feature:/,
  /^init exception:/,
  /^this Three build has no WebGPURenderer$/,
  /^this bundle was built in webgl mode$/,
  /^WebGPU init used the WebGL2 backend$/,
];

/** Map an init throw onto the same reason strings the probe uses. */
export function initFailureReason(error) {
  const message = messageOf(error).trim() || "WebGPU did not start";
  if (KNOWN_WEBGPU_REASONS.some((pattern) => pattern.test(message))) return message;
  if (/unable to create webgpu adapter/i.test(message)) return "adapter is null";
  return `init exception: ${message}`;
}

export function logWebGPUReport(report) {
  const line = report && report.ok ? "WebGPU available" : "WebGPU unavailable";
  const reason = report && report.reason ? `: ${report.reason}` : "";
  const write = report && report.ok ? console.info : console.warn;
  write.call(console, `${line}${reason}`, report);
}

export async function warmWebGLPrograms(renderer, root, camera, scene) {
  // Three r170 publishes WebGPU cache entries before the native pipeline
  // exists. A live draw sharing that entry can call setPipeline(undefined).
  // WebGPU batches use compileObjectPipeline, which waits for that pipeline
  // before the mesh is attached. This warm path stays WebGL-only.
  if (!renderer.isWebGLRenderer) return;
  if (typeof renderer.compileAsync === "function") {
    await renderer.compileAsync(root, camera, scene);
  } else {
    renderer.compile(root, camera, scene);
  }
}

/** Enough to hide stream-in hitches, few enough that the GPU process can still present. */
export const PIPELINE_COMPILE_LIMIT = 6;

let pipelineActive = 0;
const pipelineWaiters = [];

function acquirePipelineSlot() {
  if (pipelineActive < PIPELINE_COMPILE_LIMIT) {
    pipelineActive += 1;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    pipelineWaiters.push(resolve);
  });
}

function releasePipelineSlot() {
  const next = pipelineWaiters.shift();
  if (next) {
    next();
    return;
  }
  pipelineActive = Math.max(0, pipelineActive - 1);
}

export function pipelineCompileInFlight() {
  return pipelineActive;
}

/**
 * Compile one object's render pipelines on WebGPU before it joins the scene.
 * r170's signature is compileAsync(scene, camera, targetScene): the batch is
 * the first argument and the live scene is targetScene so lights are included.
 * At most PIPELINE_COMPILE_LIMIT compiles run at once. WebGL keeps warmWebGLPrograms.
 */
export async function compileObjectPipeline(renderer, object, camera, scene) {
  const webgpu = Boolean(
    renderer
    && renderer.isWebGPURenderer
    && renderer.backend
    && renderer.backend.isWebGPUBackend === true
    && typeof renderer.compileAsync === "function"
    && object,
  );
  if (!webgpu) return;
  await acquirePipelineSlot();
  try {
    await renderer.compileAsync(object, camera, scene);
  } finally {
    releasePipelineSlot();
  }
}

/**
 * r170 inserts the cache entry in Pipelines before createRenderPipelineAsync
 * assigns the GPURenderPipeline. draw() then calls setPipeline(undefined).
 * Skip that draw until the native pipeline exists.
 */
export function guardWebgpuSetPipeline(renderer) {
  const backend = renderer && renderer.backend;
  if (!backend || backend.__forgePipelineGuard || typeof backend.draw !== "function") return false;
  if (backend.isWebGPUBackend !== true) return false;
  const original = backend.draw.bind(backend);
  backend.draw = function drawWhenPipelineReady(renderObject, info) {
    const pipeline = renderObject && renderObject.pipeline;
    if (pipeline && typeof backend.get === "function") {
      const data = backend.get(pipeline);
      if (data && data.pipeline == null) return undefined;
    }
    return original(renderObject, info);
  };
  backend.__forgePipelineGuard = true;
  return true;
}

/** three r170's WebGPU getFormat() does not map RGBA_BPTC_Format (BC7), and its sRGB enum typo is "bc7-rgba-srgb". */
const RGBA_BPTC_Format = 36492;
const BC7_UNORM = "bc7-rgba-unorm";
const BC7_SRGB = "bc7-rgba-unorm-srgb";

function installBc7GetFormat(utils) {
  if (!utils || typeof utils.getFormat !== "function") return false;
  if (utils.__forgeBc7) return true;
  const original = utils.getFormat.bind(utils);
  utils.getFormat = function patchedGetFormat(texture, options) {
    if (texture && texture.format === RGBA_BPTC_Format) {
      return texture.colorSpace === "srgb" ? BC7_SRGB : BC7_UNORM;
    }
    const format = original(texture, options);
    if (format === "bc7-rgba-srgb") return BC7_SRGB;
    return format;
  };
  utils.__forgeBc7 = true;
  return true;
}

export function patchWebgpuBc7Format(renderer) {
  const backend = renderer && renderer.backend;
  if (!backend) return false;
  if (installBc7GetFormat(backend.textureUtils)) return true;
  if (backend.__forgeBc7Pending) return false;
  backend.__forgeBc7Pending = true;
  let current = backend.textureUtils;
  try {
    Object.defineProperty(backend, "textureUtils", {
      configurable: true,
      enumerable: true,
      get() { return current; },
      set(value) {
        current = value;
        installBc7GetFormat(value);
      },
    });
    return false;
  } catch {
    return false;
  }
}

/**
 * The module graph supplies its own renderer and scene classes from the same build.
 * `three.module.js` exports `WebGLRenderer`. `three.webgpu.js` does not. A stale
 * prebundle or a `webgl`-mode dist still has `WebGLRenderer`, and constructing it
 * used to report plain "WebGL" even when WebGPU was the choice.
 * When this call asked for WebGPU and `WebGPURenderer` exists, use it.
 */
export async function createBackendRenderer(THREE, options = {}) {
  const requested = options.requested || "";
  const wantGpu = requested === "webgpu" || requested === "webgpu-strict";
  const hasWebGL = typeof THREE.WebGLRenderer === "function";
  const hasWebGPU = typeof THREE.WebGPURenderer === "function";
  if (!(wantGpu && hasWebGPU) && hasWebGL) {
    if (requested === "webgpu-strict") {
      throw new Error(options.reason || "this Three build has no WebGPURenderer");
    }
    const reason = wantGpu ? (options.reason || "this Three build has no WebGPURenderer") : "";
    return {
      renderer: new THREE.WebGLRenderer({ antialias: true }),
      backend: backendLabel({ isWebGLRenderer: true }, { requested: wantGpu ? requested : "", reason }),
      nodes: false,
      webgpu: false,
      reason,
      requested,
      note: options.note || "",
    };
  }
  const gpuRenderer = new THREE.WebGPURenderer({ antialias: true });
  const initializing = Promise.resolve().then(() => gpuRenderer.init());
  let timer;
  try {
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("WebGPU init timed out")), 8000);
    });
    await Promise.race([initializing, timeout]);
    const backend = backendLabel(gpuRenderer);
    // r170's getFallback already swapped in WebGLBackend. Reject it so boot can
    // record the reason and open the classic WebGL graph instead of a silent label.
    if (backend !== "WebGPU") throw new Error("WebGPU init used the WebGL2 backend");
    patchWebgpuBc7Format(gpuRenderer);
    guardWebgpuSetPipeline(gpuRenderer);
    return { renderer: gpuRenderer, backend, nodes: true, webgpu: true, reason: "", requested, note: options.note || "" };
  } catch (error) {
    // If the timeout fired first, release the renderer once initialization settles.
    void initializing.finally(() => gpuRenderer.dispose()).catch(() => {});
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
