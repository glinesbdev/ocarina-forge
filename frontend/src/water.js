import { Float32BufferAttribute } from "three";

const WATER_TILE_SIZE = 4;
export const FISHING_POND_WATER = "Obj_LakeHrl_WaterPlane_A_07";

export function fishingPondFish(gyaml) {
  return gyaml === "Animal_Fish_Bass" || gyaml === "Animal_Fish_Loach";
}

export function fishingPondPreviewOffset(spawnY, waterY, modelTop = 0) {
  if (!Number.isFinite(waterY) || spawnY <= waterY) return 0;
  return waterY - 0.35 - Math.max(0, modelTop) - spawnY;
}

export function isPreviewWater(material, surface, actor) {
  if (material.userData?.banc === "water" || material.name === "water") return true;
  // Existing scene preview packs keep alpha and node names but omit material tags.
  const names = `${surface} ${actor}`.replaceAll("_", " ");
  return material.transparent && /\b(?:water(?:plane|surface|fall|front|back)|shallowwater)\w*\b|\bwater\b/i.test(names);
}

function uvSpan(attribute) {
  let minU = Infinity;
  let maxU = -Infinity;
  let minV = Infinity;
  let maxV = -Infinity;
  for (let i = 0; i < attribute.count; i += 1) {
    const u = attribute.getX(i);
    const v = attribute.getY(i);
    if (u < minU) minU = u;
    if (u > maxU) maxU = u;
    if (v < minV) minV = v;
    if (v > maxV) maxV = v;
  }
  if (!Number.isFinite(minU)) return 0;
  return Math.max(maxU - minU, maxV - minV);
}

export function ensureWaterUv(geometry, channel = 0, options = {}) {
  if (!geometry || typeof geometry.hasAttribute !== "function") return false;
  const attribute = channel === 0 ? "uv" : `uv${channel}`;
  const retile = Boolean(options && options.retile);
  if (geometry.hasAttribute(attribute)) {
    // A chart that already repeats is the authored tiling. A 0..1 chart on a
    // lake stretches the 16×16 stand-in into one texel and looks flat.
    if (!retile || uvSpan(geometry.getAttribute(attribute)) > 2) return false;
  }
  const position = geometry.getAttribute("position");
  if (!position?.count) return false;
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const { min, max } = geometry.boundingBox;
  const width = max.x - min.x;
  const height = max.y - min.y;
  const depth = max.z - min.z;
  // Project onto the two broadest axes so flat lakes and upright water sheets tile.
  const horizontal = height <= width && height <= depth;
  const alongX = horizontal || depth <= width;
  const uv = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i += 1) {
    uv[2 * i] = (alongX ? position.getX(i) : position.getZ(i)) / WATER_TILE_SIZE;
    uv[2 * i + 1] = (horizontal ? position.getZ(i) : position.getY(i)) / WATER_TILE_SIZE;
  }
  geometry.setAttribute(attribute, new Float32BufferAttribute(uv, 2));
  return true;
}
