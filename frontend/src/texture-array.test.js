import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import * as THREE from "three";
import { patchWebgpuBptc } from "../webgpu-bptc-plugin.js";
import { stageBatchKey } from "./instance.js";
import {
  MAX_ARRAY_LAYERS,
  arrayGroupKey,
  arraysReady,
  compressedByteLength,
  createTextureArrayPool,
  patchArrayShader,
  textureArraysEnabled,
  writeLayerTexture,
} from "./texture-array.js";

function bc7(width, height, colorSpace, fill = 0) {
  const levels = [];
  let levelWidth = width;
  let levelHeight = height;
  const count = Math.floor(Math.log2(Math.max(width, height))) + 1;
  for (let level = 0; level < count; level += 1) {
    const data = new Uint8Array(compressedByteLength(levelWidth, levelHeight, 36492));
    data.fill(fill);
    levels.push({ data, width: levelWidth, height: levelHeight });
    levelWidth = Math.max(1, levelWidth >> 1);
    levelHeight = Math.max(1, levelHeight >> 1);
  }
  const texture = new THREE.CompressedTexture(levels, width, height, 36492);
  texture.colorSpace = colorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.needsUpdate = true;
  return texture;
}

function geometry() {
  return {
    getIndex() { return [0, 1, 2]; },
    attributes: { position: {}, normal: {}, uv: {} },
  };
}

function material(map, extras = {}) {
  return {
    map,
    side: 0,
    transparent: false,
    opacity: 1,
    alphaTest: 0,
    color: new THREE.Color(extras.color ?? 0xffffff),
    userData: extras.userData || {},
    ...extras,
    map,
  };
}

test("r170 WebGPU uploads compressed arrays through the patched BC7 format", () => {
  const utils = readFileSync(new URL("../node_modules/three/src/renderers/webgpu/utils/WebGPUTextureUtils.js", import.meta.url), "utf8");
  const constants = readFileSync(new URL("../node_modules/three/src/renderers/webgpu/utils/WebGPUConstants.js", import.meta.url), "utf8");
  assert.match(utils, /texture\.isCompressedTexture === true \|\| texture\.isCompressedArrayTexture === true/);
  assert.match(utils, /const isTextureArray = textureDescriptorGPU\.size\.depthOrArrayLayers > 1/);
  assert.match(utils, /for \( let j = 0; j < depth; j \+\+ \)/);
  assert.match(utils, /offset: j \* bytesPerImage/);
  const patchedUtils = patchWebgpuBptc(utils);
  const patchedConstants = patchWebgpuBptc(constants);
  assert.match(patchedUtils, /case RGBA_BPTC_Format:/);
  assert.match(patchedUtils, /"bc7-rgba-unorm-srgb"/);
  assert.match(patchedConstants, /BC7RGBAUnormSRGB: 'bc7-rgba-unorm-srgb'/);
  assert.equal(patchedConstants.includes("BC7RGBAUnormSRGB: 'bc7-rgba-srgb'"), false);
});

test("compressed textures of one size class share an array and drop tint", () => {
  const pool = createTextureArrayPool(THREE);
  const sizes = [64, 128, 256, 512];
  const described = [];
  const legacy = new Set();
  for (const size of sizes) {
    for (let i = 0; i < 8; i += 1) {
      for (const color of [0xffffff, 0x336699]) {
        const map = bc7(size, size, THREE.SRGBColorSpace, i + 1);
        const spec = pool.describe(material(map, { color }), geometry());
        described.push(spec);
        legacy.add(stageBatchKey({
          image: `tex-${size}-${i}`,
          alpha: "opaque",
          side: 0,
          tint: new THREE.Color(color).getHexString(),
          indexed: "idx",
          attributes: "normal,position,uv",
        }));
      }
    }
  }
  pool.finalize();
  const keys = new Set(described.map((spec) => spec.key));
  assert.equal(legacy.size, 64);
  assert.equal(keys.size, 4);
  assert.equal(described.every((spec) => spec.whiten && spec.usesArray), true);
  const arrays = new Set(described.map((spec) => spec.slots.map.group.array));
  assert.equal(arrays.size, 4);
  for (const array of arrays) {
    assert.equal(array.isCompressedArrayTexture, true);
    assert.equal(array.image.depth, 16);
    assert.equal(array.colorSpace, THREE.SRGBColorSpace);
    assert.equal(array.mipmaps[0].data.byteLength, compressedByteLength(array.image.width, array.image.height, 36492) * 16);
  }
  pool.dispose();
});

