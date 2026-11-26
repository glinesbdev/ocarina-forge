/** Caps how many unique scene meshes stay resident so a Field tab cannot OOM.

The soft limit is the live CPU copies `__forgeMemory()` still holds: geometry
arrays plus texture bytes that have not been released after the GPU upload.
BC7 GPU size is reported (`bc7Bytes`) and is not part of that total, because
it is not JS heap. The unique-mesh count and the JS heap are safety caps.
 */

/**
 * Last-resort unique-mesh cap. Live CPU bytes and the JS heap are the
 * working limits. Kokiri Child is about 470 unique models and the Field is 852.
 * A cap of 240 stopped Kokiri while the heap and the byte budget were still free,
 * and left visible models as boxes.
 */
export const RESIDENT_MODEL_SOFT_CAP = 4096;
export const RESIDENT_MODEL_HARD_CAP = 4608;
/** The count cap this viewer used before the byte budget. The orbit comparison still uses it. */
export const RESIDENT_LEGACY_COUNT_CAP = 240;
export const RESIDENT_HEAP_SOFT = Math.round(2.2 * 1024 * 1024 * 1024);
export const RESIDENT_HEAP_HARD = Math.round(2.8 * 1024 * 1024 * 1024);
export const RESIDENT_BYTE_SOFT = Math.round(1.2 * 1024 * 1024 * 1024);
export const RESIDENT_BYTE_HARD = Math.round(1.5 * 1024 * 1024 * 1024);
export const RESIDENT_BYTE_TARGET = Math.round(1.0 * 1024 * 1024 * 1024);
export const RESIDENT_TARGET_COUNT = 200;
export const RESIDENT_HEAP_TARGET_COUNT = 160;
export const RESIDENT_MAX_EVICT = 8;
/** Distance multiplier for models behind the camera. Matches the splat balancer's 1.5. */
export const RESIDENT_BEHIND_PENALTY = 1.5;
/** Frames a model must stay unwanted before it can be unloaded. */
export const RESIDENT_UNLOAD_COOLDOWN_FRAMES = 90;
/** Recompute load priority only after the camera moves farther than this. */
export const RESIDENT_MOVE_METRES = 2;
/** Recompute load priority only after the camera turns farther than this. */
export const RESIDENT_TURN_DEGREES = 30;
export const RESIDENT_TURN_DOT = Math.cos((RESIDENT_TURN_DEGREES * Math.PI) / 180);

export function uniqueResidentNames(entries) {
  const names = new Set();
  for (const entry of entries) {
    if (entry && entry.gyaml && entry.parts && entry.parts.length) names.add(entry.gyaml);
  }
  return names;
}

export function overResidentSoftCap(count, heapBytes, assetBytes = 0) {
  return count >= RESIDENT_MODEL_SOFT_CAP
    || (Number(heapBytes) || 0) >= RESIDENT_HEAP_SOFT
    || (Number(assetBytes) || 0) >= RESIDENT_BYTE_SOFT;
}

export function overResidentHardCap(count, heapBytes, assetBytes = 0) {
  return count > RESIDENT_MODEL_HARD_CAP
    || (Number(heapBytes) || 0) >= RESIDENT_HEAP_HARD
    || (Number(assetBytes) || 0) >= RESIDENT_BYTE_HARD;
}

function coord(value, index) {
  if (!value) return 0;
  if (typeof value === "object" && !Array.isArray(value)) {
    const key = index === 0 ? "x" : index === 1 ? "y" : "z";
    const component = value[key];
    return Number.isFinite(component) ? component : 0;
  }
  const component = value[index];
  return Number.isFinite(component) ? component : 0;
}

/** True when the camera has moved more than 2 m or turned more than 30°. */
export function cameraNeedsPriorityUpdate(previous, next) {
  if (!previous || !next || !previous.position || !next.position) return true;
  const dx = coord(next.position, 0) - coord(previous.position, 0);
  const dy = coord(next.position, 1) - coord(previous.position, 1);
  const dz = coord(next.position, 2) - coord(previous.position, 2);
  if (dx * dx + dy * dy + dz * dz > RESIDENT_MOVE_METRES * RESIDENT_MOVE_METRES) return true;
  if (!previous.direction || !next.direction) return true;
  const dot = coord(previous.direction, 0) * coord(next.direction, 0)
    + coord(previous.direction, 1) * coord(next.direction, 1)
    + coord(previous.direction, 2) * coord(next.direction, 2);
  return dot < RESIDENT_TURN_DOT;
}

export function effectiveDistance(item, penalty = RESIDENT_BEHIND_PENALTY) {
  const distance = Number(item && item.distance);
  const away = Number.isFinite(distance) ? distance : Infinity;
  return item && item.behind ? away * penalty : away;
}

