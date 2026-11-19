/**
 * Which LOD to draw for one actor. `null` means skip it until the camera is closer.
 * A selected actor stays on the full mesh so a transform is not applied to a coarse stand-in.
 */
export function chooseLod({ distance, radius, pixels, hasFar, selected }) {
  if (selected) return 0;
  const reach = Number.isFinite(radius) ? Math.max(radius, 0) : 0;
  const away = Number.isFinite(distance) ? Math.max(distance, 0) : 0;
  const size = Number.isFinite(pixels) ? pixels : 999;
  if (reach < 1.2 && away > 28) return null;
  if (reach < 3 && away > 48) return null;
  if (reach < 8 && away > 90) return null;
  if (size < 2.5 && reach < 20) return null;
  if (hasFar && away > 22) return 1;
  return 0;
}

/** Retail KokiriForest_01 is an untextured stand-in. The reason string is the marker. */
export function isTerrainProxy(meta) {
  return Boolean(meta && typeof meta.reason === "string" && /terrain proxy/i.test(meta.reason));
}
