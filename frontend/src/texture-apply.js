/**
 * Put a texture manifest onto materials that are already on a mesh, and take
 * those maps back off. Neither path builds geometry or asks for a glb.
 *
 * The manifest key is the glTF node name with the `lodN:` prefix removed
 * (`Body:0`). Three.js strips `:` from Object3D.name, so `lod0:Body:0`
 * becomes `lod0Body0` and no longer matches. The original stays on
 * `userData.name`; parts store that family as `materialKey`.
 */

import { noteTexturePipelineError } from "./texture-debug.js";

const SRGB = "srgb";
const LINEAR = "srgb-linear";
// three.js RepeatWrapping. A character albedo whose UVs or SRT leave 0–1 has to tile.
const REPEAT = 1000;

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
  userData.forgePlain = snapshotAppearance(material);
  return userData;
}

function snapshotAppearance(material) {
  const shot = {
    alphaTest: material.alphaTest || 0,
    transparent: Boolean(material.transparent),
    depthWrite: material.depthWrite !== false,
  };
  if (material.color && typeof material.color.clone === "function") shot.color = material.color.clone();
  if (material.roughness != null) shot.roughness = material.roughness;
  if (material.metalness != null) shot.metalness = material.metalness;
  for (const key of MAP_KEYS) shot[key] = material[key] || null;
  shot.blendMap = material.userData && material.userData.blendMap || null;
  return shot;
}

