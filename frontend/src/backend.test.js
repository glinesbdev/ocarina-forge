import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { backendLabel, drawCount, rendererChoice, rendererSearch } from "./backend.js";
import { formatStats } from "./frame.js";

test("WebGL is the default and WebGPU is opt-in", () => {
  assert.equal(rendererChoice(""), "webgl");
  assert.equal(rendererChoice("?gpu=1"), "webgpu");
  assert.equal(rendererChoice("?renderer=webgpu"), "webgpu");
  assert.equal(rendererChoice("?gpu=0"), "webgl");
  assert.equal(rendererChoice("?renderer=webgl&gpu=1"), "webgl");
  assert.equal(rendererSearch("?scene=kokiri", "webgpu"), "?scene=kokiri&gpu=1");
  assert.equal(rendererSearch("?gpu=1&scene=kokiri", "webgl"), "?scene=kokiri");
});

test("draw counts come from the backend that actually records them", () => {
  assert.equal(drawCount({ calls: 220 }, false), 220);
  assert.equal(drawCount({ calls: 1, drawCalls: 180 }, true), 180);
  assert.equal(drawCount(null, true), 0);
});

test("the WebGL2 fallback is not labeled as real WebGPU", () => {
  assert.equal(backendLabel({ isWebGPURenderer: true, backend: { isWebGPUBackend: true } }), "WebGPU");
  assert.equal(backendLabel({ isWebGPURenderer: true, backend: { isWebGLBackend: true } }), "WebGPU (WebGL2 fallback)");
  assert.equal(backendLabel({ isWebGPURenderer: true, backend: {} }), "WebGPU (WebGL2 fallback)");
  assert.equal(backendLabel({}), "WebGL");
  assert.equal(
    formatStats({
      backend: "WebGPU (WebGL2 fallback)",
      rendering: true,
      fps: 50,
      frameMs: 20,
      draws: 30,
      instances: 12,
      culled: 0,
      separate: 40,
      tris: 1000,
    }),
    "WebGPU (WebGL2 fallback) · 50 fps · 20 ms · 30 draws · 12 inst · 0 culled · 40 separate · 1k tris",
  );
});

test("the stats line names the backend and the frame time", () => {
  assert.equal(
    formatStats({
      backend: "WebGPU",
      rendering: true,
      fps: 58,
      frameMs: 17.24,
      draws: 40,
      instances: 4000,
      culled: 900,
      separate: 10553,
      tris: 80000,
    }),
    "WebGPU · 58 fps · 17.2 ms · 40 draws · 4000 inst · 900 culled · 10553 separate · 80k tris",
  );
  assert.equal(
    formatStats({ backend: "WebGL", rendering: false, fps: 60, frameMs: 16.6, draws: 240, separate: 10553, tris: 1600000 }),
    "WebGL · idle · 16.6 ms · 240 draws · 10553 separate · 1.6M tris",
  );
});

test("the default viewport still builds a WebGLRenderer", () => {
  const backend = readFileSync(new URL("./backend.js", import.meta.url), "utf8");
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(backend, /choice !== "webgpu"|rendererChoice\(search\) !== "webgpu"/);
  assert.match(backend, /new THREE\.WebGLRenderer\(\{ antialias: true \}\)/);
  assert.match(backend, /WebGPU \(WebGL2 fallback\)/);
  assert.match(backend, /backendLabel\(gpuRenderer\)/);
  assert.match(viewport, /attachTerrainColor\(material\)/);
  assert.match(viewport, /createBackendRenderer\(THREE/);
  assert.match(html, /id="use-webgpu"/);
  const node = readFileSync(new URL("./terrain-node.js", import.meta.url), "utf8");
  assert.match(node, /attribute\("uv1", "vec2"\)/);
  assert.match(node, /attribute\("uv2", "vec2"\)/);
  assert.match(node, /attribute\("uv3", "vec2"\)/);
  assert.match(node, /\.discard\(\)/);
  assert.match(node, /material\.colorNode/);
  assert.match(node, /pow\(max\(abs\(normal\), vec3\(0\.0001\)\), vec3\(4\)\)/);
  assert.doesNotMatch(node, /(?<!\.)\bnormalize\s*\(/);
  const called = new Set();
  for (const match of node.matchAll(/(?<!\.)\b([A-Za-z_][A-Za-z0-9_]*)\s*\(/g)) called.add(match[1]);
  const imported = new Set([...node.matchAll(/^\s+([A-Za-z_][A-Za-z0-9_]*),?$/gm)].map((match) => match[1]));
  const locals = new Set(["terrainCell", "terrainSample", "terrainTri", "attachTerrainColor", "Fn", "if", "for", "while", "switch", "catch"]);
  for (const name of called) {
    if (locals.has(name)) continue;
    assert.equal(imported.has(name), true, `${name} is called but not imported from three/tsl`);
  }
});
