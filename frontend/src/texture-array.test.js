import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";
import { releaseTextureCpu } from "./gpu-texture.js";
import { createBatchFiller } from "./batch-fill.js";
import { refreshBatchKeys } from "./instance.js";
import { readFileSync } from "node:fs";
import { cloneMaterialPreservingTextures, materialForGeometry } from "./instance.js";
import {
  arrayMapToken,
  canPackTexture,
  compressedCopyPlan,
  compressedLayerBytes,
  createTextureArrayPacker,
  isArrayTexture,
  layerWritePlan,
  releaseArraySlots,
  uploadWouldSeeEmptyMip,
  STOCK_TEXTURE_SLOTS,
  textureArrayGroupKey,
  writeLayerTexel,
  createLayerTexture,
  packBlocker,
  replaceLayerTexture,
} from "./texture-array.js";

function bc7(width, height, seed, { colorSpace = THREE.SRGBColorSpace, mips = 1 } = {}) {
  const levels = [];
  let w = width;
  let h = height;
  for (let level = 0; level < mips; level += 1) {
    const bytes = compressedLayerBytes(w, h, THREE.RGBA_BPTC_Format);
    const data = new Uint8Array(bytes);
    data[0] = seed;
    data[1] = level;
    levels.push({ data, width: w, height: h });
    w = Math.max(1, w >> 1);
    h = Math.max(1, h >> 1);
  }
  const texture = new THREE.CompressedTexture(levels, width, height, THREE.RGBA_BPTC_Format);
  texture.colorSpace = colorSpace;
  texture.generateMipmaps = false;
  texture.flipY = false;
  return texture;
}

test("same-size BC7 images share one array and one batch key", () => {
  const packer = createTextureArrayPacker();
  const textures = [];
  for (let i = 0; i < 40; i += 1) textures.push(bc7(8, 8, i + 1));
  const keys = new Set(textures.map((texture) => arrayMapToken(texture, packer)));
  assert.equal(keys.size, 1);
  assert.equal([...keys][0].startsWith("arr:"), true);
  const stats = packer.stats();
  assert.equal(stats.arrays, 1);
  assert.equal(stats.layers, 40);
  const placed = packer.layerOf(textures[0]);
  const bytes = compressedLayerBytes(8, 8, THREE.RGBA_BPTC_Format);
  assert.equal(bytes, 64);
  assert.equal(placed.array.mipmaps[0].data[0], 1);
  assert.equal(placed.array.mipmaps[0].data[bytes], 2);
  assert.equal(placed.array.mipmaps[0].data[39 * bytes], 40);
  assert.equal(packer.layerOf(textures[0].clone()).layer, 0);
});

test("size class and color space stay in separate arrays", () => {
  const packer = createTextureArrayPacker();
  const small = bc7(8, 8, 1);
  const large = bc7(16, 16, 2);
  const linear = bc7(8, 8, 3, { colorSpace: THREE.LinearSRGBColorSpace });
  const short = bc7(8, 8, 4);
  short.mipmaps[0] = { data: new Uint8Array(8), width: 8, height: 8 };
  assert.equal(canPackTexture(short), false);
  assert.equal(arrayMapToken(short, packer), "");
  assert.notEqual(textureArrayGroupKey(small), textureArrayGroupKey(large));
  assert.notEqual(textureArrayGroupKey(small), textureArrayGroupKey(linear));
  assert.notEqual(arrayMapToken(small, packer), arrayMapToken(large, packer));
  assert.notEqual(arrayMapToken(small, packer), arrayMapToken(linear, packer));
  assert.equal(packer.stats().arrays, 3);
});

test("a full group leaves the next image on its own key", () => {
  const packer = createTextureArrayPacker({ initialDepth: 2, maxDepth: 2 });
  const first = bc7(8, 8, 1);
  const second = bc7(8, 8, 2);
  const third = bc7(8, 8, 3);
  assert.equal(arrayMapToken(first, packer).startsWith("arr:"), true);
  assert.equal(arrayMapToken(second, packer).startsWith("arr:"), true);
  assert.equal(arrayMapToken(third, packer), "");
  assert.equal(packer.stats().layers, 2);
});

