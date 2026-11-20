import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
const page = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const node = readFileSync(new URL("./blend-node.js", import.meta.url), "utf8");

test("the moss mix is a soft threshold on the vertex mask", () => {
  assert.match(viewport, /smoothstep\(0\.35, 0\.65, mask\)/);
  assert.match(viewport, /attribute vec4 blendMask/);
  assert.match(viewport, /vMossUv/);
  assert.match(page, /id="blend-mask"/);
});

test("the node path uses the same threshold and the overlay's own scale", () => {
  assert.match(node, /smoothstep\(float\(0\.35\), float\(0\.65\)/);
  assert.match(node, /attribute\("blendMask"/);
  assert.match(node, /blendScaleUniform/);
});
