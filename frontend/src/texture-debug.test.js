import assert from "node:assert/strict";
import test from "node:test";

import { formatTextureDebug, textureDebugEnabled } from "./texture-debug.js";

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
