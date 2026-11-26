/**
 * WebGPU (and the WebGPU renderer's WebGL node backend) sample a compressed
 * texture array by layer. The layer comes from the batch's RGBA8 table, through
 * the same indirect id BatchedMesh uses for its matrices. Classic WebGL does
 * not import this file.
 */
import {
  Fn,
  float,
  int,
  ivec2,
  materialReference,
  normalMap,
  texture,
  textureLoad,
  textureSize,
  uv,
  instanceIndex,
  drawIndex,
  vec3,
  vec4,
} from "three/tsl";

function arrayTexture(slot) {
  const texture = slot?.group?.array;
  return texture && texture.isTexture === true ? texture : null;
}

function texelAt(source, x, y) {
  if (source && source.isTextureNode === true) {
    const sampled = source.uv(ivec2(x, y));
    sampled.setSampler(false);
    sampled.setUpdateMatrix(false);
    return sampled;
  }
  return textureLoad(source, ivec2(x, y));
}

/** Instance layer for one channel. Call from inside a Fn so the builder exists. */
export function layerIndex(builder, mesh, channel, layersSource) {
  const drawId = builder && builder.getDrawIndex() === null ? instanceIndex : drawIndex;
  let index = int(drawId);
  const indirect = mesh && mesh._indirectTexture;
  if (indirect && indirect.isTexture === true) {
    const size = textureSize(textureLoad(indirect), 0);
    const x = int(drawId).modInt(int(size));
    const y = int(drawId).div(int(size));
    index = int(textureLoad(indirect, ivec2(x, y)).x);
  }
  const layers = layersSource || (mesh && mesh.userData && mesh.userData.forgeLayers);
  const usable = layers && (layers.isTexture === true || layers.isTextureNode === true);
  if (!usable) return int(0);
  const side = layers.isTextureNode === true ? textureSize(layers).x : textureSize(textureLoad(layers), 0).x;
  const lx = index.modInt(int(side));
  const ly = index.div(int(side));
  const texel = texelAt(layers, lx, ly);
  const component = channel === 1 ? texel.g : channel === 2 ? texel.b : channel === 3 ? texel.a : texel.r;
  return int(component.mul(255).add(0.5));
}

export function batchLayerNode(mesh, channel) {
  return Fn((builder) => layerIndex(builder, mesh, channel))();
}

function sampleArray(builder, mesh, slot) {
  const map = arrayTexture(slot);
  if (!map) return null;
  const layer = layerIndex(builder, mesh, slot.channel);
  const channel = Number.isInteger(map.channel) ? map.channel : 0;
  const geometry = builder && builder.geometry;
  const name = channel > 0 ? `uv${channel}` : "uv";
  const hasUv = Boolean(geometry && typeof geometry.hasAttribute === "function" && geometry.hasAttribute(name));
  if (!hasUv) return null;
  const coord = channel > 0 ? uv(channel) : uv();
  return texture(map, coord).depth(layer);
}

export function attachArrayMaterial(material, mesh, spec) {
  if (!material || !spec?.slots) return material;
  const slots = spec.slots;
  if (arrayTexture(slots.map)) {
    const cutoff = Number(material.alphaTest) || 0;
    material.alphaToCoverage = false;
    if (cutoff > 0) material.alphaTestNode = float(cutoff);
    material.colorNode = Fn((builder) => {
      const sampled = sampleArray(builder, mesh, slots.map);
      if (sampled) {
        if (cutoff > 0) sampled.a.lessThanEqual(float(cutoff)).discard();
        return vec4(sampled.rgb, sampled.a);
      }
      // A missing UV used to return constant white, which is the flat cream
      // albedo. Sample the original map, or the material colour, instead.
      const fallback = slots.map && slots.map.texture;
      if (fallback && fallback.isTexture === true) {
        const flat = texture(fallback, uv());
        if (cutoff > 0) flat.a.lessThanEqual(float(cutoff)).discard();
        return vec4(flat.rgb, flat.a);
      }
      return vec4(
        materialReference("color", "color", material),
        materialReference("opacity", "float", material),
      );
    })();
  }
  if (arrayTexture(slots.roughnessMap)) {
    material.roughnessNode = Fn((builder) => {
      const sampled = sampleArray(builder, mesh, slots.roughnessMap);
      const rough = materialReference("roughness", "float", material);
      return sampled ? sampled.g.mul(rough) : rough;
    })();
  }
  if (arrayTexture(slots.metalnessMap)) {
    material.metalnessNode = Fn((builder) => {
      const sampled = sampleArray(builder, mesh, slots.metalnessMap);
      const metal = materialReference("metalness", "float", material);
      return sampled ? sampled.b.mul(metal) : metal;
    })();
  }
  if (arrayTexture(slots.aoMap)) {
    material.aoNode = Fn((builder) => {
      const sampled = sampleArray(builder, mesh, slots.aoMap);
      const intensity = materialReference("aoMapIntensity", "float", material);
      return sampled ? sampled.r.sub(1).mul(intensity).add(1) : float(1);
    })();
  }
  if (arrayTexture(slots.normalMap)) {
    material.normalNode = Fn((builder) => {
      const sampled = sampleArray(builder, mesh, slots.normalMap);
      return sampled ? normalMap(sampled) : vec3(0, 0, 1);
    })();
  }
  if (arrayTexture(slots.emissiveMap)) {
    material.emissiveNode = Fn((builder) => {
      const sampled = sampleArray(builder, mesh, slots.emissiveMap);
      const color = materialReference("emissive", "color", material);
      const intensity = materialReference("emissiveIntensity", "float", material);
      return sampled ? sampled.rgb.mul(color).mul(intensity) : color.mul(intensity);
    })();
  }
  material.userData = material.userData || {};
  material.userData.arrayNode = true;
  const previousKey = typeof material.customProgramCacheKey === "function" ? material.customProgramCacheKey() : "";
  const layers = mesh?.userData?.forgeLayers?.uuid || "none";
  material.customProgramCacheKey = () => `forge-array-node|${mesh?.uuid || ""}|${layers}|${previousKey}`;
  material.needsUpdate = true;
  return material;
}
