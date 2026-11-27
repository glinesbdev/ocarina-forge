/**
 * GPU id-buffer picking.
 *
 * Visible pickable meshes are drawn into a tiny offscreen target only when a
 * click or hover asks for it. The color is a 24-bit actor id (little-endian
 * RGB, alpha 0 means miss). A second mode packs the camera-to-surface distance
 * into the same 24 bits so the cursor can be unprojected without a CPU mesh.
 *
 * Instanced and batched draws sample a per-instance id texture. Id 0 is "not
 * an actor": locked instances discard, and terrain can still write a distance.
 * The gizmo stays off the pick layers. WebGL reads RGBA8; WebGPU's copy wants
 * a row padded to 256 bytes, so that path renders a 64-pixel-wide strip and
 * keeps the cursor pixel.
 */

import { holdTexture, isArrayTexture } from "./texture-array.js";

export const PICK_LAYER = 1;
export const PREVIEW_PICK_LAYER = 2;
export const TERRAIN_PICK_LAYER = 3;

/** WebGPU copyTextureToBuffer aligns rows to 256 bytes (64 RGBA8 pixels). */
export const WEBGPU_PICK_SPAN = 64;

/**
 * A three.js texture, or the color texture of a render target.
 * TextureNode rejects anything else (`Need a three.js texture`).
 */
export function resolvePickTexture(value) {
  if (!value) return null;
  if (value.isTexture === true) return value;
  if (value.texture && value.texture.isTexture === true) return value.texture;
  return null;
}

/**
 * The mask the id shader should discard against.
 * Albedo alpha is the usual cutout. An alphaMap is sampled from green, matching three.js.
 */
export function pickMask(source) {
  if (!source || !(source.alphaTest > 0)) return { alphaTest: 0, map: null, channel: "a" };
  const data = source.userData || {};
  const map = resolvePickTexture(source.map) || resolvePickTexture(data.forgeArrayAlbedo);
  if (map) return { alphaTest: source.alphaTest, map, channel: "a" };
  const alphaMap = resolvePickTexture(source.alphaMap) || resolvePickTexture(data.forgeArrayAlpha);
  if (alphaMap) return { alphaTest: source.alphaTest, map: alphaMap, channel: "g" };
  const held = resolvePickTexture(data.forgePickMask);
  if (held) {
    const channel = data.forgePickMaskChannel === "g" ? "g" : "a";
    return { alphaTest: source.alphaTest, map: held, channel };
  }
  return { alphaTest: source.alphaTest, map: null, channel: "a" };
}

/** A stock map slot cannot hold an array. The id shader samples `forgePickMask` with the layer. */
export function applyPickMask(material, mask, layerTexture) {
  if (!material) return;
  const map = mask && mask.channel !== "g" ? mask.map : null;
  const alpha = mask && mask.channel === "g" ? mask.map : null;
  material.map = isArrayTexture(map) ? null : (map || null);
  material.alphaMap = isArrayTexture(alpha) ? null : (alpha || null);
  material.alphaTest = mask && mask.alphaTest ? mask.alphaTest : 0;
  material.userData = material.userData || {};
  holdTexture(material.userData, "forgePickMask", (mask && mask.map) || null);
  material.userData.forgePickMaskChannel = (mask && mask.channel) || "a";
  material.userData.forgePickArray = isArrayTexture(mask && mask.map);
  if (layerTexture !== undefined) holdTexture(material.userData, "forgeLayerTexture", layerTexture || null);
}

/** Readback width. WebGPU copies a whole aligned row; WebGL can read one pixel. */
export function paddedPickWidth(span, webgpu) {
  const wanted = Math.max(1, span | 0);
  if (!webgpu) return wanted;
  return Math.ceil(wanted / WEBGPU_PICK_SPAN) * WEBGPU_PICK_SPAN;
}

export const PICK_PEEL_LIMIT = 8;

const ID_SCALE = 16777215;

export function encodeId24(id) {
  const n = Math.max(0, id | 0) & 0xffffff;
  return [n & 255, (n >> 8) & 255, (n >> 16) & 255, n ? 255 : 0];
}

