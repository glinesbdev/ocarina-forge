/** CPU/GPU memory accounting for the viewport: textures, geometries, batches, actors. */

import { arrayTexturesOf, isArrayTexture } from "./texture-array.js";

const TEXTURE_KEYS = [
  "map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap", "alphaMap",
  "bumpMap", "displacementMap", "envMap", "lightMap", "specularMap", "anisotropyMap",
  "clearcoatMap", "clearcoatNormalMap", "clearcoatRoughnessMap", "sheenRoughnessMap",
  "sheenColorMap", "specularIntensityMap", "specularColorMap", "iridescenceMap",
  "iridescenceThicknessMap", "transmissionMap", "thicknessMap",
];

function mb(bytes) {
  return Math.round((bytes / 1048576) * 10) / 10;
}

function attributeBytes(geometry) {
  if (!geometry) return 0;
  let total = 0;
  const attrs = geometry.attributes || {};
  for (const name of Object.keys(attrs)) {
    const attr = attrs[name];
    total += (attr && attr.array && attr.array.byteLength) || 0;
  }
  total += (geometry.index && geometry.index.array && geometry.index.array.byteLength) || 0;
  return total;
}

/** Live arrays, or the shape recorded when the part clone was released. */
export function geometryBudgetBytes(geometry) {
  const live = attributeBytes(geometry);
  if (live > 0) return live;
  const shape = geometry && geometry.userData && geometry.userData.forgeCpuShape;
  if (!shape) return 0;
  let total = 0;
  for (const info of Object.values(shape.attributes || {})) total += info.bytes || 0;
  if (shape.index) total += shape.index.bytes || 0;
  return total;
}

/** Allocated array depth, and the layer count the packer actually filled. */
export function arrayLayerCounts(texture) {
  if (!isArrayTexture(texture)) return null;
  const alloc = Math.max(1, (texture.image && texture.image.depth) | 0);
  const used = texture.userData && Number(texture.userData.forgeUsedLayers);
  return {
    used: Number.isInteger(used) && used > 0 ? used : alloc,
    alloc,
  };
}

function arrayLayers(texture) {
  const counts = arrayLayerCounts(texture);
  return counts ? counts.used : 1;
}

/** A 2D image whose pixels have been copied into an array. The array is the resident copy. */
export function supersededArraySource(texture) {
  if (!texture || isArrayTexture(texture) || !texture.userData) return false;
  return texture.userData.forgeInArray === true || texture.userData.forgePackedHold === true;
}

export function bc7GpuBytes(texture) {
  if (!texture || !texture.isCompressedTexture || texture.format !== 36492) return 0;
  if (supersededArraySource(texture)) return 0;
  return gpuTextureBytes(texture);
}

const MODEL_TEXTURE_KEYS = ["map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap", "alphaMap"];
const MODEL_EXTRA_KEYS = ["mossMap", "orm1Map", "normal1Map", "blendMap"];

/** Geometry plus BC7 bytes attributed once per unique model. Shared textures count on the first model. */
export function residentModelBytes(entries) {
  const totals = new Map();
  const geometries = new Set();
  const textures = new Set();
  const list = entries && typeof entries.values === "function" ? entries.values() : entries || [];
  for (const entry of list) {
    if (!entry || !entry.gyaml || !entry.parts || !entry.parts.length) continue;
    let bytes = totals.get(entry.gyaml) || 0;
    const seenGeo = new Set();
    const seenTex = new Set();
    for (const part of entry.parts) {
      if (part.geometry && !seenGeo.has(part.geometry)) {
        seenGeo.add(part.geometry);
        if (!geometries.has(part.geometry)) {
          geometries.add(part.geometry);
          bytes += geometryBudgetBytes(part.geometry);
        }
      }
      const material = part.material;
      if (!material) continue;
      const maps = [];
      for (const key of MODEL_TEXTURE_KEYS) if (material[key]) maps.push(material[key]);
      const extra = material.userData || {};
      for (const key of MODEL_EXTRA_KEYS) if (extra[key]) maps.push(extra[key]);
      for (const texture of maps) {
        if (!texture || seenTex.has(texture) || textures.has(texture)) continue;
        seenTex.add(texture);
        textures.add(texture);
        bytes += bc7GpuBytes(texture);
      }
    }
    totals.set(entry.gyaml, bytes);
  }
  return totals;
}

