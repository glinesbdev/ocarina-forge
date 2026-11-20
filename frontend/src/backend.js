/**
 * WebGL stays the default. WebGPU is opt-in (`?webgpu=1` or `?renderer=webgpu`)
 * because r170's WebGPU backend is a trial, not a proven win on Kokiri.
 * The old `?gpu=1` checkbox flag is ignored. No navigator.gpu at all still
 * uses WebGLRenderer. A missing adapter keeps WebGPURenderer on its WebGL2
 * backend and the stats line says so.
 */

export function rendererChoice(search) {
  const params = searchParams(search);
  const named = (params.get("renderer") || "").toLowerCase();
  if (named === "webgl" || params.get("webgpu") === "0" || params.get("gpu") === "0") return "webgl";
  if (named === "webgpu" || params.get("webgpu") === "1") return "webgpu";
  return "webgl";
}

/** Search string for a reload. Other query keys are kept. `gpu` is the retired flag. */
export function rendererSearch(search, choice) {
  const params = searchParams(search);
  params.delete("renderer");
  params.delete("gpu");
  if (choice === "webgpu") params.set("webgpu", "1");
  else params.delete("webgpu");
  const text = params.toString();
  return text ? `?${text}` : "";
}

/**
 * `?gpu=1` was the stage-bar checkbox. Drop it so a saved link loads WebGL.
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

export async function createBackendRenderer(THREE, search) {
  const webgl = () => new THREE.WebGLRenderer({ antialias: true });
  if (rendererChoice(search) !== "webgpu") {
    return { renderer: webgl(), backend: "WebGL", note: "", nodes: false };
  }
  if (typeof navigator === "undefined" || !navigator.gpu) {
    return {
      renderer: webgl(),
      backend: "WebGL",
      note: "WebGPU is not in this browser. Using WebGL.",
      nodes: false,
    };
  }
  let gpuRenderer = null;
  try {
    const gpuModule = await import("three/webgpu");
    const WebGPURenderer = gpuModule.WebGPURenderer;
    gpuRenderer = new WebGPURenderer({ antialias: true });
    const timeout = new Promise((_, reject) => {
      setTimeout(() => reject(new Error("WebGPU init timed out")), 8000);
    });
    await Promise.race([gpuRenderer.init(), timeout]);
    const backend = backendLabel(gpuRenderer);
    if (backend === "WebGPU (WebGL2 fallback)") {
      return {
        renderer: gpuRenderer,
        backend,
        note: "No WebGPU adapter. Drawing with the WebGL2 fallback, not a WebGPU device.",
        nodes: true,
      };
    }
    if (backend !== "WebGPU") {
      gpuRenderer.dispose();
      gpuRenderer = null;
      return {
        renderer: webgl(),
        backend: "WebGL",
        note: "WebGPU did not start. Using WebGL.",
        nodes: false,
      };
    }
    return { renderer: gpuRenderer, backend, note: "", nodes: true };
  } catch (error) {
    console.warn(error);
    if (gpuRenderer && typeof gpuRenderer.dispose === "function") {
      try {
        gpuRenderer.dispose();
      } catch {
        /* the failed device is already gone */
      }
    }
    return {
      renderer: webgl(),
      backend: "WebGL",
      note: "WebGPU did not start. Using WebGL.",
      nodes: false,
    };
  }
}
