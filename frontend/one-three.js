/**
 * WebGPU and WebGL three builds each set `window.__THREE__`. Lights, materials,
 * and BatchedMesh only work when the renderer and the objects are the same copy.
 * The WebGPU graph uses three.webgpu.js for `three` and `three/webgpu`.
 * r187 puts TSL in three.tsl.js, which that build imports, so `three/tsl`
 * stays on three.tsl.js. The WebGL2 graph uses `three.module.js`.
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

/** Module ids use forward slashes. `path.join` on Windows puts backslashes back. */
export function moduleId(file) {
  return String(file).replaceAll("\\", "/");
}

/** r187 keeps TSL in three.tsl.js. three.webgpu.js imports that file; it does not re-export `reference`. */
export function tslBuild(webgpuFile) {
  // Fallback only. resolveId prefers Vite's resolution of `./three.tsl.js` from
  // the WebGPU build, which realpaths the file. A hand-built path can differ
  // on Windows (drive-letter case, directory case) and Rollup then loads a
  // second Node class with its own id counter.
  const file = moduleId(webgpuFile);
  const slash = file.lastIndexOf("/");
  return `${slash === -1 ? "" : file.slice(0, slash)}/three.tsl.js`;
}

async function canonicalTsl(resolve, webgpuFile) {
  if (typeof resolve !== "function") return tslBuild(webgpuFile);
  const resolved = await resolve("./three.tsl.js", moduleId(webgpuFile), { skipSelf: true });
  const id = typeof resolved === "string" ? resolved : resolved?.id;
  if (id && !resolved?.external) return id;
  return tslBuild(webgpuFile);
}

/** The resolved file that must be the WebGPU build, or null to keep it. A TSL prebundle points at three.tsl.js. */
export function sameThreeBuild(resolvedId, webgpuFile) {
  const file = unmark(resolvedId).split("?")[0];
  if (file.endsWith("/three.tsl.js") || file.endsWith("\\three.tsl.js")) return null;
  if (/[/\\]\.vite[/\\]deps[/\\]three_tsl\.js$/.test(file)) return tslBuild(webgpuFile);
  if (file === webgpuFile || file.endsWith("/three.webgpu.js") || file.endsWith("\\three.webgpu.js")) return webgpuFile;
  if (file.endsWith("/three.module.js") || file.endsWith("\\three.module.js")) return webgpuFile;
  if (/[/\\]\.vite[/\\]deps[/\\]three(?:_webgpu)?\.js$/.test(file)) return webgpuFile;
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
      if (source.startsWith("\0") || (importer && importer.startsWith("\0"))) return null;
      const fromMarked = Boolean(importer && importer.includes(WEBGPU_MARK));
      const sourceMarked = source.includes(WEBGPU_MARK);
      if (!fromMarked && !sourceMarked) return null;
      const bare = unmark(source);
      if (bare === "three" || bare === "three/webgpu") return moduleId(webgpuFile);
      if (bare === "three/tsl") return canonicalTsl(this.resolve, webgpuFile);
      const raw = rawDependency(bare, webgpuFile);
      if (raw) return mark(moduleId(raw));
      const importerFile = importer ? unmark(importer) : undefined;
      const resolved = await this.resolve(bare, importerFile, { skipSelf: true });
      if (!resolved || resolved.external) return resolved;
      const replacement = sameThreeBuild(resolved.id, webgpuFile);
      if (replacement) {
        if (moduleId(replacement).endsWith("/three.tsl.js")) return canonicalTsl(this.resolve, webgpuFile);
        return moduleId(replacement);
      }
      const file = unmark(resolved.id).split("?")[0];
      if (!SCRIPT.test(file)) return moduleId(resolved.id);
      return mark(moduleId(resolved.id));
    },
    load(id) {
      if (!id.includes(WEBGPU_MARK) || id.includes("\0")) return null;
      return fs.readFileSync(unmark(id).split("?")[0], "utf8");
    },
  };
}
