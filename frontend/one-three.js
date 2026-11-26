/**
 * WebGPU and WebGL three builds each set `window.__THREE__`. Lights, materials,
 * and BatchedMesh only work when the renderer and the objects are the same copy.
 * The WebGPU graph uses three.webgpu.js for `three` and `three/webgpu`.
 * r187 puts TSL in three.tsl.js. three.webgpu.js imports that file and does not
 * re-export `reference`, so `three/tsl` stays on three.tsl.js. The WebGL2 graph
 * uses `three.module.js`.
 *
 * Prebundled addons inline the WebGL build, so the WebGPU graph loads the raw
 * addon sources and points their `three` import at the WebGPU build.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const WEBGPU_MARK = "three=webgpu";

const SCRIPT = /\.(?:[cm]?js|jsx|ts|tsx)$/;
const SRC_DIR = fileURLToPath(new URL("./src/", import.meta.url));
const THREE_SPECIFIERS = new Set(["three", "three/webgpu", "three/tsl"]);

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

/** r187 keeps TSL in three.tsl.js, next to three.webgpu.js. */
export function tslBuild(webgpuFile) {
  const file = moduleId(webgpuFile);
  const slash = file.lastIndexOf("/");
  return `${slash === -1 ? "" : file.slice(0, slash)}/three.tsl.js`;
}

/** `three/tsl`, `./three.tsl.js`, and a marked absolute path are the same file. */
export function isTslRequest(source) {
  const bare = moduleId(unmark(source).split("?")[0]);
  if (bare === "three/tsl" || bare === "three/tsl.js") return true;
  return bare.endsWith("/three.tsl.js");
}

function tslFileId(resolved, webgpuFile) {
  const fallback = tslBuild(webgpuFile);
  const raw = typeof resolved === "string" ? resolved : resolved?.id;
  if (!raw || resolved?.external) return fallback;
  const file = moduleId(unmark(raw).split("?")[0]);
  return file.endsWith("/three.tsl.js") ? file : fallback;
}

/** The resolved file that must be the WebGPU build, or null to keep it. A TSL prebundle points at three.tsl.js. */
export function sameThreeBuild(resolvedId, webgpuFile) {
  const file = moduleId(unmark(resolvedId).split("?")[0]);
  if (file.endsWith("/three.tsl.js")) return null;
  if (/\/\.vite\/deps\/three_tsl\.js$/.test(file)) return tslBuild(webgpuFile);
  const webgpu = moduleId(webgpuFile);
  if (file === webgpu || file.endsWith("/three.webgpu.js")) return webgpuFile;
  if (file.endsWith("/three.module.js")) return webgpuFile;
  if (/\/\.vite\/deps\/three(?:_webgpu)?\.js$/.test(file)) return webgpuFile;
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

const exportCache = new Map();

/** Names a three build file actually exports. `export { a as b }` counts as `b`. */
export function exportedNames(file) {
  const id = moduleId(file);
  const cached = exportCache.get(id);
  if (cached) return cached;
  const text = fs.readFileSync(file, "utf8");
  const names = new Set();
  for (const block of text.matchAll(/export\s*\{([^}]+)\}/g)) {
    for (const part of block[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/).pop().trim();
      if (/^[A-Za-z_$]/.test(name)) names.add(name);
    }
  }
  for (const match of text.matchAll(/export\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)/g)) {
    names.add(match[1]);
  }
  exportCache.set(id, names);
  return names;
}

function withoutComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:\\])\/\/.*$/gm, "$1");
}

