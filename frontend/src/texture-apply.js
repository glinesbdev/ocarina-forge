/**
 * Put a texture manifest onto materials that are already on a mesh, and take
 * those maps back off. Neither path builds geometry or asks for a glb.
 *
 * The manifest key is the glTF node name with the `lodN:` prefix removed
 * (`Body:0`). Three.js strips `:` from Object3D.name, so `lod0:Body:0`
 * becomes `lod0Body0` and no longer matches. The original stays on
 * `userData.name`; parts store that family as `materialKey`.
 */

const SRGB = "srgb";
const LINEAR = "srgb-linear";

export function materialKey(part) {
  if (part && typeof part === "object") {
    if (part.materialKey) return String(part.materialKey);
    return String(part.name || "").replace(/^lod\d+:/, "");
  }
  return String(part || "").replace(/^lod\d+:/, "");
}

function asTexture(value) {
  return value && value.isTexture === true ? value : null;
}

const MAP_KEYS = ["map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap"];

function rememberPlain(material) {
  const userData = material.userData || (material.userData = {});
  if (userData.forgePlain) return userData;
  const plain = { alphaTest: material.alphaTest || 0, transparent: Boolean(material.transparent) };
  if (material.color && typeof material.color.clone === "function") plain.color = material.color.clone();
  if (material.roughness != null) plain.roughness = material.roughness;
  if (material.metalness != null) plain.metalness = material.metalness;
  for (const key of MAP_KEYS) plain[key] = material[key] || null;
  plain.blendMap = material.userData && material.userData.blendMap || null;
  userData.forgePlain = plain;
  return userData;
}

function replaceMap(material, key, texture, plain, colorSpace) {
  const image = asTexture(texture);
  if (image) {
    if (colorSpace) image.colorSpace = colorSpace;
    image.needsUpdate = true;
  }
  material[key] = image || null;
  return image && image !== plain[key] ? image : null;
}

/**
 * `loadTexture(url)` returns one texture. The same URL is loaded once.
 * Maps already on the material are replaced. The plain color is kept so a
 * later clear can restore it.
 */
export async function applyTextureManifest(parts, manifest, loadTexture) {
  const materials = (manifest && manifest.materials) || {};
  const jobs = [];
  for (const part of parts || []) {
    const slot = materials[materialKey(part)];
    if (!slot || !part.material) continue;
    if (![slot.map, slot.normalMap, slot.orm, slot.emissiveMap, slot.moss].some(Boolean)) continue;
    jobs.push(assignSlot(part.material, slot, loadTexture));
  }
  await Promise.all(jobs);
}

async function assignSlot(material, slot, loadTexture) {
  const userData = rememberPlain(material);
  if (userData.forgeMapUrl === (slot.map || "") && material.map && material.map.isTexture === true) return;
  const pending = new Map();
  const take = (url) => {
    if (!url) return null;
    if (!pending.has(url)) pending.set(url, Promise.resolve(loadTexture(url)));
    return pending.get(url);
  };
  const [map, normalMap, orm, emissive, moss] = await Promise.all([
    take(slot.map),
    take(slot.normalMap),
    take(slot.orm),
    take(slot.emissiveMap),
    take(slot.moss),
  ]);
  const plain = userData.forgePlain;
  replaceMap(material, "map", map, plain, SRGB);
  replaceMap(material, "normalMap", normalMap, plain, LINEAR);
  replaceMap(material, "roughnessMap", orm, plain, LINEAR);
  replaceMap(material, "metalnessMap", orm, plain, LINEAR);
  replaceMap(material, "aoMap", orm, plain, LINEAR);
  replaceMap(material, "emissiveMap", emissive, plain, LINEAR);
  const mossTex = asTexture(moss);
  if (mossTex) {
    mossTex.colorSpace = LINEAR;
    mossTex.needsUpdate = true;
    userData.blendMap = mossTex;
  }
  if (slot.color && material.color && typeof material.color.setRGB === "function") {
    const color = slot.color;
    material.color.setRGB(Number(color[0]) || 0, Number(color[1]) || 0, Number(color[2]) || 0);
  }
  if (asTexture(orm)) {
    material.roughness = 1;
    material.metalness = 1;
  }
  if (slot.alpha === "mask") {
    material.transparent = false;
    material.alphaTest = Number(slot.cutoff) || 0.5;
    material.depthWrite = true;
  } else if (slot.alpha === "blend") {
    material.transparent = true;
    material.depthWrite = false;
  }
  userData.forgeMapUrl = slot.map || "";
  material.needsUpdate = true;
}

/**
 * Drop the maps this module assigned and restore the plain material.
 * `release` runs once per texture object before dispose. Shared images that
 * were on the material before textures were applied are left in place.
 */
export function clearTextureMaps(parts, release = () => {}) {
  const seen = new Set();
  for (const part of parts || []) {
    const material = part && part.material;
    const plain = material && material.userData && material.userData.forgePlain;
    if (!material || !plain) continue;
    for (const key of MAP_KEYS) {
      const current = material[key];
      material[key] = plain[key] || null;
      if (!current || current === plain[key] || seen.has(current)) continue;
      seen.add(current);
      try {
        release(current);
      } catch {
        // The GPU copy is already gone.
      }
      if (typeof current.dispose === "function") current.dispose();
    }
    const blendMap = material.userData.blendMap;
    material.userData.blendMap = plain.blendMap || null;
    if (blendMap && blendMap !== plain.blendMap && !seen.has(blendMap)) {
      seen.add(blendMap);
      try {
        release(blendMap);
      } catch {
        // The GPU copy is already gone.
      }
      if (typeof blendMap.dispose === "function") blendMap.dispose();
    }
    if (plain.color && material.color && typeof material.color.copy === "function") material.color.copy(plain.color);
    if (plain.roughness != null) material.roughness = plain.roughness;
    if (plain.metalness != null) material.metalness = plain.metalness;
    material.alphaTest = plain.alphaTest || 0;
    material.transparent = plain.transparent;
    material.needsUpdate = true;
    delete material.userData.forgePlain;
    delete material.userData.forgeMapUrl;
  }
}
