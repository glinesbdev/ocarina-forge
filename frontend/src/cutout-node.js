/**
 * Masked materials on the node renderer. MeshStandardNodeMaterial multiplies
 * the vec3 colour by the map, and that product's alpha is 0, so the card is
 * either discarded entirely or, once alpha is forced back to 1, painted as a
 * solid sheet. The colour node keeps the texel alpha. The alpha test then
 * discards the cutout. WebGL never imports this file and keeps alphaToCoverage.
 */
import { Fn, batchIndirectIndex, float, int, ivec2, materialReference, normalMap, texture, textureLoad, uv, vec4 } from "three/tsl";
import { isArrayTexture, releaseArraySlots } from "./texture-array.js";
import { attachBlend } from "./blend-node.js";

const reported = new Set();

/** Layer index for one slot of the per-instance float texture. Values are stored as floats, not normalized bytes. */
function layerIndex(layerTexture, channel) {
  const texel = textureLoad(layerTexture, ivec2(int(batchIndirectIndex), int(0)));
  const component = channel === 1 ? texel.g : channel === 2 ? texel.b : channel === 3 ? texel.a : texel.r;
  return int(component);
}

function sampleMap(material, map, uvNode, channel) {
  if (map && isArrayTexture(map)) {
    const layers = material.userData && material.userData.forgeLayerTexture;
    // A missing texel still has to pass an index. `texture_2d_array` rejects
    // a 3-argument textureSample, and that one pipeline kills the command buffer.
    const depth = layers ? layerIndex(layers, channel) : int(0);
    return texture(map, uvNode).depth(depth);
  }
  return texture(map, uvNode);
}

function albedoSource(material) {
  const userData = material && material.userData;
  if (userData && isArrayTexture(userData.forgeArrayAlbedo)) return userData.forgeArrayAlbedo;
  if (material && isArrayTexture(material.map)) return material.map;
  return material && material.map && material.map.isTexture === true ? material.map : null;
}

function holdArray(material, map) {
  if (!isArrayTexture(map)) return;
  material.userData = material.userData || {};
  material.userData.forgeArrayAlbedo = map;
  if (material.map === map) material.map = null;
}

function occlusionFrom(red, material) {
  const intensity = materialReference("aoMapIntensity", "float", material);
  return red.sub(float(1)).mul(intensity).add(float(1));
}

export function attachCutout(material) {
  const map = albedoSource(material);
  if (!material || material.colorNode || !(material.alphaTest > 0) || !map || map.isTexture !== true) return material;
  const tag = material.userData && material.userData.banc;
  if (tag === "terrain" || material.userData.terrainNode || material.userData.terrainShader) return material;
  holdArray(material, map);
  const cutoff = Number(material.alphaTest) || 0;
  material.alphaToCoverage = false;
  material.transparent = false;
  material.depthWrite = true;
  material.alphaTestNode = float(cutoff);
  material.colorNode = Fn((builder) => {
    const geometry = builder && builder.geometry;
    const hasUv = Boolean(geometry && typeof geometry.hasAttribute === "function" && geometry.hasAttribute("uv"));
    const tint = materialReference("color", "color", material);
    if (!hasUv) {
      const name = (builder.object && (builder.object.name || builder.object.type)) || "mesh";
      if (!reported.has(name)) {
        reported.add(name);
        console.warn(`WebGPU cutout "${name}" has no uv. It draws a flat colour instead of sampling attribute "uv".`);
      }
      return vec4(tint, 1);
    }
    const sampled = sampleMap(material, map, uv(), 0);
    sampled.a.lessThanEqual(float(cutoff)).discard();
    return vec4(sampled.rgb.mul(tint), sampled.a);
  })();
  material.userData.cutoutNode = true;
  return material;
}

/**
 * Opaque maps on the node renderer. `MaterialNode.COLOR` decides whether to
 * sample `material.map` when the program is first built. A geometry glb is
 * compiled with `map` null, and that program's cache key does not follow a
 * map assigned later, so the fragment stays the flat material colour.
 * This colour node samples the map that is on the material now. A 2D map stays
 * on the material. An array is moved to `userData.forgeArrayAlbedo` and sampled
 * with `.depth(layer)`, because a stock `map` sample has no layer index.
 * WebGL never imports this file.
 */
export function attachAlbedo(material) {
  const map = albedoSource(material);
  if (!material || material.colorNode || !map || map.isTexture !== true) return material;
  const userData = material.userData;
  // A blend material already names two layers. Sampling material.map here
  // replaces that mix with whichever texture landed in the albedo slot,
  // which is how a mask or a single layer covers the ground.
  if (userData && (userData.terrainNode || userData.cutoutNode || userData.blend || userData.blendNode || userData.terrainShader || userData.banc === "terrain")) {
    return material;
  }
  holdArray(material, map);
  // Opaque albedo keeps alpha 1. Returning the texel alpha punches holes
  // wherever a mask or a block stores a low alpha, which draws as black
  // blotches on a light mesh. Cutouts use attachCutout. Blend keeps the alpha.
  const opaque = material.transparent !== true;
  material.colorNode = Fn((builder) => {
    const channel = Number.isInteger(map.channel) ? map.channel : 0;
    const name = channel > 0 ? `uv${channel}` : "uv";
    const geometry = builder && builder.geometry;
    const hasUv = Boolean(
      builder && typeof builder.hasGeometryAttribute === "function"
        ? builder.hasGeometryAttribute(name)
        : geometry && typeof geometry.hasAttribute === "function" && geometry.hasAttribute(name),
    );
    const tint = materialReference("color", "color", material);
    // Do not construct `uv()` on this branch. The node is cached with the
    // material, and a batch whose geometry has no TEXCOORD still draws it.
    if (!hasUv) return vec4(tint, 1);
    const sampled = sampleMap(material, map, uv(channel), 0);
    return vec4(sampled.rgb.mul(tint), opaque ? float(1) : sampled.a);
  })();
  material.userData = material.userData || userData || {};
  material.userData.albedoNode = true;
  material.needsUpdate = true;
  return material;
}

