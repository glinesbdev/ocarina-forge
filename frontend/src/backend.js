/**
 * WebGL stays the default. WebGPU is opt-in (`?gpu=1` or `?renderer=webgpu`)
 * because r170's WebGPU backend is a trial, not a proven win on Kokiri.
 * A missing adapter falls back to the same WebGLRenderer the default uses.
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
 * `calls` for how many times render() ran.
 */
export function drawCount(info, webgpu) {
  if (!info) return 0;
  if (webgpu) return Number(info.drawCalls) || 0;
  return Number(info.calls) || 0;
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
    if (!gpuRenderer.backend || gpuRenderer.backend.isWebGPUBackend !== true) {
      gpuRenderer.dispose();
      gpuRenderer = null;
      return {
        renderer: webgl(),
        backend: "WebGL",
        note: "WebGPU did not start. Using WebGL.",
        nodes: false,
      };
    }
    return { renderer: gpuRenderer, backend: "WebGPU", note: "", nodes: true };
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