test("alpha mode and side stay separate, and a repeated layout does not", () => {
  const pool = createTextureArrayPool(THREE);
  const map = bc7(64, 64, THREE.SRGBColorSpace, 4);
  const other = bc7(64, 64, THREE.SRGBColorSpace, 5);
  const opaque = pool.describe(material(map), geometry());
  const again = pool.describe(material(other), {
    getIndex() { return [0, 1, 2]; },
    attributes: { uv: {}, position: {}, normal: {} },
  });
  const mask = pool.describe(material(bc7(64, 64, THREE.SRGBColorSpace), { alphaTest: 0.5 }), geometry());
  const sided = pool.describe(material(bc7(64, 64, THREE.SRGBColorSpace), { side: 2 }), geometry());
  assert.equal(opaque.key, again.key);
  assert.notEqual(opaque.key, mask.key);
  assert.notEqual(opaque.key, sided.key);
  const blend = {
    map: bc7(64, 64, THREE.SRGBColorSpace),
    side: 0,
    transparent: false,
    alphaTest: 0,
    color: new THREE.Color(0xff00aa),
    userData: { blend: { channel: "r" } },
  };
  const custom = pool.describe(blend, geometry());
  assert.equal(custom.custom, true);
  assert.equal(custom.whiten, false);
  assert.equal(custom.usesArray, false);
  pool.dispose();
});

test("colour space, mips, and a sealed group each open their own array", () => {
  const pool = createTextureArrayPool(THREE);
  const srgb = pool.describe(material(bc7(32, 32, THREE.SRGBColorSpace)), geometry());
  const linear = pool.describe(material(bc7(32, 32, THREE.LinearSRGBColorSpace)), geometry());
  assert.notEqual(srgb.slots.map.groupId, linear.slots.map.groupId);
  const shortMips = bc7(32, 32, THREE.SRGBColorSpace);
  shortMips.mipmaps = [shortMips.mipmaps[0]];
  const oneMip = pool.describe(material(shortMips), geometry());
  assert.notEqual(oneMip.slots.map.groupId, srgb.slots.map.groupId);
  pool.finalize();
  const later = pool.describe(material(bc7(32, 32, THREE.SRGBColorSpace, 9)), geometry());
  assert.notEqual(later.slots.map.groupId, srgb.slots.map.groupId);
  pool.finalize();
  assert.equal(later.slots.map.group.array.image.depth, 1);
  assert.equal(srgb.slots.map.group.array.image.depth, 1);
  pool.dispose();
});

test("an ORM map shared by roughness, metalness, and AO is one layer", () => {
  const pool = createTextureArrayPool(THREE);
  const orm = bc7(64, 64, THREE.LinearSRGBColorSpace, 7);
  const albedo = bc7(64, 64, THREE.SRGBColorSpace, 3);
  const spec = pool.describe({
    map: albedo,
    roughnessMap: orm,
    metalnessMap: orm,
    aoMap: orm,
    side: 0,
    transparent: false,
    opacity: 1,
    alphaTest: 0,
    color: new THREE.Color(0xffffff),
    userData: {},
  }, geometry());
  pool.finalize();
  assert.equal(spec.slots.roughnessMap.array, true);
  assert.equal(spec.slots.metalnessMap.same, true);
  assert.equal(spec.slots.aoMap.same, true);
  assert.equal(spec.slots.metalnessMap.layer, spec.slots.roughnessMap.layer);
  assert.equal(spec.slots.metalnessMap.channel, 2);
  assert.equal(spec.slots.roughnessMap.group.array.image.depth, 1);
  assert.equal(spec.key.includes("roughnessMap:"), true);
  assert.equal(spec.key.includes("metalnessMap:same"), true);
  assert.equal(spec.key.includes("aoMap:same"), true);
  pool.dispose();
});

