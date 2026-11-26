/**
 * Gates for a batched instance. The mesh lives on a BatchedMesh, not on the
 * actor's pivot, so hide, lock, picking, and the gizmo have to agree on these
 * flags or a copy stays clickable after its category is locked.
 *
 * `applyActorMatrix` is the gizmo write. It touches setMatrixAt only for slots
 * whose actor is the one being dragged. Other copies of the same geometry stay
 * on the matrices they already have. BatchedMesh has no instanceMatrix; flagging
 * that buffer is what made the drag throw after the first slot.
 */

// Verified initial state from the shipped GameDataList. Forge has no live save flags.
const PRESENCE_DEFAULTS = new Map([["IsHyruleFieldBridgeClose", false]]);

export function presentByDefault(params) {
  const presence = params?.[1]?.Presence?.[1];
  const flag = PRESENCE_DEFAULTS.get(presence?.FlagName?.[1]);
  const negation = presence?.IsNegation?.[1];
  if (flag === undefined || typeof negation !== "boolean") return true;
  return flag !== negation;
}

export function actorShown({ status, present = true, category, volume, showHidden, categories, hideVolumes, layer, layersVisible }) {
  const hiddenByStatus = (status === "hidden" || !present) && !showHidden;
  const hiddenByCategory = categories?.[category] === "hide";
  const hiddenByVolume = Boolean(volume) && Boolean(hideVolumes);
  const hiddenByLayer = Boolean(layer) && layersVisible?.[layer] === false;
  return !hiddenByStatus && !hiddenByCategory && !hiddenByVolume && !hiddenByLayer;
}

export function actorLocked({ category, categories, companion, pickable }) {
  return categories?.[category] === "lock" || (Boolean(companion) && pickable === false);
}

export function canPick({ shown, locked }) {
  return Boolean(shown) && !locked;
}

export function canGizmo({ id, shown, locked }) {
  return Boolean(id) && Boolean(shown) && !locked;
}

/** Copies that share a key are one BatchedMesh. The key is the material, not the actor. */
export function batchKey({ image, alpha, side, tint, indexed, attributes }) {
  return `${image}|${alpha}|s${side}|c${tint}|${indexed}|${attributes}`;
}

/**
 * Identity of one image for the batch key. Bitmap hashes still live in the
 * viewport. Compressed images are `{width,height,data}`: `drawImage` throws,
 * and a shared `"bitmap"` label then puts every same-size map on one mesh.
 * Clones share `source`, so identical pixels still share a key.
 */
export function imageIdentity(texture) {
  const image = texture && texture.image;
  if (!image) return "none";
  const width = image.width || image.videoWidth || 0;
  const height = image.height || image.videoHeight || 0;
  const source = texture.source;
  const id = (source && (source.uuid || source.id)) || texture.uuid || texture.id || "";
  return `${width}x${height}:${id}`;
}

/**
 * Rewrite `part.batchKey` from the material that now holds the maps.
 * Returns true when any key changed, which means the current meshes were
 * grouped before those maps existed.
 */
export function refreshBatchKeys(parts, keyFor) {
  let changed = false;
  if (typeof keyFor !== "function") return false;
  for (const part of parts || []) {
    if (!part || !part.material || !part.geometry) continue;
    const next = `${keyFor(part.material, part.geometry)}|${part.materialKey || ""}`;
    if (part.batchKey !== next) {
      part.batchKey = next;
      changed = true;
    }
  }
  return changed;
}

/**
 * The drawn mesh has to use the part material that holds the map. A batch
 * compiled before the upload, or a clone made because the uv was missing,
 * keeps a flat material while the part still has the image.
 * `null` means the mesh is already drawing that map. `missingUv` means the
 * batch geometry cannot sample the map's channel, so the caller leaves it.
 */
export function materialToDraw(mesh, parts) {
  const records = [];
  for (const part of parts || []) records.push({ part });
  const next = materialForBatch(records, mesh && mesh.material);
  const map = next && next.map && next.map.isTexture === true ? next.map : null;
  if (!map || !mesh) return null;
  const channel = Number.isInteger(map.channel) ? map.channel : 0;
  const name = channel > 0 ? `uv${channel}` : "uv";
  const geometry = mesh.geometry;
  const hasUv = Boolean(
    geometry && (typeof geometry.hasAttribute === "function"
      ? geometry.hasAttribute(name)
      : geometry.attributes && geometry.attributes[name]),
  );
  if (!hasUv) return { material: null, map, missingUv: true };
  const current = mesh.material;
  const currentMap = current && current.map && current.map.isTexture === true ? current.map : null;
  if (current === next && currentMap === map) return null;
  return { material: next, map, missingUv: false };
}

