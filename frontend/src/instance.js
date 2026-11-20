/**
 * Gates for a batched instance. The mesh lives on a BatchedMesh, not on the
 * actor's pivot, so hide, lock, picking, and the gizmo have to agree on these
 * flags or a copy stays clickable after its category is locked.
 *
 * `applyActorMatrix` is the gizmo write. It touches setMatrixAt only for slots
 * whose actor is the one being dragged. Other copies of the same geometry stay
 * on the matrices they already have. BatchedMesh has no instanceMatrix; flagging
 * that buffer is what made the drag throw after the first slot.
 */

export function actorShown({ status, category, volume, showHidden, categories, hideVolumes }) {
  const hiddenByStatus = status === "hidden" && !showHidden;
  const hiddenByCategory = categories?.[category] === "hide";
  const hiddenByVolume = Boolean(volume) && Boolean(hideVolumes);
  return !hiddenByStatus && !hiddenByCategory && !hiddenByVolume;
}

export function actorLocked({ category, categories, companion, pickable }) {
  return categories?.[category] === "lock" || (Boolean(companion) && pickable === false);
}

export function canPick({ shown, locked }) {
  return Boolean(shown) && !locked;
}

export function canGizmo({ id, shown, locked }) {
  return Boolean(id) && Boolean(shown) && !locked;
}

/** Copies that share a key are one BatchedMesh. The key is the material, not the actor. */
export function batchKey({ image, alpha, side, tint, indexed, attributes }) {
  return `${image}|${alpha}|s${side}|c${tint}|${indexed}|${attributes}`;
}

/**
 * Fold consecutive BatchedMesh ranges that share one index span into one
 * instanced draw. `batchFirst[i]` is the indirect-texture slot of that run.
 * r170's WebGPU backend ignores it and restarts every run at instance 0, so
 * `patchBatchedDraw` has to pass the slot through. Returns the draw count.
 */
export function groupGeometryDraws(mesh) {
  const count = mesh && mesh._multiDrawCount ? mesh._multiDrawCount : 0;
  if (!mesh.userData) mesh.userData = {};
  if (count <= 1) {
    mesh.userData.batchFirst = null;
    return count;
  }
  const starts = mesh._multiDrawStarts;
  const counts = mesh._multiDrawCounts;
  if (!starts || !counts) {
    mesh.userData.batchFirst = null;
    return count;
  }
  const nextStarts = [];
  const nextCounts = [];
  const nextInstances = [];
  const nextFirst = [];
  let run = 0;
  for (let i = 1; i <= count; i += 1) {
    const same = i < count && starts[i] === starts[run] && counts[i] === counts[run];
    if (same) continue;
    nextStarts.push(starts[run]);
    nextCounts.push(counts[run]);
    nextInstances.push(i - run);
    nextFirst.push(run);
    run = i;
  }
  if (!mesh._multiDrawInstances || mesh._multiDrawInstances.length < nextStarts.length) {
    mesh._multiDrawInstances = new Int32Array(Math.max(count, nextStarts.length));
  }
  if (!mesh.userData.batchFirst || mesh.userData.batchFirst.length < nextFirst.length) {
    mesh.userData.batchFirst = new Int32Array(Math.max(count, nextFirst.length));
  }
  for (let i = 0; i < nextStarts.length; i += 1) {
    starts[i] = nextStarts[i];
    counts[i] = nextCounts[i];
    mesh._multiDrawInstances[i] = nextInstances[i];
    mesh.userData.batchFirst[i] = nextFirst[i];
  }
  mesh._multiDrawCount = nextStarts.length;
  return nextStarts.length;
}

/**
 * r170 draws a multi-instance batch from instance 0. A later geometry in the
 * same mesh would then read the first run's matrices. Wrap the pass encoder
 * for that one call and supply the slot `groupGeometryDraws` recorded.
 */
export function patchBatchedDraw(backend) {
  if (!backend || backend.drawPatched || typeof backend.draw !== "function") return backend;
  const original = backend.draw;
  backend.draw = function drawGrouped(renderObject, info) {
    const object = renderObject && renderObject.object;
    const firsts = object && object.userData && object.userData.batchFirst;
    if (!firsts || !object._multiDrawCount) return original.call(this, renderObject, info);
    const data = typeof this.get === "function" ? this.get(renderObject.context) : null;
    const pass = data && data.currentPass;
    if (!pass || typeof pass.drawIndexed !== "function") return original.call(this, renderObject, info);
    const native = pass.drawIndexed;
    let slot = 0;
    pass.drawIndexed = function groupedIndexed(indexCount, instanceCount, firstIndex, baseVertex) {
      const firstInstance = firsts[slot] || 0;
      slot += 1;
      return native.call(this, indexCount, instanceCount, firstIndex, baseVertex, firstInstance);
    };
    try {
      return original.call(this, renderObject, info);
    } finally {
      pass.drawIndexed = native;
    }
  };
  backend.drawPatched = true;
  return backend;
}

const reportedMissingUv = new Set();

/**
 * A mapped material on a geometry without `uv` makes the node builder warn
 * every compile. Clone that material and drop the map. The shared material
 * stays mapped for the meshes that do have uvs. Returns the object names.
 */
export function dropMapWithoutUv(root) {
  const names = [];
  const visit = (node) => {
    if (!node) return;
    const geometry = node.geometry;
    const missing = geometry && typeof geometry.hasAttribute === "function" && !geometry.hasAttribute("uv");
    if (missing && node.material) {
      const list = Array.isArray(node.material) ? node.material : [node.material];
      let next = null;
      list.forEach((material, index) => {
        if (!material || !material.map) return;
        if (material.colorNode || (material.userData && (material.userData.terrainNode || material.userData.cutoutNode))) return;
        const copy = typeof material.clone === "function" ? material.clone() : material;
        copy.map = null;
        copy.needsUpdate = true;
        if (list.length === 1) node.material = copy;
        else {
          next = next || list.slice();
          next[index] = copy;
        }
        const name = node.name || node.type || "mesh";
        if (!reportedMissingUv.has(name)) {
          reportedMissingUv.add(name);
          names.push(name);
        }
      });
      if (next) node.material = next;
    }
    const children = node.children;
    if (!children) return;
    for (let i = 0; i < children.length; i += 1) visit(children[i]);
  };
  visit(root);
  if (names.length) {
    console.warn(`WebGPU: ${names.join(", ")} has a map and no uv. The map was dropped so the material does not sample attribute "uv".`);
  }
  return names;
}

/**
 * Write `matrix` onto the slots owned by `actorKey`. A shared slots list, or a
 * second copy of the same model, does not get the write. Hidden slots keep the
 * matrix for when they show again, and skip the GPU update.
 * Returns how many instance matrices were sent to the mesh.
 */
export function applyActorMatrix(slots, actorKey, matrix) {
  const dirty = [];
  if (!actorKey || !Array.isArray(slots)) return 0;
  for (const slot of slots) {
    if (!slot || slot.actor !== actorKey || !slot.mesh || typeof slot.mesh.setMatrixAt !== "function") continue;
    if (slot.matrix && typeof slot.matrix.copy === "function") slot.matrix.copy(matrix);
    if (slot.shown === false) continue;
    slot.mesh.setMatrixAt(slot.index, matrix);
    dirty.push(slot.mesh);
  }
  for (const mesh of dirty) {
    if (mesh.instanceMatrix) mesh.instanceMatrix.needsUpdate = true;
  }
  return dirty.length;
}
