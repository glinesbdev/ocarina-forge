import { initFailureReason, inspectWebGPU, logWebGPUReport, rendererChoice, stripLegacyGpu, webgpuFallbackNote } from "./backend.js";
import { migrateStorage } from "./storage.js";

try {
  migrateStorage(localStorage);
  localStorage.removeItem("ocarinaforge.webgpu");
  localStorage.removeItem("bancwright.webgpu");
} catch {
  /* private mode has no storage */
}

function showStrictWebGPUError(report) {
  console.error("WebGPU strict mode refused to fall back", report);
  const host = document.getElementById("app") || document.body;
  host.replaceChildren();
  const panel = document.createElement("section");
  panel.className = "strict-webgpu";
  panel.setAttribute("role", "alert");
  const title = document.createElement("h1");
  title.textContent = "WebGPU did not start";
  const reason = document.createElement("p");
  reason.textContent = (report && report.reason) || "WebGPU did not start.";
  const detail = document.createElement("pre");
  detail.textContent = JSON.stringify({
    code: report && report.code,
    reason: report && report.reason,
    message: report && report.message,
    adapterInfo: report && report.adapterInfo,
    limits: report && report.limits,
  }, null, 2);
  const link = document.createElement("a");
  link.href = "/troubleshooting.html";
  link.textContent = "Troubleshooting";
  panel.append(title, reason, detail, link);
  host.append(panel);
}

async function startWebGL(report) {
  const viewport = await import("./viewport.js");
  await viewport.prepareViewportRenderer({
    requested: "webgpu",
    reason: report.reason,
    note: webgpuFallbackNote(report.reason),
  });
}

async function start() {
  const search = location.search;
  const cleaned = stripLegacyGpu(search);
  if (cleaned !== null) history.replaceState(history.state, "", `${location.pathname}${cleaned}${location.hash}`);
  const choice = rendererChoice(cleaned ?? search);
  const webglBundle = import.meta.env.MODE === "webgl";
  if (choice === "webgpu-strict" && webglBundle) {
    const report = {
      ok: false,
      code: "webgl-bundle",
      reason: "this bundle was built in webgl mode",
      adapterInfo: null,
      limits: null,
    };
    logWebGPUReport(report);
    showStrictWebGPUError(report);
    return;
  }
  if (webglBundle && choice === "webgpu") {
    const report = {
      ok: false,
      code: "webgl-bundle",
      reason: "this bundle was built in webgl mode",
      adapterInfo: null,
      limits: null,
    };
    logWebGPUReport(report);
    await startWebGL(report);
    return import("./main.js");
  }
  if (!webglBundle && (choice === "webgpu" || choice === "webgpu-strict")) {
    const gpu = typeof navigator !== "undefined" ? navigator.gpu : undefined;
    const report = await inspectWebGPU(gpu);
    logWebGPUReport(report);
    if (!report.ok) {
      if (choice === "webgpu-strict") {
        showStrictWebGPUError(report);
        return;
      }
      await startWebGL(report);
      return import("./main.js");
    }
    try {
      const viewport = await import("./viewport.js?three=webgpu");
      const prepared = await viewport.prepareViewportRenderer({ requested: choice });
      if (!prepared.webgpu) {
        if (prepared.renderer && typeof prepared.renderer.dispose === "function") prepared.renderer.dispose();
        throw new Error(prepared.reason || "WebGPU renderer was not created");
      }
      return import("./main.js?three=webgpu");
    } catch (error) {
      const failed = {
        ok: false,
        code: "init-exception",
        reason: initFailureReason(error),
        message: error && error.message,
        adapterInfo: report.adapterInfo,
        limits: report.limits,
      };
      logWebGPUReport(failed);
      console.warn("WebGPU could not start", error);
      if (choice === "webgpu-strict") {
        showStrictWebGPUError(failed);
        return;
      }
      await startWebGL(failed);
      return import("./main.js");
    }
  }
  return import("./main.js");
}

start().catch((error) => console.error("Could not load Ocarina Forge", error));
