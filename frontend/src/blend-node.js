/**
 * TSL port of the moss blend. WebGPU ignores onBeforeCompile, so the node
 * graph mixes the overlay over the base map with the same soft threshold.
 * Roughness, metal, AO, and the overlay normal use that same weight.
 * WebGL keeps the GLSL path and writes the same uniform objects.
 */
import { Fn, attribute, float, mix, smoothstep, step, texture, uv, vec3, vec4, reference, normalMap } from "three/tsl";

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

  function mossUv() {
    const base = useUv1 ? attribute("blendUv", "vec2") : uv();
    return base.mul(scale).add(translate);
  }

  material.colorNode = Fn(() => {
    const baseUv = uv();
    const base = texture(map, baseUv);
    const moss = texture(mossMap, mossUv());
    const weight = maskWeight();
    const mixed = mix(base.rgb, moss.rgb, weight);
    const shown = mix(mixed, vec3(weight), step(float(0.5), debug));
    return vec4(shown, base.a);
  })();

  const orm1Map = material.userData.orm1Map || material.roughnessMap;
  if (material.roughnessMap || orm1Map) {
    material.roughnessNode = Fn(() => {
      const rough0 = material.roughnessMap ? texture(material.roughnessMap, uv()).g : float(material.roughness);
      const rough1 = orm1Map ? texture(orm1Map, mossUv()).g : rough0;
      return mix(rough0, rough1, maskWeight());
    })();
    material.metalnessNode = Fn(() => {
      const metal0 = material.roughnessMap ? texture(material.roughnessMap, uv()).b : float(material.metalness);
      const metal1 = orm1Map ? texture(orm1Map, mossUv()).b : metal0;
      return mix(metal0, metal1, maskWeight());
    })();
    material.aoNode = Fn(() => {
      const ao0 = material.aoMap ? texture(material.aoMap, uv()).r : float(1);
      const ao1 = orm1Map ? texture(orm1Map, mossUv()).r : ao0;
      return mix(ao0, ao1, maskWeight());
    })();
  }

  const normal1 = material.userData.normal1Map || material.normalMap;
  if (material.normalMap || normal1) {
    material.normalNode = Fn(() => {
      const baseN = material.normalMap ? normalMap(texture(material.normalMap, uv())) : normalMap(texture(normal1, uv()));
      const overN = normal1 ? normalMap(texture(normal1, mossUv())) : baseN;
      return mix(baseN, overN, maskWeight()).normalize();
    })();
  }

  material.userData.blendNode = true;
  return material;
}
