import assert from "node:assert/strict";
import test from "node:test";

function splitTop(input) {
  const args = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < input.length; i += 1) {
    const ch = input[i];
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    else if (ch === "," && depth === 0) {
      args.push(input.slice(start, i).trim());
      start = i + 1;
    }
  }
  const tail = input.slice(start).trim();
  if (tail) args.push(tail);
  return args;
}

function arraySamples(wgsl) {
  const names = new Set();
  for (const match of wgsl.matchAll(/var\s+(\w+)\s*:\s*texture_2d_array/g)) names.add(match[1]);
  const calls = [];
  for (const match of wgsl.matchAll(/texture(?:Sample(?:Level|Bias|Compare|Grad|BaseClamp)?|Load)\s*\(/g)) {
    const open = match.index + match[0].length;
    let depth = 1;
    let i = open;
    for (; i < wgsl.length && depth > 0; i += 1) {
      if (wgsl[i] === "(") depth += 1;
      else if (wgsl[i] === ")") depth -= 1;
    }
    const args = splitTop(wgsl.slice(open, i - 1));
    const first = args[0] || "";
    if (![...names].some((name) => first.split(/[^A-Za-z0-9_]/).includes(name))) continue;
    const kind = match[0].startsWith("textureLoad") ? "load" : "sample";
    calls.push({ argc: args.length, first, kind });
  }
  return { names: [...names], calls };
}

test("every array sampler passes a layer index and no stock slot keeps the array", async () => {
  const THREE = await import("three/webgpu");
  const { bindArrayMaterial } = await import("./cutout-node.js");
  const { applyPickMask, pickMask } = await import("./gpu-pick.js");
  const { createNodePickMaterial } = await import("./gpu-pick-node.js");
  const { createLayerTexture, isArrayTexture, STOCK_TEXTURE_SLOTS, writeLayerTexel } = await import("./texture-array.js");
  const { cloneMaterialPreservingTextures } = await import("./instance.js");

  const canvas = {
    width: 4,
    height: 4,
    style: {},
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getContext() { return null; },
  };
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]), 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1]), 2));
  geometry.setAttribute("tangent", new THREE.BufferAttribute(new Float32Array([1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1]), 4));
  geometry.setIndex([0, 1, 2]);

  function shaderFor(renderer, object) {
    renderer.hasFeature = () => false;
    if (renderer._bindings == null) {
      renderer._bindings = { getSharedBindGroup(group) { return group; } };
    }
    const builder = renderer.backend.createNodeBuilder(object, renderer);
    builder.camera = new THREE.PerspectiveCamera();
    builder.build();
    return builder.fragmentShader || "";
  }

  function compressedArray(seed) {
    const data = new Uint8Array(64);
    data[0] = seed;
    const texture = new THREE.CompressedArrayTexture(
      [{ data, width: 8, height: 8 }],
      8, 8, 2,
      THREE.RGBA_BPTC_Format,
    );
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.generateMipmaps = false;
    texture.flipY = false;
    texture.needsUpdate = true;
    return texture;
  }

  function withLayers(material) {
    material.userData.forgeLayerTexture = createLayerTexture(4);
    writeLayerTexel(material.userData.forgeLayerTexture, 0, 1, 2, 3, 4);
    return material;
  }

  function assertSlotsClear(material, label) {
    for (const slot of STOCK_TEXTURE_SLOTS) {
      assert.equal(isArrayTexture(material[slot]), false, `${label} ${slot}`);
    }
  }

  function assertIndexed(wgsl, label) {
    const parsed = arraySamples(wgsl);
    assert.ok(parsed.names.length > 0, `${label} declared a texture_2d_array`);
    assert.ok(parsed.calls.length > 0, `${label} samples the array`);
    for (const call of parsed.calls) {
      const needed = call.kind === "load" ? 3 : 4;
      assert.ok(call.argc >= needed, `${label} ${call.kind} has ${call.argc} args`);
    }
    assert.match(wgsl, /textureLoad/, `${label} reads the layer texel`);
  }

  const webgpu = new THREE.WebGPURenderer({ canvas, antialias: false });
  const webgl = new THREE.WebGPURenderer({ canvas, antialias: false, forceWebGL: true });

  const raw = withLayers(new THREE.MeshStandardMaterial());
  raw.map = compressedArray(1);
  const rawShader = shaderFor(webgpu, new THREE.Mesh(geometry, raw));
  const rawCalls = arraySamples(rawShader);
  assert.ok(rawCalls.calls.some((call) => call.argc < 4), "a stock map sample of an array omits array_index");

  const variants = [
    {
      name: "opaque map",
      fill(material) { material.map = compressedArray(2); },
    },
    {
      name: "cutout",
      fill(material) {
        material.map = compressedArray(3);
        material.alphaTest = 0.4;
      },
    },
    {
      name: "normal",
      fill(material) {
        material.map = compressedArray(4);
        material.normalMap = compressedArray(5);
      },
    },
    {
      name: "roughness",
      fill(material) {
        material.map = compressedArray(6);
        material.roughnessMap = compressedArray(7);
      },
    },
    {
      name: "orm ao",
      fill(material) {
        const orm = compressedArray(8);
        material.map = compressedArray(9);
        material.roughnessMap = orm;
        material.metalnessMap = orm;
        material.aoMap = orm;
      },
    },
    {
      name: "emissive",
      fill(material) {
        material.map = compressedArray(10);
        material.emissiveMap = compressedArray(11);
        material.emissive = new THREE.Color(1, 1, 1);
        material.emissiveIntensity = 1;
      },
    },
    {
      name: "alphaMap",
      fill(material) {
        material.map = compressedArray(12);
        material.alphaMap = compressedArray(13);
        material.transparent = true;
        material.opacity = 1;
      },
    },
    {
      name: "data array",
      fill(material) {
        material.map = new THREE.DataArrayTexture(new Uint8Array(4 * 4 * 2 * 4), 4, 4, 2);
      },
    },
    {
      name: "bump and light dropped",
      fill(material) {
        material.map = compressedArray(14);
        material.bumpMap = compressedArray(15);
        material.lightMap = compressedArray(16);
      },
    },
  ];

  for (const variant of variants) {
    const material = withLayers(new THREE.MeshStandardMaterial({ color: 0xffffff }));
    variant.fill(material);
    bindArrayMaterial(material);
    assertSlotsClear(material, variant.name);
    const wgsl = shaderFor(webgpu, new THREE.Mesh(geometry, material));
    assertIndexed(wgsl, variant.name);
    const glsl = shaderFor(webgl, new THREE.Mesh(geometry, material));
    assert.equal(glsl.length > 0, true, `${variant.name} WebGL shader`);
    assert.doesNotMatch(glsl, /texture_2d_array/, `${variant.name} WebGL shader`);
  }

  const bound = withLayers(new THREE.MeshStandardMaterial());
  bound.map = compressedArray(17);
  bindArrayMaterial(bound);
  const tint = cloneMaterialPreservingTextures(bound);
  assertSlotsClear(tint, "tint clone");
  tint.colorNode = null;
  tint.userData.albedoNode = false;
  bindArrayMaterial(tint);
  assertIndexed(shaderFor(webgpu, new THREE.Mesh(geometry, tint)), "tint clone");

  const mask = compressedArray(18);
  const source = withLayers(new THREE.MeshStandardMaterial());
  source.alphaTest = 0.4;
  source.userData.forgeArrayAlbedo = mask;
  const picked = pickMask(source);
  assert.equal(picked.map, mask);
  const pickMaterial = new THREE.MeshStandardMaterial();
  applyPickMask(pickMaterial, picked, source.userData.forgeLayerTexture);
  assert.equal(pickMaterial.map, null);
  assert.equal(pickMaterial.userData.forgePickMask, mask);

  const zero = new THREE.DataTexture(new Float32Array(4), 1, 1, THREE.RGBAFormat, THREE.FloatType);
  const nodePick = createNodePickMaterial({
    uniforms: {
      forgeExclude: { value: zero },
      forgeOutput: { value: 0 },
      forgePeel: { value: 0 },
      forgeFar: { value: 1000 },
    },
    getActiveObject: () => null,
  });
  applyPickMask(nodePick, picked, source.userData.forgeLayerTexture);
  assert.equal(isArrayTexture(nodePick.map), false);
  const pickShader = shaderFor(webgpu, new THREE.Mesh(geometry, nodePick));
  assertIndexed(pickShader, "gpu pick");
  assert.equal(nodePick.customProgramCacheKey().includes("arr"), true);

  const depth = new THREE.MeshDepthMaterial();
  depth.map = compressedArray(19);
  depth.alphaTest = 0.4;
  withLayers(depth);
  bindArrayMaterial(depth);
  assert.equal(isArrayTexture(depth.map), false);
  assert.equal(depth.userData.forgeArrayAlbedo.isCompressedArrayTexture, true);
});
