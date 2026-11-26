import assert from "node:assert/strict";
import test from "node:test";

import { formatTextureDebug, noteTexturePipelineError, resetTexturePipelineError, textureDebugEnabled, texturePipelineError } from "./texture-debug.js";

test("texture debug is off unless the flag or the stored value is 1", () => {
  assert.equal(textureDebugEnabled("", null), false);
  assert.equal(textureDebugEnabled("?three=webgpu", "0"), false);
  assert.equal(textureDebugEnabled("?texdebug=1", null), true);
  assert.equal(textureDebugEnabled("", "1"), true);
});

test("texture debug reports maps, uv, and whether the source uploaded", () => {
  const text = formatTextureDebug({
    shown: true,
    batches: 2,
    meshMaps: 0,
    partMaps: 1,
    litOnly: 0,
    missingUv: 1,
    foreign: 0,
    uploaded: 0,
    profileTex: 3,
    samples: [{
      name: "field",
      meshMap: false,
      partMap: true,
      uv: false,
      channel: 0,
      compressed: true,
      foreign: false,
      uploaded: false,
      sameMaterial: false,
    }],
  });
  assert.match(text, /^forge texdebug shown=1 batches=2 meshMaps=0 partMaps=1/);
  assert.match(text, /missingUv=1 foreign=0 uploaded=0 profileTex=3/);
  assert.match(text, /field meshMap=0 partMap=1 uv=0 ch=0 compressed=1/);
});

test("the texture pipeline warns once and keeps the first error", () => {
  resetTexturePipelineError();
  const warns = [];
  const original = console.warn;
  console.warn = (message) => warns.push(String(message));
  try {
    noteTexturePipelineError(new Error("Textures are not loaded."));
    noteTexturePipelineError("Textures are not loaded.");
    noteTexturePipelineError("texture download failed (404)");
    assert.equal(texturePipelineError(), "Textures are not loaded.");
    assert.deepEqual(warns, [
      "Texture pipeline: Textures are not loaded.",
      "Texture pipeline: texture download failed (404)",
    ]);
    const text = formatTextureDebug({
      shown: true,
      allow: true,
      requested: 4,
      fetched: 4,
      decoded: 0,
      assigned: 0,
      error: texturePipelineError(),
    });
    assert.match(text, /allow=1 requested=4 fetched=4 decoded=0 assigned=0 error=Textures are not loaded\./);
  } finally {
    console.warn = original;
    resetTexturePipelineError();
  }
});
