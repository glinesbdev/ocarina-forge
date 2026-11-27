/**
 * WebGPU texture arrays for batch keys.
 *
 * A compressed image joins a group of the same size, mip count, format,
 * color space, and sampler state. The batch key names that group, not the
 * image. Each instance stores its layer in a float texture indexed by
 * `batchIndirectIndex`. Blend, terrain, and water stay on the per-image key.
 * WebGL never builds a packer: it cannot sample an array from the stock material.
 */
import * as THREE from "three";
import { repairCompressedTexture } from "./gpu-texture.js";

const BLOCKS = new Map([
  [THREE.RGB_S3TC_DXT1_Format, { byteLength: 8, width: 4, height: 4 }],
  [THREE.RGBA_S3TC_DXT1_Format, { byteLength: 8, width: 4, height: 4 }],
  [THREE.RGBA_S3TC_DXT3_Format, { byteLength: 16, width: 4, height: 4 }],
  [THREE.RGBA_S3TC_DXT5_Format, { byteLength: 16, width: 4, height: 4 }],
  [THREE.RGBA_BPTC_Format, { byteLength: 16, width: 4, height: 4 }],
  [THREE.RGBA_ASTC_4x4_Format, { byteLength: 16, width: 4, height: 4 }],
  [THREE.RGBA_ASTC_5x4_Format, { byteLength: 16, width: 5, height: 4 }],
  [THREE.RGBA_ASTC_5x5_Format, { byteLength: 16, width: 5, height: 5 }],
  [THREE.RGBA_ASTC_6x5_Format, { byteLength: 16, width: 6, height: 5 }],
  [THREE.RGBA_ASTC_6x6_Format, { byteLength: 16, width: 6, height: 6 }],
  [THREE.RGBA_ASTC_8x5_Format, { byteLength: 16, width: 8, height: 5 }],
  [THREE.RGBA_ASTC_8x6_Format, { byteLength: 16, width: 8, height: 6 }],
  [THREE.RGBA_ASTC_8x8_Format, { byteLength: 16, width: 8, height: 8 }],
  [THREE.RGBA_ASTC_10x5_Format, { byteLength: 16, width: 10, height: 5 }],
  [THREE.RGBA_ASTC_10x6_Format, { byteLength: 16, width: 10, height: 6 }],
  [THREE.RGBA_ASTC_10x8_Format, { byteLength: 16, width: 10, height: 8 }],
  [THREE.RGBA_ASTC_10x10_Format, { byteLength: 16, width: 10, height: 10 }],
  [THREE.RGBA_ASTC_12x10_Format, { byteLength: 16, width: 12, height: 10 }],
  [THREE.RGBA_ASTC_12x12_Format, { byteLength: 16, width: 12, height: 12 }],
]);

function blockFor(format) {
  return BLOCKS.get(format) || null;
}

/** Bytes of one compressed layer at this mip, matching the WebGPU row/image pitch. */
export function compressedLayerBytes(width, height, format) {
  const block = blockFor(format);
  if (!block || !width || !height) return 0;
  const rows = Math.ceil(height / block.height);
  const bytesPerRow = Math.ceil(width / block.width) * block.byteLength;
  return bytesPerRow * rows;
}

function pair(value, fallback) {
  if (!value) return fallback;
  const x = Number(value.x);
  const y = Number(value.y);
  return `${Number.isFinite(x) ? x : 0}x${Number.isFinite(y) ? y : 0}`;
}

/**
 * Size class for a compressed image. Empty when the image cannot share an array
 * (uncompressed, unknown format, or a mip whose byte length is not one layer).
 */
export function textureArrayGroupKey(texture) {
  if (!texture || texture.isCompressedTexture !== true || texture.isCompressedArrayTexture === true) return "";
  const mips = texture.mipmaps;
  if (!Array.isArray(mips) || !mips.length) return "";
  const format = texture.format;
  if (!blockFor(format)) return "";
  const width = mips[0].width || (texture.image && texture.image.width) || 0;
  const height = mips[0].height || (texture.image && texture.image.height) || 0;
  if (!width || !height) return "";
  for (const mip of mips) {
    const bytes = compressedLayerBytes(mip.width, mip.height, format);
    if (!mip.data || mip.data.byteLength !== bytes) return "";
  }
  const space = texture.colorSpace || "";
  return [
    `${width}x${height}`,
    `m${mips.length}`,
    `f${format}`,
    `t${texture.type}`,
    space,
    `w${texture.wrapS}:${texture.wrapT}`,
    `r${pair(texture.repeat, "1x1")}`,
    `o${pair(texture.offset, "0x0")}`,
    `uv${Number.isInteger(texture.channel) ? texture.channel : 0}`,
    texture.flipY ? "flip" : "up",
  ].join("|");
}

export function canPackTexture(texture) {
  return textureArrayGroupKey(texture) !== "";
}

/** Blend, terrain, water, and helpers keep a per-image batch key. */
export function arrayMaterialEligible(material) {
  if (!material || !material.map || !canPackTexture(material.map)) return false;
  const data = material.userData || {};
  if (data.blend || data.blendNode || data.terrainNode || data.terrainShader || data.waterStandIn || data.helper) return false;
  if (data.banc === "terrain" || data.banc === "water") return false;
  return true;
}