export function decodeId24(r, g, b, a) {
  if (!a) return 0;
  return (r & 255) | ((g & 255) << 8) | ((b & 255) << 16);
}

export function encodeUnit24(t) {
  const n = Math.max(0, Math.min(ID_SCALE, Math.round(Math.max(0, Math.min(1, t)) * ID_SCALE)));
  return [n & 255, (n >> 8) & 255, (n >> 16) & 255, 255];
}

/** Null when the pixel was never written (alpha 0). */
export function decodeUnit24(r, g, b, a) {
  if (!a) return null;
  return ((r & 255) | ((g & 255) << 8) | ((b & 255) << 16)) / ID_SCALE;
}

/** GPU UNORM round-trip used by the id target. Exact for every byte. */
export function quantizeUnorm8(byte) {
  const clamped = Math.max(0, Math.min(255, byte | 0));
  return Math.round((clamped / 255) * 255);
}

export function pickTextureSize(count) {
  return Math.max(1, Math.ceil(Math.sqrt(Math.max(1, count | 0))));
}

export function writePickTexel(data, size, index, id) {
  if (!data || index < 0) return;
  const x = index % size;
  const y = Math.floor(index / size);
  const offset = (y * size + x) * 4;
  if (offset < 0 || offset + 3 >= data.length) return;
  data[offset] = id;
  data[offset + 1] = 0;
  data[offset + 2] = 0;
  data[offset + 3] = 0;
}

export function readPickTexel(data, size, index) {
  const x = index % size;
  const y = Math.floor(index / size);
  const offset = (y * size + x) * 4;
  return data[offset] || 0;
}

export function ndcFromClient(clientX, clientY, rect) {
  const width = rect?.width || 1;
  const height = rect?.height || 1;
  return {
    x: ((clientX - rect.left) / width) * 2 - 1,
    y: -((clientY - rect.top) / height) * 2 + 1,
  };
}

/** Framebuffer pixel. y is from the top, matching PerspectiveCamera.setViewOffset. */
export function pixelFromNdc(ndcX, ndcY, width, height) {
  const w = Math.max(1, width | 0);
  const h = Math.max(1, height | 0);
  const x = Math.min(w - 1, Math.max(0, Math.floor((ndcX * 0.5 + 0.5) * w)));
  const yFromTop = Math.min(h - 1, Math.max(0, Math.floor((ndcY * -0.5 + 0.5) * h)));
  return { x, yFromTop };
}

/**
 * Place `needed` pixels in a strip `span` wide without running off the buffer.
 * `localX` is the cursor inside that strip.
 */
export function pickStrip(pixelX, bufferWidth, span) {
  const width = Math.max(1, bufferWidth | 0);
  const wanted = Math.max(1, Math.min(span, width));
  let origin = pixelX | 0;
  if (origin + wanted > width) origin = Math.max(0, width - wanted);
  if (origin < 0) origin = 0;
  const localX = Math.min(wanted - 1, Math.max(0, (pixelX | 0) - origin));
  return { origin, span: wanted, localX };
}

export function uniqueIdsFromBuffer(bytes, width, height, rowStride = width) {
  const ids = [];
  const seen = new Set();
  const w = width | 0;
  const h = height | 0;
  const stride = Math.max(w, rowStride | 0);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const offset = (y * stride + x) * 4;
      const id = decodeId24(bytes[offset], bytes[offset + 1], bytes[offset + 2], bytes[offset + 3]);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      ids.push(id);
    }
  }
  return ids;
}

/** Actor keys in front-to-back order, plus volume faces from the CPU box test. */
export function groupsFromPick(keys, volumeHits = []) {
  const solids = [];
  for (const key of keys || []) {
    if (key && !solids.includes(key)) solids.push(key);
  }
  const volumes = [];
  const edges = [];
  const ranked = [...volumeHits].sort((a, b) => (a.distance || 0) - (b.distance || 0));
  for (const hit of ranked) {
    if (!hit?.id || solids.includes(hit.id)) continue;
    if (!volumes.includes(hit.id)) volumes.push(hit.id);
    if (hit.edge && !edges.includes(hit.id)) edges.push(hit.id);
  }
  return { solids, volumes, edges };
}

