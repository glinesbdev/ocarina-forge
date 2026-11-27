/**
 * TSL port of the moss blend. WebGPU ignores onBeforeCompile, so the node
 * graph mixes the overlay over the base map with the same soft threshold.
 * Roughness, metal, AO, and the overlay normal use that same weight.
 * WebGL keeps the GLSL path and writes the same uniform objects.
 *
 * When the images were packed, a0, a1, and the ORM or normal maps are
 * `texture_2d_array` samples. The layer index comes from the per-instance
 * lookup (`BLEND_LAYER_MAP`: r = a0, g = a1, b = the `_c0` vertex component,
 * a = base ORM). `_c0` itself stays the vertex mask. It is not a third
 * albedo image. Bake samplers are never packed.
 */
import { Fn, attribute, batchIndirectIndex, float, int, ivec2, materialReference, mix, normalMap, reference, smoothstep, step, texture, textureLoad, uv, vec3, vec4 } from "three/tsl";
import { BLEND_LAYER_MAP, BLEND_ROW, BLEND_SURFACE_MAP, isArrayTexture } from "./texture-array.js";

export function attachBlend(material) {
  const userData = material.userData || {};
  const blend = userData.blend;
  const baseMap = isArrayTexture(userData.forgeArrayAlbedo) ? userData.forgeArrayAlbedo : material.map;
  const mossPacked = !userData.forgeBlendMossFallback && isArrayTexture(userData.forgeArrayMoss);
  const mossMap = mossPacked ? userData.forgeArrayMoss : userData.blendMap;
  if (!blend || !mossMap || !baseMap || userData.blendNode) return material;
  const arrayBlend = isArrayTexture(baseMap) || isArrayTexture(mossMap)
    || isArrayTexture(userData.forgeArrayRough) || isArrayTexture(userData.forgeArrayOrm1)
    || isArrayTexture(userData.forgeArrayNormal) || isArrayTexture(userData.forgeArrayNormal1);
  const scale = arrayBlend ? null : reference("value", "vec2", userData.blendScaleUniform);
  const translate = arrayBlend ? null : reference("value", "vec2", userData.blendTranslateUniform);
  const debugHolder = userData.blendDebugUniform && Object.prototype.hasOwnProperty.call(userData.blendDebugUniform, "value")
    ? userData.blendDebugUniform
    : { value: 0 };
  const debug = reference("value", "float", debugHolder);
  const channel = Number(userData.blendChannelUniform && userData.blendChannelUniform.value) || 0;
  const useUv1 = userData.blendUv1Uniform && userData.blendUv1Uniform.value > 0.5;
  const roughMap = isArrayTexture(userData.forgeArrayRough) ? userData.forgeArrayRough : material.roughnessMap;
  const normal0 = isArrayTexture(userData.forgeArrayNormal) ? userData.forgeArrayNormal : material.normalMap;
  // A packed base map stands in for an overlay that is the same image. The
  // stock slot has already been cleared, so identity against `material.normalMap`
  // is not available here. `forgeDistinct*` is set when the overlay image differs.
  function overlayMap(packed, distinct, sourceMap, baseMap) {
    if (isArrayTexture(packed)) return packed;
    if (distinct) return sourceMap || baseMap;
    if (isArrayTexture(baseMap)) return baseMap;
    return sourceMap || baseMap;
  }
  const normal1 = overlayMap(userData.forgeArrayNormal1, userData.forgeDistinctNormal1, userData.normal1Map, normal0);
  const orm1Map = overlayMap(userData.forgeArrayOrm1, userData.forgeDistinctOrm1, userData.orm1Map, roughMap);
  const aoOwn = Boolean(userData.forgeDistinctAo);
  const aoMap = aoOwn
    ? (isArrayTexture(userData.forgeArrayAo) ? userData.forgeArrayAo : material.aoMap)
    : (isArrayTexture(userData.forgeArrayAo) ? roughMap : material.aoMap);

  function layerTexel(row) {
    const layers = material.userData && material.userData.forgeLayerTexture;
    if (!layers) return null;
    return textureLoad(layers, ivec2(int(batchIndirectIndex), int(row)));
  }

  function componentOf(texel, index) {
    if (index === 1) return texel.g;
    if (index === 2) return texel.b;
    if (index === 3) return texel.a;
    return texel.r;
  }

  function sampleRole(map, uvNode, index, row) {
    if (!map || !uvNode) return null;
    if (!isArrayTexture(map)) return texture(map, uvNode);
    const texel = layerTexel(row);
    const depth = texel ? int(componentOf(texel, index)) : int(0);
    return texture(map, uvNode).depth(depth);
  }

  function maskWeight() {
    const mask = attribute("blendMask", "vec4");
    if (!arrayBlend) {
      const component = channel === 1 ? mask.g : channel === 2 ? mask.b : channel === 3 ? mask.a : mask.r;
      return smoothstep(float(0.35), float(0.65), component);
    }
    const texel = layerTexel(BLEND_ROW.albedo);
    const c0 = texel ? componentOf(texel, BLEND_LAYER_MAP.c0) : float(channel);
    const low = mix(mask.r, mask.g, step(float(0.5), c0));
    const high = mix(mask.b, mask.a, step(float(2.5), c0));
    const component = mix(low, high, step(float(1.5), c0));
    return smoothstep(float(0.35), float(0.65), component);
  }

  function geometryHas(builder, name) {
    const geometry = builder && builder.geometry;
    if (geometry && typeof geometry.hasAttribute === "function") return geometry.hasAttribute(name);
    return Boolean(geometry && geometry.attributes && geometry.attributes[name]);
  }

  function texCoord(builder, textureNode) {
    const uvChannel = textureNode && Number.isInteger(textureNode.channel) ? textureNode.channel : 0;
    const name = uvChannel > 0 ? `uv${uvChannel}` : "uv";
    if (!geometryHas(builder, name)) return null;
    return uvChannel > 0 ? uv(uvChannel) : uv();
  }

  function aoCoord(builder, textureNode) {
    const primary = texCoord(builder, textureNode);
    if (primary) return primary;
    if (geometryHas(builder, "uv1")) return uv(1);
    return null;
  }

  function mossUv(builder) {
    const base = useUv1 ? attribute("blendUv", "vec2") : texCoord(builder, baseMap);
    if (!base) return null;
    if (!arrayBlend) return base.mul(scale).add(translate);
    const srt = layerTexel(BLEND_ROW.place);
    if (!srt) return base;
    return base.mul(srt.rg).add(srt.ba);
  }

  material.colorNode = Fn((builder) => {
    const baseUv = texCoord(builder, baseMap);
    const mossAt = mossUv(builder);
    if (!baseUv || !mossAt) return vec4(materialReference("color", "color", material), 1);
    const base = sampleRole(baseMap, baseUv, BLEND_LAYER_MAP.a0, BLEND_ROW.albedo);
    const moss = sampleRole(mossMap, mossAt, BLEND_LAYER_MAP.a1, BLEND_ROW.albedo);
    const weight = maskWeight();
    const tint = blend.skinColor || [1, 1, 1];
    const skinColor = vec3(Number(tint[0] ?? 1), Number(tint[1] ?? 1), Number(tint[2] ?? 1));
    const low = blend.skin
      ? (blend.skinVertex ? attribute("blendMask", "vec4").rgb.mul(skinColor) : skinColor)
      : base.rgb;
    const mixed = mix(low, moss.rgb, weight);
    const shown = mix(mixed, vec3(weight), step(float(0.5), debug));
    return vec4(shown, base.a);
  })();

  if (roughMap || orm1Map) {
    const orm1Own = Boolean(userData.forgeDistinctOrm1) || Boolean(orm1Map && roughMap && orm1Map !== roughMap);
    material.roughnessNode = Fn((builder) => {
      const at = texCoord(builder, roughMap);
      const mossAt = mossUv(builder);
      const roughSample = roughMap && at ? sampleRole(roughMap, at, BLEND_LAYER_MAP.orm, BLEND_ROW.albedo) : null;
      const rough0 = roughSample ? roughSample.g : float(material.roughness);
      const over = orm1Map && mossAt
        ? sampleRole(orm1Map, mossAt, orm1Own ? BLEND_SURFACE_MAP.orm1 : BLEND_LAYER_MAP.orm, orm1Own ? BLEND_ROW.surface : BLEND_ROW.albedo)
        : null;
      const rough1 = over ? over.g : rough0;
      return mix(rough0, rough1, maskWeight());
    })();
    material.metalnessNode = Fn((builder) => {
      const at = texCoord(builder, roughMap);
      const mossAt = mossUv(builder);
      const roughSample = roughMap && at ? sampleRole(roughMap, at, BLEND_LAYER_MAP.orm, BLEND_ROW.albedo) : null;
      const metal0 = roughSample ? roughSample.b : float(material.metalness);
      const over = orm1Map && mossAt
        ? sampleRole(orm1Map, mossAt, orm1Own ? BLEND_SURFACE_MAP.orm1 : BLEND_LAYER_MAP.orm, orm1Own ? BLEND_ROW.surface : BLEND_ROW.albedo)
        : null;
      const metal1 = over ? over.b : metal0;
      return mix(metal0, metal1, maskWeight());
    })();
    material.aoNode = Fn((builder) => {
      const at = aoCoord(builder, aoMap || roughMap);
      const mossAt = mossUv(builder);
      const aoSample = aoMap && at
        ? sampleRole(aoMap, at, aoOwn ? BLEND_SURFACE_MAP.ao : BLEND_LAYER_MAP.orm, aoOwn ? BLEND_ROW.surface : BLEND_ROW.albedo)
        : null;
      const ao0 = aoSample ? aoSample.r : float(1);
      const over = orm1Map && mossAt
        ? sampleRole(orm1Map, mossAt, orm1Own ? BLEND_SURFACE_MAP.orm1 : BLEND_LAYER_MAP.orm, orm1Own ? BLEND_ROW.surface : BLEND_ROW.albedo)
        : null;
      const ao1 = over ? over.r : ao0;
      return mix(ao0, ao1, maskWeight());
    })();
  }

  if (normal0 || normal1) {
    const normal1Own = Boolean(userData.forgeDistinctNormal1) || Boolean(normal1 && normal0 && normal1 !== normal0);
    material.normalNode = Fn((builder) => {
      const at = texCoord(builder, normal0 || normal1);
      const mossAt = mossUv(builder);
      if (!at) return vec3(0, 0, 1);
      const baseSample = normal0
        ? sampleRole(normal0, at, BLEND_SURFACE_MAP.normal, BLEND_ROW.surface)
        : sampleRole(normal1, at, normal1Own ? BLEND_SURFACE_MAP.normal1 : BLEND_SURFACE_MAP.normal, BLEND_ROW.surface);
      const baseN = normalMap(baseSample);
      const overSample = normal1 && mossAt
        ? sampleRole(normal1, mossAt, normal1Own ? BLEND_SURFACE_MAP.normal1 : BLEND_SURFACE_MAP.normal, BLEND_ROW.surface)
        : null;
      const overN = overSample ? normalMap(overSample) : baseN;
      return mix(baseN, overN, maskWeight()).normalize();
    })();
  }

  material.userData.blendNode = true;
  return material;
}