/** The mesh has to draw a material that actually holds the uploaded map. */
export function materialForBatch(records, fallback) {
  for (const record of records || []) {
    const material = record && record.part && record.part.material;
    const map = material && material.map;
    if (map && map.isTexture === true) return material;
  }
  return fallback;
}

/**
 * True when a resident mesh was compiled before this upload, or when the
 * mesh is still holding a different material than the one that received it.
 * `forgeAlbedo` is the map the mesh was built with. A missing stamp with a
 * live map is the geometry-glb path: the batch existed before the manifest.
 */
export function batchMissesUpload(parts, batches) {
  const materials = new Set();
  for (const part of parts || []) {
    if (part && part.material) materials.add(part.material);
  }
  const held = new Set();
  for (const batch of batches || []) {
    const mesh = batch && (batch.mesh || batch);
    const material = mesh && mesh.material;
    if (!material || !materials.has(material)) continue;
    held.add(material);
    const now = material.map && material.map.isTexture === true ? material.map : null;
    const userData = mesh.userData;
    const stamped = userData && Object.prototype.hasOwnProperty.call(userData, "forgeAlbedo")
      ? (userData.forgeAlbedo && userData.forgeAlbedo.isTexture === true ? userData.forgeAlbedo : null)
      : undefined;
    if (stamped === undefined) {
      if (now) return true;
    } else if (stamped !== now) return true;
  }
  for (const material of materials) {
    const map = material.map && material.map.isTexture === true ? material.map : null;
    if (map && !held.has(material)) return true;
  }
  return false;
}

/**
 * r187's WebGPU backend draws `_mergedDrawStarts/Counts/InstanceCounts` and
 * advances `firstInstance` by each run. The batch shader indexes
 * `_indirectTexture` with that instance index. `_updateDrawParameters` fills
 * both arrays, but `BatchedMesh.onBeforeRender` never calls it with
 * `'merged'` on WebGPU, so a mesh would draw nothing. Calling it every frame
 * is safe: it returns immediately when visibility, frustum, and sort are quiet.
 * Returns the merged draw count.
 */
export function scheduleMergedDraws(mesh, camera, geometry, material) {
  if (!mesh || typeof mesh._updateDrawParameters !== "function") return 0;
  mesh._updateDrawParameters(camera, geometry, material, "merged");
  return mesh._mergedDrawCount || 0;
}

/** Folded WebGPU draws. r187 counts those on `_mergedDrawCount`. */
export function batchSubDrawCount(mesh) {
  if (!mesh) return 0;
  if (mesh._mergedDrawStarts && typeof mesh._mergedDrawCount === "number") return mesh._mergedDrawCount;
  return mesh._multiDrawCount || 0;
}

const reportedMissingUv = new WeakSet();

function pullUserDataTextures(userData) {
  const held = [];
  const walk = (object, path) => {
    if (!object || typeof object !== "object") return;
    for (const key of Object.keys(object)) {
      const value = object[key];
      if (value && value.isTexture) {
        held.push({ path: path.concat(key), texture: value });
        delete object[key];
      } else if (value && typeof value === "object" && value.constructor === Object) {
        walk(value, path.concat(key));
      }
    }
  };
  walk(userData, []);
  return held;
}

function restoreUserDataTextures(userData, held) {
  if (!userData) return;
  for (const { path, texture } of held) {
    let object = userData;
    for (let i = 0; i < path.length - 1; i += 1) {
      const key = path[i];
      if (!object[key] || typeof object[key] !== "object") object[key] = {};
      object = object[key];
    }
    object[path[path.length - 1]] = texture;
  }
}

/**
 * three's Material.clone does JSON.parse(JSON.stringify(userData)), which
 * calls Texture.toJSON. Compressed maps and CPU-released images have no
 * serializable image.data, so three logs "Unable to serialize Texture."
 * Pull live textures out first, clone, then put them back on both materials.
 */
export function cloneMaterialPreservingTextures(material) {
  if (!material || typeof material.clone !== "function") return material;
  const held = pullUserDataTextures(material.userData);
  const copy = material.clone();
  restoreUserDataTextures(material.userData, held);
  copy.userData = copy.userData || {};
  restoreUserDataTextures(copy.userData, held);
  return copy;
}

