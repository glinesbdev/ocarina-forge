/**
 * Part geometries are cloned out of the glTF so BatchedMesh can upload them.
 * Picking used to keep that clone (and a BVH) on the CPU. After addGeometry the
 * batch holds its own arrays; the clone can be dropped and copied back only if
 * a later rebuild needs it. Water surfaces and the selected model stay resident.
 *
 * Parking moves normals and UVs off the batch after the GPU buffer exists.
 * A glTF accessor is often a view into one shared ArrayBuffer. Copying that
 * view must not also keep the whole buffer, and it must not copy the buffer
 * once per attribute.
 */

import { BufferAttribute } from "three";

export function noteGeometryBatch(geometry, mesh, geometryId) {
  if (!geometry || !mesh || geometryId === undefined || geometryId === null) return geometry;
  if (!geometry.userData) geometry.userData = {};
  geometry.userData.forgeBatch = { mesh, geometryId };
  return geometry;
}

function shapeOf(geometry) {
  const attributes = {};
  for (const name of Object.keys(geometry.attributes || {})) {
    const attr = geometry.attributes[name];
    if (!attr || !attr.array) continue;
    attributes[name] = {
      itemSize: attr.itemSize,
      normalized: Boolean(attr.normalized),
      count: attr.count,
      bytes: attr.array.byteLength || 0,
    };
  }
  const index = geometry.index && geometry.index.array
    ? { count: geometry.index.count, bytes: geometry.index.array.byteLength || 0 }
    : null;
  return { attributes, index };
}

function emptyArray(array) {
  const Ctor = array.constructor;
  return new Ctor(0);
}

function isInterleaved(attr) {
  return Boolean(attr && attr.isInterleavedBufferAttribute);
}

function releaseStored(attr, seen) {
  if (!attr) return 0;
  if (isInterleaved(attr)) {
    const data = attr.data;
    if (!data || !data.array || !data.array.byteLength || seen.has(data)) return 0;
    seen.add(data);
    const bytes = data.array.byteLength;
    data.array = emptyArray(data.array);
    data.count = 0;
    return bytes;
  }
  if (!attr.array || !attr.array.byteLength) return 0;
  const bytes = attr.array.byteLength;
  attr.array = emptyArray(attr.array);
  attr.count = 0;
  return bytes;
}

/** Drop CPU arrays. Returns the bytes released. No-op until a batch link exists. */
export function releaseGeometryCpu(geometry) {
  if (!geometry || geometry.userData?.forgeCpuReleased) return 0;
  if (!geometry.userData?.forgeBatch) return 0;
  if (geometry.userData.forgeKeepCpu) return 0;
  const shape = shapeOf(geometry);
  const seen = new Set();
  let bytes = 0;
  for (const name of Object.keys(shape.attributes)) bytes += releaseStored(geometry.attributes[name], seen);
  if (shape.index && geometry.index) bytes += releaseStored(geometry.index, seen);
  if (!bytes) return 0;
  if (geometry.boundsTree) {
    try {
      geometry.disposeBoundsTree?.();
    } catch {
      /* the tree is going away with the arrays */
    }
    geometry.boundsTree = undefined;
  }
  geometry.userData.forgeCpuReleased = true;
  geometry.userData.forgeCpuShape = shape;
  return bytes;
}

function copyRange(src, dst, start, count, itemSize) {
  const Ctor = src.array.constructor;
  const out = new Ctor(count * itemSize);
  if (src.isInterleavedBufferAttribute) {
    for (let i = 0; i < count; i += 1) {
      for (let c = 0; c < itemSize; c += 1) out[i * itemSize + c] = src.getComponent(start + i, c);
    }
  } else {
    const from = start * itemSize;
    out.set(src.array.subarray(from, from + out.length));
  }
  dst.array = out;
  dst.count = count;
  dst.needsUpdate = false;
}

/**
 * Restore a released clone from the batch that owns the uploaded copy.
 * Returns false when there is nothing to copy back.
 */