function applyAppearance(material, shot) {
  for (const key of MAP_KEYS) material[key] = (shot && shot[key]) || null;
  if (shot && shot.color && material.color && typeof material.color.copy === "function") material.color.copy(shot.color);
  if (shot && shot.roughness != null) material.roughness = shot.roughness;
  if (shot && shot.metalness != null) material.metalness = shot.metalness;
  material.alphaTest = (shot && shot.alphaTest) || 0;
  material.transparent = Boolean(shot && shot.transparent);
  if (shot && shot.depthWrite != null) material.depthWrite = shot.depthWrite;
  material.userData.blendMap = (shot && shot.blendMap) || null;
  material.needsUpdate = true;
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
function hasSlotImage(slot) {
  return [slot.map, slot.normalMap, slot.orm, slot.emissiveMap, slot.moss].some(Boolean);
}

/**
 * One row per material key. `missing` is file not found, `decode` is a file
 * that opened and did not yield an image, `not-applied` is a response the
 * mesh did not take, and `applied` is a map now on the material.
 */
/**
 * One glTF material object shared by two shape keys would take every texture
 * write. Clone it once per key. Parts that already share a key keep one object,
 * so a single assign still covers both LODs.
 */
export function separateSharedMaterials(parts) {
  const owner = new Map();
  const replacement = new Map();
  for (const part of parts || []) {
    const material = part && part.material;
    if (!material) continue;
    const key = materialKey(part);
    const previous = owner.get(material);
    if (!previous) {
      owner.set(material, key);
      continue;
    }
    if (previous === key) continue;
    const saved = replacement.get(key);
    if (saved) {
      part.material = saved;
      continue;
    }
    const copy = cloneMaterial(material);
    owner.set(copy, key);
    replacement.set(key, copy);
    part.material = copy;
  }
  return parts;
}

function cloneMaterial(material) {
  if (typeof material.clone === "function") {
    const copy = material.clone();
    copy.userData = { ...(material.userData || {}) };
    return copy;
  }
  const copy = { ...material, map: material.map || null, userData: { ...(material.userData || {}) } };
  if (material.color && typeof material.color.clone === "function") copy.color = material.color.clone();
  return copy;
}

export async function applyTextureManifest(parts, manifest, loadTexture) {
  separateSharedMaterials(parts);
  const materials = (manifest && manifest.materials) || {};
  if (manifest && manifest.skipped) {
    return [{ key: "", status: "not-applied", error: manifest.reason || "Textures are not loaded.", found: "" }];
  }
  if (manifest && manifest.error && !Object.keys(materials).length) {
    return [{ key: "", status: manifest.status || "missing", error: manifest.error, found: "" }];
  }
  const report = [];
  const jobs = [];
  const seen = new Set();
  for (const part of parts || []) {
    const key = materialKey(part);
    if (seen.has(key)) continue;
    seen.add(key);
    const slot = materials[key];
    if (!part.material) {
      report.push({ key, status: "not-applied", error: "not applied. This mesh has no material.", found: "" });
      continue;
    }
    if (!slot) {
      report.push({ key, status: "not-applied", error: "not applied. The texture response has no entry for this mesh.", found: "" });
      continue;
    }
    if (!hasSlotImage(slot)) {
      const status = slot.status === "decode" ? "decode" : "missing";
      const error = slot.error || (status === "decode" ? "decode error" : "file not found");
      report.push({ key, status, error, found: slot.found || "" });
      continue;
    }
    jobs.push(assignSlot(part.material, slot, loadTexture).then((outcome) => {
      if (slot.map && !outcome.applied) {
        report.push({ key, status: "not-applied", error: "not applied. The image request failed.", found: slot.found || "" });
        return;
      }
      report.push({
        key,
        status: "applied",
        error: "",
        found: slot.found || slot.map || "",
        tint: slot.tint || null,
        uv: slot.uv || "",
        tile: slot.tile || "",
      });
    }));
  }
  await Promise.all(jobs);
  return report;
}

export function formatTextureReport(report) {
  const lines = [];
  for (const row of report || []) {
    const key = row.key ? `${row.key}: ` : "";
    const error = String(row.error || "");
    const lower = error.toLowerCase();
    if (row.status === "applied") {
      let line = `${key}applied ${row.found || "texture"}`.trim();
      if (Array.isArray(row.tint) && row.tint.length >= 3) {
        const nums = row.tint.slice(0, 3).map((item) => Number(item).toFixed(2));
        line += `; tint ${nums.join(" ")}`;
      }
      if (row.uv) line += `; uv ${row.uv}`;
      if (row.tile) line += `; repeats ${row.tile}`;
      lines.push(line);
    } else if (row.status === "decode") {
      lines.push(lower.includes("decode") ? `${key}${error}` : `${key}decode error. ${error}`.trim());
    } else if (row.status === "not-applied") {
      lines.push(lower.includes("not applied") ? `${key}${error}` : `${key}not applied. ${error}`.trim());
    } else {
      lines.push(lower.includes("file not found") ? `${key}${error}` : `${key}file not found. ${error}`.trim());
    }
  }
  return lines.join(" ");
}

async function assignSlot(material, slot, loadTexture) {
  const userData = rememberPlain(material);
  if (userData.forgeMapUrl === (slot.map || "") && userData.forgeLit) return { applied: true };
  const pending = new Map();
  const take = (url) => {
    if (!url) return null;
    if (!pending.has(url)) {
      pending.set(url, Promise.resolve()
        .then(() => loadTexture(url))
        .catch((error) => {
          noteTexturePipelineError(error || `texture download failed (${url})`);
          return null;
        }));
    }
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
    mossTex.colorSpace = slot.blend && slot.blend.skin ? SRGB : LINEAR;
    mossTex.needsUpdate = true;
    userData.blendMap = mossTex;
    if (userData.blendMapUniform) userData.blendMapUniform.value = mossTex;
  }
  if (slot.blend && userData.blend) {
    if (slot.blend.skin && !userData.blend.skin) userData.blend.skin = true;
    if (slot.blend.skinVertex && !userData.blend.skinVertex) userData.blend.skinVertex = true;
    if (slot.blend.skinColor && !userData.blend.skinColor) userData.blend.skinColor = slot.blend.skinColor;
  }
  if (slot.color && material.color && typeof material.color.setRGB === "function") {
    const color = slot.color;
    material.color.setRGB(Number(color[0]) || 0, Number(color[1]) || 0, Number(color[2]) || 0);
  }
  if (slot.standIn === "water") userData.waterStandIn = true;
  if (slot.texture) userData.forgeAlbedoName = String(slot.texture);
  if (slot.sampler) userData.forgeAlbedoSampler = String(slot.sampler);
  if (slot.format) userData.forgeAlbedoFormat = String(slot.format);
  const albedo = asTexture(material.map);
  if (albedo && slot.tile) {
    albedo.wrapS = REPEAT;
    albedo.wrapT = REPEAT;
    albedo.needsUpdate = true;
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
  const lit = MAP_KEYS.some((key) => asTexture(material[key])) || asTexture(userData.blendMap);
  if (lit) userData.forgeLit = snapshotAppearance(material);
  material.needsUpdate = true;
  return { applied: Boolean(lit) };
}

/**
 * Swap the mesh between the plain material and the resident textured one.
 * Synchronous. Does not fetch, decode, or dispose. Batches share `part.material`,
 * so writing the maps here updates those draws.
 */
/**
 * Put `forgeLit` back on the material when the stage bar is showing textures.
 * A later clear can leave the snapshot full and the live slots empty. Hiding
 * textures does not call this.
 */
export function restoreShownMaps(parts, shown) {
  if (!shown) return 0;
  const seen = new Set();
  let restored = 0;
  for (const part of parts || []) {
    const material = part && part.material;
    if (!material || seen.has(material)) continue;
    seen.add(material);
    const lit = material.userData && material.userData.forgeLit;
    if (!lit) continue;
    let changed = false;
    for (const key of MAP_KEYS) {
      const held = lit[key];
      if (!held || held.isTexture !== true || material[key] === held) continue;
      material[key] = held;
      changed = true;
      restored += 1;
    }
    if (changed) material.needsUpdate = true;
  }
  return restored;
}

export function presentTextures(parts, shown) {
  const seen = new Set();
  let swapped = 0;
  for (const part of parts || []) {
    const material = part && part.material;
    if (!material || seen.has(material)) continue;
    seen.add(material);
    const lit = material.userData && material.userData.forgeLit;
    const plain = material.userData && material.userData.forgePlain;
    if (!lit || !plain) continue;
    applyAppearance(material, shown ? lit : plain);
    swapped += 1;
  }
  return swapped;
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
    const lit = material.userData.forgeLit;
    if (lit) {
      for (const key of MAP_KEYS) {
        const held = lit[key];
        if (held && held !== plain[key]) material[key] = held;
      }
      if (lit.blendMap) material.userData.blendMap = lit.blendMap;
    }
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
    delete material.userData.forgeLit;
    delete material.userData.forgeMapUrl;
  }
}