test("growing the array keeps the layers already packed", () => {
  const packer = createTextureArrayPacker({ initialDepth: 2, maxDepth: 8 });
  const textures = [bc7(8, 8, 1), bc7(8, 8, 2), bc7(8, 8, 3)];
  arrayMapToken(textures[0], packer);
  arrayMapToken(textures[1], packer);
  const beforeGrow = packer.layerOf(textures[0]).array;
  arrayMapToken(textures[2], packer);
  const array = packer.layerOf(textures[0]).array;
  const bytes = compressedLayerBytes(8, 8, THREE.RGBA_BPTC_Format);
  assert.notEqual(array, beforeGrow);
  assert.ok(array.image.depth >= 3);
  assert.equal(array.mipmaps[0].data[0], 1);
  assert.equal(array.mipmaps[0].data[2 * bytes], 3);
  assert.equal(packer.stats().arrays, 1);
  const swapped = packer.takeSwapped();
  assert.equal(swapped.get(beforeGrow), array);
});

test("families that share a shader key build one mesh", () => {
  const families = ["Body:0", "Cliff:2", "Hair:1", "Rock:4", "Tree:5", "Door:6", "Sign:7", "Pot:8"];
  const parts = families.map((materialKey) => ({
    material: {},
    geometry: {},
    materialKey,
    batchKey: "",
  }));
  assert.equal(refreshBatchKeys(parts, () => "arr:8x8|opaque"), true);
  assert.equal(new Set(parts.map((part) => part.batchKey)).size, 1);
  assert.equal(parts[0].batchKey, "arr:8x8|opaque");
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const material = new THREE.MeshBasicMaterial();
  const filler = createBatchFiller({
    createMesh({ instances, vertices, indices, material: batchMaterial }) {
      return new THREE.BatchedMesh(instances, vertices, Math.max(indices, 1), batchMaterial);
    },
  });
  const records = families.map((name) => ({ geometry, part: { name } }));
  const result = filler.append(parts[0].batchKey, material, records);
  assert.equal(result.added.length, 8);
  assert.equal(filler.stats().batches, 1);
  assert.equal(filler.stats().constructed, 1);
  geometry.dispose();
});

test("without a packer an image keeps its own key", () => {
  const texture = bc7(8, 8, 1);
  assert.equal(arrayMapToken(texture, null), "");
  assert.equal(arrayMapToken(texture, undefined), "");
});

test("a clone and a no-uv material do not keep an array on a stock slot", () => {
  const array = new THREE.CompressedArrayTexture(
    [{ data: new Uint8Array(64), width: 8, height: 8 }],
    8, 8, 2,
    THREE.RGBA_BPTC_Format,
  );
  const material = new THREE.MeshStandardMaterial();
  material.map = array;
  material.aoMap = array;
  material.normalMap = array;
  material.emissiveMap = array;
  material.alphaMap = array;
  const copy = cloneMaterialPreservingTextures(material);
  for (const slot of STOCK_TEXTURE_SLOTS) {
    assert.equal(isArrayTexture(copy[slot]), false, slot);
  }
  assert.equal(copy.userData.forgeArrayAlbedo, array);
  assert.equal(copy.userData.forgeArrayAo, array);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
  const bare = new THREE.MeshStandardMaterial();
  bare.map = array;
  bare.roughnessMap = array;
  const drawn = materialForGeometry(bare, geometry);
  assert.equal(isArrayTexture(drawn.map), false);
  assert.equal(isArrayTexture(drawn.roughnessMap), false);
  assert.equal(drawn.userData.forgeArrayAlbedo, array);
  geometry.dispose();
  array.dispose();
});

