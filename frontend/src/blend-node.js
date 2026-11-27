/**
 * TSL port of the moss blend. WebGPU ignores onBeforeCompile, so the node
 * graph mixes the overlay over the base map with the same soft threshold.
 * Roughness, metal, AO, and the overlay normal use that same weight.
 * WebGL keeps the GLSL path and writes the same uniform objects.
 */
import { Fn, attribute, float, materialReference, mix, smoothstep, step, texture, uv, vec3, vec4, reference, normalMap } from "three/tsl";

export function attachBlend(material) {
  const blend = material.userData && material.userData.blend;
  const mossMap = material.userData && material.userData.blendMap;
  if (!blend || !mossMap || !material.map || material.userData.blendNode) return material;
  const scale = reference("value", "vec2", material.userData.blendScaleUniform);
  const translate = reference("value", "vec2", material.userData.blendTranslateUniform);
  const debug = reference("value", "float", material.userData.blendDebugUniform);
  const channel = Number(material.userData.blendChannelUniform && material.userData.blendChannelUniform.value) || 0;
  const useUv1 = material.userData.blendUv1Uniform && material.userData.blendUv1Uniform.value > 0.5;
  const map = material.map;

  function maskWeight() {
    const mask = attribute("blendMask", "vec4");
    const component = channel === 1 ? mask.g : channel === 2 ? mask.b : channel === 3 ? mask.a : mask.r;
    return smoothstep(float(0.35), float(0.65), component);
  }

  function geometryHas(builder, name) {
    const geometry = builder && builder.geometry;
    if (geometry && typeof geometry.hasAttribute === "function") return geometry.hasAttribute(name);
    return Boolean(geometry && geometry.attributes && geometry.attributes[name]);
  }

  function texCoord(builder, texture) {
    const channel = texture && Number.isInteger(texture.channel) ? texture.channel : 0;
    const name = channel > 0 ? `uv${channel}` : "uv";
    if (!geometryHas(builder, name)) return null;
    return channel > 0 ? uv(channel) : uv();
  }

  function aoCoord(builder, texture) {
    const primary = texCoord(builder, texture);
    if (primary) return primary;
    if (geometryHas(builder, "uv1")) return uv(1);
    return null;
  }

  function mossUv(builder) {
    const base = useUv1 ? attribute("blendUv", "vec2") : texCoord(builder, map);
    if (!base) return null;
    return base.mul(scale).add(translate);
  }

  material.colorNode = Fn((builder) => {
    const baseUv = texCoord(builder, map);
    const mossAt = mossUv(builder);
    if (!baseUv || !mossAt) return vec4(materialReference("color", "color", material), 1);
    const base = texture(map, baseUv);
    const moss = texture(mossMap, mossAt);
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

  const orm1Map = material.userData.orm1Map || material.roughnessMap;
  if (material.roughnessMap || orm1Map) {
    material.roughnessNode = Fn((builder) => {
      const at = texCoord(builder, material.roughnessMap);
      const mossAt = mossUv(builder);
      const rough0 = material.roughnessMap && at ? texture(material.roughnessMap, at).g : float(material.roughness);
      const rough1 = orm1Map && mossAt ? texture(orm1Map, mossAt).g : rough0;
      return mix(rough0, rough1, maskWeight());
    })();
    material.metalnessNode = Fn((builder) => {
      const at = texCoord(builder, material.roughnessMap);
      const mossAt = mossUv(builder);
      const metal0 = material.roughnessMap && at ? texture(material.roughnessMap, at).b : float(material.metalness);
      const metal1 = orm1Map && mossAt ? texture(orm1Map, mossAt).b : metal0;
      return mix(metal0, metal1, maskWeight());
    })();
    material.aoNode = Fn((builder) => {
      const at = aoCoord(builder, material.aoMap);
      const mossAt = mossUv(builder);
      const ao0 = material.aoMap && at ? texture(material.aoMap, at).r : float(1);
      const ao1 = orm1Map && mossAt ? texture(orm1Map, mossAt).r : ao0;
      return mix(ao0, ao1, maskWeight());
    })();
  }

  const normal1 = material.userData.normal1Map || material.normalMap;
  if (material.normalMap || normal1) {
    material.normalNode = Fn((builder) => {
      const at = texCoord(builder, material.normalMap || normal1);
      const mossAt = mossUv(builder);
      if (!at) return vec3(0, 0, 1);
      const baseN = material.normalMap ? normalMap(texture(material.normalMap, at)) : normalMap(texture(normal1, at));
      const overN = normal1 && mossAt ? normalMap(texture(normal1, mossAt)) : baseN;
      return mix(baseN, overN, maskWeight()).normalize();
    })();
  }

  material.userData.blendNode = true;
  return material;
}
