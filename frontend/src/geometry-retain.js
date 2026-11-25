/**
 * Part geometries are cloned out of the glTF so BatchedMesh can upload them.
 * Picking used to keep that clone (and a BVH) on the CPU. After addGeometry the
 * batch holds its own arrays; the clone can be dropped and copied back only if
 * a later rebuild needs it. Water surfaces and the selected model stay resident.
 */

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

/** Drop CPU arrays. Returns the bytes released. No-op until a batch link exists. */
export function releaseGeometryCpu(geometry) {
  if (!geometry || geometry.userData?.forgeCpuReleased) return 0;
  if (!geometry.userData?.forgeBatch) return 0;
  if (geometry.userData.forgeKeepCpu) return 0;
  const shape = shapeOf(geometry);
  let bytes = 0;
  for (const info of Object.values(shape.attributes)) bytes += info.bytes;
  if (shape.index) bytes += shape.index.bytes;
  if (!bytes) return 0;
  for (const name of Object.keys(shape.attributes)) {
    const attr = geometry.attributes[name];
    attr.array = emptyArray(attr.array);
    attr.count = 0;
  }
  if (shape.index && geometry.index) {
    geometry.index.array = emptyArray(geometry.index.array);
    geometry.index.count = 0;
  }
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