export function budgetAssetBytes(report) {
  const geometry = (report && report.geometries && report.geometries.cpuBytes) || 0;
  const bc7 = (report && report.textures && report.textures.byFormat && report.textures.byFormat.BC7 && report.textures.byFormat.BC7.gpu) || 0;
  return { geometry, bc7, total: geometry + bc7 };
}

function textureExtent(texture) {
  const image = texture.image || (texture.source && texture.source.data) || {};
  if (texture.isCompressedTexture && texture.mipmaps && texture.mipmaps[0]) {
    return { width: texture.mipmaps[0].width || image.width || 0, height: texture.mipmaps[0].height || image.height || 0 };
  }
  return { width: image.width || 0, height: image.height || 0 };
}

function mipCpuBytes(texture) {
  if (texture.userData?.forgeCpuReleased || texture.source?.userData?.forgeCpuReleased) return 0;
  if (texture.isCompressedTexture && Array.isArray(texture.mipmaps)) {
    let total = 0;
    for (const level of texture.mipmaps) {
      const data = level && level.data;
      total += (data && (data.byteLength || data.length)) || 0;
    }
    return total;
  }
  const image = texture.image || (texture.source && texture.source.data);
  if (!image) return 0;
  if (image.byteLength) return image.byteLength;
  const { width, height } = textureExtent(texture);
  const levels = texture.generateMipmaps ? 4 / 3 : 1;
  return Math.round(width * height * 4 * levels);
}

function gpuTextureBytes(texture) {
  const { width, height } = textureExtent(texture);
  if (!width || !height) return mipCpuBytes(texture);
  const levels = (texture.mipmaps && texture.mipmaps.length) || (texture.generateMipmaps ? Math.floor(Math.log2(Math.max(width, height))) + 1 : 1);
  let w = width;
  let h = height;
  let total = 0;
  const bytesPerPixel = texture.isCompressedTexture ? (texture.format === 33777 ? 0.5 : 1) : 4;
  const layers = arrayLayers(texture);
  for (let i = 0; i < levels; i += 1) {
    total += Math.max(16, Math.ceil(w / 4) * Math.ceil(h / 4) * (bytesPerPixel * 16)) * layers;
    w = Math.max(1, w >> 1);
    h = Math.max(1, h >> 1);
  }
  return Math.round(total);
}

function collectMaterialTextures(material, into) {
  if (!material) return;
  for (const key of TEXTURE_KEYS) {
    if (material[key]) into.add(material[key]);
  }
  const extra = material.userData || {};
  for (const key of ["mossMap", "orm1Map", "normal1Map", "blendMap"]) {
    if (extra[key]) into.add(extra[key]);
  }
  const lit = extra.forgeLit;
  if (lit) {
    for (const key of TEXTURE_KEYS) {
      if (lit[key]) into.add(lit[key]);
    }
    if (lit.blendMap) into.add(lit.blendMap);
  }
  for (const texture of arrayTexturesOf(material)) into.add(texture);
}

function formatName(texture) {
  if (!texture) return "none";
  if (texture.isCompressedTexture) {
    if (texture.format === 36492) return "BC7";
    if (texture.format === 33777) return "BC1";
    if (texture.format === 33778) return "BC2";
    if (texture.format === 33779) return "BC3";
    return `compressed:${texture.format}`;
  }
  return "RGBA";
}

/**
 * @param {{
 *   renderer?: object,
 *   entries?: Map,
 *   batches?: Array,
 *   parsedTemplates?: Map,
 *   recentTerrain?: Map,
 *   terrainRoot?: object,
 *   stats?: object,
 * }} state
 */