test("the layer table stores the instance index in RGBA8", () => {
  const pool = createTextureArrayPool(THREE);
  const first = pool.describe(material(bc7(16, 16, THREE.SRGBColorSpace, 1)), geometry());
  const second = pool.describe(material(bc7(16, 16, THREE.SRGBColorSpace, 2)), geometry());
  const normal = bc7(16, 16, THREE.LinearSRGBColorSpace, 3);
  const withNormal = pool.describe({
    ...material(bc7(16, 16, THREE.SRGBColorSpace, 4)),
    normalMap: normal,
  }, geometry());
  pool.finalize();
  const mesh = { userData: {} };
  const texture = writeLayerTexture(THREE, mesh, [
    { instanceId: 0, part: { arrayBatch: first } },
    { instanceId: 1, part: { arrayBatch: second } },
    { instanceId: 3, part: { arrayBatch: withNormal } },
  ]);
  const data = texture.image.data;
  assert.equal(data[0], first.slots.map.layer);
  assert.equal(data[4], second.slots.map.layer);
  assert.equal(data[12], withNormal.slots.map.layer);
  assert.equal(data[13], withNormal.slots.normalMap.layer);
  assert.equal(mesh.userData.forgeLayers, texture);
  pool.dispose();
});

test("a full array of 256 layers opens the next slice", () => {
  const pool = createTextureArrayPool(THREE);
  let last = null;
  for (let i = 0; i < MAX_ARRAY_LAYERS; i += 1) {
    last = pool.describe(material(bc7(8, 8, THREE.SRGBColorSpace, i & 255)), geometry());
  }
  const overflow = pool.describe(material(bc7(8, 8, THREE.SRGBColorSpace, 3)), geometry());
  assert.equal(last.slots.map.layer, MAX_ARRAY_LAYERS - 1);
  assert.notEqual(overflow.slots.map.groupId, last.slots.map.groupId);
  assert.equal(overflow.slots.map.layer, 0);
  pool.dispose();
});

