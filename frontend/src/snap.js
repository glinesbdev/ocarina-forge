/** Pick the walkable surface under an actor.
 *
 * `hits` are `{ y }` from a downward ray cast at the drag's X and Z. The
 * nearest surface at or just under the pivot wins. A hit above the pivot is
 * used only when it is within a couple of metres, so a ceiling or a huge
 * arena mesh does not pull the actor up. No acceptable hit returns null:
 * the caller keeps the current height instead of inventing y = 0.
 *
 * Snap writes that Y only. X and Z stay on the drag. The gizmo plane has to
 * be sampled at the unsnapped height: once Y jumps, the plane follows, and
 * the same mouse ray hits it at a different X and Z. */

/** A platform you started just under can still catch you. A ceiling cannot. */
export const SNAP_ABOVE_REACH = 2;

/** Cached /api/ground samples farther than this are a different column (an
 * island edge is only a metre or two from the lakebed). */
export const GROUND_SNAP_RADIUS = 0.2;

export function chooseSnap(originY, hits, options = {}) {
  const epsilon = Number.isFinite(options.epsilon) ? options.epsilon : 0.05;
  const aboveReach = Number.isFinite(options.aboveReach) ? options.aboveReach : SNAP_ABOVE_REACH;
  const finite = (hits || []).map((hit) => hit && hit.y).filter((y) => Number.isFinite(y));
  if (!finite.length || !Number.isFinite(originY)) return null;
  const below = finite.filter((y) => y <= originY + epsilon);
  if (below.length) return Math.max(...below);
  const above = finite.filter((y) => y > originY && y - originY <= aboveReach);
  if (!above.length) return null;
  return Math.min(...above);
}

/**
 * Intersection of a view ray with the horizontal plane the translate gizmo
 * samples. `planeY` is the height the drag controls assigned, not the snapped
 * height. Sampling the snapped height is what pins X and Z: the ray that
 * crosses the drag at one point crosses y = 95 at another.
 */
export function dragPlanePoint(origin, direction, planeY) {
  const dy = direction[1];
  if (!Number.isFinite(dy) || Math.abs(dy) < 1e-8 || !Number.isFinite(planeY)) return null;
  const t = (planeY - origin[1]) / dy;
  if (!Number.isFinite(t)) return null;
  return [
    origin[0] + direction[0] * t,
    planeY,
    origin[2] + direction[2] * t,
  ];
}

/** Banc-qualified ids (`Banc/...::0x…`) and the bare hash are the same actor. */
export function isSameActor(a, b) {
  if (a == null || b == null || a === "" || b === "") return false;
  const left = String(a);
  const right = String(b);
  if (left === right) return true;
  const hash = (value) => (value.includes("::") ? value.slice(value.lastIndexOf("::") + 2) : value);
  const leftHash = hash(left);
  const rightHash = hash(right);
  return /^0x[0-9a-f]+$/i.test(leftHash) && leftHash.toLowerCase() === rightHash.toLowerCase();
}

function finiteSnapHits(hits) {
  return (hits || []).filter((hit) => hit && Number.isFinite(hit.y));
}

/**
 * Outdoor ground wins when a heightmap or map mesh produced a hit.
 * A room with neither uses a collision mesh, then the visible static and
 * batched geometry. An empty list is a miss, not y = 0.
 */
export function selectSnapHits({ ground = [], collision = [], models = [] } = {}) {
  const walkable = finiteSnapHits(ground);
  if (walkable.length) return walkable;
  const solids = finiteSnapHits(collision);
  if (solids.length) return solids;
  return finiteSnapHits(models);
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

/** The green translate handle. Snap must not pull this drag back to the ground. */
export function isExplicitLift(axis) {
  return axis === "Y";
}

/**
 * Whether this translate step should take its Y from the ground.
 * X, Z, and the XZ plane snap while they move. Any translate that is not a
 * pure Y lift also snaps when the pointer goes up, so a sideways drop still
 * lands. An explicit Y lift keeps the height the user left, during the drag
 * and after release.
 */
export function shouldSnapTranslate(axis, phase) {
  if (!axis || isExplicitLift(axis)) return false;
  if (phase === "move") return axis === "X" || axis === "Z" || axis === "XZ";
  return phase === "release";
}

/**
 * Actor id left out of the ground query and out of collision hits.
 * Every dragged mesh is excluded. Land_ pieces are the ones that actually
 * sit in the walkable surface, and they must not snap to themselves.
 */
export function snapExcludeId(actor) {
  if (!actor || actor.id == null || actor.id === "") return "";
  return String(actor.id);
}

/** True when this hit is the actor currently being dragged. */
export function isSnapSelfHit(actorId, ignoreId) {
  return isSameActor(actorId, ignoreId);
}

/**
 * One translate for a snap step. `gizmo`, `visible`, and (on release) `stored`
 * are the same array, so a ground hit cannot move the handle without the model.
 * `surfaceY` null leaves the height alone. Snap off, or an explicit Y lift,
 * also leaves it alone.
 */
export function translateAfterSnap(translate, surfaceY, options = {}) {
  const next = [translate[0], translate[1], translate[2]];
  const snap = Boolean(options.snapOn)
    && shouldSnapTranslate(options.axis, options.phase)
    && Number.isFinite(surfaceY);
  if (snap) next[1] = surfaceY;
  return {
    translate: next,
    snapped: snap,
    gizmo: next,
    visible: next,
    stored: options.phase === "release" ? next : null,
  };
}

export function shouldApplyGroundReply(requestX, requestZ, currentX, currentZ, latestX, latestZ, maxDistance = GROUND_SNAP_RADIUS) {
  // A reply applies if the pivot is still in that column, or if this is the
  // latest requested column and the pivot has only moved a short way.
  if (groundReplyApplies(requestX, requestZ, currentX, currentZ, maxDistance)) return true;
  if (latestX == null || latestZ == null) return false;
  if (!groundReplyApplies(requestX, requestZ, latestX, latestZ, 1e-6)) return false;
  return groundReplyApplies(requestX, requestZ, currentX, currentZ, maxDistance);
}