/**
 * Farthest unique models that are not in `keep`, up to `maxEvict`, until the
 * count and the byte total are at or below their targets. `keep` is typically
 * the current foreground set plus the selection.
 *
 * With `cooldown: true`, a model is armed the first time it is the farthest
 * unwanted resident and is not unloaded until `cooldownUntil` has passed.
 * A model still cooling down blocks eviction of anything closer.
 */
export function planEvictions(candidates, options = {}) {
  const keep = options.keep || new Set();
  const count = Number(options.count) || 0;
  const heap = Number(options.heapBytes) || 0;
  const assetBytes = Number(options.assetBytes) || 0;
  const maxEvict = Number.isFinite(options.maxEvict) ? options.maxEvict : RESIDENT_MAX_EVICT;
  const target = Number.isFinite(options.targetCount)
    ? options.targetCount
    : (heap >= RESIDENT_HEAP_HARD || assetBytes >= RESIDENT_BYTE_HARD
      ? RESIDENT_HEAP_TARGET_COUNT
      : RESIDENT_TARGET_COUNT);
  const byteTarget = Number.isFinite(options.targetBytes) ? options.targetBytes : RESIDENT_BYTE_TARGET;
  const now = Number(options.now) || 0;
  const cooldown = Boolean(options.cooldown);
  const ignoreCooldown = Boolean(options.ignoreCooldown);
  const evict = [];
  const arm = [];
  let remaining = count;
  let remainingBytes = assetBytes;
  const ranked = [...candidates].filter((item) => item && item.name && !keep.has(item.name));
  ranked.sort((left, right) => effectiveDistance(right) - effectiveDistance(left));
  for (const item of ranked) {
    if (evict.length >= maxEvict) break;
    if (remaining <= target && remainingBytes <= byteTarget) break;
    if (cooldown && !ignoreCooldown) {
      const until = Number(item.cooldownUntil);
      if (!Number.isFinite(until)) {
        arm.push(item.name);
        break;
      }
      if (now < until) break;
    }
    evict.push(item.name);
    remaining -= 1;
    const bytes = Number(item.bytes);
    const share = bytes > 0 ? bytes : (count > 0 ? assetBytes / count : 0);
    remainingBytes -= share;
  }
  return { evict, arm };
}

export function modelsToEvict(candidates, options = {}) {
  return planEvictions(candidates, options).evict;
}

/** A model in the current view (tier below 4) may evict a farther resident without waiting out the unload cooldown. */
export function evictImmediatelyFor(tier) {
  const value = Number(tier);
  return Number.isFinite(value) && value < 4;
}

/**
 * Split a priority-sorted load queue.
 * Names that can launch are `queued`. Names the background filler will still
 * take, while the budget is open, are `background`. Names the budget is holding,
 * including a background tail stuck behind the cap, are `capped`.
 * `canLaunch` is consulted only for eligible names until one cannot launch.
 */
export function classifyResidentQueue(names, { eligible, canLaunch, atCap = false } = {}) {
  let queued = 0;
  let background = 0;
  let capped = 0;
  let blocked = false;
  for (const name of names || []) {
    const isEligible = typeof eligible === "function" && eligible(name);
    if (!blocked && isEligible) {
      if (typeof canLaunch === "function" && canLaunch(name)) {
        queued += 1;
        continue;
      }
      blocked = true;
    }
    if (atCap || blocked) capped += 1;
    else background += 1;
  }
  return { queued, background, capped };
}

export function isWorsePriority(candidate, incoming) {
  const left = candidate || { tier: 5, distance: Infinity };
  const right = incoming || { tier: 5, distance: Infinity };
  const leftTier = Number(left.tier);
  const rightTier = Number(right.tier);
  if (leftTier !== rightTier) return leftTier > rightTier;
  return effectiveDistance(left) > effectiveDistance(right);
}

function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6D2B79F5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * One in-place orbit of a Field-sized unique-model set.
 * `before` is the old count cap that re-sorts after 1 m. `after` is the byte
 * budget, the 2 m / 30° gate, the behind-camera penalty, and the unload cooldown.
 * `rapidReloads` counts a model unloaded and loaded again within 60 frames.
 */
export function simulateFieldOrbit(options = {}) {
  const modelCount = options.modelCount || 852;
  const orbits = options.orbits || 3;
  const stepDegrees = options.stepDegrees || 3;
  const orbitRadius = options.orbitRadius || 25;
  const random = mulberry32(options.seed || 8);
  const models = [];
  for (let i = 0; i < modelCount; i += 1) {
    const angle = random() * Math.PI * 2;
    const dist = 8 + random() * 600;
    const heavy = random() < 0.3;
    const megabytes = heavy ? 12 + random() * 28 : 1 + random() * 4;
    models.push({
      name: `m${i}`,
      x: Math.cos(angle) * dist,
      z: Math.sin(angle) * dist,
      bytes: Math.round(megabytes * 1024 * 1024),
    });
  }
  const steps = Math.round((360 / stepDegrees) * orbits);
  return {
    modelCount,
    steps,
    stepDegrees,
    before: runOrbitPolicy(models, { mode: "count", steps, stepDegrees, orbitRadius }),
    after: runOrbitPolicy(models, { mode: "bytes", steps, stepDegrees, orbitRadius }),
  };
}

