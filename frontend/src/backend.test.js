import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { drawCount, rendererChoice, rendererSearch } from "./backend.js";
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
  assert.match(backend, /isWebGPUBackend !== true/);
  assert.match(viewport, /attachTerrainColor\(material\)/);
  assert.match(viewport, /createBackendRenderer\(THREE/);
  assert.match(html, /id="use-webgpu"/);
  const node = readFileSync(new URL("./terrain-node.js", import.meta.url), "utf8");
  assert.match(node, /attribute\("uv1", "vec2"\)/);
  assert.match(node, /attribute\("uv2", "vec2"\)/);
  assert.match(node, /attribute\("uv3", "vec2"\)/);
  assert.match(node, /\.discard\(\)/);
  assert.match(node, /material\.colorNode/);
});
