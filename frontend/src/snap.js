/** Pick the ground or collision top under an actor.
 *
 * `hits` are `{ y }` from a downward ray. A surface at or below the pivot,
 * within `step` metres above it (a curb), wins. The highest of those is the
 * one the actor stands on. If the pivot is under every surface, the nearest
 * one above it is used so a drop still lands on the floor. */

/** Cached /api/ground samples farther than this are a different column (an
 * island edge is only a metre or two from the lakebed). */
export const GROUND_SNAP_RADIUS = 0.2;

export function chooseSnap(originY, hits, options = {}) {
  const step = Number.isFinite(options.step) ? options.step : 1.5;
  const finite = (hits || []).map((hit) => hit && hit.y).filter((y) => Number.isFinite(y));
  if (!finite.length || !Number.isFinite(originY)) return null;
  const under = finite.filter((y) => y <= originY + step);
  if (under.length) return Math.max(...under);
  const above = finite.filter((y) => y > originY);
  if (!above.length) return null;
  return Math.min(...above);
}

export function nearestGroundSample(samples, x, z, maxDistance = GROUND_SNAP_RADIUS) {
  let best = null;
  let bestDistance = maxDistance;
  for (const sample of samples || []) {
    if (!sample || !Number.isFinite(sample.y)) continue;
    const distance = Math.hypot(sample.x - x, sample.z - z);
    if (distance <= bestDistance) {
      best = sample;
      bestDistance = distance;
    }
  }
  return best;
}

export function groundReplyApplies(requestX, requestZ, currentX, currentZ, maxDistance = GROUND_SNAP_RADIUS) {
  return Math.hypot(currentX - requestX, currentZ - requestZ) <= maxDistance;
}

export function isMovedTerrainActor(gyaml) {
  return /^Land_/i.test(gyaml || "");
}

export function shouldApplyGroundReply(requestX, requestZ, currentX, currentZ, latestX, latestZ, maxDistance = GROUND_SNAP_RADIUS) {
  // A reply applies if the pivot is still in that column, or if this is the
  // latest requested column and the pivot has only moved a short way.
  if (groundReplyApplies(requestX, requestZ, currentX, currentZ, maxDistance)) return true;
  if (latestX == null || latestZ == null) return false;
  if (!groundReplyApplies(requestX, requestZ, latestX, latestZ, 1e-6)) return false;
  return groundReplyApplies(requestX, requestZ, currentX, currentZ, maxDistance);
}
