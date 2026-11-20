/**
 * Which LOD to draw for one actor. `null` means skip it until the camera is closer.
 * A selected actor stays on the full mesh so a transform is not applied to a coarse stand-in.
 *
 * Hide distances are twice the first pass (56 / 96 / 180 m). The coarse mesh
 * is chosen by screen size: under about 64 px. A pixel count of 900 or more
 * means the canvas size was not measured, and the coarse mesh then starts
 * past 44 m. A shape under about three pixels is still dropped.
 */
export function chooseLod({ distance, radius, pixels, hasFar, selected }) {
  if (selected) return 0;
  const reach = Number.isFinite(radius) ? Math.max(radius, 0) : 0;
  const away = Number.isFinite(distance) ? Math.max(distance, 0) : 0;
  const size = Number.isFinite(pixels) ? pixels : 999;
  if (reach < 1.2 && away > 56) return null;
  if (reach < 3 && away > 96) return null;
  if (reach < 8 && away > 180) return null;
  if (size < 2.5 && reach < 20) return null;
  if (!hasFar) return 0;
  if (size >= 900) return away > 44 ? 1 : 0;
  return size < 64 ? 1 : 0;
}

/**
 * LOD after frustum and draw-distance cuts. `null` means the instance is not drawn.
 * `maxDistance` of 0 keeps the size rules only. A selected actor is not distance-culled.
 * Occlusion is not tested here.
 */
export function instanceLod({ distance, radius, pixels, hasFar, selected, inFrustum, maxDistance }) {
  if (!inFrustum) return null;
  const cap = Number(maxDistance);
  if (!selected && Number.isFinite(cap) && cap > 0 && distance > cap) return null;
  return chooseLod({ distance, radius, pixels, hasFar, selected });
}

/** Retail KokiriForest_01 is an untextured stand-in. The reason string is the marker. */
export function isTerrainProxy(meta) {
  return Boolean(meta && typeof meta.reason === "string" && /terrain proxy/i.test(meta.reason));
}
