/** Caps how many unique scene meshes stay resident so a Field tab cannot OOM. */

export const RESIDENT_MODEL_SOFT_CAP = 240;
export const RESIDENT_MODEL_HARD_CAP = 280;
export const RESIDENT_HEAP_SOFT = Math.round(2.2 * 1024 * 1024 * 1024);
export const RESIDENT_HEAP_HARD = Math.round(2.8 * 1024 * 1024 * 1024);
export const RESIDENT_TARGET_COUNT = 200;
export const RESIDENT_HEAP_TARGET_COUNT = 160;
export const RESIDENT_MAX_EVICT = 8;

export function uniqueResidentNames(entries) {
  const names = new Set();
  for (const entry of entries) {
    if (entry && entry.gyaml && entry.parts && entry.parts.length) names.add(entry.gyaml);
  }
  return names;
}

export function heapBytesFrom(memory) {
  const used = memory && Number(memory.usedJSHeapSize);
  return Number.isFinite(used) && used > 0 ? used : 0;
}

export function overResidentSoftCap(count, heapBytes) {
  return count >= RESIDENT_MODEL_SOFT_CAP || heapBytes >= RESIDENT_HEAP_SOFT;
}

export function overResidentHardCap(count, heapBytes) {
  return count > RESIDENT_MODEL_HARD_CAP || heapBytes >= RESIDENT_HEAP_HARD;
}

/**
 * Farthest unique models that are not in `keep`, up to `maxEvict`, until the
 * count is at or below the target. `keep` is typically the current foreground
 * set plus the selection.
 */
export function modelsToEvict(candidates, options = {}) {
  const keep = options.keep || new Set();
  const count = Number(options.count) || 0;
  const heap = Number(options.heapBytes) || 0;
  const maxEvict = Number.isFinite(options.maxEvict) ? options.maxEvict : RESIDENT_MAX_EVICT;
  const target = Number.isFinite(options.targetCount)
    ? options.targetCount
    : (heap >= RESIDENT_HEAP_HARD ? RESIDENT_HEAP_TARGET_COUNT : RESIDENT_TARGET_COUNT);
  const evict = [];
  let remaining = count;
  const ranked = [...candidates].filter((item) => item && item.name && !keep.has(item.name));
  ranked.sort((left, right) => (right.distance || 0) - (left.distance || 0));
  for (const item of ranked) {
    if (evict.length >= maxEvict) break;
    if (remaining <= target) break;
    evict.push(item.name);
    remaining -= 1;
  }
  return evict;
}

export function isWorsePriority(candidate, incoming) {
  const left = candidate || { tier: 5, distance: Infinity };
  const right = incoming || { tier: 5, distance: Infinity };
  const leftDistance = Number(left.distance);
  const rightDistance = Number(right.distance);
  const leftTier = Number(left.tier);
  const rightTier = Number(right.tier);
  if (leftTier !== rightTier) return leftTier > rightTier;
  return (Number.isFinite(leftDistance) ? leftDistance : Infinity) > (Number.isFinite(rightDistance) ? rightDistance : Infinity);
}