export function pointFromDistance(origin, direction, distance, target) {
  return target.copy(origin).addScaledVector(direction, distance);
}

/** Gizmos live on layer 0. Pick cameras never enable that bit. */
export function pickLayerMask({ preview = false, terrain = false } = {}) {
  let mask = 1 << PICK_LAYER;
  if (preview) mask |= 1 << PREVIEW_PICK_LAYER;
  if (terrain) mask |= 1 << TERRAIN_PICK_LAYER;
  return mask;
}

export function layerIntersects(objectMask, pickMask) {
  return (objectMask & pickMask) !== 0;
}

export const PICK_VERTEX_WORLD = `
{
  vec4 forgeWorld = vec4( transformed, 1.0 );
  #ifdef USE_BATCHING
    forgeWorld = batchingMatrix * forgeWorld;
  #endif
  #ifdef USE_INSTANCING
    forgeWorld = instanceMatrix * forgeWorld;
  #endif
  forgeWorld = modelMatrix * forgeWorld;
  vForgeWorld = forgeWorld.xyz;
  #ifdef USE_BATCHING
    vForgeInstance = getIndirectIndex( gl_DrawID );
  #elif defined( USE_INSTANCING )
    vForgeInstance = float( gl_InstanceID );
  #else
    vForgeInstance = -1.0;
  #endif
}
`;

export const PICK_FRAGMENT_LIB = `
uniform highp sampler2D forgePickIds;
uniform highp sampler2D forgeExclude;
uniform float forgePickBase;
uniform float forgeDepthOnly;
uniform float forgeOutput;
uniform float forgePeel;
uniform vec3 forgeCameraPos;
uniform float forgeFar;
varying vec3 vForgeWorld;
varying float vForgeInstance;

float forgeSample(sampler2D tex, float index) {
  int size = textureSize(tex, 0).x;
  int j = int(index + 0.5);
  if (j < 0) return 0.0;
  int y = j / size;
  int x = j - y * size;
  return texelFetch(tex, ivec2(x, y), 0).r;
}

float forgeResolvedId() {
  // The indirect index lives in the vertex stage. A negative varying means
  // this draw is a single mesh and the id is the uniform.
  if (vForgeInstance >= 0.0) return forgeSample(forgePickIds, vForgeInstance);
  return forgePickBase;
}

vec4 forgePack24(int n) {
  return vec4(
    float(n & 255) / 255.0,
    float((n >> 8) & 255) / 255.0,
    float((n >> 16) & 255) / 255.0,
    1.0
  );
}
`;

export const PICK_FRAGMENT_OUT = `
{
  float forgeId = forgeResolvedId();
  if (forgeOutput > 0.5) {
    float forgeDist = length(vForgeWorld - forgeCameraPos);
    float forgeT = clamp(forgeDist / max(forgeFar, 0.001), 0.0, 1.0);
    gl_FragColor = forgePack24(int(forgeT * 16777215.0 + 0.5));
  } else {
    if (forgePeel > 0.5 && forgeId > 0.5 && forgeSample(forgeExclude, forgeId) > 0.5) discard;
    if (forgeId < 0.5 && forgeDepthOnly < 0.5) discard;
    gl_FragColor = forgePack24(int(forgeId + 0.5));
  }
}
`;

export function installPickShader(shader, uniforms) {
  Object.assign(shader.uniforms, uniforms);
  shader.vertexShader = shader.vertexShader
    .replace("#include <common>", "#include <common>\nvarying vec3 vForgeWorld;\nvarying float vForgeInstance;")
    .replace("#include <project_vertex>", `#include <project_vertex>\n${PICK_VERTEX_WORLD}`);
  shader.fragmentShader = shader.fragmentShader
    .replace("#include <common>", `#include <common>\n${PICK_FRAGMENT_LIB}`)
    .replace("#include <opaque_fragment>", PICK_FRAGMENT_OUT)
    .replace("#include <tonemapping_fragment>", "")
    .replace("#include <colorspace_fragment>", "")
    .replace("#include <fog_fragment>", "")
    .replace("#include <premultiplied_alpha_fragment>", "")
    .replace("#include <dithering_fragment>", "");
  return shader;
}

