export function rendererChoice(search) {
  const params = searchParams(search);
  const named = (params.get("renderer") || "").toLowerCase();
  if (named === "webgl" || params.get("webgpu") === "0" || params.get("gpu") === "0") return "webgl";
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

/** Stats label. A WebGPURenderer on the WebGL2 backend must not read as real WebGPU. */
export function backendLabel(renderer) {
  if (!renderer || renderer.isWebGPURenderer !== true) return "WebGL";
  if (renderer.backend && renderer.backend.isWebGPUBackend === true) return "WebGPU";
  return "WebGPU (WebGL2 fallback)";
}

export async function warmWebGLPrograms(renderer, root, camera, scene) {
  // Three r170 publishes WebGPU cache entries before the native pipeline
  // exists. A live draw sharing that entry can call setPipeline(undefined).
  if (!renderer.isWebGLRenderer) return;
  if (typeof renderer.compileAsync === "function") {
    await renderer.compileAsync(root, camera, scene);
  } else {
    renderer.compile(root, camera, scene);
  }
}

/** The module graph supplies its own renderer and scene classes from the same build. */
export async function createBackendRenderer(THREE) {
  if (typeof THREE.WebGLRenderer === "function") {
    return { renderer: new THREE.WebGLRenderer({ antialias: true }), backend: "WebGL", nodes: false };
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
    if (backend !== "WebGPU") throw new Error("No usable WebGPU adapter was found.");
    return { renderer: gpuRenderer, backend, nodes: true };
  } catch (error) {
    // If the timeout fired first, release the renderer once initialization settles.
    void initializing.finally(() => gpuRenderer.dispose()).catch(() => {});
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
