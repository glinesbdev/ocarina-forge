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

/**
 * Copies that share a key are one BatchedMesh.
 * Tint is per instance (`setColorAt`), so it is not part of the key.
 * `sizeClass` is the texture-array group (or a 2D source id) and replaces `image`.
 */
export function batchKey({ image, alpha, side, indexed, attributes, sizeClass }) {
  const imageKey = sizeClass || image || "flat";
  return `${imageKey}|${alpha}|s${side}|${indexed}|${attributes}`;
}

/**
 * Streaming still batches one model at a time, before arrays are sealed.
 * That key keeps tint and the image signature so a texture written onto the
 * shared material shows up immediately, and two shapes do not fold together.
 */
export function stageBatchKey({ image, alpha, side, tint, indexed, attributes }) {
  return `${image}|${alpha}|s${side}|c${tint}|${indexed}|${attributes}`;
}

/**
 * Fold consecutive BatchedMesh ranges that share one index span into one
 * instanced draw. `batchFirst[i]` is the indirect-texture slot of that run.
 * r170's WebGPU backend ignores it and restarts every run at instance 0, so
 * `patchBatchedDraw` has to pass the slot through. Returns the draw count.
 */
export function groupGeometryDraws(mesh) {
  const count = mesh && mesh._multiDrawCount ? mesh._multiDrawCount : 0;
  if (!mesh.userData) mesh.userData = {};
  if (count <= 1) {
    mesh.userData.batchFirst = null;
    mesh._multiDrawInstances = null;
    return count;
  }
  const starts = mesh._multiDrawStarts;
  const counts = mesh._multiDrawCounts;
  if (!starts || !counts) {
    mesh.userData.batchFirst = null;
    mesh._multiDrawInstances = null;
    return count;
  }
  const nextStarts = [];
  const nextCounts = [];
  const nextInstances = [];
  const nextFirst = [];
  let run = 0;
  for (let i = 1; i <= count; i += 1) {
    const same = i < count && starts[i] === starts[run] && counts[i] === counts[run];
    if (same) continue;
    nextStarts.push(starts[run]);
    nextCounts.push(counts[run]);
    nextInstances.push(i - run);
    nextFirst.push(run);
    run = i;
  }
  if (!mesh._multiDrawInstances || mesh._multiDrawInstances.length < nextStarts.length) {
    mesh._multiDrawInstances = new Int32Array(Math.max(count, nextStarts.length));
  }
  if (!mesh.userData.batchFirst || mesh.userData.batchFirst.length < nextFirst.length) {
    mesh.userData.batchFirst = new Int32Array(Math.max(count, nextFirst.length));
  }
  for (let i = 0; i < nextStarts.length; i += 1) {
    starts[i] = nextStarts[i];
    counts[i] = nextCounts[i];
    mesh._multiDrawInstances[i] = nextInstances[i];
    mesh.userData.batchFirst[i] = nextFirst[i];
  }
  mesh._multiDrawCount = nextStarts.length;
  return nextStarts.length;
}

/**
 * r170 draws a multi-instance batch from instance 0. A later geometry in the
 * same mesh would then read the first run's matrices. Wrap the pass encoder
 * for that one call and supply the slot `groupGeometryDraws` recorded.
 */
export function patchBatchedDraw(backend) {
  if (!backend || backend.drawPatched || typeof backend.draw !== "function") return backend;
  const original = backend.draw;
  backend.draw = function drawGrouped(renderObject, info) {
    const object = renderObject && renderObject.object;
    const firsts = object && object.userData && object.userData.batchFirst;
    if (!firsts || !object._multiDrawCount) return original.call(this, renderObject, info);
    const data = typeof this.get === "function" ? this.get(renderObject.context) : null;
    const pass = data && data.currentPass;
    if (!pass || typeof pass.drawIndexed !== "function") return original.call(this, renderObject, info);
    const native = pass.drawIndexed;
    let slot = 0;
    pass.drawIndexed = function groupedIndexed(indexCount, instanceCount, firstIndex, baseVertex) {
      const firstInstance = firsts[slot] || 0;
      slot += 1;
      return native.call(this, indexCount, instanceCount, firstIndex, baseVertex, firstInstance);
    };
    try {
      return original.call(this, renderObject, info);
    } finally {
      pass.drawIndexed = native;
    }
  };
  backend.drawPatched = true;
  return backend;
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