test("the node material samples the compressed array by layer", async () => {
  globalThis.self = globalThis;
  const THREE = await import("three/webgpu");
  const { attachArrayMaterial } = await import("./texture-array-node.js");
  const pool = createTextureArrayPool(THREE);
  function level(fill, colorSpace) {
    const data = new Uint8Array(16);
    data.fill(fill);
    const texture = new THREE.CompressedTexture([{ data, width: 4, height: 4 }], 4, 4, 36492);
    texture.colorSpace = colorSpace;
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;
    return texture;
  }
  const albedo = level(1, THREE.SRGBColorSpace);
  const normal = level(2, THREE.LinearSRGBColorSpace);
  const orm = level(3, THREE.LinearSRGBColorSpace);
  const spec = pool.describe({
    map: albedo,
    normalMap: normal,
    roughnessMap: orm,
    metalnessMap: orm,
    aoMap: orm,
    side: 0,
    transparent: false,
    opacity: 1,
    alphaTest: 0.4,
    color: new THREE.Color(0xff8844),
    roughness: 1,
    metalness: 1,
    aoMapIntensity: 1,
    userData: {},
  }, geometry());
  pool.finalize();
  const material = new THREE.MeshStandardNodeMaterial({
    color: 0xffffff,
    map: spec.slots.map.group.array,
    normalMap: spec.slots.normalMap.group.array,
    roughnessMap: spec.slots.roughnessMap.group.array,
    alphaTest: 0.4,
  });
  const buffer = new THREE.BufferGeometry();
  buffer.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
  buffer.setAttribute("normal", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]), 3));
  buffer.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1]), 2));
  buffer.setIndex([0, 1, 2]);
  const mesh = new THREE.BatchedMesh(2, 8, 8, material);
  mesh.addGeometry(buffer);
  mesh.addInstance(0);
  mesh.userData.forgeLayers = writeLayerTexture(THREE, mesh, [{ instanceId: 0, part: { arrayBatch: spec } }]);
  attachArrayMaterial(material, mesh, spec);
  const program = new THREE.NodeMaterial();
  program.fragmentNode = material.colorNode;
  program.forceSinglePass = true;
  mesh.material = program;
  const canvas = {
    width: 4,
    height: 4,
    style: {},
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getContext() { return null; },
  };
  const renderer = new THREE.WebGPURenderer({ canvas, antialias: false });
  renderer.hasFeature = () => false;
  const builder = renderer.backend.createNodeBuilder(mesh, renderer);
  builder.camera = new THREE.PerspectiveCamera();
  builder.build();
  const frag = builder.fragmentShader || "";
  assert.match(frag, /texture_2d_array/);
  assert.match(frag, /discard/);
  const glRenderer = new THREE.WebGPURenderer({ canvas, antialias: false, forceWebGL: true });
  glRenderer.hasFeature = () => false;
  glRenderer.backend.extensions = { has() { return false; } };
  const glBuilder = glRenderer.backend.createNodeBuilder(mesh, glRenderer);
  glBuilder.camera = new THREE.PerspectiveCamera();
  glBuilder.build();
  assert.match(glBuilder.fragmentShader || "", /sampler2DArray/);
  pool.dispose();
});

test("the WebGL patch samples the array at the instance layer", () => {
  const shader = {
    vertexShader: "#include <common>\nvoid main() {\n#include <project_vertex>\n}\n",
    fragmentShader: [
      "uniform sampler2D map;",
      "uniform sampler2D normalMap;",
      "uniform sampler2D roughnessMap;",
      "vec4 sampledDiffuseColor = texture2D( map, vMapUv );",
      "vec4 texelRoughness = texture2D( roughnessMap, vRoughnessMapUv );",
      "normal = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;",
    ].join("\n"),
  };
  patchArrayShader(shader, {
    slots: {
      map: { array: true, channel: 0 },
      normalMap: { array: false, channel: -1 },
      roughnessMap: { array: true, channel: 2, same: false },
    },
  });
  assert.match(shader.vertexShader, /vForgeBatchId = getIndirectIndex\( gl_DrawID \)/);
  assert.match(shader.fragmentShader, /uniform sampler2DArray map;/);
  assert.match(shader.fragmentShader, /texture\( map, vec3\( vMapUv, forgeLayer\(0\) \) \)/);
  assert.match(shader.fragmentShader, /texture\( roughnessMap, vec3\( vRoughnessMapUv, forgeLayer\(2\) \) \)/);
  assert.match(shader.fragmentShader, /uniform sampler2D normalMap;/);
  assert.match(shader.fragmentShader, /texture2D\( normalMap, vNormalMapUv \)/);
  assert.match(shader.fragmentShader, /float forgeLayer\(int channel\)/);
  assert.equal(arrayGroupKey({ isCompressedTexture: false }), "");
});

function fakeWebgpu({ fail = false } = {}) {
  const gpus = new Map();
  const copies = [];
  const writes = [];
  const renderer = {
    copies,
    writes,
    backend: {
      isWebGPUBackend: true,
      device: {
        pushErrorScope() {},
        popErrorScope() {
          return Promise.resolve(fail ? { message: "validation" } : null);
        },
        queue: {
          writeTexture(destination, data, _layout, size) {
            writes.push({ origin: destination.origin, mip: destination.mipLevel, bytes: data.byteLength, size });
          },
        },
      },
      get(texture) {
        return gpus.get(texture) || null;
      },
    },
    _textures: {
      updateTexture(texture) {
        gpus.set(texture, { texture: { label: texture.name || "gpu" }, initialized: true, isDefaultTexture: false });
      },
    },
    copyTextureToTexture(src, dst, region, position, level) {
      this._textures.updateTexture(src);
      this._textures.updateTexture(dst);
      copies.push({ level, layer: position ? position.z : 0, width: region.width, height: region.height });
    },
  };
  return renderer;
}

