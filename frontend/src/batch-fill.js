/**
 * One live BatchedMesh per batch key. Geometry and instances append as models
 * arrive. Capacity doubles when the next add would not fit. A second mesh for
 * the same key is only created when that grow throws, which is the overflow.
 */

export function doubledCapacity(current, needed) {
  let cap = current > 0 ? current : 1;
  const target = needed > 0 ? needed : 0;
  if (cap >= target) return cap;
  while (cap < target) {
    if (cap > 0x3fffffff) return target;
    cap *= 2;
  }
  return cap;
}

function geometryUse(geometry) {
  const position = geometry.getAttribute
    ? geometry.getAttribute("position")
    : geometry.attributes && geometry.attributes.position;
  const verts = position ? position.count : 0;
  const index = geometry.getIndex ? geometry.getIndex() : geometry.index;
  const indices = index ? index.count : verts;
  return { verts, indices: Math.max(indices, 1) };
}

function instanceHighWater(mesh, active) {
  const info = mesh && mesh._instanceInfo;
  return info ? info.length : active;
}

/**
 * How many BatchedMesh objects a staged rebuild constructs.
 * `before` is one mesh per key inside each arrival (they are not merged).
 * `after` is one mesh per distinct key across the whole run.
 */
export function simulateBatchArrival(arrivals) {
  let before = 0;
  const seen = new Set();
  for (const keys of arrivals) {
    const local = new Set(keys);
    before += local.size;
    for (const key of local) seen.add(key);
  }
  return { before, after: seen.size, keys: seen.size };
}

export function createBatchFiller({ createMesh, minInstances = 8, minVertices = 64, minIndices = 64 } = {}) {
  if (typeof createMesh !== "function") throw new Error("createMesh is required");
  const byKey = new Map();
  let constructed = 0;
  let overflow = 0;
  let overflowSerial = 0;

  function stats() {
    return { constructed, overflow, keys: byKey.size, batches: byKey.size };
  }

  function list() {
    return [...byKey.values()];
  }

  function get(key) {
    return byKey.get(key) || null;
  }

  function usedGeometry(batch) {
    let verts = 0;
    let indices = 0;
    for (const info of batch.geometries.values()) {
      verts += info.verts;
      indices += info.indices;
    }
    return { verts, indices };
  }

  function create(key, material, instances, vertices, indices) {
    const mesh = createMesh({
      key,
      material,
      instances,
      vertices,
      indices,
    });
    constructed += 1;
    const batch = {
      key,
      mesh,
      material,
      instanceCap: instances,
      vertexCap: vertices,
      indexCap: indices,
      geometries: new Map(),
      active: 0,
    };
    byKey.set(key, batch);
    return batch;
  }

  function grow(batch, instNeeded, vertNeeded, indexNeeded) {
    const nextInst = doubledCapacity(batch.instanceCap, instNeeded);
    const nextVert = doubledCapacity(batch.vertexCap, vertNeeded);
    const nextIndex = doubledCapacity(batch.indexCap, indexNeeded);
    if (nextInst !== batch.instanceCap) {
      batch.mesh.setInstanceCount(nextInst);
      batch.instanceCap = nextInst;
    }
    if (nextVert !== batch.vertexCap || nextIndex !== batch.indexCap) {
      batch.mesh.setGeometrySize(nextVert, nextIndex);
      batch.vertexCap = nextVert;
      batch.indexCap = nextIndex;
    }
  }

  function appendInto(key, material, records) {
    const pending = [];
    const seen = new Set();
    let extraVerts = 0;
    let extraIndices = 0;
    let batch = byKey.get(key);
    for (const record of records) {
      const geometry = record && record.geometry;
      if (!geometry || seen.has(geometry)) continue;
      if (batch && batch.geometries.has(geometry)) continue;
      seen.add(geometry);
      const cost = geometryUse(geometry);
      if (!cost.verts) continue;
      extraVerts += cost.verts;
      extraIndices += cost.indices;
      pending.push({ geometry, ...cost });
    }
    const usable = records.filter((record) => record && record.geometry && geometryUse(record.geometry).verts);
    if (!usable.length) return { batch, added: [], fresh: false, linked: [] };
    const fresh = !batch;
    if (!batch) {
      batch = create(
        key,
        material,
        doubledCapacity(minInstances, usable.length),
        doubledCapacity(minVertices, extraVerts),
        doubledCapacity(minIndices, extraIndices),
      );
    } else {
      const used = usedGeometry(batch);
      const high = instanceHighWater(batch.mesh, batch.active);
      grow(batch, high + usable.length, used.verts + extraVerts, used.indices + extraIndices);
    }
    const linked = [];
    for (const item of pending) {
      const geometryId = batch.mesh.addGeometry(item.geometry);
      batch.geometries.set(item.geometry, { geometryId, verts: item.verts, indices: item.indices });
      linked.push({ geometry: item.geometry, geometryId });
    }
    const added = [];
    for (const record of usable) {
      const info = batch.geometries.get(record.geometry);
      if (!info) continue;
      const instanceId = batch.mesh.addInstance(info.geometryId);
      batch.active += 1;
      added.push({ record, instanceId, geometryId: info.geometryId });
    }
    return { batch, added, fresh, linked };
  }

  function plan(batch, records) {
    const pending = [];
    const seen = new Set();
    let extraVerts = 0;
    let extraIndices = 0;
    for (const record of records) {
      const geometry = record && record.geometry;
      if (!geometry || seen.has(geometry) || batch.geometries.has(geometry)) continue;
      seen.add(geometry);
      const cost = geometryUse(geometry);
      if (!cost.verts) continue;
      extraVerts += cost.verts;
      extraIndices += cost.indices;
      pending.push(cost);
    }
    const usable = records.filter((record) => record && record.geometry && geometryUse(record.geometry).verts);
    const used = usedGeometry(batch);
    const high = instanceHighWater(batch.mesh, batch.active);
    return {
      instNeeded: high + usable.length,
      vertNeeded: used.verts + extraVerts,
      indexNeeded: used.indices + extraIndices,
    };
  }

  function append(key, material, records) {
    const existing = byKey.get(key);
    if (existing) {
      try {
        const needed = plan(existing, records);
        grow(existing, needed.instNeeded, needed.vertNeeded, needed.indexNeeded);
      } catch (error) {
        overflow += 1;
        overflowSerial += 1;
        const created = appendInto(`${key}\u0000${overflowSerial}`, material, records);
        created.overflow = true;
        created.error = error;
        return created;
      }
    }
    return appendInto(key, material, records);
  }

  function removeInstance(key, instanceId) {
    const batch = byKey.get(key);
    if (!batch) return null;
    batch.mesh.deleteInstance(instanceId);
    batch.active = Math.max(0, batch.active - 1);
    return batch;
  }

  function dropIfEmpty(key) {
    const batch = byKey.get(key);
    if (!batch || batch.active > 0) return null;
    byKey.delete(key);
    return batch;
  }

  function forget(key) {
    const batch = byKey.get(key);
    if (!batch) return null;
    byKey.delete(key);
    return batch;
  }

  function detachAll() {
    byKey.clear();
  }

  return {
    append,
    removeInstance,
    dropIfEmpty,
    forget,
    detachAll,
    get,
    list,
    stats,
  };
}