test("WebGL never constructs the array packer", () => {
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  assert.match(viewport, /createTextureArrayPacker\(\{/);
  assert.match(viewport, /copyLayers: copyPackedArrayLayers/);
  assert.match(viewport, /uploadLayer: uploadPackedArrayLayer/);
  assert.match(viewport, /forgePackedHold/);
  assert.match(viewport, /releaseTextureCpu\(texture, \{ uploaded: true \}\)/);
  assert.match(viewport, /compressedCopyPlan\(src\.mipmaps/);
  assert.match(viewport, /createCommandEncoder\(\{ label: "packed-array-grow" \}\)/);
  assert.doesNotMatch(viewport, /backend\.copyTextureToTexture/);
  assert.match(viewport, /WebGL keeps a per-image key/);
  assert.match(viewport, /cloneMaterialPreservingTextures\(source\)/);
  assert.match(viewport, /settlePackedArrays\(\)/);
  assert.match(viewport, /releaseSwapped\(\)/);
  assert.match(viewport, /replaceLayerTexture\(/);
  assert.match(viewport, /pinAuthoredMatrix\(mesh\)/);
  assert.match(viewport, /pinSurfaceMeshes\(\)/);
  assert.match(viewport, /imagePack: imagePackDump\(\)/);
  assert.doesNotMatch(viewport, /material = source\.clone\(\)/);
  assert.doesNotMatch(viewport, /material\.map = albedo\.array/);
  assert.doesNotMatch(viewport, /material\.normalMap = placed/);
  assert.doesNotMatch(viewport, /material\.roughnessMap = placed/);
  assert.doesNotMatch(viewport, /material\.roughnessMap = userData\.forgeArrayRough/);
});

test("a single image allocates one layer and compact drops spare capacity", () => {
  const packer = createTextureArrayPacker();
  const only = bc7(8, 8, 9);
  arrayMapToken(only, packer);
  assert.equal(packer.layerOf(only).array.image.depth, 1);
  const textures = [bc7(8, 8, 1), bc7(8, 8, 2), bc7(8, 8, 3)];
  const grown = createTextureArrayPacker();
  for (const texture of textures) arrayMapToken(texture, grown);
  const fat = grown.layerOf(textures[0]).array;
  assert.ok(fat.image.depth > 3);
  const bytes = compressedLayerBytes(8, 8, THREE.RGBA_BPTC_Format);
  grown.compact();
  const slim = grown.layerOf(textures[0]).array;
  assert.equal(slim.image.depth, 3);
  assert.notEqual(slim, fat);
  assert.equal(slim.mipmaps[0].data[0], 1);
  assert.equal(slim.mipmaps[0].data[2 * bytes], 3);
  assert.equal(fat.mipmaps[0].data.byteLength, 0);
  assert.equal(fat.source.dataReady, false);
  assert.equal(uploadWouldSeeEmptyMip(fat), false);
  assert.equal(grown.takeSwapped().get(fat), slim);
});

test("an array no batch samples is disposed with its mip buffers", () => {
  const packer = createTextureArrayPacker();
  const keep = bc7(8, 8, 1);
  const drop = bc7(16, 16, 2);
  arrayMapToken(keep, packer);
  arrayMapToken(drop, packer);
  const kept = packer.layerOf(keep).array;
  const dropped = packer.layerOf(drop).array;
  packer.retain(new Set([kept]));
  assert.equal(packer.stats().arrays, 1);
  assert.equal(dropped.mipmaps[0].data.byteLength, 0);
  assert.equal(dropped.source.dataReady, false);
  assert.equal(uploadWouldSeeEmptyMip(dropped), false);
  assert.equal(packer.sources().has(keep), true);
});

test("an array on userData is not enumerable and does not serialize", () => {
  const array = {
    isTexture: true,
    isCompressedArrayTexture: true,
    toJSON() {
      throw new Error("texture was serialized");
    },
  };
  const material = { map: array, userData: {} };
  releaseArraySlots(material);
  assert.equal(material.map, null);
  assert.equal(material.userData.forgeArrayAlbedo, array);
  assert.equal(Object.keys(material.userData).includes("forgeArrayAlbedo"), false);
  assert.equal(JSON.stringify(material.userData).includes("forgeArrayAlbedo"), false);
});

test("the layer texel stores albedo, normal, and roughness", () => {
  const texture = createLayerTexture(4);
  assert.equal(writeLayerTexel(texture, 2, 5, 6, 7, 0), true);
  assert.equal(texture.image.data[8], 5);
  assert.equal(texture.image.data[9], 6);
  assert.equal(texture.image.data[10], 7);
  assert.equal(writeLayerTexel(texture, 4, 1, 0, 0, 0), false);
  texture.dispose();
});

function logicalChain(size) {
  const mips = [];
  let width = size;
  let height = size;
  while (true) {
    mips.push({ width, height });
    if (width === 1 && height === 1) break;
    width = Math.max(1, width >> 1);
    height = Math.max(1, height >> 1);
  }
  return mips;
}

test("compressed copies round a partial block up to 4", () => {
  for (const size of [768, 384, 96]) {
    const logical = logicalChain(size);
    const plan = compressedCopyPlan(logical, THREE.RGBA_BPTC_Format, 2);
    assert.ok(plan);
    assert.equal(plan.length, logical.length);
    for (const copy of plan) {
      assert.equal(copy.width % 4, 0, `${size} level ${copy.level} width ${copy.width}`);
      assert.equal(copy.height % 4, 0, `${size} level ${copy.level} height ${copy.height}`);
      assert.ok(copy.width >= 4 && copy.height >= 4);
      assert.equal(copy.depth, 2);
    }
    const extent = (logicalWidth) => plan.find((copy) => logical[copy.level].width === logicalWidth);
    assert.equal(extent(6).width, 8);
    assert.equal(extent(6).height, 8);
    assert.equal(extent(3).width, 4);
    assert.equal(extent(1).width, 4);
    assert.equal(extent(size).width, size);
  }
  assert.equal(compressedCopyPlan([], THREE.RGBA_BPTC_Format, 1), null);
});

test("a failed GPU grow drops the new array and keeps the previous one", () => {
  let built = null;
  const packer = createTextureArrayPacker({
    copyLayers(src, dst) {
      built = dst;
      return false;
    },
    uploadLayer() {
      return true;
    },
  });
  const first = bc7(8, 8, 1);
  const second = bc7(8, 8, 2);
  arrayMapToken(first, packer);
  const array = packer.layerOf(first).array;
  array.userData.forgeGpuUploaded = true;
  assert.equal(releaseTextureCpu(array, { uploaded: true }), true);
  assert.equal(arrayMapToken(second, packer), "");
  assert.equal(packer.layerOf(first).array, array);
  assert.equal(packer.stats().arrays, 1);
  assert.equal(built.userData.forgeCpuReleased, true);
  assert.equal(built.source.dataReady, false);
  assert.notEqual(built, array);
});

test("an upload plan rejects a zero-length mip", () => {
  const empty = { width: 4, height: 4, data: new Uint8Array(0) };
  assert.equal(layerWritePlan(empty, THREE.RGBA_BPTC_Format), null);
  const broken = bc7(4, 4, 1);
  broken.mipmaps[0].data = new Uint8Array(0);
  assert.equal(uploadWouldSeeEmptyMip(broken), true);
  const sound = bc7(4, 4, 2);
  const plan = layerWritePlan(sound.mipmaps[0], sound.format);
  assert.equal(plan.data.byteLength, compressedLayerBytes(4, 4, THREE.RGBA_BPTC_Format));
  assert.equal(uploadWouldSeeEmptyMip(sound), false);
});

test("growing after the CPU copy is released copies on the GPU and does not upload an empty mip", () => {
  const copies = [];
  const uploads = [];
  const packer = createTextureArrayPacker({
    copyLayers(src, dst, depth) {
      copies.push({ src, dst, depth });
      dst.userData.forgeGpuUploaded = true;
      return true;
    },
    uploadLayer(dst, layer, writes) {
      uploads.push({ dst, layer, bytes: writes.map((mip) => mip.data.byteLength) });
      return writes.every((mip) => layerWritePlan(mip, dst.format) && mip.data.byteLength > 0);
    },
  });
  const first = bc7(8, 8, 1);
  const second = bc7(8, 8, 2);
  arrayMapToken(first, packer);
  const array = packer.layerOf(first).array;
  array.userData.forgeGpuUploaded = true;
  assert.equal(releaseTextureCpu(array, { uploaded: true }), true);
  assert.equal(array.source.dataReady, false);
  assert.equal(uploadWouldSeeEmptyMip(array), false);
  assert.equal(arrayMapToken(second, packer).startsWith("arr:"), true);
  const grown = packer.layerOf(second).array;
  assert.equal(copies.length, 1);
  assert.equal(copies[0].src, array);
  assert.equal(copies[0].dst, grown);
  assert.equal(copies[0].depth, 1);
  assert.notEqual(grown, array);
  assert.equal(grown.source.dataReady, false);
  assert.equal(uploadWouldSeeEmptyMip(grown), false);
  assert.equal(uploadWouldSeeEmptyMip(array), false);
  assert.equal(array.source.dataReady, false);
  assert.equal(uploads.length, 1);
  assert.ok(uploads[0].bytes.every((bytes) => bytes > 0));
  assert.equal(packer.layerOf(second).layer, 1);
});

test("layer indices of existing images stay put across a grow and a compact", () => {
  let disposed = 0;
  const packer = createTextureArrayPacker({ initialDepth: 1, maxDepth: 8 });
  const first = bc7(8, 8, 1);
  const second = bc7(8, 8, 2);
  const third = bc7(8, 8, 3);
  assert.equal(packer.layerOf(first).layer, 0);
  const before = packer.layerOf(first).array;
  const originalDispose = before.dispose.bind(before);
  before.dispose = () => {
    disposed += 1;
    originalDispose();
  };
  assert.equal(packer.layerOf(second).layer, 1);
  assert.equal(packer.layerOf(first).layer, 0);
  assert.equal(packer.layerOf(first).array !== before, true);
  assert.equal(disposed, 0);
  assert.equal(packer.layerOf(third).layer, 2);
  assert.equal(packer.layerOf(second).layer, 1);
  assert.equal(packer.layerOf(first).layer, 0);
  packer.compact();
  assert.equal(packer.layerOf(first).layer, 0);
  assert.equal(packer.layerOf(second).layer, 1);
  assert.equal(packer.layerOf(third).layer, 2);
  assert.equal(disposed, 0);
  packer.releaseSwapped();
  assert.ok(disposed >= 1);

  const lookup = createLayerTexture(2);
  writeLayerTexel(lookup, 0, 3, 1, 2, 0);
  writeLayerTexel(lookup, 1, 4, 5, 6, 7);
  const wider = replaceLayerTexture(lookup, 4);
  assert.equal(wider.image.data[0], 3);
  assert.equal(wider.image.data[1], 1);
  assert.equal(wider.image.data[4], 4);
  assert.equal(wider.image.data[7], 7);
  lookup.dispose();
  wider.dispose();
});

test("a padded mip still packs and a short one is logged per image", () => {
  const padded = bc7(4, 4, 1);
  const bytes = compressedLayerBytes(4, 4, THREE.RGBA_BPTC_Format);
  const extra = new Uint8Array(bytes + 16);
  extra[0] = 9;
  padded.mipmaps[0].data = extra;
  assert.equal(packBlocker(padded), "");
  const packer = createTextureArrayPacker();
  assert.equal(arrayMapToken(padded, packer).startsWith("arr:"), true);
  const short = bc7(4, 4, 2);
  short.mipmaps[0].data = new Uint8Array(4);
  const reason = packBlocker(short);
  assert.match(reason, /^mip-bytes:0:4\//);
  assert.equal(arrayMapToken(short, packer), "");
  const report = packer.imagePackReport();
  assert.equal(report.counts[reason], 1);
  assert.equal(report.images.length, 1);
  assert.equal(report.images[0].reason, reason);
  assert.equal(report.images[0].width, 4);
});