const SAMPLED_SLOTS = [
  "map", "normalMap", "roughnessMap", "metalnessMap", "emissiveMap", "alphaMap",
  "bumpMap", "displacementMap", "specularMap", "aoMap", "lightMap",
];

function hasUvAttribute(geometry, name) {
  if (!geometry || !name) return false;
  if (typeof geometry.hasAttribute === "function") return geometry.hasAttribute(name);
  return Boolean(geometry.attributes && geometry.attributes[name]);
}

/**
 * The node material reads `uv` for channel 0 and `uv1` for channel 1.
 * AO and light maps are the second set when their channel is 1. Channel 0,
 * including an occlusion texture written without texCoord, reads `uv`.
 */
function sampledAttribute(slot, texture) {
  const channel = texture && Number.isInteger(texture.channel) ? texture.channel : 0;
  if ((slot === "aoMap" || slot === "lightMap") && channel > 0) return `uv${channel}`;
  return channel > 0 ? `uv${channel}` : "uv";
}

/**
 * A mapped material on a geometry without its UV channel makes the node builder warn
 * every compile. Clone that material and drop the samples. The shared material
 * stays mapped for the meshes that do have uvs. Returns the object names.
 * Colour nodes that already avoid `uv` keep their map. AO and light maps are
 * skipped when `uv` (channel 0) or `uv1` (channel 1) is missing.
 */
export function dropMapWithoutUv(root) {
  const names = [];
  const visit = (node) => {
    if (!node) return;
    const geometry = node.geometry;
    if (geometry && node.material && (typeof geometry.hasAttribute === "function" || geometry.attributes)) {
      const list = Array.isArray(node.material) ? node.material : [node.material];
      let next = null;
      list.forEach((material, index) => {
        if (!material) return;
        const ownsColor = Boolean(
          material.colorNode
          || (material.userData && (material.userData.terrainNode || material.userData.cutoutNode || material.userData.blendNode)),
        );
        const drop = [];
        for (const slot of SAMPLED_SLOTS) {
          if (slot === "map" && ownsColor) continue;
          const texture = material[slot];
          if (!texture) continue;
          if (hasUvAttribute(geometry, sampledAttribute(slot, texture))) continue;
          drop.push(slot);
        }
        if (!drop.length) return;
        const copy = typeof material.clone === "function" ? cloneMaterialPreservingTextures(material) : material;
        for (const slot of drop) copy[slot] = null;
        if (drop.includes("aoMap")) copy.aoMapIntensity = 0;
        if (drop.includes("lightMap")) copy.lightMapIntensity = 0;
        copy.needsUpdate = true;
        if (list.length === 1) node.material = copy;
        else {
          next = next || list.slice();
          next[index] = copy;
        }
        if (!reportedMissingUv.has(material)) {
          reportedMissingUv.add(material);
          names.push(node.name || node.type || "mesh");
        }
      });
      if (next) node.material = next;
    }
    const children = node.children;
    if (!children) return;
    for (let i = 0; i < children.length; i += 1) visit(children[i]);
  };
  visit(root);
  if (names.length) {
    console.debug(`WebGPU: ${names.join(", ")} has a map and no uv. The map was dropped so the material does not sample attribute "uv".`);
  }
  return names;
}

/**
 * Write `matrix` onto the slots owned by `actorKey`. A shared slots list, or a
 * second copy of the same model, does not get the write. Hidden slots get the
 * same matrix: the draw can still be showing the previous one.
 * Returns how many instance matrices were sent to the mesh.
 */
export function applyActorMatrix(slots, actorKey, matrix) {
  const dirty = [];
  if (!actorKey || !Array.isArray(slots)) return 0;
  for (const slot of slots) {
    if (!slot || slot.actor !== actorKey || !slot.mesh || typeof slot.mesh.setMatrixAt !== "function") continue;
    if (slot.matrix && typeof slot.matrix.copy === "function") slot.matrix.copy(matrix);
    // Hidden slots used to skip the GPU write. The cull can mark a slot hidden
    // while the draw still shows the last matrix, and the next Y drag was the
    // first setMatrixAt that caught the mesh up. Write every owned slot.
    slot.mesh.setMatrixAt(slot.index, matrix);
    dirty.push(slot.mesh);
  }
  for (const mesh of dirty) {
    if (mesh.instanceMatrix) mesh.instanceMatrix.needsUpdate = true;
  }
  return dirty.length;
}