export function ensureGeometryCpu(geometry) {
  if (!geometry?.userData?.forgeCpuReleased) return true;
  const link = geometry.userData.forgeBatch;
  const info = link?.mesh?._geometryInfo?.[link.geometryId];
  const srcGeo = link?.mesh?.geometry;
  if (!info || info.active === false || !srcGeo) return false;
  const shape = geometry.userData.forgeCpuShape;
  if (!shape) return false;
  for (const [name, meta] of Object.entries(shape.attributes)) {
    const src = srcGeo.getAttribute ? srcGeo.getAttribute(name) : srcGeo.attributes?.[name];
    const dst = geometry.attributes[name];
    if (!src || !dst || !src.array?.length) return false;
    if (isInterleaved(dst)) {
      const Ctor = src.array && src.array.constructor ? src.array.constructor : Float32Array;
      const packed = new Ctor(meta.count * meta.itemSize);
      if (src.isInterleavedBufferAttribute) {
        for (let i = 0; i < meta.count; i += 1) {
          for (let c = 0; c < meta.itemSize; c += 1) packed[i * meta.itemSize + c] = src.getComponent(info.vertexStart + i, c);
        }
      } else {
        const from = info.vertexStart * meta.itemSize;
        packed.set(src.array.subarray(from, from + packed.length));
      }
      geometry.setAttribute(name, new BufferAttribute(packed, meta.itemSize, meta.normalized));
      continue;
    }
    copyRange(src, dst, info.vertexStart, info.vertexCount, meta.itemSize);
  }
  if (shape.index) {
    const src = srcGeo.index || srcGeo.getIndex?.();
    const dst = geometry.index;
    if (!src || !dst || !src.array?.length) return false;
    const Ctor = src.array.constructor;
    const out = new Ctor(info.indexCount);
    for (let i = 0; i < info.indexCount; i += 1) {
      const absolute = typeof src.getX === "function" ? src.getX(info.indexStart + i) : src.array[info.indexStart + i];
      out[i] = absolute - info.vertexStart;
    }
    dst.array = out;
    dst.count = info.indexCount;
  }
  geometry.userData.forgeCpuReleased = false;
  return true;
}

const PARKED_ATTRIBUTES = ["normal", "uv", "uv1", "uv2", "tangent", "color", "blendMask", "blendUv"];

function readAttr(geometry, name) {
  return typeof geometry.getAttribute === "function" ? geometry.getAttribute(name) : geometry.attributes?.[name];
}

function backingBuffer(attr) {
  if (!attr) return null;
  if (isInterleaved(attr)) return attr.data && attr.data.array ? attr.data.array.buffer : null;
  return attr.array ? attr.array.buffer : null;
}

function ownsBuffer(array) {
  return Boolean(array && array.byteOffset === 0 && array.buffer.byteLength === array.byteLength);
}

/** One packed copy of this attribute. A view is copied. An owned buffer is moved. */
function takePacked(attr) {
  if (!attr) return null;
  if (isInterleaved(attr)) {
    const data = attr.data && attr.data.array;
    if (!data || !data.byteLength || !attr.count) return null;
    const out = new data.constructor(attr.count * attr.itemSize);
    for (let i = 0; i < attr.count; i += 1) {
      for (let c = 0; c < attr.itemSize; c += 1) out[i * attr.itemSize + c] = attr.getComponent(i, c);
    }
    return out;
  }
  const array = attr.array;
  if (!array || !array.byteLength) return null;
  if (ownsBuffer(array)) return array;
  const copy = new array.constructor(array.length);
  copy.set(array);
  return copy;
}

function installPacked(geometry, name, attr, array) {
  const keepCount = attr.count;
  if (name === "index") {
    attr.array = array;
    attr.count = array.length || keepCount;
    return attr;
  }
  if (isInterleaved(attr)) {
    const packed = new BufferAttribute(array, attr.itemSize, attr.normalized);
    if (!array.length) packed.count = keepCount;
    geometry.setAttribute(name, packed);
    return packed;
  }
  attr.array = array;
  if (!array.length) attr.count = keepCount;
  return attr;
}

/**
 * Move the attributes a rebuild needs, and a raycast does not, out of the
 * batch geometry. Position and the index stay: snap raycasts the batch, and
 * the bounding sphere was already computed. `park` holds the typed arrays
 * (a worker, when the page has one) until the next rebuild.
 *
 * Shared glTF buffers are not sliced once per attribute. Position and the
 * index are copied out first when they share that buffer, and each parked
 * attribute contributes its own packed range. The original buffer is then
 * unreferenced.
 */
