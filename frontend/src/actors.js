/**
 * Decide what a scene refresh has to do to the batches already on screen.
 * A transform, including one copy of a repeated model or several poses restored
 * by undo, patches those actors' matrices. Adding, removing, or swapping a
 * model has to rebuild, and the caller keeps the old meshes until that finishes.
 */

function sameVector(left, right) {
  const a = Array.isArray(left) ? left : [1, 1, 1];
  const b = Array.isArray(right) ? right : [1, 1, 1];
  if (a.length < 3 || b.length < 3) return false;
  for (let i = 0; i < 3; i += 1) {
    if (Math.abs(Number(a[i]) - Number(b[i])) > 1e-4) return false;
  }
  return true;
}

function samePlacement(left, right) {
  return (
    left.gyaml === right.gyaml &&
    Boolean(left.companion) === Boolean(right.companion) &&
    Boolean(left.volume) === Boolean(right.volume)
  );
}

export function planActorRefresh(previous, next) {
  if (!Array.isArray(previous) || !Array.isArray(next)) return { mode: "rebuild" };
  if (previous.length !== next.length) return { mode: "rebuild" };
  const prior = new Map();
  for (const actor of previous) {
    if (!actor || !actor.id || prior.has(actor.id)) return { mode: "rebuild" };
    prior.set(actor.id, actor);
  }
  const actors = [];
  for (const actor of next) {
    const old = actor && prior.get(actor.id);
    if (!old || !samePlacement(old, actor)) return { mode: "rebuild" };
    if (Boolean(old.logicOnly) !== Boolean(actor.logicOnly) || old.fetchModel !== actor.fetchModel || old.layer !== actor.layer) {
      return { mode: "rebuild" };
    }
    const moved = !sameVector(old.translate, actor.translate) || !sameVector(old.rotate, actor.rotate) || !sameVector(old.scale, actor.scale);
    const dressed =
      old.status !== actor.status ||
      old.category !== actor.category ||
      old.name !== actor.name ||
      Boolean(old.pickable) !== Boolean(actor.pickable);
    if (moved || dressed) actors.push(actor);
  }
  return actors.length ? { mode: "patch", actors } : { mode: "keep" };
}
