/**
 * r170's GLTFLoader.loadTexture only looks at `image.uri` when choosing a
 * handler. Embedded bufferView images (no uri) always go through
 * ImageBitmapLoader/TextureLoader, even when mimeType is image/ktx2.
 *
 * After models-53, packed KTX2/OCFG maps often live in the GLB as bufferViews.
 * Some of those are already reached via KHR_texture_basisu, which passes the
 * GPU/KTX2 loader into loadImageSource. Others (plain `texture.source` pointing
 * at an image/ktx2 bufferView) do not. ImageBitmapLoader then fails on the
 * object URL and logs `THREE.GLTFLoader: Couldn't load texture blob:http://.../<uuid>`,
 * and the material is left without a map.
 *
 * This wrapper keeps the stock loadImageSource (blob URL, revoke after settle)
 * but hands those images the GPU loader, the same path KHR_texture_basisu uses.
 */

import { readKtx2, splitPacked } from "./gpu-texture.js";

const KTX2_MIME = /^(image\/ktx2|image\/ktx|image\/basis|image\/ocfg)(;|$)/i;

export function toBytes(data) {
  if (!data) return new Uint8Array();
  if (data instanceof Uint8Array) return data;
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  return new Uint8Array(data);
}

export function isKtx2OrPacked(data) {
  const bytes = toBytes(data);
  const packed = splitPacked(bytes);
  return Boolean(readKtx2(packed.ktx));
}

export function shouldUseGpuLoader(imageDef, data) {
  const mime = String((imageDef && imageDef.mimeType) || "");
  if (KTX2_MIME.test(mime)) return true;
  return data != null && isKtx2OrPacked(data);
}

function detachedBuffer(bytes) {
  const view = toBytes(bytes);
  if (view.byteOffset === 0 && view.byteLength === view.buffer.byteLength) return view.buffer;
  return view.slice().buffer;
}

function asTexture(result, THREE) {
  if (!result || result.isTexture) return result;
  const texture = new THREE.Texture(result);
  texture.needsUpdate = true;
  return texture;
}

/**
 * Decode a texture from already-held bytes. Loaders with parse() (the GPU
 * KTX2/OCFG loader) never see a blob URL. Other loaders get an object URL that
 * is revoked only after their load callback or error.
 */
export function loadTextureFromBytes(loader, data, mimeType, THREE) {
  const bytes = toBytes(data);
  return new Promise((resolve, reject) => {
    if (loader && typeof loader.parse === "function") {
      try {
        loader.parse(detachedBuffer(bytes), (texture) => resolve(asTexture(texture, THREE)), reject);
      } catch (error) {
        reject(error);
      }
      return;
    }
    const blob = new Blob([bytes], { type: mimeType || "application/octet-stream" });
    if (typeof createImageBitmap === "function") {
      createImageBitmap(blob, { imageOrientation: "none", premultiplyAlpha: "none" }).then((image) => {
        resolve(asTexture(image, THREE));
      }, reject);
      return;
    }
    if (!loader || typeof loader.load !== "function") {
      reject(new Error("no texture loader"));
      return;
    }
    const url = URL.createObjectURL(blob);
    const finish = (callback) => (value) => {
      URL.revokeObjectURL(url);
      callback(value);
    };
    try {
      loader.load(url, finish(resolve), undefined, finish(reject));
    } catch (error) {
      URL.revokeObjectURL(url);
      reject(error);
    }
  });
}

/**
 * Drop-in replacement for GLTFParser.loadImageSource. External /api/model-textures/
 * URIs stay on the shared-image path. Everything else uses the stock loader, but
 * bufferView (and other) KTX2/OCFG images get the GPU loader instead of
 * ImageBitmapLoader. The stock path's blob URL is then fetched by our
 * loader.load() and revoked only after parse's onLoad, which is after the bytes
 * are already in memory.
 */
export function loadForgeImageSource({
  parser,
  sourceIndex,
  imageLoader,
  gpuLoader,
  original,
  loadExternal,
}) {
  const image = parser.json && parser.json.images && parser.json.images[sourceIndex];
  if (!image) return original(sourceIndex, imageLoader);
  const uri = image.uri;
  if (typeof loadExternal === "function" && uri && String(uri).startsWith("/api/model-textures/")) {
    return loadExternal(sourceIndex, imageLoader, image);
  }
  const loader = gpuLoader && shouldUseGpuLoader(image) ? gpuLoader : imageLoader;
  return original(sourceIndex, loader);
}
