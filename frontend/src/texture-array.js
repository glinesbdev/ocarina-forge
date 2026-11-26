/**
 * Group compressed textures that share a size, mip count, format, colour
 * space, wrap, and UV channel into one CompressedArrayTexture. The batch
 * stores each instance's layer in an RGBA8 table (the same index layout as
 * BatchedMesh's colour texture). Classic WebGL materials sample that array
 * through onBeforeCompile. Node materials use texture-array-node.js.
 *
 * A published group is not rewritten. releaseTextureCpu drops the mip bytes,
 * and this machine's WebGL2 context has no copyImageSubData, so a texture
 * that arrives after publish opens a new slice instead of reading empty mips.
 */

import { batchKey } from "./instance.js";
import { repairCompressedTexture } from "./gpu-texture.js";

export const MAX_ARRAY_LAYERS = 256;

const SLOT_NAMES = ["map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap"];

/** Bytes of one BC1/BC3/BC7 mip. 0 means this format is not packed into an array. */
export function compressedByteLength(width, height, format) {
  const block = format === 33777 || format === 33776 ? 8
    : format === 33778 || format === 33779 || format === 36492 ? 16
      : 0;
  if (!block || !width || !height) return 0;
  return Math.ceil(width / 4) * Math.ceil(height / 4) * block;
}

export function sourceId(texture) {
  if (!texture) return "none";
  const source = texture.source;
  if (source && (source.uuid || source.id)) return String(source.uuid || source.id);
  return String(texture.uuid || texture.id || "tex");
}

/**
 * Textures that share this key can be layers of one array.
 * Empty when the texture is not a compressed 2D map with readable mips.
 */
export function arrayGroupKey(texture) {
  if (!texture || texture.isCompressedTexture !== true || texture.isCompressedArrayTexture === true) return "";
  const maps = texture.mipmaps;
  if (!Array.isArray(maps) || !maps.length) return "";
  const width = maps[0].width || texture.image?.width || 0;
  const height = maps[0].height || texture.image?.height || 0;
  if (!width || !height) return "";
  for (const level of maps) {
    const expected = compressedByteLength(level.width, level.height, texture.format);
    if (!expected || !level.data || level.data.byteLength !== expected) return "";
  }
  const channel = Number.isInteger(texture.channel) ? texture.channel : 0;
  const wrapS = texture.wrapS ?? 1000;
  const wrapT = texture.wrapT ?? 1000;
  return `${texture.format}|${width}x${height}|m${maps.length}|${texture.colorSpace || ""}|uv${channel}|w${wrapS}x${wrapT}`;
}

function copyMips(texture) {
  const key = arrayGroupKey(texture);
  if (!key) return null;
  const levels = [];
  for (const level of texture.mipmaps) {
    const copy = new Uint8Array(level.data.byteLength);
    copy.set(level.data);
    levels.push({ width: level.width, height: level.height, data: copy });
  }
  return levels;
}

function isCustomMaterial(material) {
  const data = material?.userData || {};
  if (data.blend) return true;
  if (data.terrainShader || data.terrainNode) return true;
  if (data.banc === "terrain") return true;
  return false;
}

function alphaKey(material) {
  if (material?.transparent) return `blend:${material.opacity}`;
  if (material?.alphaTest > 0) return `mask:${material.alphaTest}`;
  return "opaque";
}

function layoutKey(geometry) {
  const indexed = typeof geometry?.getIndex === "function"
    ? Boolean(geometry.getIndex())
    : Boolean(geometry?.index);
  const attributes = geometry?.attributes ? Object.keys(geometry.attributes).sort().join(",") : "";
  return { indexed: indexed ? "idx" : "no", attributes };
}

function emptySlots() {
  return { map: null, normalMap: null, roughnessMap: null, metalnessMap: null, aoMap: null, emissiveMap: null };
}

function identitySlot(texture) {
  if (!texture || texture.isTexture !== true) return null;
  return {
    array: false,
    layer: 0,
    channel: -1,
    group: null,
    groupId: "",
    sourceId: sourceId(texture),
    texture,
    same: false,
  };
}

function sameSource(a, b) {
  if (!a || !b) return false;
  return a === b || sourceId(a) === sourceId(b);
}

