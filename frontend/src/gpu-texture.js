/**
 * Upload a packed texture without decoding it on the main thread.
 *
 * OCFG is a KTX2 plus the JPEG or PNG three already knows how to turn into an
 * ImageBitmap. The KTX2 is the native BC1, BC3, BC7, or ASTC blocks. three
 * r170's KTX2 loader rejects those vkFormats (it only raw-loads a short list,
 * and a Basis file is vkFormat 0). A vkFormat of 0 is handed back to that
 * loader. Anything else the GPU cannot sample uses the trailing image.
 *
 * WebGPU caps come from the renderer's `backend.hasFeature`, not from
 * `getContext().getExtension` — a GPUCanvasContext has no WebGL extensions,
 * and probing it used to force every texture down the RGBA bitmap path.
 */

const KTX_MAGIC = [0xab, 0x4b, 0x54, 0x58, 0x20, 0x32, 0x30, 0xbb, 0x0d, 0x0a, 0x1a, 0x0a];

export const VK_FORMAT = {
  bc1: 133,
  bc3: 137,
  bc7: 145,
};

export function splitPacked(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (bytes.length >= 8 && bytes[0] === 0x4f && bytes[1] === 0x43 && bytes[2] === 0x46 && bytes[3] === 0x47) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const size = view.getUint32(4, true);
    if (size >= 48 && 8 + size <= bytes.length) {
      return { ktx: bytes.subarray(8, 8 + size), fallback: bytes.subarray(8 + size) };
    }
  }
  return { ktx: bytes, fallback: new Uint8Array() };
}

export function mipCount(width, height) {
  const edge = Math.max(width || 1, height || 1);
  return Math.floor(Math.log2(edge)) + 1;
}

