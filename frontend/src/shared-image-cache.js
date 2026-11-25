/**
 * Shared-image cache for /api/model-textures/ URIs. Callers clone the cached
 * texture, so every clone shares one Source.
 *
 * After the resident budget releases CPU copies, that Source holds a closed
 * ImageBitmap (0x0) or emptied mips. A new clone is a new GPU texture, and
 * three sizes it from the image: log2(0) gives mipLevelCount -Infinity and
 * GPUDevice.createTexture throws. A released hit is dropped and reloaded.
 */

export function textureCpuReleased(texture) {
  if (!texture) return false;
  if (texture.userData?.forgeCpuReleased || texture.source?.userData?.forgeCpuReleased) return true;
  const image = texture.image;
  if (texture.isCompressedTexture) {
    const levels = texture.mipmaps || [];
    return levels.length > 0 && levels.every((level) => !level || !level.data || level.data.byteLength === 0);
  }
  // A closed ImageBitmap reports 0x0.
  return Boolean(image) && typeof image.close === "function" && (!image.width || !image.height);
}

export function createSharedImageCache({ limit = 128, released = textureCpuReleased } = {}) {
  const entries = new Map();

  function start(uri, load) {
    const pending = Promise.resolve()
      .then(load)
      .catch((error) => {
        if (entries.get(uri) === pending) entries.delete(uri);
        throw error;
      });
    entries.set(uri, pending);
    if (entries.size > limit) entries.delete(entries.keys().next().value);
    return pending;
  }

  function get(uri, load) {
    const hit = entries.get(uri);
    if (!hit) return start(uri, load);
    entries.delete(uri);
    entries.set(uri, hit);
    return hit.then((texture) => {
      if (!released(texture)) return texture;
      if (entries.get(uri) === hit) entries.delete(uri);
      const again = entries.get(uri);
      return again || start(uri, load);
    });
  }

  return { get, entries };
}