export function createTextureArrayPool(THREE) {
  const groups = new Map();
  const bySource = new Map();
  const open = new Map();

  function groupFor(baseKey, texture) {
    const current = open.get(baseKey);
    if (current && !current.published && current.layers && current.layers.length < MAX_ARRAY_LAYERS) return current;
    const slice = current ? current.slice + 1 : 0;
    const group = {
      id: `${baseKey}#${slice}`,
      baseKey,
      slice,
      layers: [],
      published: false,
      array: null,
      format: texture.format,
      colorSpace: texture.colorSpace || "",
      wrapS: texture.wrapS ?? 1000,
      wrapT: texture.wrapT ?? 1000,
      channel: Number.isInteger(texture.channel) ? texture.channel : 0,
      anisotropy: texture.anisotropy || 1,
    };
    groups.set(group.id, group);
    open.set(baseKey, group);
    return group;
  }

  function claim(texture) {
    if (!texture || texture.isTexture !== true) return null;
    const id = sourceId(texture);
    const existing = bySource.get(id);
    if (existing) return existing;
    const key = arrayGroupKey(texture);
    if (!key) return null;
    texture.userData = texture.userData || {};
    texture.userData.forgeArrayHold = true;
    const mips = copyMips(texture);
    if (!mips) {
      texture.userData.forgeArrayHold = false;
      return null;
    }
    const group = groupFor(key, texture);
    const layer = group.layers.length;
    group.layers.push(mips);
    const assignment = { layer, group, groupId: group.id };
    bySource.set(id, assignment);
    texture.userData.forgeArray = { layer, groupId: group.id };
    return assignment;
  }

  function take(texture, channel) {
    const assignment = claim(texture);
    if (!assignment) return identitySlot(texture);
    return {
      array: true,
      layer: assignment.layer,
      channel,
      group: assignment.group,
      groupId: assignment.groupId,
      sourceId: sourceId(texture),
      texture,
      same: false,
    };
  }

  function describe(material, geometry) {
    const color = material?.color?.isColor ? material.color : null;
    if (!material || isCustomMaterial(material)) {
      return { custom: true, key: "", whiten: false, usesArray: false, slots: emptySlots(), color };
    }
    const slots = emptySlots();
    slots.map = take(material.map, 0);
    slots.normalMap = take(material.normalMap, 1);
    slots.roughnessMap = take(material.roughnessMap, 2);
    const roughness = material.roughnessMap;
    if (material.metalnessMap && sameSource(material.metalnessMap, roughness) && slots.roughnessMap) {
      slots.metalnessMap = { ...slots.roughnessMap, texture: material.metalnessMap, same: true };
    }
    if (material.aoMap && sameSource(material.aoMap, roughness) && slots.roughnessMap) {
      slots.aoMap = { ...slots.roughnessMap, texture: material.aoMap, same: true };
    }
    const spareName = !slots.aoMap && arrayGroupKey(material.aoMap) ? "aoMap"
      : arrayGroupKey(material.emissiveMap) ? "emissiveMap"
        : !slots.metalnessMap && arrayGroupKey(material.metalnessMap) ? "metalnessMap"
          : "";
    if (spareName) slots[spareName] = take(material[spareName], 3);
    if (!slots.aoMap) slots.aoMap = identitySlot(material.aoMap);
    if (!slots.emissiveMap) slots.emissiveMap = identitySlot(material.emissiveMap);
    if (!slots.metalnessMap) slots.metalnessMap = identitySlot(material.metalnessMap);
    const parts = [];
    for (const name of SLOT_NAMES) {
      const slot = slots[name];
      if (!slot) continue;
      if (slot.same) {
        parts.push(`${name}:same`);
        continue;
      }
      parts.push(slot.array ? `${name}:${slot.groupId}` : `${name}:id:${slot.sourceId}`);
    }
    const layout = layoutKey(geometry);
    const usesArray = SLOT_NAMES.some((name) => slots[name]?.array);
    return {
      custom: false,
      key: batchKey({
        image: parts.join("|") || "flat",
        alpha: alphaKey(material),
        side: material.side ?? 0,
        indexed: layout.indexed,
        attributes: layout.attributes,
      }),
      whiten: true,
      usesArray,
      slots,
      color,
    };
  }

  function finalize() {
    if (!THREE) return;
    for (const group of groups.values()) {
      if (group.published || !group.layers || !group.layers.length) continue;
      const layers = group.layers;
      const mipmaps = [];
      const levels = layers[0].length;
      for (let level = 0; level < levels; level += 1) {
        let total = 0;
        for (const layer of layers) total += layer[level].data.byteLength;
        const data = new Uint8Array(total);
        let offset = 0;
        for (const layer of layers) {
          data.set(layer[level].data, offset);
          offset += layer[level].data.byteLength;
        }
        mipmaps.push({ data, width: layers[0][level].width, height: layers[0][level].height });
      }
      const texture = new THREE.CompressedArrayTexture(
        mipmaps,
        mipmaps[0].width,
        mipmaps[0].height,
        layers.length,
        group.format,
      );
      texture.colorSpace = group.colorSpace || THREE.NoColorSpace;
      texture.wrapS = group.wrapS;
      texture.wrapT = group.wrapT;
      texture.channel = group.channel;
      texture.anisotropy = group.anisotropy;
      texture.flipY = false;
      texture.generateMipmaps = false;
      texture.name = `forge-array-${group.slice}`;
      texture.userData.forgeArrayOwner = true;
      texture.userData.forgeArrayGroup = group.id;
      repairCompressedTexture(texture);
      group.array = texture;
      group.published = true;
      group.layers = null;
    }
  }

  function dispose() {
    for (const group of groups.values()) {
      if (group.array && typeof group.array.dispose === "function") group.array.dispose();
      group.array = null;
      group.layers = null;
    }
    groups.clear();
    bySource.clear();
    open.clear();
  }

  return { claim, describe, finalize, dispose, groups };
}

