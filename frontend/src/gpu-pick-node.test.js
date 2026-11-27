import assert from "node:assert/strict";
import test from "node:test";

test("the WebGPU pick material builds for meshes with and without maps", async () => {
  globalThis.self = globalThis;
  const THREE = await import("three/webgpu");
  const { createNodePickMaterial, nodeSystemsDiffer, rejectSplitNodeSystem } = await import("./gpu-pick-node.js");
  assert.equal(nodeSystemsDiffer, false);
  const reported = [];
  const previousError = console.error;
  console.error = (...args) => {
    reported.push(args.join(" "));
  };
  try {
    const split = function SplitNode() {};
    const other = function OtherNode() {};
    assert.equal(rejectSplitNodeSystem(split, other), true);
    assert.equal(rejectSplitNodeSystem(split, split), false);
  } finally {
    console.error = previousError;
  }
  assert.equal(reported.length, 1);
  assert.match(reported[0], /two copies of three's node system/);
  assert.match(reported[0], /CPU raycast/);
  const canvas = {
    width: 4,
    height: 4,
    style: {},
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getContext() { return null; },
  };
  const zero = new THREE.DataTexture(new Float32Array([0, 0, 0, 0]), 1, 1, THREE.RGBAFormat, THREE.FloatType);
  const uniforms = {
    forgeExclude: { value: zero },
    forgeOutput: { value: 0 },
    forgePeel: { value: 0 },
    forgeFar: { value: 1000 },
  };
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]), 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1]), 2));
  geometry.setIndex([0, 1, 2]);

  function shaderFor(renderer, object) {
    renderer.hasFeature = () => false;
    // init() is what installs Bindings. A shader build before that sees null,
    // and r187 still calls getSharedBindGroup when the field is not undefined.
    if (renderer._bindings == null) {
      renderer._bindings = { getSharedBindGroup(group) { return group; } };
    }
    const builder = renderer.backend.createNodeBuilder(object, renderer);
    builder.camera = new THREE.PerspectiveCamera();
    builder.build();
    return builder.fragmentShader || "";
  }

  const webgpu = new THREE.WebGPURenderer({ canvas, antialias: false });
  const webglBackend = new THREE.WebGPURenderer({ canvas, antialias: false, forceWebGL: true });

  const plain = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  const plainShader = shaderFor(webgpu, new THREE.Mesh(geometry, plain));
  assert.match(plainShader, /textureLoad/);
  assert.doesNotMatch(plainShader, /Need a three\.js texture/);
  // The unpack is a native function. Its body is the source string, so a node
  // build cannot replace an operand with render.cameraProjectionMatrix.
  const packBody = plainShader.slice(plainShader.indexOf("fn pack24"), plainShader.indexOf("@fragment"));
  assert.match(packBody, /fn pack24\s*\(\s*value\s*:\s*u32\s*\)\s*->\s*vec4<f32>/);
  assert.match(packBody, /f32\(value & 255u\) \/ 255\.0/);
  assert.match(packBody, /f32\(\(value \/ 256u\) & 255u\) \/ 255\.0/);
  assert.match(packBody, /f32\(\(value \/ 65536u\) & 255u\) \/ 255\.0/);
  assert.doesNotMatch(packBody, /camera|render\./);
  assert.match(plainShader, /pack24\s*\(\s*u32\s*\(/);
  assert.doesNotMatch(plainShader, /pack24\s*\(\s*render\./);
  assert.doesNotMatch(plainShader, /camera(?:Projection|View)Matrix\s*\//);
  assert.doesNotMatch(plainShader, /\/\s*render\.camera(?:Projection|View)Matrix/);

  const mapped = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  mapped.alphaTest = 0.4;
  mapped.map = new THREE.DataTexture(new Uint8Array([255, 0, 0, 128]), 1, 1);
  const mappedShader = shaderFor(webgpu, new THREE.Mesh(geometry, mapped));
  assert.match(mappedShader, /discard/);
  assert.match(mappedShader, /textureSample|textureLoad/);

  const alphaOnly = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  alphaOnly.alphaTest = 0.4;
  alphaOnly.alphaMap = new THREE.DataTexture(new Uint8Array([0, 255, 0, 255]), 1, 1);
  const alphaShader = shaderFor(webgpu, new THREE.Mesh(geometry, alphaOnly));
  assert.match(alphaShader, /discard/);

  const cutoffOnly = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  cutoffOnly.alphaTest = 0.4;
  const cutoffShader = shaderFor(webgpu, new THREE.Mesh(geometry, cutoffOnly));
  assert.match(cutoffShader, /textureLoad/);

  const placeholder = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  placeholder.alphaTest = 0.4;
  placeholder.map = { url: "/api/model-textures/not-a-texture" };
  const placeholderShader = shaderFor(webgpu, new THREE.Mesh(geometry, placeholder));
  assert.match(placeholderShader, /textureLoad/);
  assert.equal(placeholder.userData.forgePickMask, undefined);

  const target = new THREE.WebGLRenderTarget(2, 2);
  const fromTarget = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  fromTarget.alphaTest = 0.4;
  fromTarget.map = target;
  const targetShader = shaderFor(webgpu, new THREE.Mesh(geometry, fromTarget));
  assert.match(targetShader, /discard/);
  assert.equal(fromTarget.userData.forgePickMask, target.texture);

  const ids = new THREE.DataTexture(new Float32Array(16), 2, 2, THREE.RGBAFormat, THREE.FloatType);
  const batchMaterial = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  const batch = new THREE.BatchedMesh(4, 8, 8, batchMaterial);
  batch.addGeometry(geometry);
  batch.userData.forgePickTexture = ids;
  const batchShader = shaderFor(webgpu, batch);
  assert.match(batchShader, /textureLoad/);

  const instancedMaterial = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  const instanced = new THREE.InstancedMesh(geometry, instancedMaterial, 2);
  instanced.userData.forgePickTexture = ids;
  const instancedShader = shaderFor(webgpu, instanced);
  assert.match(instancedShader, /textureLoad/);

  const fallback = createNodePickMaterial({ uniforms, getActiveObject: () => null });
  fallback.alphaTest = 0.4;
  fallback.map = mapped.map;
  const glShader = shaderFor(webglBackend, new THREE.Mesh(geometry, fallback));
  assert.match(glShader, /discard/);
  assert.match(glShader, /texture/);
  assert.match(glShader, /vec4 pack24\s*\(\s*uint value\s*\)/);
  assert.match(glShader, /float\(\(value \/ 256u\) & 255u\)/);
  assert.doesNotMatch(glShader, /pack24\s*\(\s*render\./);
});
