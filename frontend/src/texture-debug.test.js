import assert from "node:assert/strict";
import test from "node:test";

import { formatActorTextureDebug, formatTextureDebug, noteTexturePipelineError, resetTexturePipelineError, textureDebugEnabled, texturePipelineError } from "./texture-debug.js";

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
    const actor = formatActorTextureDebug({
      gyaml: "Land_LakeHrl_Tree_A_01",
      name: "Tree",
      parts: [{
        name: "Bark:0",
        material: "Mt_Bark",
        banc: "",
        sampler: "_a0",
        texture: "Tree_Alb",
        format: "bc7 1024x1024",
        map: true,
        url: "/api/model-textures/abc.ktx2",
        compressed: true,
        channel: 0,
        vertexColors: false,
        colorNode: true,
        albedoNode: true,
        blend: false,
        blendNode: false,
        moss: false,
        ao: false,
        alphaTest: 0,
        transparent: false,
        color: [1, 1, 1],
        helper: "",
        space: "srgb",
        attributes: ["position", "uv 0.000..1.000,0.000..1.000"],
      }],
      roles: [{ material: "Mt_Bark", sampler: "_a0", texture: "Tree_Alb", uv: "_u0", alpha: "opaque" }],
    });
    assert.match(actor, /forge texdebug actor/);
    assert.match(actor, /gyaml=Land_LakeHrl_Tree_A_01/);
    assert.match(actor, /sampler=_a0 texture=Tree_Alb format=bc7 1024x1024/);
    assert.match(actor, /space=srgb/);
    assert.match(actor, /albedoNode=1/);
    assert.match(actor, /blendNode=0/);
    assert.match(actor, /moss=0/);
    assert.match(actor, /attrs=position\|uv 0.000..1.000,0.000..1.000/);
    assert.match(actor, /role Mt_Bark sampler=_a0 texture=Tree_Alb uv=_u0/);
  } finally {
    console.warn = original;
    resetTexturePipelineError();
  }
});
