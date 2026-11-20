/** Pick the ground or collision top under an actor.
 *
 * `hits` are `{ y }` from a downward ray. A surface at or below the pivot,
 * within `step` metres above it (a curb), wins. The highest of those is the
 * one the actor stands on. If the pivot is under every surface, the nearest
 * one above it is used so a drop still lands on the floor. */

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