function zeroTexture(THREE) {
  const data = new Float32Array([0, 0, 0, 0]);
  const texture = new THREE.DataTexture(data, 1, 1, THREE.RGBAFormat, THREE.FloatType);
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  texture.userData.forgePickIds = data;
  texture.userData.forgePickSize = 1;
  return texture;
}

function ensureIdTexture(THREE, texture, count) {
  const size = pickTextureSize(count);
  const needed = size * size * 4;
  if (texture && texture.userData?.forgePickIds?.length === needed) return texture;
  const data = new Float32Array(needed);
  const next = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.FloatType);
  next.magFilter = THREE.NearestFilter;
  next.minFilter = THREE.NearestFilter;
  next.generateMipmaps = false;
  next.needsUpdate = true;
  next.userData.forgePickIds = data;
  next.userData.forgePickSize = size;
  if (texture) texture.dispose?.();
  return next;
}

function pickIdsUnchanged(texture, ids) {
  const data = texture.userData && texture.userData.forgePickIds;
  const size = texture.userData && texture.userData.forgePickSize;
  if (!data || !size) return false;
  const stored = texture.userData.forgePickCount | 0;
  const count = ids.length;
  const limit = Math.min(size * size, Math.max(stored, count));
  for (let i = 0; i < limit; i += 1) {
    const want = i < count ? (ids[i] || 0) : 0;
    if (readPickTexel(data, size, i) !== want) return false;
  }
  return stored === count;
}

function fillIdTexture(texture, ids) {
  if (pickIdsUnchanged(texture, ids)) return false;
  const data = texture.userData.forgePickIds;
  const size = texture.userData.forgePickSize;
  data.fill(0);
  for (let i = 0; i < ids.length; i += 1) writePickTexel(data, size, i, ids[i] || 0);
  texture.userData.forgePickCount = ids.length;
  texture.needsUpdate = true;
  return true;
}

/**
 * Owns the offscreen target, the id table, and the override material.
 * `readPixel` is injectable in tests.
 */
