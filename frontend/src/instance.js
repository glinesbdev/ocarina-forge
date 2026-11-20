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
 * r170's WebGPU backend issues one draw per BatchedMesh instance. When every
 * visible instance shares one geometry range, fold them into a single instanced
 * draw. `instance_index` still addresses the indirect texture. Mixed geometry
 * stays one draw per instance, because one draw call has one index range.
 * Returns the draw count the backend will issue.
 */
export function collapseSameGeometryDraw(mesh) {
  const count = mesh && mesh._multiDrawCount ? mesh._multiDrawCount : 0;
  if (count <= 1) return count;
  const starts = mesh._multiDrawStarts;
  const counts = mesh._multiDrawCounts;
  if (!starts || !counts) return count;
  const start = starts[0];
  const length = counts[0];
  for (let i = 1; i < count; i += 1) {
    if (starts[i] !== start || counts[i] !== length) return count;
  }
  if (!mesh._multiDrawInstances) mesh._multiDrawInstances = new Int32Array(Math.max(count, 1));
  mesh._multiDrawInstances[0] = count;
  mesh._multiDrawCount = 1;
  return 1;
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
