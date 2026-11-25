/**
 * WebGPU id-buffer material. The WebGL graph never imports this file.
 * The fragment writes the same 24-bit little-endian packing as gpu-pick.js.
 * Alpha-tested meshes discard before the id is written so foliage holes miss.
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
  positionWorld,
  reference,
  texture,
  textureLoad,
  textureSize,
  uv,
  vec4,
} from "three/tsl";

function sampleR(tex, index) {
  const size = textureSize(textureLoad(tex), 0).x;
  const j = int(index);
  const x = j.modInt(int(size));
  const y = j.div(int(size));
  return textureLoad(tex, ivec2(x, y)).x;
}

function pack24(n) {
  const value = int(n);
  const r = float(value.bitAnd(int(255))).div(255);
  const g = float(value.shiftRight(int(8)).bitAnd(int(255))).div(255);
  const b = float(value.shiftRight(int(16)).bitAnd(int(255))).div(255);
  return vec4(r, g, b, float(1));
}

export function createNodePickMaterial({ uniforms, getActiveObject }) {
  const material = new NodeMaterial();
  material.fragmentNode = Fn((builder) => {
    const object = builder.object;
    const cutoff = Number(material.alphaTest) || 0;
    const map = material.map;
    const geometry = builder.geometry;
    const hasUv = Boolean(geometry && typeof geometry.hasAttribute === "function" && geometry.hasAttribute("uv"));
    if (cutoff > 0 && map && hasUv) {
      const sampled = texture(map, uv());
      sampled.a.lessThanEqual(float(cutoff)).discard();
    }
    let id;
    if (object && object.isBatchedMesh) {
      const indirect = object.userData.forgeIndirect || object._indirectTexture;
      const drawId = builder.getDrawIndex() === null ? instanceIndex : drawIndex;
      id = sampleR(object.userData.forgePickTexture, sampleR(indirect, drawId));
    } else if (object && object.isInstancedMesh) {
      id = sampleR(object.userData.forgePickTexture, instanceIndex);
    } else {
      id = reference("userData.forgePickId", "float");
    }
    const output = reference("value", "float", uniforms.forgeOutput);
    const peel = reference("value", "float", uniforms.forgePeel);
    const far = reference("value", "float", uniforms.forgeFar);
    const depthOnly = reference("userData.forgePickDepthOnly", "float");
    const exclude = sampleR(reference("value", "texture", uniforms.forgeExclude), id);
    const selecting = output.lessThan(float(0.5));
    selecting.and(peel.greaterThan(float(0.5))).and(id.greaterThan(float(0.5))).and(exclude.greaterThan(float(0.5))).discard();
    selecting.and(id.lessThanEqual(float(0.5))).and(depthOnly.lessThan(float(0.5))).discard();
    const distance = positionWorld.sub(cameraPosition).length();
    const t = distance.div(far.max(float(0.001))).clamp(0, 1);
    return selecting.select(pack24(id.add(0.5)), pack24(t.mul(16777215).add(0.5)));
  })();
  material.customProgramCacheKey = () => {
    const object = getActiveObject ? getActiveObject() : null;
    const cut = material.alphaTest > 0 && material.map ? "cut" : "solid";
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
