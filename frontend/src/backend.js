/**
 * WebGL stays the default. WebGPU is opt-in (`?gpu=1` or `?renderer=webgpu`)
 * because r170's WebGPU backend is a trial, not a proven win on Kokiri.
 * No navigator.gpu at all still uses WebGLRenderer. A missing adapter keeps
 * WebGPURenderer on its WebGL2 backend and the stats line says so.
 */

export function rendererChoice(search) {
  const raw = typeof search === "string" ? search : "";
  const params = new URLSearchParams(raw.charAt(0) === "?" ? raw.slice(1) : raw);
  const named = (params.get("renderer") || "").toLowerCase();
  const gpu = params.get("gpu");
  if (named === "webgl" || gpu === "0") return "webgl";
  if (named === "webgpu" || gpu === "1") return "webgpu";
  return "webgl";
}

/** Search string for a reload. Other query keys are kept. */
export function rendererSearch(search, choice) {
  const raw = typeof search === "string" ? search : "";
  const params = new URLSearchParams(raw.charAt(0) === "?" ? raw.slice(1) : raw);
  params.delete("renderer");
  if (choice === "webgpu") params.set("gpu", "1");
  else params.delete("gpu");
  const text = params.toString();
  return text ? `?${text}` : "";
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