export async function parkGeometryAttributes(geometry, park, { ready } = {}) {
  if (!geometry || !park || geometry.userData?.forgeParked || geometry.userData?.forgeKeepCpu) return 0;
  const names = [];
  const arrays = [];
  const undo = [];
  const parkedBuffers = new Set();
  for (const name of PARKED_ATTRIBUTES) {
    const attr = readAttr(geometry, name);
    if (!attr || !attr.array || !attr.array.byteLength) continue;
    if (typeof ready === "function" && !ready(attr)) continue;
    const buffer = backingBuffer(attr);
    if (buffer) parkedBuffers.add(buffer);
    names.push(name);
  }
  if (!names.length) return 0;
  const planned = names.slice();
  const keep = [["position", readAttr(geometry, "position")]];
  const index = typeof geometry.getIndex === "function" ? geometry.getIndex() : geometry.index;
  if (index) keep.push(["index", index]);
  for (const [name, attr] of keep) {
    const buffer = backingBuffer(attr);
    if (!attr || !buffer || !parkedBuffers.has(buffer)) continue;
    undo.push({ name, previous: attr });
    installPacked(geometry, name, attr, takePacked(attr));
  }
  let bytes = 0;
  const interleaved = new Set();
  const parkedNames = [];
  for (const name of planned) {
    const attr = readAttr(geometry, name);
    if (!attr) continue;
    const held = takePacked(attr);
    if (!held || !held.byteLength) continue;
    undo.push({ name, previous: attr, array: isInterleaved(attr) ? null : attr.array, count: attr.count });
    if (isInterleaved(attr) && attr.data) interleaved.add(attr.data);
    installPacked(geometry, name, attr, emptyArray(held));
    parkedNames.push(name);
    arrays.push(held);
    bytes += held.byteLength;
  }
  if (!arrays.length) return 0;
  const id = geometry.uuid || `geo-${parkedNames.join("-")}`;
  if (!geometry.userData) geometry.userData = {};
  try {
    await park.park(id, arrays);
  } catch (error) {
    for (const item of undo) {
      if (item.name === "index") geometry.setIndex(item.previous);
      else geometry.setAttribute(item.name, item.previous);
      if (item.array) {
        item.previous.array = item.array;
        item.previous.count = item.count;
      }
    }
    throw error;
  }
  for (const data of interleaved) {
    if (data.array && data.array.byteLength) {
      data.array = emptyArray(data.array);
      data.count = 0;
    }
  }
  geometry.userData.forgeParked = parkedNames;
  geometry.userData.forgeParkId = id;
  return bytes;
}

/** Copy parked attributes back onto the geometry before a rebuild reads them. */
export async function unparkGeometryAttributes(geometry, park) {
  const names = geometry?.userData?.forgeParked;
  const id = geometry?.userData?.forgeParkId;
  if (!names || !id || !park) return false;
  const arrays = await park.take(id);
  if (!arrays || arrays.length !== names.length) return false;
  for (let i = 0; i < names.length; i += 1) {
    const attr = typeof geometry.getAttribute === "function"
      ? geometry.getAttribute(names[i])
      : geometry.attributes?.[names[i]];
    if (!attr || !arrays[i] || !arrays[i].length) return false;
    attr.array = arrays[i];
    attr.count = arrays[i].length / (attr.itemSize || 1);
  }
  geometry.userData.forgeParked = null;
  geometry.userData.forgeParkId = null;
  return true;
}

/** Attribute counts for audits after the CPU arrays have been released. */
export function geometryAttributeView(geometry) {
  if (!geometry) return { attributes: {}, indexCount: null };
  const shape = geometry.userData?.forgeCpuReleased ? geometry.userData.forgeCpuShape : null;
  if (shape) {
    const attributes = {};
    for (const [name, info] of Object.entries(shape.attributes)) {
      attributes[name] = { itemSize: info.itemSize, count: info.count, normalized: info.normalized };
    }
    return { attributes, indexCount: shape.index ? shape.index.count : null };
  }
  const attributes = {};
  for (const [name, attr] of Object.entries(geometry.attributes || {})) {
    attributes[name] = { itemSize: attr.itemSize, count: attr.count, normalized: Boolean(attr.normalized) };
  }
  const indexCount = geometry.index ? geometry.index.count : null;
  return { attributes, indexCount };
}