export function readKtx2(bytes) {
  if (!bytes || bytes.length < 104) return null;
  for (let i = 0; i < KTX_MAGIC.length; i += 1) {
    if (bytes[i] !== KTX_MAGIC[i]) return null;
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const vkFormat = view.getUint32(12, true);
  const width = view.getUint32(20, true);
  const height = view.getUint32(24, true);
  const levelCount = view.getUint32(40, true);
  if (!width || !height || levelCount < 1 || levelCount > 16) return null;
  if (bytes.length < 80 + levelCount * 24) return null;
  const levels = [];
  let levelWidth = width;
  let levelHeight = height;
  for (let level = 0; level < levelCount; level += 1) {
    const at = 80 + level * 24;
    const offset = Number(view.getBigUint64(at, true));
    const length = Number(view.getBigUint64(at + 8, true));
    if (length <= 0 || offset < 0 || offset + length > bytes.length) return null;
    levels.push({
      width: levelWidth,
      height: levelHeight,
      data: bytes.subarray(offset, offset + length),
    });
    levelWidth = Math.max(1, levelWidth >> 1);
    levelHeight = Math.max(1, levelHeight >> 1);
  }
  return { vkFormat, width, height, levels };
}

/** 1008 is LinearMipmapLinear. 1006 is Linear. A short chain must not ask for mips. */
export function minFilterFor(uploaded, width, height) {
  return uploaded >= mipCount(width, height) ? 1008 : 1006;
}

export function repairCompressedTexture(texture) {
  if (!texture || !texture.isCompressedTexture) return;
  // Do not mark the texture dirty after the CPU copies have been dropped.
  if (texture.userData?.forgeCpuReleased || texture.source?.userData?.forgeCpuReleased) return;
  const uploaded = (texture.mipmaps && texture.mipmaps.length) || 1;
  const width = (texture.image && texture.image.width) || 0;
  const height = (texture.image && texture.image.height) || 0;
  texture.generateMipmaps = false;
  texture.minFilter = minFilterFor(uploaded, width, height);
  texture.magFilter = 1006;
  texture.needsUpdate = true;
}


function emptyTexelData(data) {
  if (!data) return data;
  if (typeof data.byteLength === "number" && data.byteLength === 0) return data;
  if (ArrayBuffer.isView(data)) return new data.constructor(0);
  return data;
}

/**
 * Drop CPU-side texel copies after the GPU has the allocation.
 * Clones that share a Source or the same mipmaps array are marked together so
 * a later material does not try to re-upload emptied mips.
 */
export function textureImageSize(texture) {
  const image = (texture && (texture.image || (texture.source && texture.source.data))) || {};
  const mip = texture && texture.mipmaps && texture.mipmaps[0];
  const width = Number(image.width || (mip && mip.width) || 0) || 0;
  const height = Number(image.height || (mip && mip.height) || 0) || 0;
  return { width, height };
}

/**
 * True when this texture object already has a GPU image that is not three's
 * 1×1 white default. CPU bytes stay until then: releasing them first makes
 * WebGPU skip the upload (`source.dataReady` is false) and the material's
 * texture binding has nothing to sample.
 */
export function textureGpuReady(backend, texture) {
  if (!backend || typeof backend.get !== "function" || !texture) return false;
  try {
    const data = backend.get(texture);
    return Boolean(data && (data.texture || data.externalTexture) && data.isDefaultTexture !== true);
  } catch {
    return false;
  }
}

export function releaseTextureCpu(texture) {
  if (!texture) return false;
  const source = texture.source;
  if (texture.userData?.forgeCpuReleased || source?.userData?.forgeCpuReleased) {
    if (texture.userData) texture.userData.forgeCpuReleased = true;
    return false;
  }
  // Read size first. close() on an ImageBitmap makes width/height 0, and
  // three's WebGPU path then does log2(0)+1 = -Infinity for mipLevelCount.
  const { width, height } = textureImageSize(texture);
  // Mutate in place. Do not set needsUpdate: that retriggers an empty upload.
  const mipmaps = texture.mipmaps;
  if (Array.isArray(mipmaps)) {
    for (const level of mipmaps) {
      if (level && level.data && level.data.byteLength) level.data = emptyTexelData(level.data);
    }
  }
  const image = texture.image || (source && source.data);
  if (image && typeof image.close === "function") {
    try {
      image.close();
    } catch {
      /* ImageBitmap already closed */
    }
    // Leave a size stub so a later GPU recreate (dispose + new clone, or a
    // scene change that rebuilds the binding) cannot see a 0x0 image.
    if (source) source.data = { width, height };
  } else if (image && image.data && image.data.byteLength) {
    image.data = emptyTexelData(image.data);
  }
  texture.generateMipmaps = false;
  // three uploads when source.dataReady is true. The CPU pixels are gone, so
  // a later createTexture (scene switch dispose + rebuild) must not try to
  // copyExternalImageToTexture from the size stub.
  if (source) source.dataReady = false;
  texture.userData = texture.userData || {};
  texture.userData.forgeCpuReleased = true;
  texture.userData.forgeCpuSize = { width, height };
  if (source) {
    source.userData = source.userData || {};
    source.userData.forgeCpuReleased = true;
  }
  return true;
}

export function gpuTexturesEnabled(search, stored) {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  const flag = params.get("textures") || params.get("gpuTextures");
  if (flag === "bitmap" || flag === "0") return false;
  if (flag === "gpu" || flag === "1") return true;
  if (stored === "0" || stored === false) return false;
  return true;
}

export function chooseTexture(buffer, options) {
  const enabled = !options || options.enabled !== false;
  const caps = (options && options.caps) || {};
  const gl = options && options.gl;
  const packed = splitPacked(buffer);
  const info = readKtx2(packed.ktx);
  const format = enabled && info ? formatFor(info.vkFormat, caps) : 0;
  if (format) {
    if (canUploadCompressed(gl, format, info.levels)) {
      return { kind: "compressed", format, width: info.width, height: info.height, levels: info.levels, reason: "" };
    }
    if (info.levels.length > 1 && canUploadCompressed(gl, format, [info.levels[0]])) {
      return {
        kind: "compressed",
        format,
        width: info.width,
        height: info.height,
        levels: [info.levels[0]],
        reason: "only the base mip uploaded",
      };
    }
    const misaligned = info.width % 4 !== 0 || info.height % 4 !== 0;
    return {
      kind: "bitmap",
      bytes: packed.fallback,
      reason: misaligned ? "size is not a multiple of 4" : "the GPU rejected the blocks",
    };
  }
  if (!info) {
    // Not a KTX2. The texture manifest stores JPEG or PNG when the image is
    // not a multiple of 4, and those bytes are the file, not an OCFG trailer.
    return { kind: "bitmap", bytes: packed.ktx.length ? packed.ktx : packed.fallback, reason: "" };
  }
  if (info.vkFormat !== 0) {
    return { kind: "bitmap", bytes: packed.fallback, reason: "" };
  }
  return { kind: "basis", bytes: packed.ktx, reason: "" };
}

/**
 * GLTFLoader calls loader.load(blobUrl), then the KTX2 loader fetches and parse().
 * A stand-in that only implements parse() throws, the loader swallows it, and
 * the material is left with no map. Problems stays empty.
 */
const reportedTextureFallback = new Set();

function textureKey(buffer, url) {
  if (url) return String(url);
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer || 0);
  const n = bytes.length;
  return `${n}:${n ? bytes[0] : 0}:${n > 1 ? bytes[1] : 0}:${n > 16 ? bytes[16] : 0}`;
}