export function createGpuPicker({ THREE, renderer, scene, getCamera, createNodeMaterial = null, gpuPick = true }) {
  const zero = zeroTexture(THREE);
  const uniforms = {
    forgePickIds: { value: zero },
    forgeExclude: { value: zero },
    forgePickBase: { value: 0 },
    forgeDepthOnly: { value: 0 },
    forgeOutput: { value: 0 },
    forgePeel: { value: 0 },
    forgeCameraPos: { value: new THREE.Vector3() },
    forgeFar: { value: 1000 },
  };
  let activeObject = null;
  const material = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    toneMapped: false,
    fog: false,
    transparent: false,
    depthTest: true,
    depthWrite: true,
    side: THREE.DoubleSide,
    blending: THREE.NoBlending,
  });
  material.userData.forgePick = true;
  material.customProgramCacheKey = () => {
    const object = activeObject;
    // WebGL shares one instanced program and updates the id texture uniform.
    // WebGPU's instance node captures the mesh, so each preview mesh stays unique.
    if (object?.isInstancedMesh) return nodeRenderer ? `forge-pick-inst-${object.uuid}` : "forge-pick-inst";
    if (object?.isBatchedMesh) return "forge-pick-batch";
    return "forge-pick-mesh";
  };
  material.onBeforeCompile = (shader) => installPickShader(shader, uniforms);
  let nodeMaterial = null;
  // Native WebGPU's copy aligns rows to 256 bytes. The WebGL2 backend of
  // WebGPURenderer does not, but both backends compile node materials.
  const nativeWebgpu = Boolean(renderer?.isWebGPURenderer && renderer.backend?.isWebGPUBackend);
  const nodeRenderer = Boolean(renderer?.isWebGPURenderer);
  const spanFor = () => (nativeWebgpu ? WEBGPU_PICK_SPAN : 1);
  const Target = THREE.WebGLRenderTarget || THREE.RenderTarget;
  let target = null;
  let targetWidth = 0;

  const keyToId = new Map();
  const idToKey = new Map();
  let nextId = 1;
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const point = new THREE.Vector3();
  const pickCamera = new THREE.PerspectiveCamera(50, 1, 0.05, 8000);
  pickCamera.layers.disableAll();

  function drawingSize() {
    const canvas = renderer?.domElement;
    return {
      width: canvas?.width || 1,
      height: canvas?.height || 1,
    };
  }

  function ensureTarget(width) {
    if (target && targetWidth === width) return target;
    target?.dispose?.();
    target = new Target(width, 1, {
      format: THREE.RGBAFormat,
      type: THREE.UnsignedByteType,
      depthBuffer: true,
      stencilBuffer: false,
      samples: 0,
      magFilter: THREE.NearestFilter,
      minFilter: THREE.NearestFilter,
      generateMipmaps: false,
    });
    if (target.texture) {
      target.texture.colorSpace = THREE.LinearSRGBColorSpace;
      target.texture.generateMipmaps = false;
    }
    if (target.viewport) target.viewport.set(0, 0, width, 1);
    if (target.scissor) target.scissor.set(0, 0, width, 1);
    target.scissorTest = false;
    targetWidth = width;
    return target;
  }

  function idFor(key) {
    if (!key) return 0;
    let id = keyToId.get(key);
    if (id) return id;
    id = nextId;
    nextId += 1;
    if (nextId > 0xffffff) nextId = 1;
    keyToId.set(key, id);
    idToKey.set(id, key);
    return id;
  }

  function keyFor(id) {
    return idToKey.get(id) || null;
  }

  function growExclude(maxId) {
    const count = Math.max(1, maxId + 1);
    const size = pickTextureSize(count);
    const current = uniforms.forgeExclude.value;
    if (current?.userData?.forgePickSize === size && current !== zero) return current;
    const data = new Float32Array(size * size * 4);
    const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.FloatType);
    texture.magFilter = THREE.NearestFilter;
    texture.minFilter = THREE.NearestFilter;
    texture.generateMipmaps = false;
    texture.needsUpdate = true;
    texture.userData.forgePickIds = data;
    texture.userData.forgePickSize = size;
    if (current && current !== zero) current.dispose?.();
    uniforms.forgeExclude.value = texture;
    return texture;
  }

  function setExcluded(ids) {
    const maxId = ids.reduce((max, id) => Math.max(max, id), 0);
    const texture = growExclude(maxId);
    const data = texture.userData.forgePickIds;
    data.fill(0);
    const size = texture.userData.forgePickSize;
    for (const id of ids) writePickTexel(data, size, id, 1);
    texture.needsUpdate = true;
    uniforms.forgePeel.value = ids.length ? 1 : 0;
  }

  function writeInstanceIds(mesh, ids) {
    if (!mesh) return false;
    const texture = ensureIdTexture(THREE, mesh.userData.forgePickTexture, Math.max(ids.length, 1));
    mesh.userData.forgePickTexture = texture;
    const wrote = fillIdTexture(texture, ids);
    if (mesh.isBatchedMesh && mesh._indirectTexture) mesh.userData.forgeIndirect = mesh._indirectTexture;
    return wrote;
  }

  function prepareObject(object, sourceMaterial) {
    activeObject = object;
    const src = sourceMaterial || object?.material;
    uniforms.forgePickBase.value = object?.userData?.forgePickId || 0;
    uniforms.forgeDepthOnly.value = object?.userData?.forgePickDepthOnly ? 1 : 0;
    uniforms.forgePickIds.value = resolvePickTexture(object?.userData?.forgePickTexture) || zero;
    const mask = pickMask(src);
    const nextMap = mask.channel === "a" && !isArrayTexture(mask.map) ? mask.map : null;
    const nextAlpha = mask.channel === "g" && !isArrayTexture(mask.map) ? mask.map : null;
    const defineChange = (material.alphaTest > 0) !== (mask.alphaTest > 0)
      || Boolean(material.map) !== Boolean(nextMap)
      || Boolean(material.alphaMap) !== Boolean(nextAlpha)
      || (nodeMaterial?.userData?.forgePickArray === true) !== isArrayTexture(mask.map);
    const layerTexture = src && src.userData ? src.userData.forgeLayerTexture : null;
    applyPickMask(material, mask, layerTexture);
    if (defineChange) material.needsUpdate = true;
    if (nodeMaterial) {
      applyPickMask(nodeMaterial, mask, layerTexture);
      if (defineChange) nodeMaterial.needsUpdate = true;
    }
  }

  function aim(camera, ndcX, ndcY) {
    const { width, height } = drawingSize();
    const pixel = pixelFromNdc(ndcX, ndcY, width, height);
    const strip = pickStrip(pixel.x, width, spanFor());
    const row = Math.min(height, strip.span > 1 ? 1 : 1);
    pickCamera.fov = camera.fov;
    pickCamera.zoom = camera.zoom;
    pickCamera.near = camera.near;
    pickCamera.far = camera.far;
    pickCamera.coordinateSystem = camera.coordinateSystem;
    pickCamera.position.copy(camera.position);
    pickCamera.quaternion.copy(camera.quaternion);
    pickCamera.scale.copy(camera.scale);
    pickCamera.up.copy(camera.up);
    pickCamera.updateMatrixWorld(true);
    pickCamera.setViewOffset(width, height, strip.origin, pixel.yFromTop, strip.span, row);
    uniforms.forgeCameraPos.value.setFromMatrixPosition(camera.matrixWorld);
    uniforms.forgeFar.value = camera.far || 1;
    return { strip, pixel };
  }

  function applyLayers({ preview, terrain }) {
    pickCamera.layers.disableAll();
    pickCamera.layers.enable(PICK_LAYER);
    if (preview) pickCamera.layers.enable(PREVIEW_PICK_LAYER);
    if (terrain) pickCamera.layers.enable(TERRAIN_PICK_LAYER);
  }

  async function readPixels(rt, x, y, width, height) {
    if (typeof renderer.readRenderTargetPixelsAsync !== "function") {
      throw new Error("This renderer cannot read the pick target.");
    }
    if (renderer.isWebGLRenderer) {
      const buffer = new Uint8Array(width * height * 4);
      await renderer.readRenderTargetPixelsAsync(rt, x, y, width, height, buffer);
      return buffer;
    }
    return renderer.readRenderTargetPixelsAsync(rt, x, y, width, height, 0);
  }

  async function renderPick({ ndcX, ndcY, preview = false, terrain = false, output = 0, excluded = [] }) {
    if (!gpuPick) throw new Error("GPU picking is off.");
    const camera = getCamera();
    if (!camera || !renderer || !scene) throw new Error("The pick target is not ready.");
    const { strip } = aim(camera, ndcX, ndcY);
    applyLayers({ preview, terrain });
    setExcluded(output === 0 ? excluded : []);
    uniforms.forgeOutput.value = output;
    const rt = ensureTarget(paddedPickWidth(strip.span, nativeWebgpu));
    const previousTarget = renderer.getRenderTarget?.() ?? null;
    const previousOverride = scene.overrideMaterial;
    const previousBackground = scene.background;
    const previousFog = scene.fog;
    const previousTone = renderer.toneMapping;
    const previousClear = renderer.getClearColor ? renderer.getClearColor(new THREE.Color()) : null;
    const previousAlpha = renderer.getClearAlpha ? renderer.getClearAlpha() : 1;
    const previousViewport = renderer.getViewport ? renderer.getViewport(new THREE.Vector4()) : null;
    const override = nodeMaterial || material;
    try {
      scene.overrideMaterial = override;
      scene.background = null;
      scene.fog = null;
      renderer.toneMapping = THREE.NoToneMapping;
      renderer.setClearColor?.(0x000000, 0);
      // Clear the padded row, then draw 1:1 into the left `span` pixels.
      if (rt.viewport) rt.viewport.set(0, 0, rt.width, 1);
      renderer.setRenderTarget(rt);
      renderer.clear?.(true, true, true);
      if (rt.viewport) rt.viewport.set(0, 0, strip.span, 1);
      renderer.setRenderTarget(rt);
      renderer.setViewport?.(0, 0, strip.span, 1);
      const rendered = renderer.render(scene, pickCamera);
      if (rendered && typeof rendered.then === "function") await rendered;
    } finally {
      // Restore before the readback await so a beauty frame cannot draw ids.
      scene.overrideMaterial = previousOverride;
      scene.background = previousBackground;
      scene.fog = previousFog;
      renderer.toneMapping = previousTone;
      if (previousClear && renderer.setClearColor) renderer.setClearColor(previousClear, previousAlpha);
      if (previousViewport && renderer.setViewport) renderer.setViewport(previousViewport);
      renderer.setRenderTarget(previousTarget);
      uniforms.forgeOutput.value = 0;
      uniforms.forgePeel.value = 0;
      activeObject = null;
    }
    const buffer = await readPixels(rt, 0, 0, rt.width, 1);
    return { buffer, strip };
  }

  function pixelBytes(buffer, localX) {
    const offset = localX * 4;
    return [buffer[offset] || 0, buffer[offset + 1] || 0, buffer[offset + 2] || 0, buffer[offset + 3] || 0];
  }

  async function pickIds({ ndcX, ndcY, preview = false, peel = false }) {
    const found = [];
    const passes = peel ? PICK_PEEL_LIMIT : 1;
    for (let i = 0; i < passes; i += 1) {
      const { buffer, strip } = await renderPick({
        ndcX, ndcY, preview, terrain: false, output: 0, excluded: found,
      });
      const [r, g, b, a] = pixelBytes(buffer, strip.localX);
      const id = decodeId24(r, g, b, a);
      if (!id || found.includes(id)) break;
      found.push(id);
      if (!peel) break;
    }
    return found.map((id) => keyFor(id)).filter(Boolean);
  }

  async function pickDistance({ ndcX, ndcY, preview = false, terrain = true }) {
    const camera = getCamera();
    const { buffer, strip } = await renderPick({
      ndcX, ndcY, preview, terrain, output: 1, excluded: [],
    });
    const [r, g, b, a] = pixelBytes(buffer, strip.localX);
    const t = decodeUnit24(r, g, b, a);
    if (t == null || !camera) return null;
    pointer.set(ndcX, ndcY);
    ray.setFromCamera(pointer, camera);
    return pointFromDistance(ray.ray.origin, ray.ray.direction, t * (camera.far || 1), point).clone();
  }

  async function attachNodeMaterial() {
    if (!nodeRenderer || nodeMaterial || typeof createNodeMaterial !== "function") return false;
    nodeMaterial = await createNodeMaterial({ THREE, uniforms, getActiveObject: () => activeObject });
    if (nodeMaterial) {
      nodeMaterial.userData.forgePick = true;
      nodeMaterial.side = THREE.DoubleSide;
      nodeMaterial.transparent = false;
      nodeMaterial.depthTest = true;
      nodeMaterial.depthWrite = true;
      nodeMaterial.fog = false;
      nodeMaterial.toneMapped = false;
      nodeMaterial.blending = THREE.NoBlending;
      nodeMaterial.forceSinglePass = true;
    }
    return Boolean(nodeMaterial);
  }

  return {
    idFor,
    keyFor,
    writeInstanceIds,
    prepareObject,
    pickIds,
    pickDistance,
    attachNodeMaterial,
    get material() { return nodeMaterial || material; },
    reset() {
      keyToId.clear();
      idToKey.clear();
      nextId = 1;
      const exclude = uniforms.forgeExclude.value;
      if (exclude && exclude !== zero) {
        exclude.dispose?.();
        uniforms.forgeExclude.value = zero;
      }
    },
    dispose() {
      target?.dispose?.();
      target = null;
      material.dispose?.();
      nodeMaterial?.dispose?.();
      zero.dispose?.();
      const exclude = uniforms.forgeExclude.value;
      if (exclude && exclude !== zero) exclude.dispose?.();
    },
  };
}