function runOrbitPolicy(models, { mode, steps, stepDegrees, orbitRadius }) {
  const resident = new Map();
  const seen = new Set();
  const evictFrame = new Map();
  const cooldown = new Map();
  let evictions = 0;
  let reloads = 0;
  let rapidReloads = 0;
  let recomputes = 0;
  let peakBytes = 0;
  let peakCount = 0;
  let anchor = null;
  const yawStep = (stepDegrees * Math.PI) / 180;
  for (let frame = 1; frame <= steps; frame += 1) {
    const yaw = yawStep * (frame - 1);
    const cam = { x: Math.sin(yaw) * orbitRadius, y: 8, z: Math.cos(yaw) * orbitRadius };
    const fx = -cam.x;
    const fy = -cam.y;
    const fz = -cam.z;
    const length = Math.hypot(fx, fy, fz) || 1;
    const forward = { x: fx / length, y: fy / length, z: fz / length };
    const nextAnchor = {
      position: [cam.x, cam.y, cam.z],
      direction: [forward.x, forward.y, forward.z],
    };
    const moved = mode === "count"
      ? !anchor || anchorDistanceSquared(anchor, nextAnchor) > 1 || anchorDot(anchor, nextAnchor) < 0.998
      : cameraNeedsPriorityUpdate(anchor, nextAnchor);
    if (moved) {
      recomputes += 1;
      anchor = nextAnchor;
      const ranked = models.map((model) => {
        const dx = model.x - cam.x;
        const dy = -cam.y;
        const dz = model.z - cam.z;
        const distance = Math.hypot(dx, dy, dz);
        const behind = dx * forward.x + dy * forward.y + dz * forward.z < 0;
        return { name: model.name, distance, behind, bytes: model.bytes };
      });
      ranked.sort((left, right) => (
        mode === "bytes" ? effectiveDistance(left) - effectiveDistance(right) : left.distance - right.distance
      ));
      const countCap = mode === "bytes" ? RESIDENT_MODEL_SOFT_CAP : RESIDENT_LEGACY_COUNT_CAP;
      const wanted = [];
      let bytes = 0;
      for (const item of ranked) {
        if (wanted.length >= countCap) break;
        if (mode === "bytes" && wanted.length > 0 && bytes + item.bytes > RESIDENT_BYTE_SOFT) break;
        wanted.push(item);
        bytes += item.bytes;
      }
      const wantedNames = new Set(wanted.map((item) => item.name));
      if (mode === "bytes") {
        for (const name of wantedNames) cooldown.delete(name);
      }
      for (const name of [...resident.keys()]) {
        if (wantedNames.has(name)) continue;
        if (mode === "bytes") {
          const until = cooldown.get(name);
          if (until == null) {
            cooldown.set(name, frame + RESIDENT_UNLOAD_COOLDOWN_FRAMES);
            continue;
          }
          if (frame < until) continue;
          cooldown.delete(name);
        }
        resident.delete(name);
        evictFrame.set(name, frame);
        evictions += 1;
      }
      let residentBytes = 0;
      for (const bytes of resident.values()) residentBytes += bytes;
      for (const item of wanted) {
        if (resident.has(item.name)) continue;
        const room = mode === "bytes"
          ? resident.size < RESIDENT_MODEL_SOFT_CAP && (resident.size === 0 || residentBytes + item.bytes <= RESIDENT_BYTE_SOFT)
          : resident.size < RESIDENT_LEGACY_COUNT_CAP;
        if (!room) continue;
        if (seen.has(item.name)) {
          reloads += 1;
          const left = evictFrame.get(item.name);
          if (Number.isFinite(left) && frame - left < 60) rapidReloads += 1;
        }
        seen.add(item.name);
        resident.set(item.name, item.bytes);
        residentBytes += item.bytes;
      }
    }
    let total = 0;
    for (const bytes of resident.values()) total += bytes;
    if (total > peakBytes) peakBytes = total;
    if (resident.size > peakCount) peakCount = resident.size;
  }
  return { evictions, reloads, rapidReloads, recomputes, peakBytes, peakCount, resident: resident.size };
}

function anchorDistanceSquared(previous, next) {
  const dx = next.position[0] - previous.position[0];
  const dy = next.position[1] - previous.position[1];
  const dz = next.position[2] - previous.position[2];
  return dx * dx + dy * dy + dz * dz;
}

function anchorDot(previous, next) {
  return previous.direction[0] * next.direction[0]
    + previous.direction[1] * next.direction[1]
    + previous.direction[2] * next.direction[2];
}
