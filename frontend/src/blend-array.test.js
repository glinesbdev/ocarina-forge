import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { BLEND_LAYER_MAP } from "./texture-array.js";

function splitTop(input) {
  const args = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < input.length; i += 1) {
    const ch = input[i];
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    else if (ch === "," && depth === 0) {
      args.push(input.slice(start, i).trim());
      start = i + 1;
    }
  }
  const tail = input.slice(start).trim();
  if (tail) args.push(tail);
  return args;
}

function arraySamples(wgsl) {
  const names = new Set();
  for (const match of wgsl.matchAll(/var\s+(\w+)\s*:\s*texture_2d_array/g)) names.add(match[1]);
  const calls = [];
  for (const match of wgsl.matchAll(/texture(?:Sample(?:Level|Bias|Compare|Grad|BaseClamp)?|Load)\s*\(/g)) {
    const open = match.index + match[0].length;
    let depth = 1;
    let i = open;
    for (; i < wgsl.length && depth > 0; i += 1) {
      if (wgsl[i] === "(") depth += 1;
      else if (wgsl[i] === ")") depth -= 1;
    }
    const args = splitTop(wgsl.slice(open, i - 1));
    const first = args[0] || "";
    if (![...names].some((name) => first.split(/[^A-Za-z0-9_]/).includes(name))) continue;
    const kind = match[0].startsWith("textureLoad") ? "load" : "sample";
    calls.push({ argc: args.length, first, kind, args });
  }
  return { names: [...names], calls };
}

test("blend node reads a0, a1, and c0 from the layer map", () => {
  const source = readFileSync(new URL("./blend-node.js", import.meta.url), "utf8");
  assert.match(source, /BLEND_LAYER_MAP\.a0/);
  assert.match(source, /BLEND_LAYER_MAP\.a1/);
  assert.match(source, /BLEND_LAYER_MAP\.c0/);
  assert.equal(BLEND_LAYER_MAP.a0, 0);
  assert.equal(BLEND_LAYER_MAP.a1, 1);
  assert.equal(BLEND_LAYER_MAP.c0, 2);
  const glsl = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  assert.match(glsl, /texture2D\(blendMap/);
  assert.match(glsl, /WebGL keeps a per-image key/);
});

test("generated blend-array WGSL passes array_index for a0 and a1", async () => {
  const THREE = await import("three/webgpu");
  const { bindArrayMaterial } = await import("./cutout-node.js");
  const { attachBlend } = await import("./blend-node.js");
  const { createLayerTexture, isArrayTexture, STOCK_TEXTURE_SLOTS, writeBlendLayerTexel, writeBlendPlacementTexel } = await import("./texture-array.js");

  const canvas = {
    width: 4,
    height: 4,
    style: {},
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getContext() { return null; },
  };
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]), 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1]), 2));
  geometry.setAttribute("blendMask", new THREE.BufferAttribute(new Float32Array(12), 4));
  geometry.setIndex([0, 1, 2]);

  function compressedArray(seed) {
    const data = new Uint8Array(64);
    data[0] = seed;
    const texture = new THREE.CompressedArrayTexture(
      [{ data, width: 8, height: 8 }],
      8, 8, 2,
      THREE.RGBA_BPTC_Format,
    );
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.generateMipmaps = false;
    texture.flipY = false;
    texture.needsUpdate = true;
    return texture;
  }

  function shaderFor(renderer, object) {
    renderer.hasFeature = () => false;
    if (renderer._bindings == null) {
      renderer._bindings = { getSharedBindGroup(group) { return group; } };
    }
    const builder = renderer.backend.createNodeBuilder(object, renderer);
    builder.camera = new THREE.PerspectiveCamera();
    builder.build();
    return builder.fragmentShader || "";
  }

  function indexed(wgsl, label) {
    const parsed = arraySamples(wgsl);
    assert.ok(parsed.names.length > 0, `${label} declares texture_2d_array`);
    const samples = parsed.calls.filter((call) => call.kind === "sample");
    assert.ok(samples.length >= 2, `${label} samples a0 and a1`);
    for (const call of samples) {
      assert.ok(call.argc >= 4, `${label} textureSample includes array_index (${call.argc} args)`);
    }
    assert.match(wgsl, /textureLoad/, `${label} reads the a0/a1/c0 lookup`);
    return parsed;
  }

  const renderer = new THREE.WebGPURenderer({ canvas, antialias: false });
  const albedo = compressedArray(1);
  const moss = compressedArray(2);
  const rough = compressedArray(3);
  const lookup = createLayerTexture(4, 3);
  writeBlendLayerTexel(lookup, 0, 1, 2, 1, 3);
  writeBlendPlacementTexel(lookup, 0, 4, 2, 0.25, 0);

  const blended = new THREE.MeshStandardMaterial();
  blended.map = albedo;
  blended.roughnessMap = rough;
  blended.metalnessMap = rough;
  blended.userData.blend = { channel: "g", uv: "_u0", scale: [4, 2], translate: [0.25, 0] };
  blended.userData.blendMap = moss;
  blended.userData.blendChannelUniform = { value: 1 };
  blended.userData.blendUv1Uniform = { value: 0 };
  blended.userData.blendDebugUniform = { value: 0 };
  blended.userData.forgeLayerTexture = lookup;
  bindArrayMaterial(blended);
  for (const slot of STOCK_TEXTURE_SLOTS) {
    assert.equal(isArrayTexture(blended[slot]), false, slot);
  }
  assert.equal(blended.userData.forgeArrayAlbedo, albedo);
  const wgsl = shaderFor(renderer, new THREE.Mesh(geometry, blended));
  indexed(wgsl, "blend array");
  assert.match(wgsl, /textureSample\s*\([^)]+,[^)]+,[^)]+,[^)]+\)/);

  const flat = new THREE.DataTexture(new Uint8Array(16), 2, 2);
  flat.colorSpace = THREE.SRGBColorSpace;
  flat.needsUpdate = true;
  const plain = new THREE.MeshStandardMaterial();
  plain.map = flat;
  plain.userData.blend = { channel: "r", uv: "_u0", scale: [1, 1], translate: [0, 0] };
  plain.userData.blendMap = flat;
  plain.userData.blendScaleUniform = { value: new THREE.Vector2(1, 1) };
  plain.userData.blendTranslateUniform = { value: new THREE.Vector2(0, 0) };
  plain.userData.blendDebugUniform = { value: 0 };
  plain.userData.blendChannelUniform = { value: 0 };
  plain.userData.blendUv1Uniform = { value: 0 };
  attachBlend(plain);
  const flatShader = shaderFor(renderer, new THREE.Mesh(geometry, plain));
  assert.equal(arraySamples(flatShader).names.length, 0, "an unpacked blend does not sample texture_2d_array");

  geometry.dispose();
  albedo.dispose();
  moss.dispose();
  rough.dispose();
  lookup.dispose();
  flat.dispose();
  renderer.dispose();
});