function warnTextureFallback(key, reason) {
  const id = key || "texture";
  if (reportedTextureFallback.has(id)) return;
  reportedTextureFallback.add(id);
  console.warn(`Texture has no bitmap fallback (${reason || "unreadable"}). Using a flat colour.`);
}

function plainTexture(THREE) {
  const data = new Uint8Array([255, 255, 255, 255]);
  const texture = typeof THREE.DataTexture === "function"
    ? new THREE.DataTexture(data, 1, 1)
    : new THREE.Texture({ width: 1, height: 1, data });
  texture.needsUpdate = true;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.flipY = false;
  texture.userData = { ...(texture.userData || {}), forgePlainFallback: true };
  return texture;
}

function decodeBitmap(bytes) {
  if (bytes && bytes.length && typeof createImageBitmap === "function") {
    return createImageBitmap(new Blob([bytes]), { imageOrientation: "none", premultiplyAlpha: "none" });
  }
  if (!bytes || !bytes.length || typeof Image === "undefined" || typeof URL === "undefined" || typeof Blob === "undefined") {
    return Promise.reject(new Error("no bitmap fallback"));
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([bytes]));
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("no bitmap fallback"));
    };
    image.src = url;
  });
}

export function createGpuTextureLoader(options) {
  const THREE = options.THREE;
  const gl = options.gl;
  const caps = options.caps;
  const basisParse = options.basisParse;
  const onFallback = options.onFallback;
  const enabled = options.enabled || (() => true);

  function usePlain(onLoad, key, reason) {
    warnTextureFallback(key, reason);
    try {
      onLoad(plainTexture(THREE));
    } catch {
      /* the caller already moved on */
    }
  }

  function parse(buffer, onLoad, onError, textureId) {
    const key = textureId || textureKey(buffer);
    try {
      const choice = chooseTexture(buffer, { enabled: enabled(), caps, gl });
      if (choice.kind === "compressed") {
        if (choice.reason && onFallback) onFallback(choice.reason);
        const texture = new THREE.CompressedTexture(
          choice.levels.map((level) => ({ data: level.data, width: level.width, height: level.height })),
          choice.width,
          choice.height,
          choice.format,
        );
        texture.needsUpdate = true;
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.flipY = false;
        texture.generateMipmaps = false;
        onLoad(texture);
        return;
      }
      if (choice.kind === "bitmap") {
        if (choice.reason && onFallback) onFallback(choice.reason);
        const bytes = choice.bytes;
        if (bytes && bytes.length) {
          decodeBitmap(bytes).then((image) => {
            const texture = new THREE.Texture(image);
            texture.needsUpdate = true;
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.flipY = false;
            onLoad(texture);
          }, (error) => usePlain(onLoad, key, (error && error.message) || choice.reason || "no bitmap fallback"));
          return;
        }
        usePlain(onLoad, key, choice.reason || "no bitmap fallback");
        return;
      }
      if (choice.kind === "basis" && basisParse) {
        const view = choice.bytes && choice.bytes.length ? choice.bytes : new Uint8Array(buffer);
        const basisBuffer = view.byteOffset === 0 && view.byteLength === view.buffer.byteLength
          ? view.buffer
          : view.slice().buffer;
        basisParse(basisBuffer, onLoad, onError);
        return;
      }
    } catch (error) {
      usePlain(onLoad, key, error && error.message);
      return;
    }
    usePlain(onLoad, key, "no texture");
  }

  function download(url, onLoad, onError, deferred) {
    fetch(url).then((response) => {
      if (!response.ok) throw new Error(`texture download failed (${response.status})`);
      return response.arrayBuffer();
    }).then((buffer) => {
      const apply = () => {
        try {
          parse(buffer, onLoad, onError, url);
        } catch (error) {
          usePlain(onLoad, url, error && error.message);
        }
      };
      if (deferred && options.scheduleDetailParse) options.scheduleDetailParse(apply);
      else apply();
    }).catch((error) => {
      try {
        if (onError) onError(error);
        else usePlain(onLoad, url, error && error.message);
      } catch {
        usePlain(onLoad, url, error && error.message);
      }
    });
  }

  return {
    load: (url, onLoad, _onProgress, onError) => download(url, onLoad, onError, false),
    loadDeferred: (url, onLoad, _onProgress, onError) => download(url, onLoad, onError, true),
    parse,
  };
}

