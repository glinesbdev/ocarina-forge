/**
 * Upload a packed texture without decoding it on the main thread.
 *
 * OCFG is a KTX2 plus the JPEG or PNG three already knows how to turn into an
 * ImageBitmap. The KTX2 is the native BC1, BC3, BC7, or ASTC blocks. three
 * r170's KTX2 loader rejects those vkFormats (it only raw-loads a short list,
 * and a Basis file is vkFormat 0). A vkFormat of 0 is handed back to that
 * loader. Anything else the GPU cannot sample uses the trailing image.
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

export function readKtx2(bytes) {
  if (!bytes || bytes.length < 104) return null;
  for (let i = 0; i < KTX_MAGIC.length; i += 1) {
    if (bytes[i] !== KTX_MAGIC[i]) return null;
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const vkFormat = view.getUint32(12, true);
  const width = view.getUint32(20, true);
  const height = view.getUint32(24, true);
  // Header is 80 bytes. Mip 0's byteOffset and byteLength follow it.
  const offset = Number(view.getBigUint64(80, true));
  const length = Number(view.getBigUint64(88, true));
  if (offset < 0 || length <= 0 || offset + length > bytes.length) return null;
  return { vkFormat, width, height, data: bytes.subarray(offset, offset + length) };
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

export function textureSupport(gl) {
  if (!gl || !gl.getExtension) return { s3tc: false, bptc: false, astc: false };
  return {
    s3tc: Boolean(gl.getExtension("WEBGL_compressed_texture_s3tc")),
    bptc: Boolean(gl.getExtension("EXT_texture_compression_bptc")),
    astc: Boolean(gl.getExtension("WEBGL_compressed_texture_astc")),
  };
}
