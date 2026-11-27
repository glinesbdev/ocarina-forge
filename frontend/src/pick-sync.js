/** True when two pick-id lists would write the same texels. */
export function sameIdList(previous, next) {
  if (!previous || !next || previous.length !== next.length) return false;
  for (let i = 0; i < next.length; i += 1) {
    if ((previous[i] || 0) !== (next[i] || 0)) return false;
  }
  return true;
}

/**
 * Write pick ids for dirty batches only. Unchanged lists are not uploaded.
 * Clean batches are left alone, which is what an orbit with no click needs.
 */
export function syncDirtyPickIds(batches, { idsFor, write, previous, remember } = {}) {
  let uploads = 0;
  const list = batches || [];
  for (const batch of list) {
    if (!batch || !batch.pickIdsDirty) continue;
    const mesh = batch.mesh;
    const pick = mesh && mesh.userData && mesh.userData.pick;
    if (!mesh || !pick) {
      batch.pickIdsDirty = false;
      continue;
    }
    const next = idsFor(batch, pick);
    const prior = previous(batch);
    if (sameIdList(prior, next)) {
      batch.pickIdsDirty = false;
      continue;
    }
    const wrote = write(mesh, next);
    remember(batch, next);
    batch.pickIdsDirty = false;
    if (wrote !== false) uploads += 1;
  }
  return { uploads };
}
