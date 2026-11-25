import { rendererChoice, stripLegacyGpu } from "./backend.js";
import { migrateStorage } from "./storage.js";

try {
  migrateStorage(localStorage);
  localStorage.removeItem("ocarinaforge.webgpu");
  localStorage.removeItem("bancwright.webgpu");
} catch {
  /* private mode has no storage */
}

async function start() {
  const search = location.search;
  const cleaned = stripLegacyGpu(search);
  if (cleaned !== null) history.replaceState(history.state, "", `${location.pathname}${cleaned}${location.hash}`);
  if (import.meta.env.MODE !== "webgl" && rendererChoice(cleaned ?? search) === "webgpu") {
    let note = "WebGPU is unavailable in this browser. Using WebGL2.";
    let gpu = false;
    if (navigator.gpu) {
      try {
        const viewport = await import("./viewport.js?three=webgpu");
        await viewport.prepareViewportRenderer();
        gpu = true;
      } catch (error) {
        console.warn("WebGPU could not start", error);
        note = "WebGPU could not start. Using WebGL2.";
      }
    }
    if (gpu) return import("./main.js?three=webgpu");
    const viewport = await import("./viewport.js");
    await viewport.prepareViewportRenderer(note);
  }
  return import("./main.js");
}

start().catch((error) => console.error("Could not load Ocarina Forge", error));