export function arraysReady(spec) {
  if (!spec?.usesArray || !spec.slots) return false;
  return SLOT_NAMES.some((name) => {
    const slot = spec.slots[name];
    return Boolean(slot?.array && slot.group?.array);
  });
}

export function applyArrayMaps(material, spec) {
  if (!material || !spec?.slots) return material;
  for (const name of SLOT_NAMES) {
    const slot = spec.slots[name];
    if (slot?.array && slot.group?.array) material[name] = slot.group.array;
  }
  return material;
}

function channelByte(slots, channel) {
  for (const name of SLOT_NAMES) {
    const slot = slots[name];
    if (slot?.array && !slot.same && slot.channel === channel) return slot.layer & 255;
  }
  return 0;
}

/** RGBA8 layer table. Texel j is instance j, matching BatchedMesh's colour texture. */
export function writeLayerTexture(THREE, mesh, records) {
  let count = 1;
  for (const record of records || []) count = Math.max(count, (record.instanceId ?? 0) + 1);
  const size = Math.ceil(Math.sqrt(count));
  const data = new Uint8Array(size * size * 4);
  for (const record of records || []) {
    if (!Number.isInteger(record.instanceId)) continue;
    const index = record.instanceId;
    const slots = record.part?.arrayBatch?.slots;
    if (!slots) continue;
    const x = index % size;
    const y = Math.floor(index / size);
    const offset = (y * size + x) * 4;
    data[offset] = channelByte(slots, 0);
    data[offset + 1] = channelByte(slots, 1);
    data[offset + 2] = channelByte(slots, 2);
    data[offset + 3] = channelByte(slots, 3);
  }
  const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.UnsignedByteType);
  texture.colorSpace = THREE.NoColorSpace;
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.flipY = false;
  texture.unpackAlignment = 1;
  texture.needsUpdate = true;
  texture.userData.forgeLayerTable = true;
  if (mesh) {
    mesh.userData = mesh.userData || {};
    const previous = mesh.userData.forgeLayers;
    if (previous && previous !== texture && typeof previous.dispose === "function") previous.dispose();
    mesh.userData.forgeLayers = texture;
  }
  return texture;
}

