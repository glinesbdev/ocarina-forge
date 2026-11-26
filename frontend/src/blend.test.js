import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
const page = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const node = readFileSync(new URL("./blend-node.js", import.meta.url), "utf8");

test("the moss mix is a soft threshold on the vertex mask", () => {
  assert.match(viewport, /smoothstep\(0\.35, 0\.65, mask\)/);
  assert.match(viewport, /blendSkinVertex > 0\.5/);
  assert.match(viewport, /low \*= vBlendMask\.rgb/);
  assert.match(viewport, /attribute vec4 blendMask/);
  assert.match(viewport, /vMossUv/);
  assert.match(page, /id="blend-mask"/);
});

test("one blend program mixes roughness and the overlay normal", () => {
  assert.match(viewport, /roughnessFactor = mix\(roughnessFactor, surface1\.g, blendWeight\)/);
  assert.match(viewport, /metalnessFactor = mix\(metalnessFactor, surface1\.b, blendWeight\)/);
  assert.match(viewport, /normalize\(tbn \* overlayN\)/);
  assert.match(viewport, /customProgramCacheKey = \(\) => \(useUv1 \? "blend\|uv1" : "blend"\)/);
  assert.doesNotMatch(viewport, /customProgramCacheKey = \(\) => `blend\|\$\{blend\.channel\}/);
});

test("the node path uses the same threshold and the overlay's own scale", () => {
  assert.match(node, /smoothstep\(float\(0\.35\), float\(0\.65\)/);
  assert.match(node, /blend\.skinVertex/);
  assert.match(node, /skinColor/);
  assert.match(node, /attribute\("blendMask"/);
  assert.match(node, /blendScaleUniform/);
  assert.match(node, /roughnessNode/);
  assert.match(node, /metalnessNode/);
  assert.match(node, /aoNode/);
  assert.match(node, /normalMap/);
});