/**
 * `arr:<group>` when this image was packed, otherwise "". The packer assigns
 * the layer as a side effect so the batch key and the instance texel agree.
 * A group that is already at `maxDepth` returns "" and that image keeps its own key.
 */
export function arrayMapToken(texture, packer) {
  if (!packer || !canPackTexture(texture)) return "";
  const placed = packer.layerOf(texture);
  if (!placed) return "";
  return `arr:${placed.group.key}`;
}

function identityOf(texture) {
  return (texture && texture.source) || texture;
}

export function createTextureArrayPacker({ initialDepth = 8, maxDepth = 256 } = {}) {
  const groups = new Map();
  // A deeper array is a new texture. The GPU texture's layer count is fixed,
  // so the previous object is disposed and materials have to be pointed at the
  // replacement. `takeSwapped` is the old → new chain since the last call.
  const swapped = new Map();

  function ensure(group, needed, texture) {
    let capacity = group.capacity || initialDepth;
    while (capacity < needed) capacity *= 2;
    if (capacity > maxDepth) capacity = maxDepth;
    if (needed > capacity) return false;
    if (group.array && group.capacity >= needed) return true;
    const previous = group.array;
    const mips = texture.mipmaps;
    const format = texture.format;
    const packed = mips.map((mip) => {
      const bytes = compressedLayerBytes(mip.width, mip.height, format);
      return { width: mip.width, height: mip.height, data: new Uint8Array(bytes * capacity) };
    });
    if (previous) {
      for (let level = 0; level < packed.length; level += 1) {
        const bytes = compressedLayerBytes(mips[level].width, mips[level].height, format);
        const prior = previous.mipmaps[level];
        if (prior && prior.data) packed[level].data.set(prior.data.subarray(0, bytes * group.depth));
      }
    }
    const array = new THREE.CompressedArrayTexture(
      packed,
      mips[0].width,
      mips[0].height,
      capacity,
      format,
      texture.type,
    );
    array.colorSpace = texture.colorSpace || THREE.NoColorSpace;
    array.wrapS = texture.wrapS;
    array.wrapT = texture.wrapT;
    array.wrapR = THREE.ClampToEdgeWrapping;
    array.flipY = false;
    array.generateMipmaps = false;
    array.anisotropy = texture.anisotropy || 1;
    if (texture.repeat) array.repeat.copy(texture.repeat);
    if (texture.offset) array.offset.copy(texture.offset);
    if (Number.isInteger(texture.channel)) array.channel = texture.channel;
    repairCompressedTexture(array);
    if (previous) {
      swapped.set(previous, array);
      previous.dispose();
    }
    group.array = array;
    group.capacity = capacity;
    group.fullUpload = Boolean(previous);
    return true;
  }

  function layerOf(texture) {
    const key = textureArrayGroupKey(texture);
    if (!key) return null;
    let group = groups.get(key);
    if (!group) {
      group = { key, layers: new Map(), depth: 0, capacity: 0, array: null, fullUpload: false };
      groups.set(key, group);
    }
    const id = identityOf(texture);
    const existing = group.layers.get(id);
    if (existing != null) return { group, layer: existing, array: group.array };
    if (group.depth >= maxDepth) return null;
    if (!ensure(group, group.depth + 1, texture)) return null;
    const layer = group.depth;
    const format = texture.format;
    for (let level = 0; level < texture.mipmaps.length; level += 1) {
      const mip = texture.mipmaps[level];
      const bytes = compressedLayerBytes(mip.width, mip.height, format);
      group.array.mipmaps[level].data.set(mip.data, layer * bytes);
    }
    group.layers.set(id, layer);
    group.depth += 1;
    if (group.fullUpload) group.array.layerUpdates.clear();
    else group.array.addLayerUpdate(layer);
    group.array.needsUpdate = true;
    group.fullUpload = false;
    return { group, layer, array: group.array };
  }

  function stats() {
    let layers = 0;
    let arrays = 0;
    for (const group of groups.values()) {
      if (group.array) arrays += 1;
      layers += group.depth;
    }
    return { arrays, layers, groups: groups.size };
  }

  function takeSwapped() {
    if (!swapped.size) return null;
    const copy = new Map(swapped);
    swapped.clear();
    return copy;
  }

  function dispose() {
    for (const group of groups.values()) {
      if (group.array && typeof group.array.dispose === "function") group.array.dispose();
    }
    groups.clear();
    swapped.clear();
  }

  return { groupKey: textureArrayGroupKey, layerOf, stats, takeSwapped, dispose };
}

/** One RGBA float texel per instance: albedo, normal, roughness, unused. */
export function createLayerTexture(capacity) {
  const width = Math.max(1, capacity | 0);
  const data = new Float32Array(width * 4);
  const texture = new THREE.DataTexture(data, width, 1, THREE.RGBAFormat, THREE.FloatType);
  texture.colorSpace = THREE.NoColorSpace;
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.flipY = false;
  texture.needsUpdate = true;
  return texture;
}

export function writeLayerTexel(texture, instanceId, albedo, normal, rough, extra = 0) {
  const data = texture && texture.image && texture.image.data;
  if (!data || instanceId < 0 || instanceId * 4 + 3 >= data.length) return false;
  const at = instanceId * 4;
  data[at] = albedo;
  data[at + 1] = normal;
  data[at + 2] = rough;
  data[at + 3] = extra;
  texture.needsUpdate = true;
  return true;
}