/**
 * Normal, roughness, occlusion, emissive, and alpha stay on nodes when they
 * are arrays. The built-in slots sample a 2D texture. The layer comes from
 * the same texel as the albedo (green = normal, blue = roughness, alpha =
 * a packed occlusion, emissive, or alpha map that is not the roughness image).
 * Metalness is the ORM blue channel only when the part said so. Occlusion that
 * shares that image is the red channel of the same sample.
 */
export function attachArraySurface(material) {
  if (!material || !material.userData) return material;
  releaseArraySlots(material);
  const userData = material.userData;
  const normal = userData.forgeArrayNormal;
  if (isArrayTexture(normal)) {
    const channel = Number.isInteger(normal.channel) ? normal.channel : 0;
    material.normalNode = normalMap(sampleMap(material, normal, uv(channel), 1));
  }
  const rough = userData.forgeArrayRough;
  const ao = userData.forgeArrayAo;
  const aoSharesRough = isArrayTexture(ao) && ao === rough;
  if (isArrayTexture(rough)) {
    const channel = Number.isInteger(rough.channel) ? rough.channel : 0;
    const sampled = sampleMap(material, rough, uv(channel), 2);
    material.roughnessNode = sampled.g;
    if (userData.forgeArrayOrm === true) material.metalnessNode = sampled.b;
    if (aoSharesRough) material.aoNode = occlusionFrom(sampled.r, material);
  }
  const extra = !aoSharesRough && isArrayTexture(ao)
    ? { map: ao, kind: "ao" }
    : isArrayTexture(userData.forgeArrayEmissive)
      ? { map: userData.forgeArrayEmissive, kind: "emissive" }
      : isArrayTexture(userData.forgeArrayAlpha)
        ? { map: userData.forgeArrayAlpha, kind: "alpha" }
        : null;
  if (extra) {
    const channel = Number.isInteger(extra.map.channel) ? extra.map.channel : 0;
    const sampled = sampleMap(material, extra.map, uv(channel), 3);
    if (extra.kind === "ao") material.aoNode = occlusionFrom(sampled.r, material);
    else if (extra.kind === "emissive") {
      const color = materialReference("emissive", "color", material);
      const intensity = materialReference("emissiveIntensity", "float", material);
      material.emissiveNode = sampled.rgb.mul(color).mul(intensity);
    } else {
      material.opacityNode = sampled.g.mul(materialReference("opacity", "float", material));
    }
  }
  releaseArraySlots(material);
  return material;
}

/**
 * Pull arrays off stock slots, then build the colour and surface nodes.
 * Calling this twice rebuilds those nodes from `userData`.
 */
function clearReplacedSlots(material) {
  const userData = material.userData || {};
  if (isArrayTexture(userData.forgeArrayAlbedo)) material.map = null;
  if (isArrayTexture(userData.forgeArrayNormal)) material.normalMap = null;
  if (isArrayTexture(userData.forgeArrayRough)) {
    material.roughnessMap = null;
    if (userData.forgeArrayOrm === true) material.metalnessMap = null;
  }
  if (isArrayTexture(userData.forgeArrayAo)) material.aoMap = null;
  if (isArrayTexture(userData.forgeArrayEmissive)) material.emissiveMap = null;
  if (isArrayTexture(userData.forgeArrayAlpha)) material.alphaMap = null;
}

export function bindArrayMaterial(material) {
  if (!material) return material;
  material.userData = material.userData || {};
  releaseArraySlots(material);
  clearReplacedSlots(material);
  const arrayAlbedo = isArrayTexture(material.userData.forgeArrayAlbedo) || isArrayTexture(material.map);
  if (arrayAlbedo || isArrayTexture(material.userData.forgeArrayNormal) || isArrayTexture(material.userData.forgeArrayRough) || isArrayTexture(material.userData.forgeArrayAo) || isArrayTexture(material.userData.forgeArrayEmissive) || isArrayTexture(material.userData.forgeArrayAlpha)) {
    material.colorNode = null;
    material.alphaTestNode = null;
    material.normalNode = null;
    material.roughnessNode = null;
    material.metalnessNode = null;
    material.aoNode = null;
    material.emissiveNode = null;
    material.opacityNode = null;
    material.userData.albedoNode = false;
    material.userData.cutoutNode = false;
  }
  if (material.userData.blend) {
    // The blend mix samples a0/a1 from the lookup. The plain surface nodes
    // read those same channels as normal and roughness, so they must not run.
    material.userData.blendNode = false;
    material.colorNode = null;
    material.normalNode = null;
    material.roughnessNode = null;
    material.metalnessNode = null;
    material.aoNode = null;
    material.emissiveNode = null;
    material.opacityNode = null;
    attachBlend(material);
  } else {
    if (arrayAlbedo || (material.map && material.map.isTexture === true && !isArrayTexture(material.map))) {
      if (material.alphaTest > 0) attachCutout(material);
      else attachAlbedo(material);
    }
    attachArraySurface(material);
  }
  releaseArraySlots(material);
  clearReplacedSlots(material);
  material.needsUpdate = true;
  return material;
}