test("WebGPU writes every mip from that layer's own bytes", async () => {
  const pool = createTextureArrayPool(THREE);
  const map = bc7(16, 16, THREE.SRGBColorSpace, 9);
  const spec = pool.describe(material(map), geometry());
  pool.finalize({ concatenate: false });
  const renderer = fakeWebgpu();
  const result = await pool.publish(renderer);
  assert.equal(result.failed, 0);
  assert.equal(result.copied, 1);
  assert.equal(arraysReady(spec), true);
  assert.equal(renderer.copies.length, 0);
  assert.deepEqual(renderer.writes.map((write) => write.size.width), [16, 8, 4, 4, 4]);
  assert.ok(renderer.writes.every((write) => write.bytes > 0 && write.origin.z === 0));
  assert.equal(map.userData.forgeCpuReleased, true);
  assert.equal(map.mipmaps[0].data.byteLength, 0);
  pool.dispose();
});

test("an array with no texel bytes stays unresolved", async () => {
  const pool = createTextureArrayPool(THREE);
  pool.setRetainMips(false);
  const map = bc7(16, 16, THREE.SRGBColorSpace, 4);
  const spec = pool.describe(material(map), geometry());
  for (const level of map.mipmaps) level.data = new Uint8Array(0);
  pool.finalize({ concatenate: false });
  const result = await pool.publish(fakeWebgpu());
  assert.equal(result.failed, 1);
  assert.equal(arraysReady(spec), false);
  assert.equal(spec.slots.map.group.filled, false);
  assert.equal(spec.slots.map.group.array, null);
  pool.dispose();
});

test("texture arrays are off unless the page opts in, and a disabled pool does not copy mips", () => {
  assert.equal(textureArraysEnabled("", null), false);
  assert.equal(textureArraysEnabled("?arrays=1", null), true);
  assert.equal(textureArraysEnabled("?textureArrays=0", "1"), false);
  assert.equal(textureArraysEnabled("", "1"), true);
  const pool = createTextureArrayPool(THREE);
  pool.setEnabled(false);
  const map = bc7(16, 16, THREE.SRGBColorSpace, 4);
  const before = map.mipmaps[0].data.byteLength;
  const spec = pool.describe(material(map), geometry());
  assert.equal(spec.usesArray, false);
  assert.equal(spec.whiten, true);
  assert.equal(spec.slots.map.array, false);
  assert.equal(map.mipmaps[0].data.byteLength, before);
  assert.equal(map.userData.forgeArrayHold, undefined);
  assert.equal(arraysReady(spec), false);
  pool.dispose();
});

test("a failed array upload is not sampled, so the batch keeps its own texture", async () => {
  const pool = createTextureArrayPool(THREE);
  const map = bc7(16, 16, THREE.SRGBColorSpace, 4);
  const spec = pool.describe(material(map), geometry());
  pool.finalize({ concatenate: false });
  const result = await pool.publish(fakeWebgpu({ fail: true }));
  assert.equal(result.failed, 1);
  assert.equal(arraysReady(spec), false);
  assert.equal(spec.slots.map.group.array, null);
  assert.equal(map.userData.forgeCpuReleased, undefined);
  assert.ok(map.mipmaps[0].data.byteLength > 0);
  pool.dispose();
});

test("the array colour node does not emit a constant white", () => {
  const source = readFileSync(new URL("./texture-array-node.js", import.meta.url), "utf8");
  assert.equal(source.includes("vec4(1, 1, 1, 1)"), false);
  assert.equal(source.includes("vec4(1,1,1,1)"), false);
});
