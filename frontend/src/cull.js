/**
 * Which LOD to draw for one actor. `null` means skip it until the camera is closer.
 * A selected actor stays on the full mesh so a transform is not applied to a coarse stand-in.
 *
 * `distance` is metres from the camera to the instance's world bounding sphere
 * (center distance minus radius), not the distance to the world origin and not
 * the distance to the actor pivot. Hide distances are twice the first pass
 * (56 / 96 / 180 m) and apply to that gap.
 *
 * A model with a far mesh never drops both levels: past those distances, or
 * under about three pixels, it stays on the coarse mesh. A model with no far
 * mesh still drops. The coarse mesh is also chosen under about 64 px. A pixel
 * count of 900 or more means the canvas size was not measured, and the coarse
 * mesh then starts past 44 m.
 */
export function chooseLod({ distance, radius, pixels, hasFar, selected }) {
  if (selected) return 0;
  const reach = Number.isFinite(radius) ? Math.max(radius, 0) : 0;
  const away = Number.isFinite(distance) ? Math.max(distance, 0) : 0;
  const size = Number.isFinite(pixels) ? pixels : 999;
  const pastProp =
    (reach < 1.2 && away > 56) ||
    (reach < 3 && away > 96) ||
    (reach < 8 && away > 180) ||
    (size < 2.5 && reach < 20);
  if (pastProp) return hasFar ? 1 : null;
  if (!hasFar) return 0;
  if (size >= 900) return away > 44 ? 1 : 0;
  return size < 64 ? 1 : 0;
}

/**
 * LOD after frustum and draw-distance cuts. `null` means the instance is not drawn.
 * `distance` is the same bounds gap as `chooseLod`. `maxDistance` of 0 keeps the
 * size rules only. A selected actor is not distance-culled. The field is not an
 * instance and must not be passed here. Occlusion is not tested here.
 */
export function instanceLod({ distance, radius, pixels, hasFar, selected, inFrustum, maxDistance }) {
  if (!inFrustum) return null;
  const cap = Number(maxDistance);
  const away = Number.isFinite(distance) ? distance : Infinity;
  if (!selected && Number.isFinite(cap) && cap > 0 && away > cap) return null;
  return chooseLod({ distance: away, radius, pixels, hasFar, selected });
}

/**
 * If the chosen level was not built, keep the other one. Returning null here
 * is what hid both the full mesh and the far mesh.
 */
export function resolveLod(desired, levels) {
  if (desired == null) return null;
  const have = Array.isArray(levels) ? levels : [];
  if (have.includes(desired)) return desired;
  if (have.includes(0)) return 0;
  return have.length ? have[0] : null;
}

/** Use the original glTF node name. Three removes colons from animation binding names. */
export function modelLod(object) {
  for (let current = object; current; current = current.parent) {
    const match = /^lod(\d+):(.*)$/.exec(current.userData?.name || current.name || "");
    if (match) return { lod: Number(match[1]), family: match[2] || current.uuid };
  }
  return { lod: 0, family: object.uuid };
}

/** Metres from the camera to the sphere surface. A sphere that reaches the camera is 0. */
export function boundsGap(distanceToCenter, radius) {
  const away = Number.isFinite(distanceToCenter) ? Math.max(distanceToCenter, 0) : Infinity;
  const reach = Number.isFinite(radius) ? Math.max(radius, 0) : 0;
  if (!Number.isFinite(away)) return Infinity;
  return Math.max(0, away - reach);
}

/** Lower tiers load first. Keep small distant props behind useful detail in the view. */
export function modelLoadTier({ selected, inFrustum, gap, radius, hasPreview }) {
  if (selected) return 0;
  if (inFrustum) {
    if (gap < 80 || (hasPreview && radius >= (radius + gap) * 0.12)) return 1;
    if (radius >= (radius + gap) * 0.008) return 2;
  }
  if (gap < 28) return 3;
  return inFrustum ? 4 : 5;
}

export function isForegroundModel(tier) {
  return tier < 4;
}

/**
 * Local bounding sphere through the same column-major matrix the instance is drawn with.
 * The center is not left at the world origin unless that matrix puts it there.
 */
export function transformSphere(center, radius, matrix, target = { center: [0, 0, 0], radius: 0 }) {
  const x = center && Number.isFinite(center[0]) ? center[0] : 0;
  const y = center && Number.isFinite(center[1]) ? center[1] : 0;
  const z = center && Number.isFinite(center[2]) ? center[2] : 0;
  const m = matrix || [];
  const at = (index) => (Number.isFinite(m[index]) ? m[index] : 0);
  if (!target.center) target.center = [0, 0, 0];
  target.center[0] = at(0) * x + at(4) * y + at(8) * z + at(12);
  target.center[1] = at(1) * x + at(5) * y + at(9) * z + at(13);
  target.center[2] = at(2) * x + at(6) * y + at(10) * z + at(14);
  const sx = Math.hypot(at(0), at(1), at(2));
  const sy = Math.hypot(at(4), at(5), at(6));
  const sz = Math.hypot(at(8), at(9), at(10));
  const reach = Number.isFinite(radius) ? Math.max(radius, 0) : 0;
  target.radius = reach * Math.max(sx, sy, sz);
  return target;
}

/** Retail KokiriForest_01 is an untextured stand-in. The reason string is the marker. */
export function isTerrainProxy(meta) {
  return Boolean(meta && typeof meta.reason === "string" && /terrain proxy/i.test(meta.reason));
}
