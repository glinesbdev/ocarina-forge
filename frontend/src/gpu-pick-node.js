/**
 * WebGPU id-buffer material. The WebGL graph never imports this file.
 * The fragment writes the same 24-bit little-endian packing as gpu-pick.js.
 * Alpha-tested meshes discard before the id is written so foliage holes miss.
 *
 * texture() and textureLoad() require a THREE.Texture. A ReferenceNode, a
 * render target, or a missing map is not one, and TextureNode throws
 * "Need a three.js texture" while the shader is built. Masks are resolved to
 * a real texture first. The peel texture is a TextureNode whose value follows
 * the uniform, because wrapping the uniform in reference(..., "texture") and
 * then textureLoad() stores the reference itself as the texture.
 */
import { NodeMaterial } from "three/webgpu";
import {
  Fn,
  cameraPosition,
  drawIndex,
  float,
  instanceIndex,
  int,
  ivec2,
  materialReference,
  positionWorld,
  reference,
  texture,
  textureSize,
  vec4,
} from "three/tsl";
import { pickMask, resolvePickTexture } from "./gpu-pick.js";

function texelNode(tex) {
  const node = texture(tex).setSampler(false).setUpdateMatrix(false);
  return node;
}

function sampleR(tex, index) {
  if (!tex || (tex.isTexture !== true && tex.isTextureNode !== true)) return float(0);
  const base = tex.isTextureNode === true ? tex : texelNode(tex);
  const size = textureSize(base).x;
  const j = int(index);
  const x = j.mod(int(size));
  const y = j.div(int(size));
  const sampled = base.sample(ivec2(x, y));
  sampled.setSampler(false);
  sampled.setUpdateMatrix(false);
  return sampled.x;
}

function watchedTexel(getTexture) {
  const node = texelNode(getTexture());
  node.onObjectUpdate(() => {
    const next = getTexture();
    if (next && next.isTexture === true) node.value = next;
  });
  return node;
}

function maskNode(material, hasUv) {
  const mask = pickMask(material);
  if (!mask.map || !hasUv) return;
  material.userData.forgePickMask = mask.map;
  const sampled = materialReference("userData.forgePickMask", "texture", material);
  const texel = mask.channel === "g" ? sampled.g : sampled.a;
  texel.lessThanEqual(materialReference("alphaTest", "float", material)).discard();
}

export function createNodePickMaterial({ uniforms, getActiveObject }) {
  const excludeNode = watchedTexel(() => resolvePickTexture(uniforms.forgeExclude.value));
  const material = new NodeMaterial();
  material.fragmentNode = Fn((builder) => {
    const object = builder.object;
    const geometry = builder.geometry;
    const hasUv = Boolean(geometry && typeof geometry.hasAttribute === "function" && geometry.hasAttribute("uv"));
    maskNode(material, hasUv);
    let id;
    const pickTex = resolvePickTexture(object?.userData?.forgePickTexture);
    const indirect = resolvePickTexture(object?.userData?.forgeIndirect) || resolvePickTexture(object?._indirectTexture);
    if (object && object.isBatchedMesh && pickTex && indirect) {
      const drawId = builder.getDrawIndex() === null ? instanceIndex : drawIndex;
      id = sampleR(pickTex, sampleR(indirect, drawId));
    } else if (object && object.isInstancedMesh && pickTex) {
      id = sampleR(pickTex, instanceIndex);
    } else {
      id = reference("userData.forgePickId", "float");
    }
    const output = reference("value", "float", uniforms.forgeOutput);
    const peel = reference("value", "float", uniforms.forgePeel);
    const far = reference("value", "float", uniforms.forgeFar);
    const depthOnly = reference("userData.forgePickDepthOnly", "float");
    const exclude = sampleR(excludeNode, id);
    const selecting = output.lessThan(float(0.5));
    selecting.and(peel.greaterThan(float(0.5))).and(id.greaterThan(float(0.5))).and(exclude.greaterThan(float(0.5))).discard();
    selecting.and(id.lessThanEqual(float(0.5))).and(depthOnly.lessThan(float(0.5))).discard();
    const distance = positionWorld.sub(cameraPosition).length();
    const t = distance.div(far.max(float(0.001))).clamp(0, 1);
    return selecting.select(pack24(id.add(0.5)), pack24(t.mul(16777215).add(0.5)));
  })();
  material.customProgramCacheKey = () => {
    const object = getActiveObject ? getActiveObject() : null;
    const mask = pickMask(material);
    const cut = mask.map ? `cut-${mask.channel}` : "solid";
    const tex = object?.userData?.forgePickTexture?.uuid || "none";
    if (object?.isInstancedMesh) return `forge-pick-inst-${object.uuid}-${cut}-${tex}`;
    if (object?.isBatchedMesh) return `forge-pick-batch-${cut}-${tex}`;
    return `forge-pick-mesh-${cut}`;
  };
  material.fog = false;
  material.toneMapped = false;
  material.transparent = false;
  material.depthTest = true;
  material.depthWrite = true;
  material.forceSinglePass = true;
  material.userData.forgePick = true;
  return material;
}

function pack24(n) {
  const value = int(n);
  const r = float(value.bitAnd(int(255))).div(255);
  const g = float(value.shiftRight(int(8)).bitAnd(int(255))).div(255);
  const b = float(value.shiftRight(int(16)).bitAnd(int(255))).div(255);
  return vec4(r, g, b, float(1));
}
