/**
 * WebGPU texture arrays for batch keys.
 *
 * A compressed image joins a group of the same size, mip count, format,
 * color space, and sampler state. The batch key names that group, not the
 * image. Each instance stores its layer in a float texture indexed by
 * `batchIndirectIndex`. Blend, terrain, and water stay on the per-image key.
 * WebGL never builds a packer: it cannot sample an array from the stock material.
 * An array is never left on a stock slot (`map`, `aoMap`, `normalMap`, …).
 * Those slots compile `textureSample(tex, sampler, uv)` with no layer index,
 * and one invalid pipeline drops the whole command buffer. The array textures
 * live on non-enumerable material fields so a clone does not JSON-serialize them.
 * A grown array is a new texture. While the previous texture still has its
 * CPU mips, those bytes are copied into the new one. Once that CPU copy has
 * been released, the grow copies layers on the GPU (`copyLayers`) and uploads
 * only the new layer. Layers are append-only: a grow or a compact copies
 * `0..depth-1` in order and never renumbers a live image. The previous GPU
 * texture stays bound until `releaseSwapped`, which the view calls after it
 * has pointed every material at the new array in the same frame. The CPU
 * mips are sealed immediately (`dataReady` false, buffers emptied) so three
 * cannot `writeTexture` a 0-byte mip while that GPU texture is still sampled.
 */
import * as THREE from "three";
import { releaseTextureCpu, repairCompressedTexture, requestCompressedUpload } from "./gpu-texture.js";

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

/**
 * Physical size of one compressed mip. BC is a 4×4 block, so a logical 6
 * rounds to 8 and a 3 or 1 rounds to 4. `copyTextureToTexture` rejects a
 * width that is not a multiple of that block.
 */
export function compressedCopySize(width, height, format) {
  const block = blockFor(format);
  if (!block) return null;
  const round = (value, unit) => Math.max(unit, Math.ceil((value || 1) / unit) * unit);
  return {
    width: round(width, block.width),
    height: round(height, block.height),
  };
}

/**
 * Every mip of one grow, rounded, before any encoder is opened. Null when a
 * mip has no block size. `depth` is the layer count copied with each mip.
 */
export function compressedCopyPlan(mips, format, layerCount) {
  if (!blockFor(format) || !Array.isArray(mips) || !mips.length) return null;
  const depth = layerCount | 0;
  if (depth < 1) return null;
  const copies = [];
  for (let level = 0; level < mips.length; level += 1) {
    const mip = mips[level] || {};
    const extent = compressedCopySize(mip.width, mip.height, format);
    if (!extent) return null;
    copies.push({ level, width: extent.width, height: extent.height, depth });
  }
  return copies;
}

/**
 * One layer of one mip, ready for `writeTexture`. Null when the buffer is
 * shorter than the block pitch: that write would be the 0-byte validation error.
 */
export function layerWritePlan(mip, format) {
  const block = blockFor(format);
  if (!block || !mip) return null;
  const bytes = compressedLayerBytes(mip.width, mip.height, format);
  const data = mip.data;
  if (!bytes || !data || data.byteLength < bytes) return null;
  const rows = Math.ceil(mip.height / block.height);
  return {
    data: data.subarray(0, bytes),
    bytesPerRow: Math.ceil(mip.width / block.width) * block.byteLength,
    rowsPerImage: rows,
    width: Math.ceil(mip.width / block.width) * block.width,
    height: rows * block.height,
  };
}

/**
 * True when three would hand `writeTexture` a 0-length mip. A resident array
 * and a sealed source (`dataReady` false, or already released) do not upload.
 */
