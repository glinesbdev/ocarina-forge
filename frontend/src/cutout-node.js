/**
 * Masked materials on the node renderer. MeshStandardNodeMaterial multiplies
 * the vec3 colour by the map, and that product's alpha is 0, so the card is
 * either discarded entirely or, once alpha is forced back to 1, painted as a
 * solid sheet. The colour node keeps the texel alpha. The alpha test then
 * discards the cutout. WebGL never imports this file and keeps alphaToCoverage.
 */
import { Fn, float, materialReference, texture, uv, vec4 } from "three/tsl";

const reported = new Set();

export function attachCutout(material) {
  if (!material || material.colorNode || !(material.alphaTest > 0) || !material.map) return material;
  const tag = material.userData && material.userData.banc;
  if (tag === "terrain" || material.userData.terrainNode || material.userData.terrainShader) return material;
  const map = material.map;
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
    const sampled = texture(map, uv());
    sampled.a.lessThanEqual(float(cutoff)).discard();
    return vec4(sampled.rgb.mul(tint), sampled.a);
  })();
  material.userData.cutoutNode = true;
  return material;
}