export function canUploadCompressed(gl, format, levels) {
  if (!format || !levels || !levels.length) return false;
  if (levels[0].width % 4 !== 0 || levels[0].height % 4 !== 0) return false;
  // A WebGPU canvas context has no compressedTexImage2D. Caps already decided the format.
  if (!gl || typeof gl.compressedTexImage2D !== "function") return true;
  const tex = gl.createTexture();
  if (!tex) return false;
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.getError();
  let ok = true;
  for (let level = 0; level < levels.length; level += 1) {
    const item = levels[level];
    gl.compressedTexImage2D(gl.TEXTURE_2D, level, format, item.width, item.height, 0, item.data);
    if (gl.getError()) {
      ok = false;
      break;
    }
  }
  gl.bindTexture(gl.TEXTURE_2D, null);
  gl.deleteTexture(tex);
  return ok;
}

const ASTC = {
  157: 37808, 158: 37808,
  159: 37809, 160: 37809,
  161: 37810, 162: 37810,
  163: 37811, 164: 37811,
  165: 37812, 166: 37812,
  167: 37813, 168: 37813,
  169: 37814, 170: 37814,
  171: 37815, 172: 37815,
  173: 37816, 174: 37816,
  175: 37817, 176: 37817,
  177: 37818, 178: 37818,
  179: 37819, 180: 37819,
  181: 37820, 182: 37820,
  183: 37821, 184: 37821,
};

export function formatFor(vkFormat, support) {
  const caps = support || {};
  if (vkFormat === VK_FORMAT.bc1 && caps.s3tc) return 33777;
  if (vkFormat === VK_FORMAT.bc3 && caps.s3tc) return 33779;
  if (vkFormat === VK_FORMAT.bc7 && caps.bptc) return 36492;
  if (ASTC[vkFormat] && caps.astc) return ASTC[vkFormat];
  return 0;
}

/**
 * WebGPU features are advertised on the renderer backend after init().
 * Passing only the canvas context leaves every compressed cap false.
 */
export function textureSupport(gl, renderer) {
  const backend = renderer && renderer.backend;
  if (backend && (backend.isWebGPUBackend === true || renderer.isWebGPURenderer === true) && typeof backend.hasFeature === "function") {
    const bc = backend.hasFeature("texture-compression-bc");
    return {
      s3tc: bc,
      bptc: bc,
      astc: backend.hasFeature("texture-compression-astc"),
      webgpu: true,
    };
  }
  if (!gl || typeof gl.getExtension !== "function") return { s3tc: false, bptc: false, astc: false };
  return {
    s3tc: Boolean(gl.getExtension("WEBGL_compressed_texture_s3tc")),
    bptc: Boolean(gl.getExtension("EXT_texture_compression_bptc")),
    astc: Boolean(gl.getExtension("WEBGL_compressed_texture_astc")),
  };
}