function withoutLiterals(source) {
  return withoutComments(source)
    .replace(/`(?:\\[\s\S]|[^`])*`/g, "``")
    .replace(/'(?:\\.|[^'])*'/g, "''")
    .replace(/"(?:\\.|[^"])*"/g, '""');
}

export function namedThreeImports(source) {
  const found = [];
  const code = withoutComments(source);
  const re = /(?:^|\n)\s*import\s+(?:type\s+)?([^'";]*?)\s+from\s+["']([^"']+)["']/g;
  for (const match of code.matchAll(re)) {
    const specifier = match[2];
    if (!THREE_SPECIFIERS.has(specifier)) continue;
    const brace = match[1].match(/\{([\s\S]*)\}/);
    if (!brace) continue;
    for (const part of brace[1].split(",")) {
      const trimmed = part.trim();
      if (!trimmed) continue;
      const pieces = trimmed.split(/\s+as\s+/);
      const exported = pieces[0].trim();
      const local = pieces[pieces.length - 1].trim();
      if (!/^[A-Za-z_$]/.test(exported)) continue;
      found.push({ specifier, exported, local });
    }
  }
  return found;
}

function entryFiles(specifier, webgpuFile, appModule) {
  const build = path.dirname(webgpuFile);
  if (specifier === "three/tsl") return [tslBuild(webgpuFile)];
  if (specifier === "three/webgpu") return [moduleId(webgpuFile)];
  if (specifier === "three") {
    const moduleBuild = moduleId(path.join(build, "three.module.js"));
    return appModule ? [moduleId(webgpuFile), moduleBuild] : [moduleBuild];
  }
  return [];
}

function lineOf(source, needle) {
  const index = source.indexOf(needle);
  if (index < 0) return 1;
  return source.slice(0, index).split("\n").length;
}

function localNames(source) {
  const names = new Set();
  const code = withoutLiterals(source);
  for (const match of code.matchAll(/function\s+([A-Za-z_$][\w$]*)/g)) names.add(match[1]);
  for (const match of code.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) names.add(match[1]);
  for (const match of code.matchAll(/class\s+([A-Za-z_$][\w$]*)/g)) names.add(match[1]);
  return names;
}

/**
 * Import names the resolved three entry does not export, and TSL calls that
 * were never imported. `appModule` files are in both graphs, so `three` has to
 * export the name from three.webgpu.js and from three.module.js.
 */
export function problemsInSource(source, fileLabel, webgpuFile, appModule = true) {
  const problems = [];
  const imported = new Set();
  for (const binding of namedThreeImports(source)) {
    imported.add(binding.local);
    for (const entry of entryFiles(binding.specifier, webgpuFile, appModule)) {
      const names = exportedNames(entry);
      if (names.has(binding.exported)) continue;
      const where = moduleId(entry).split("/").slice(-1)[0];
      problems.push(
        `${fileLabel}:${lineOf(source, binding.exported)} imports ${binding.exported} from "${binding.specifier}", and ${where} does not export it`,
      );
    }
  }
  const importedTsl = namedThreeImports(source).some((binding) => binding.specifier === "three/tsl" || binding.specifier === "three/webgpu");
  if (!importedTsl) return problems;
  const tsl = exportedNames(tslBuild(webgpuFile));
  const locals = localNames(source);
  const code = withoutLiterals(source);
  const called = new Set();
  for (const match of code.matchAll(/(?<!\.)\b([A-Za-z_$][\w$]*)\s*\(/g)) called.add(match[1]);
  for (const name of called) {
    if (!tsl.has(name) || imported.has(name) || locals.has(name)) continue;
    problems.push(
      `${fileLabel}:${lineOf(source, name)} calls ${name}, which three.tsl.js exports, but the file does not import it`,
    );
  }
  return problems;
}

export function collectThreeImportProblems(srcDir = SRC_DIR, webgpuFile, { includeTests = false } = {}) {
  const problems = [];
  const files = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const file = path.join(dir, name);
      if (fs.statSync(file).isDirectory()) walk(file);
      else if (file.endsWith(".js")) files.push(file);
    }
  };
  walk(srcDir);
  for (const file of files) {
    const testFile = file.endsWith(".test.js");
    if (testFile && !includeTests) continue;
    const source = fs.readFileSync(file, "utf8");
    const label = moduleId(path.relative(srcDir, file));
    problems.push(...problemsInSource(source, label, webgpuFile, !testFile));
  }
  return problems;
}

export function oneThreePlugin(webgpuFile) {
  let tslId = null;
  let resolvingTsl = false;
  // One id for `three/tsl` and for three.webgpu.js's `./three.tsl.js`, marked or
  // not. Vite's id is used only when it is actually three.tsl.js. On Windows the
  // `?three=webgpu` resolve came back as three.webgpu.js, which does not export
  // `reference`, and sameThreeBuild would have kept that file.
  async function canonicalTsl(context) {
    if (tslId) return tslId;
    const fallback = tslBuild(webgpuFile);
    if (resolvingTsl) return fallback;
    resolvingTsl = true;
    try {
      const resolve = context && context.resolve;
      if (typeof resolve !== "function") {
        tslId = fallback;
        return tslId;
      }
      let resolved;
      try {
        resolved = await resolve.call(context, "./three.tsl.js", moduleId(webgpuFile), { skipSelf: true });
      } catch {
        tslId = fallback;
        return tslId;
      }
      tslId = tslFileId(resolved, webgpuFile);
      return tslId;
    } finally {
      resolvingTsl = false;
    }
  }

  return {
    name: "one-three-on-webgpu",
    enforce: "pre",
    buildStart() {
      const problems = collectThreeImportProblems(SRC_DIR, webgpuFile);
      if (!problems.length) return;
      this.error(`three import does not match the r187 entry:\n${problems.join("\n")}`);
    },
    async resolveId(source, importer) {
      if (source.startsWith("\0") || (importer && importer.startsWith("\0"))) return null;
      // Marked and unmarked graphs, and the relative import inside three.webgpu.js,
      // have to return this same id. A query on the importer must not take another path.
      if (isTslRequest(source)) return canonicalTsl(this);
      const fromMarked = Boolean(importer && importer.includes(WEBGPU_MARK));
      const sourceMarked = source.includes(WEBGPU_MARK);
      if (!fromMarked && !sourceMarked) return null;
      const bare = moduleId(unmark(source).split("?")[0]);
      if (bare === "three" || bare === "three/webgpu") return moduleId(webgpuFile);
      const raw = rawDependency(bare, webgpuFile);
      if (raw) return mark(moduleId(raw));
      const importerFile = importer ? unmark(importer) : undefined;
      const resolved = await this.resolve(bare, importerFile, { skipSelf: true });
      if (!resolved || resolved.external) return resolved;
      const replacement = sameThreeBuild(resolved.id, webgpuFile);
      if (replacement) {
        if (isTslRequest(replacement)) return canonicalTsl(this);
        return moduleId(replacement);
      }
      const file = moduleId(unmark(resolved.id).split("?")[0]);
      if (isTslRequest(file)) return canonicalTsl(this);
      if (!SCRIPT.test(file)) return moduleId(resolved.id);
      return mark(moduleId(resolved.id));
    },
    load(id) {
      if (!id.includes(WEBGPU_MARK) || id.includes("\0")) return null;
      return fs.readFileSync(unmark(id).split("?")[0], "utf8");
    },
  };
}
