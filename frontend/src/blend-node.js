/**
 * TSL port of the moss blend. WebGPU ignores onBeforeCompile, so the node
 * graph mixes the overlay over the base map with the same soft threshold.
 * WebGL keeps the GLSL path and writes the same uniform objects.
 */
import { Fn, attribute, float, mix, smoothstep, step, texture, uv, vec3, vec4, reference } from "three/tsl";

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
  material.colorNode = Fn(() => {
    const baseUv = uv();
    const mossUv = (useUv1 ? attribute("blendUv", "vec2") : baseUv).mul(scale).add(translate);
    const base = texture(map, baseUv);
    const moss = texture(mossMap, mossUv);
    const mask = attribute("blendMask", "vec4");
    const component = channel === 1 ? mask.g : channel === 2 ? mask.b : channel === 3 ? mask.a : mask.r;
    const weight = smoothstep(float(0.35), float(0.65), component);
    const mixed = mix(base.rgb, moss.rgb, weight);
    const shown = mix(mixed, vec3(weight), step(float(0.5), debug));
    return vec4(shown, base.a);
  })();
  material.userData.blendNode = true;
  return material;
}