const SAMPLE_SNIPPETS = {
  map: ["uniform sampler2D map;", "texture2D( map, vMapUv )", "vMapUv"],
  normalMap: ["uniform sampler2D normalMap;", "texture2D( normalMap, vNormalMapUv )", "vNormalMapUv"],
  roughnessMap: ["uniform sampler2D roughnessMap;", "texture2D( roughnessMap, vRoughnessMapUv )", "vRoughnessMapUv"],
  metalnessMap: ["uniform sampler2D metalnessMap;", "texture2D( metalnessMap, vMetalnessMapUv )", "vMetalnessMapUv"],
  aoMap: ["uniform sampler2D aoMap;", "texture2D( aoMap, vAoMapUv )", "vAoMapUv"],
  emissiveMap: ["uniform sampler2D emissiveMap;", "texture2D( emissiveMap, vEmissiveMapUv )", "vEmissiveMapUv"],
};

function layerFunction(indexExpression, declareVarying) {
  const varying = declareVarying ? "varying float vForgeBatchId;\n" : "";
  return `
${varying}uniform sampler2D forgeLayers;
float forgeLayer(int channel) {
  int size = textureSize(forgeLayers, 0).x;
  int j = int(${indexExpression} + 0.5);
  int x = j - (size * (j / size));
  int y = j / size;
  vec4 texel = texelFetch(forgeLayers, ivec2(x, y), 0);
  float component = texel.r;
  if (channel == 1) component = texel.g;
  else if (channel == 2) component = texel.b;
  else if (channel == 3) component = texel.a;
  return floor(component * 255.0 + 0.5);
}
`;
}

function replaceSample(shader, name, channel) {
  const snippet = SAMPLE_SNIPPETS[name];
  if (!snippet || !shader.includes(snippet[1])) return shader;
  const uniform = snippet[0].replace("sampler2D", "sampler2DArray");
  const sample = `texture( ${name}, vec3( ${snippet[2]}, forgeLayer(${channel}) ) )`;
  return shader.replaceAll(snippet[0], uniform).replaceAll(snippet[1], sample);
}

/**
 * Classic WebGL MeshStandardMaterial declares sampler2D. Point the slots that
 * were packed into an array at sampler2DArray and the instance's layer.
 */
export function patchArrayShader(shader, spec, { indexExpression = "vForgeBatchId", declareVarying = true } = {}) {
  if (!shader || !spec?.slots) return shader;
  if (declareVarying && shader.vertexShader && !shader.vertexShader.includes("vForgeBatchId")) {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying float vForgeBatchId;")
      .replace(
        "#include <project_vertex>",
        "#include <project_vertex>\n#ifdef USE_BATCHING\n\tvForgeBatchId = getIndirectIndex( gl_DrawID );\n#else\n\tvForgeBatchId = 0.0;\n#endif",
      );
  }
  if (shader.fragmentShader && !shader.fragmentShader.includes("float forgeLayer(")) {
    shader.fragmentShader = layerFunction(indexExpression, declareVarying) + shader.fragmentShader;
  }
  for (const name of SLOT_NAMES) {
    const slot = spec.slots[name];
    if (!slot?.array || slot.channel < 0) continue;
    shader.fragmentShader = replaceSample(shader.fragmentShader, name, slot.channel);
  }
  return shader;
}

export function patchPickArrayShader(shader) {
  return patchArrayShader(shader, {
    slots: { map: { array: true, channel: 0 } },
  }, { indexExpression: "vForgeInstance", declareVarying: false });
}

export function installWebGLArrayMaterial(material, mesh, spec) {
  if (!material || !spec) return material;
  const previous = material.onBeforeCompile;
  const previousKey = typeof material.customProgramCacheKey === "function" ? material.customProgramCacheKey() : "";
  const mask = SLOT_NAMES.filter((name) => spec.slots?.[name]?.array).join(",");
  material.onBeforeCompile = function forgeArrayCompile(shader, renderer) {
    if (typeof previous === "function" && previous !== forgeArrayCompile) previous.call(this, shader, renderer);
    shader.uniforms.forgeLayers = { value: mesh?.userData?.forgeLayers || null };
    patchArrayShader(shader, spec);
  };
  material.customProgramCacheKey = () => `forge-array|${mask}|${previousKey}`;
  material.needsUpdate = true;
  return material;
}
