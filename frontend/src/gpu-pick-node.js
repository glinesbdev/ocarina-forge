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
import { Node, NodeMaterial } from "three/webgpu";
import {
  Fn,
  Node as TslNode,
  attribute,
  cameraPosition,
  drawIndex,
  float,
  glslFn,
  instanceIndex,
  int,
  ivec2,
  materialReference,
  positionWorld,
  reference,
  texture,
  textureSize,
  uint,
  wgslFn,
} from "three/tsl";
import { pickMask, resolvePickTexture } from "./gpu-pick.js";

const SPLIT_NODE_SYSTEM = "WebGPU loaded two copies of three's node system (three/webgpu and three/tsl). GPU picking is off; clicks use the CPU raycast.";

// One Node class means one id counter. A second copy restarts that counter, and
// the builder then treats our nodes as the camera uniforms that share those ids.
// This must not throw: a throw during the dynamic import takes down the editor.
export function rejectSplitNodeSystem(webgpuNode, tslNode) {
  if (webgpuNode === tslNode) return false;
  console.error(SPLIT_NODE_SYSTEM);
  return true;
}

export const nodeSystemsDiffer = rejectSplitNodeSystem(Node, TslNode);

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
  if (nodeSystemsDiffer) return null;
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
    const feature = geometry && (
      (typeof geometry.hasAttribute === "function" && geometry.hasAttribute("_feature_id_0"))
      || (geometry.attributes && geometry.attributes._feature_id_0)
    );
    if (feature) {
      id = attribute("_feature_id_0", "float");
    } else if (object && object.isBatchedMesh && pickTex && indirect) {
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
    return selecting.select(pack24(id.add(0.5), builder), pack24(t.mul(16777215).add(0.5), builder));
  })();
  material.customProgramCacheKey = () => {
    const object = getActiveObject ? getActiveObject() : null;
    const mask = pickMask(material);
    const cut = mask.map ? `cut-${mask.channel}` : "solid";
    const tex = object?.userData?.forgePickTexture?.uuid || "none";
    const featureAttr = object?.geometry && (
      object.geometry.getAttribute?.("_feature_id_0") || object.geometry.attributes?._feature_id_0
    );
    if (featureAttr) return `forge-pick-feature-${cut}`;
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

// 24-bit little-endian unpack, as shader source rather than a TSL expression.
// A Field render of the TSL form (shifts, then unsigned division) still wrote
// the camera projection and view uniforms in place of two children of this
// vec4. Those names are emitted only by the camera uniform nodes. The built
// dist has one Node class, and a headless build of the same graph does not
// swap them, so the substitution happens inside the browser's node build of
// those children. A function body is text: the builder cannot
// retarget an operand inside it. The WebGL backend of the node test parses GLSL.
const PACK24_WGSL = `
fn pack24(value: u32) -> vec4<f32> {
	let r = f32(value & 255u) / 255.0;
	let g = f32((value / 256u) & 255u) / 255.0;
	let b = f32((value / 65536u) & 255u) / 255.0;
	return vec4<f32>(r, g, b, 1.0);
}
`;
const PACK24_GLSL = `
vec4 pack24(uint value) {
	float r = float(value & 255u) / 255.0;
	float g = float((value / 256u) & 255u) / 255.0;
	float b = float((value / 65536u) & 255u) / 255.0;
	return vec4(r, g, b, 1.0);
}
`;
const pack24Wgsl = wgslFn(PACK24_WGSL);
const pack24Glsl = glslFn(PACK24_GLSL);

function pack24(n, builder) {
  const value = uint(n);
  // WebGLBackend leaves this unset. Only an explicit WebGPU backend gets WGSL.
  const webgpu = builder?.renderer?.backend?.isWebGPUBackend === true || builder?.renderer?.backend == null;
  return (webgpu ? pack24Wgsl : pack24Glsl)({ value });
}