export function collectMemoryReport(state = {}) {
  const textures = new Set();
  const geometries = new Map(); // geometry -> { role, bytes }
  const addGeometry = (geometry, role) => {
    if (!geometry) return;
    const prev = geometries.get(geometry);
    if (prev) {
      if (!prev.roles.includes(role)) prev.roles.push(role);
      return;
    }
    geometries.set(geometry, { roles: [role], bytes: attributeBytes(geometry) });
  };

  const entries = state.entries || new Map();
  const batches = state.batches || [];
  const parsedTemplates = state.parsedTemplates || new Map();

  let actors = 0;
  let instanced = 0;
  const models = new Map();
  for (const entry of entries.values()) {
    actors += 1;
    const name = entry.gyaml || "(none)";
    models.set(name, (models.get(name) || 0) + 1);
    if (entry.slots && entry.slots.length) instanced += 1;
    for (const part of entry.parts || []) {
      addGeometry(part.geometry, "part");
      collectMaterialTextures(part.material, textures);
    }
  }

  for (const parsed of parsedTemplates.values()) {
    for (const part of parsed.parts || []) {
      addGeometry(part.geometry, "template-part");
      collectMaterialTextures(part.material, textures);
    }
    if (parsed.source) {
      parsed.source.traverse?.((child) => {
        if (child.geometry) addGeometry(child.geometry, "template-source");
        const mats = Array.isArray(child.material) ? child.material : [child.material];
        for (const material of mats) collectMaterialTextures(material, textures);
      });
    }
  }

  let batchCpu = 0;
  let batchInstanceCapacity = 0;
  for (const batch of batches) {
    const mesh = batch.mesh || batch;
    if (mesh.geometry) {
      addGeometry(mesh.geometry, "batched");
      batchCpu += attributeBytes(mesh.geometry);
    }
    batchInstanceCapacity += mesh.maxInstanceCount || mesh._maxInstanceCount || 0;
    const material = mesh.material;
    const mats = Array.isArray(material) ? material : [material];
    for (const mat of mats) collectMaterialTextures(mat, textures);
  }

  if (state.terrainRoot) {
    state.terrainRoot.traverse?.((child) => {
      if (child.geometry) addGeometry(child.geometry, "terrain");
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      for (const mat of mats) collectMaterialTextures(mat, textures);
    });
  }

  const textureRows = [];
  let texCpu = 0;
  let texGpu = 0;
  let compressed = 0;
  let rgba = 0;
  let uniqueSources = 0;
  let uniqueCpu = 0;
  let uniqueGpu = 0;
  let cpuArray = 0;
  let cpu2d = 0;
  let cpuPacked = 0;
  let usedLayers = 0;
  let allocLayers = 0;
  const seenSources = new Set();
  const byFormat = {};
  for (const texture of textures) {
    const kind = formatName(texture);
    const cpu = mipCpuBytes(texture);
    const gpu = supersededArraySource(texture) ? 0 : gpuTextureBytes(texture);
    const { width, height } = textureExtent(texture);
    const sourceId = (texture.source && texture.source.uuid) || texture.uuid || texture.id;
    const first = !seenSources.has(sourceId);
    if (first) {
      seenSources.add(sourceId);
      uniqueSources += 1;
      uniqueCpu += cpu;
      uniqueGpu += gpu;
      const layers = arrayLayerCounts(texture);
      if (layers) {
        cpuArray += cpu;
        usedLayers += layers.used;
        allocLayers += layers.alloc;
      } else if (supersededArraySource(texture)) {
        cpuPacked += cpu;
      } else {
        cpu2d += cpu;
      }
    }
    texCpu += cpu;
    texGpu += gpu;
    if (texture.isCompressedTexture) compressed += 1;
    else rgba += 1;
    byFormat[kind] = byFormat[kind] || { count: 0, unique: 0, cpu: 0, gpu: 0 };
    byFormat[kind].count += 1;
    if (first) byFormat[kind].unique += 1;
    // Unique-source bytes are the ones that actually sit on the GPU.
    if (first) {
      byFormat[kind].cpu += cpu;
      byFormat[kind].gpu += gpu;
    }
    textureRows.push({ kind, width, height, cpu, gpu, id: texture.uuid || texture.id, unique: first });
  }
  texCpu = uniqueCpu;
  texGpu = uniqueGpu;
  textureRows.sort((a, b) => b.gpu - a.gpu || b.cpu - a.cpu);

  const geoByRole = {};
  let geoCpu = 0;
  for (const item of geometries.values()) {
    geoCpu += item.bytes;
    for (const role of item.roles) {
      geoByRole[role] = geoByRole[role] || { count: 0, bytes: 0 };
      geoByRole[role].count += 1;
      geoByRole[role].bytes += item.bytes;
    }
  }

  const uniqueModels = models.size;
  const topModels = [...models.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20).map(([name, count]) => ({ name, count }));

  const heap = typeof performance !== "undefined" && performance.memory
    ? { usedMB: mb(performance.memory.usedJSHeapSize), totalMB: mb(performance.memory.totalJSHeapSize), limitMB: mb(performance.memory.jsHeapSizeLimit) }
    : null;

  const info = state.renderer && state.renderer.info && state.renderer.info.render;
  const gpuMemory = state.renderer && state.renderer.info && state.renderer.info.memory;
  const stats = state.stats || {};

  return {
    heap,
    gpu: gpuMemory ? { geometries: gpuMemory.geometries || 0, textures: gpuMemory.textures || 0 } : null,
    textures: {
      count: textures.size,
      unique: uniqueSources,
      compressed,
      rgba,
      cpuBytes: texCpu,
      gpuBytes: texGpu,
      cpuMB: mb(texCpu),
      gpuMB: mb(texGpu),
      cpuArrayBytes: cpuArray,
      cpu2dBytes: cpu2d,
      cpuPackedBytes: cpuPacked,
      usedLayers,
      allocLayers,
      byFormat: Object.fromEntries(Object.entries(byFormat).map(([k, v]) => [k, { ...v, cpuMB: mb(v.cpu), gpuMB: mb(v.gpu) }])),
      top: textureRows.slice(0, 20).map((row) => ({ ...row, cpuMB: mb(row.cpu), gpuMB: mb(row.gpu) })),
    },
    geometries: {
      unique: geometries.size,
      cpuBytes: geoCpu,
      cpuMB: mb(geoCpu),
      byRole: Object.fromEntries(Object.entries(geoByRole).map(([k, v]) => [k, { ...v, mb: mb(v.bytes) }])),
      batchedCpuMB: mb(batchCpu),
    },
    actors: {
      total: actors,
      instanced,
      uniqueModels,
      instancesPerModel: uniqueModels ? Math.round((actors / uniqueModels) * 10) / 10 : 0,
      topModels,
    },
    batches: {
      count: batches.length,
      instanceCapacity: batchInstanceCapacity,
    },
    frame: {
      fps: stats.fps ?? null,
      frameMs: stats.frameMs ?? null,
      cpuMs: stats.cpuMs ?? null,
      updateMs: stats.updateMs ?? null,
      cullMs: stats.cullMs ?? null,
      submitMs: stats.submitMs ?? null,
      gpuMs: stats.gpuMs ?? null,
      objects: stats.objects ?? null,
      batches: stats.batchCount ?? null,
      subDraws: stats.subDraws ?? null,
      draws: stats.draws != null ? stats.draws : (info && (info.drawCalls || info.calls)) || null,
      triangles: (info && info.triangles) || null,
      instances: stats.instances ?? null,
      culled: stats.culled ?? null,
      batchKeys: stats.batchKeys ?? null,
      keyReasons: stats.keyReasons ?? null,
      imagePack: stats.imagePack ?? null,
      pickUploads: stats.pickUploads ?? null,
    },
    profile: stats.profile ?? null,
    wasmSurfaceMB: stats.wasmSurfaceMB ?? null,
  };
}
