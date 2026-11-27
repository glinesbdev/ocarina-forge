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
  if (!material || material.colorNode || !(material.alphaTest > 0) || !material.map || material.map.isTexture !== true) return material;
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

/**
 * Opaque maps on the node renderer. `MaterialNode.COLOR` decides whether to
 * sample `material.map` when the program is first built. A geometry glb is
 * compiled with `map` null, and that program's cache key does not follow a
 * map assigned later, so the fragment stays the flat material colour.
 * This colour node samples the map that is on the material now. The map stays
 * on the material so the profile count and the bind group see the same image.
 * WebGL never imports this file.
 */
export function attachAlbedo(material) {
  if (!material || material.colorNode || !material.map || material.map.isTexture !== true) return material;
  const userData = material.userData;
  // A blend material already names two layers. Sampling material.map here
  // replaces that mix with whichever texture landed in the albedo slot,
  // which is how a mask or a single layer covers the ground.
  if (userData && (userData.terrainNode || userData.cutoutNode || userData.blend || userData.blendNode || userData.terrainShader || userData.banc === "terrain")) {
    return material;
  }
  const map = material.map;
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
    const sampled = texture(map, uv(channel));
    return vec4(sampled.rgb.mul(tint), opaque ? float(1) : sampled.a);
  })();
  material.userData = userData || {};
  material.userData.albedoNode = true;
  material.needsUpdate = true;
  return material;
}