export function uploadWouldSeeEmptyMip(texture) {
  if (!texture || texture.isCompressedTexture !== true) return false;
  if (texture.userData?.forgeGpuResident) return false;
  if (texture.userData?.forgeCpuReleased || texture.source?.userData?.forgeCpuReleased) return false;
  if (texture.source && texture.source.dataReady === false) return false;
  const levels = texture.mipmaps || [];
  return levels.some((level) => !level || !level.data || level.data.byteLength === 0);
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
 * Why a compressed image cannot join an array. Empty when it can.
 * A mip longer than one layer is padding and still packs; a shorter mip cannot.
 */
export function packBlocker(texture) {
  if (!texture || texture.isCompressedTexture !== true) return "not-compressed";
  if (texture.isCompressedArrayTexture === true) return "already-array";
  const mips = texture.mipmaps;
  if (!Array.isArray(mips) || !mips.length) return "no-mips";
  const format = texture.format;
  if (!blockFor(format)) return "format";
  const width = mips[0].width || (texture.image && texture.image.width) || 0;
  const height = mips[0].height || (texture.image && texture.image.height) || 0;
  if (!width || !height) return "size";
  for (let level = 0; level < mips.length; level += 1) {
    const mip = mips[level];
    const bytes = compressedLayerBytes(mip && mip.width, mip && mip.height, format);
    const got = mip && mip.data ? mip.data.byteLength : 0;
    if (!bytes || got < bytes) return `mip-bytes:${level}:${got}/${bytes}`;
  }
  return "";
}

/**
 * Size class for a compressed image. Empty when the image cannot share an array
 * (uncompressed, unknown format, or a mip shorter than one layer).
 */
export function textureArrayGroupKey(texture) {
  if (packBlocker(texture)) return "";
  const mips = texture.mipmaps;
  const format = texture.format;
  const width = mips[0].width || (texture.image && texture.image.width) || 0;
  const height = mips[0].height || (texture.image && texture.image.height) || 0;
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
  if (data.banc === "terrain" || data.banc === "water" || data.banc === "ground" || data.groundSurface) return false;
  if (data.forgeSurface === "terrain" || data.forgeSurface === "ground") return false;
  return true;
}

/**
 * `arr:<group>` when this image was packed, otherwise "". The packer assigns
 * the layer as a side effect so the batch key and the instance texel agree.
 * A group that is already at `maxDepth` returns "" and that image keeps its own key.
 */
export function arrayMapToken(texture, packer) {
  if (!packer) return "";
  const blocker = packBlocker(texture);
  if (blocker) {
    if (typeof packer.noteReject === "function") packer.noteReject(texture, blocker);
    return "";
  }
  const placed = packer.layerOf(texture);
  if (!placed) return "";
  if (typeof packer.notePacked === "function") packer.notePacked(texture);
  return `arr:${placed.group.key}`;
}

function identityOf(texture) {
  return (texture && texture.source) || texture;
}

function sealArray(texture) {
  if (!texture) return;
  // Clear the upload gate before the bytes go. The GPU image stays until
  // releaseSwapped, so a material still pointing here samples the old layers
  // instead of a destroyed texture or a half-copied replacement.
  if (texture.source) texture.source.dataReady = false;
  texture.userData = texture.userData || {};
  texture.userData.forgeCpuReleased = true;
  const levels = texture.mipmaps;
  if (Array.isArray(levels)) {
    for (const level of levels) {
      if (level && level.data && level.data.byteLength) level.data = new Uint8Array(0);
    }
  }
}

function retireArray(texture) {
  sealArray(texture);
  if (texture && typeof texture.dispose === "function") texture.dispose();
}

function cpuIntact(texture) {
  if (!texture || texture.userData?.forgeCpuReleased) return false;
  const levels = texture.mipmaps;
  if (!Array.isArray(levels) || !levels.length) return false;
  return levels.every((level) => level && level.data && level.data.byteLength > 0);
}

function buildPackedArray(texture, capacity, previous, depth, resident = false) {
  const mips = texture.mipmaps;
  const format = texture.format;
  const packed = mips.map((mip) => {
    const bytes = compressedLayerBytes(mip.width, mip.height, format);
    return { width: mip.width, height: mip.height, data: new Uint8Array(bytes * capacity) };
  });
  // Never copy a released chain. A 0-length source copies nothing and the
  // new array would upload blank layers, or fail validation if the buffer
  // itself was emptied.
  if (!resident && previous && depth > 0 && cpuIntact(previous)) {
    for (let level = 0; level < packed.length; level += 1) {
      const mip = mips[level];
      const bytes = compressedLayerBytes(mip.width, mip.height, format);
      const prior = previous.mipmaps && previous.mipmaps[level];
      const source = prior && prior.data;
      if (!source || source.byteLength < bytes * depth || !bytes) continue;
      packed[level].data.set(source.subarray(0, bytes * depth));
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
  if (resident) {
    // Sized buffers, but three must not write them. The GPU copy fills the
    // old layers and one writeTexture fills the new one.
    if (array.source) array.source.dataReady = false;
    array.userData.forgeGpuResident = true;
    array.needsUpdate = true;
    return array;
  }
  repairCompressedTexture(array);
  return array;
}

export function createTextureArrayPacker({ initialDepth = 1, maxDepth = 256, copyLayers = null, uploadLayer = null } = {}) {
  const groups = new Map();
  // Textures whose bytes have been copied into an array. The caller drops
  // their CPU buffers once no drawn 2D slot still samples them.
  const packedTextures = new Set();
  // A deeper array is a new texture. The GPU texture's layer count is fixed,
  // so the previous object is disposed and its JS buffers are dropped.
  // `takeSwapped` is the old → new chain since the last call.
  const swapped = new Map();
  // Previous GPU textures. Sealed (CPU gone) but not disposed until the view
  // has rebound every material onto the replacement.
  const pendingDispose = [];
  const rejects = new Map();

  function noteReject(texture, reason) {
    const id = identityOf(texture) || texture;
    if (!id) return;
    const mip = texture && texture.mipmaps && texture.mipmaps[0];
    const image = texture && texture.image;
    rejects.set(id, {
      reason: reason || "unpacked",
      width: (mip && mip.width) || (image && image.width) || 0,
      height: (mip && mip.height) || (image && image.height) || 0,
      format: texture && texture.format != null ? texture.format : "",
      name: (texture && (texture.name || (texture.userData && texture.userData.forgeFullTexture))) || "",
    });
  }

  function notePacked(texture) {
    const id = identityOf(texture) || texture;
    if (id) rejects.delete(id);
  }

  function imagePackReport() {
    const counts = {};
    const images = [];
    for (const info of rejects.values()) {
      const reason = info.reason || "unpacked";
      counts[reason] = (counts[reason] || 0) + 1;
      images.push(info);
    }
    return { counts, images };
  }

  function queueDispose(texture) {
    if (!texture || pendingDispose.includes(texture)) return;
    sealArray(texture);
    pendingDispose.push(texture);
  }

  function releaseSwapped() {
    const pending = pendingDispose.splice(0, pendingDispose.length);
    for (const texture of pending) {
      if (texture && typeof texture.dispose === "function") texture.dispose();
    }
  }

  // Build the next array without publishing it. A failed layer write drops
  // the destination and leaves the previous array, still with its GPU image,
  // as the one materials sample.
  function growArray(group, needed, texture) {
    let capacity = group.capacity || initialDepth;
    while (capacity < needed) capacity *= 2;
    if (capacity > maxDepth) capacity = maxDepth;
    if (needed > capacity) return { reason: "depth-cap" };
    if (group.array && group.capacity >= needed) return { array: group.array, previous: null, capacity: group.capacity };
    const previous = group.array;
    if (previous && !cpuIntact(previous)) {
      if (typeof copyLayers !== "function" || previous.userData?.forgeGpuUploaded !== true) return { reason: "gpu-not-ready" };
      const array = buildPackedArray(texture, capacity, null, 0, true);
      if (!copyLayers(previous, array, group.depth)) {
        retireArray(array);
        return { reason: "gpu-copy" };
      }
      array.userData.forgeGpuUploaded = true;
      releaseTextureCpu(array, { uploaded: true });
      return { array, previous, capacity };
    }
    const array = buildPackedArray(texture, capacity, previous, group.depth);
    if (uploadWouldSeeEmptyMip(array)) {
      retireArray(array);
      return { reason: "empty-mip" };
    }
    return { array, previous, capacity };
  }

  function publish(group, staged, gpu) {
    if (staged.array === group.array) return;
    if (staged.previous) {
      swapped.set(staged.previous, staged.array);
      queueDispose(staged.previous);
    }
    group.array = staged.array;
    group.capacity = staged.capacity;
    group.fullUpload = !gpu && Boolean(staged.previous);
  }

  function dropStaged(group, staged) {
    if (staged && staged.array && staged.array !== group.array) retireArray(staged.array);
  }

  function layerOf(texture) {
    const key = textureArrayGroupKey(texture);
    if (!key) {
      noteReject(texture, packBlocker(texture) || "unpacked");
      return null;
    }
    let group = groups.get(key);
    if (!group) {
      group = { key, layers: new Map(), depth: 0, capacity: 0, array: null, fullUpload: false };
      groups.set(key, group);
    }
    const id = identityOf(texture);
    const existing = group.layers.get(id);
    if (existing != null) return { group, layer: existing, array: group.array };
    if (group.depth >= maxDepth) {
      noteReject(texture, "depth-cap");
      return null;
    }
    const staged = growArray(group, group.depth + 1, texture);
    if (!staged.array) {
      noteReject(texture, staged.reason || "grow");
      return null;
    }
    const layer = group.depth;
    const array = staged.array;
    const gpu = Boolean(array.userData?.forgeGpuResident) || !cpuIntact(array);
    if (gpu) {
      const writes = texture.mipmaps.map((mip) => ({
        width: mip.width,
        height: mip.height,
        data: mip.data && mip.data.byteLength ? mip.data.slice() : new Uint8Array(0),
      }));
      if (writes.some((mip) => mip.data.byteLength === 0)) {
        dropStaged(group, staged);
        noteReject(texture, "empty-layer");
        return null;
      }
      if (typeof uploadLayer !== "function" || !uploadLayer(array, layer, writes)) {
        dropStaged(group, staged);
        noteReject(texture, "upload");
        return null;
      }
    } else {
      const format = texture.format;
      for (let level = 0; level < texture.mipmaps.length; level += 1) {
        const mip = texture.mipmaps[level];
        const bytes = compressedLayerBytes(mip.width, mip.height, format);
        const target = array.mipmaps[level] && array.mipmaps[level].data;
        if (!target || target.byteLength < (layer + 1) * bytes || !mip.data || mip.data.byteLength < bytes) {
          dropStaged(group, staged);
          noteReject(texture, `cpu-write:${level}`);
          return null;
        }
        target.set(mip.data.subarray(0, bytes), layer * bytes);
      }
      if (uploadWouldSeeEmptyMip(array)) {
        dropStaged(group, staged);
        noteReject(texture, "empty-mip");
        return null;
      }
    }
    publish(group, staged, gpu);
    group.layers.set(id, layer);
    group.depth += 1;
    packedTextures.add(texture);
    if (!gpu) {
      if (group.fullUpload) group.array.layerUpdates.clear();
      else group.array.addLayerUpdate(layer);
      requestCompressedUpload(group.array);
    }
    group.fullUpload = false;
    notePacked(texture);
    return { group, layer, array: group.array };
  }

  function compact() {
    for (const group of groups.values()) {
      if (!group.array || !group.depth || group.capacity === group.depth) continue;
      const previous = group.array;
      if (!cpuIntact(previous)) {
        // Spare capacity stays until a GPU copy can shrink it. Rebuilding
        // from a released chain would upload empty mips.
        if (typeof copyLayers !== "function" || previous.userData?.forgeGpuUploaded !== true) continue;
        const array = buildPackedArray(previous, group.depth, null, 0, true);
        if (!copyLayers(previous, array, group.depth)) {
          retireArray(array);
          continue;
        }
        array.userData.forgeGpuUploaded = true;
        releaseTextureCpu(array, { uploaded: true });
        swapped.set(previous, array);
        queueDispose(previous);
        group.array = array;
        group.capacity = group.depth;
        continue;
      }
      const array = buildPackedArray(previous, group.depth, previous, group.depth);
      if (uploadWouldSeeEmptyMip(array)) {
        retireArray(array);
        continue;
      }
      swapped.set(previous, array);
      queueDispose(previous);
      group.array = array;
      group.capacity = group.depth;
    }
  }

  function retain(live) {
    if (!live || typeof live.has !== "function") return;
    for (const [key, group] of groups) {
      if (group.array && live.has(group.array)) continue;
      const index = pendingDispose.indexOf(group.array);
      if (index >= 0) pendingDispose.splice(index, 1);
      retireArray(group.array);
      groups.delete(key);
    }
  }

  function sources() {
    return packedTextures;
  }

  function arrayFor(texture) {
    const key = textureArrayGroupKey(texture);
    const group = key && groups.get(key);
    if (!group || !group.layers.has(identityOf(texture))) return null;
    return group.array;
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
    releaseSwapped();
    for (const group of groups.values()) retireArray(group.array);
    groups.clear();
    swapped.clear();
    packedTextures.clear();
    rejects.clear();
  }

  return {
    groupKey: textureArrayGroupKey,
    layerOf,
    stats,
    takeSwapped,
    releaseSwapped,
    dispose,
    compact,
    retain,
    sources,
    arrayFor,
    noteReject,
    notePacked,
    imagePackReport,
  };
}

/** One RGBA float texel per instance: albedo, normal, roughness, extra layer. */
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

/** A wider lookup. Existing instance texels are copied in order and not renumbered. */
export function replaceLayerTexture(current, capacity) {
  const next = createLayerTexture(capacity);
  const previous = current && current.image && current.image.data;
  if (previous && next.image && next.image.data) {
    next.image.data.set(previous.subarray(0, Math.min(previous.length, next.image.data.length)));
  }
  return next;
}

/** Stock samplers. An array here becomes `texture_2d_array` with no `array_index`. */
export const STOCK_TEXTURE_SLOTS = [
  "map", "alphaMap", "aoMap", "bumpMap", "displacementMap", "emissiveMap",
  "envMap", "lightMap", "metalnessMap", "normalMap", "roughnessMap", "specularMap",
  "specularIntensityMap", "specularColorMap", "clearcoatMap", "clearcoatNormalMap",
  "clearcoatRoughnessMap", "sheenColorMap", "sheenRoughnessMap", "transmissionMap",
  "thicknessMap", "iridescenceMap", "iridescenceThicknessMap", "anisotropyMap",
];

const ARRAY_SLOT_FIELD = {
  map: "forgeArrayAlbedo",
  normalMap: "forgeArrayNormal",
  roughnessMap: "forgeArrayRough",
  metalnessMap: "forgeArrayRough",
  aoMap: "forgeArrayAo",
  emissiveMap: "forgeArrayEmissive",
  alphaMap: "forgeArrayAlpha",
};

export function isArrayTexture(texture) {
  return Boolean(
    texture
    && (
      texture.isCompressedArrayTexture === true
      || texture.isDataArrayTexture === true
      || texture.isArrayTexture === true
    ),
  );
}

/** Sampled array and layer textures. Non-enumerable, so a clone's JSON skips them. */
export const LIVE_ARRAY_FIELDS = [
  "forgeArrayAlbedo", "forgeArrayNormal", "forgeArrayRough",
  "forgeArrayAo", "forgeArrayEmissive", "forgeArrayAlpha",
  "forgeLayerTexture", "forgePickMask",
];

export function holdTexture(object, key, texture) {
  if (!object) return;
  Object.defineProperty(object, key, {
    value: texture ?? null,
    writable: true,
    configurable: true,
    enumerable: false,
  });
}

export function arrayTexturesOf(material) {
  const userData = material && material.userData;
  if (!userData) return [];
  const found = [];
  const seen = new Set();
  for (const key of LIVE_ARRAY_FIELDS) {
    const value = userData[key];
    if (!value || value.isTexture !== true || seen.has(value)) continue;
    seen.add(value);
    found.push(value);
  }
  return found;
}

/**
 * Move every array off a stock slot. The texture is kept on a non-enumerable
 * `userData` field so a node can sample it with `.depth(layer)` and
 * `Material.clone` does not JSON-serialize it. A slot with no field is
 * cleared: a bump or light array is dropped rather than sampled without an index.
 */
export function releaseArraySlots(material) {
  if (!material) return false;
  const userData = material.userData || (material.userData = {});
  if (isArrayTexture(material.metalnessMap) && (
    material.metalnessMap === material.roughnessMap
    || material.metalnessMap === userData.forgeArrayRough
  )) {
    userData.forgeArrayOrm = true;
  }
  let cleared = false;
  for (const slot of STOCK_TEXTURE_SLOTS) {
    const texture = material[slot];
    if (!isArrayTexture(texture)) continue;
    const field = ARRAY_SLOT_FIELD[slot];
    if (field && !isArrayTexture(userData[field])) holdTexture(userData, field, texture);
    material[slot] = null;
    cleared = true;
  }
  return cleared;
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
