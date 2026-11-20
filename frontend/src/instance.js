/**
 * Gates for a batched instance. The mesh lives on a BatchedMesh, not on the
 * actor's pivot, so hide, lock, picking, and the gizmo have to agree on these
 * flags or a copy stays clickable after its category is locked.
 *
 * Selection tint and the gizmo matrix write stay in the viewport: they call
 * setColorAt and setMatrixAt on the instance this actor owns.
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
