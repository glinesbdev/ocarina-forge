/**
 * Whether a viewport refresh should fetch the field again.
 * Actor edits come back through the same banc. That must not rebuild the terrain.
 * A different scene is the only reason to load another field.
 */
export function shouldReloadTerrain(current, next) {
  if (typeof next !== "string" || next.length === 0) return false;
  return next !== current;
}
