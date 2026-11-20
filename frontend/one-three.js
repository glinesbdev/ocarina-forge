/**
 * WebGPU and WebGL three builds each set `window.__THREE__`. Lights, materials,
 * and BatchedMesh only work when the renderer and the objects are the same copy.
 * `?gpu=1` loads a second module graph in which every `three` import is the
 * WebGPU build. The default page keeps `three.module.js`.
 *
 * Prebundled addons inline the WebGL build, so the WebGPU graph loads the raw
 * addon sources and points their `three` import at the WebGPU build.
 */
import fs from "node:fs";
import path from "node:path";

export const WEBGPU_MARK = "three=webgpu";

const SCRIPT = /\.(?:[cm]?js|jsx|ts|tsx)$/;

export function unmark(id) {
  const next = String(id).replace(/([?&])three=webgpu(?=&|$)/g, "");
  return next.endsWith("?") ? next.slice(0, -1) : next;
}

export function mark(id) {
  if (String(id).includes(WEBGPU_MARK)) return id;
  return `${id}${String(id).includes("?") ? "&" : "?"}${WEBGPU_MARK}`;
}

/** The resolved file that must be the WebGPU build, or null to keep it. */
export function sameThreeBuild(resolvedId, webgpuFile) {
  const file = unmark(resolvedId).split("?")[0];
  if (file === webgpuFile || file.endsWith("/three.webgpu.js") || file.endsWith("\\three.webgpu.js")) return webgpuFile;
  if (file.endsWith("/three.module.js") || file.endsWith("\\three.module.js")) return webgpuFile;
  if (/[/\\]\.vite[/\\]deps[/\\]three(?:_webgpu|_tsl)?\.js$/.test(file)) return webgpuFile;
  return null;
}

/** Raw source for a dependency Vite would otherwise prebundle with the WebGL build. */
export function rawDependency(bare, webgpuFile) {
  const threeRoot = path.resolve(path.dirname(webgpuFile), "..");
  const addon = /^three\/addons\/(.+)$/.exec(bare);
  if (addon) {
    const base = path.resolve(threeRoot, "examples/jsm", addon[1]);
    if (fs.existsSync(base)) return base;
    if (fs.existsSync(`${base}.js`)) return `${base}.js`;
  }
  if (bare === "three-mesh-bvh") {
    const entry = path.resolve(threeRoot, "../three-mesh-bvh/src/index.js");
    if (fs.existsSync(entry)) return entry;
  }
  return null;
}

export function oneThreePlugin(webgpuFile) {
  return {
    name: "one-three-on-webgpu",
    enforce: "pre",
    async resolveId(source, importer) {
      const fromMarked = Boolean(importer && importer.includes(WEBGPU_MARK));
      const sourceMarked = source.includes(WEBGPU_MARK);
      if (!fromMarked && !sourceMarked) return null;
      const bare = unmark(source);
      const raw = rawDependency(bare, webgpuFile);
      if (raw) return mark(raw);
      const importerFile = importer ? unmark(importer) : undefined;
      const resolved = await this.resolve(bare, importerFile, { skipSelf: true });
      if (!resolved || resolved.external) return resolved;
      const replacement = sameThreeBuild(resolved.id, webgpuFile);
      if (replacement) return replacement;
      const file = unmark(resolved.id).split("?")[0];
      if (!SCRIPT.test(file)) return resolved.id;
      return mark(resolved.id);
    },
    load(id) {
      if (!id.includes(WEBGPU_MARK)) return null;
      return fs.readFileSync(unmark(id).split("?")[0], "utf8");
    },
  };
}
