import assert from "node:assert/strict";
import test from "node:test";

import { chooseTexture, createGpuTextureLoader, formatFor, gpuTexturesEnabled, minFilterFor, readKtx2, splitPacked, textureSupport,
  releaseTextureCpu,
  repairCompressedTexture, textureGpuReady, VK_FORMAT } from "./gpu-texture.js";

function makePacked() {
  const ktx = new Uint8Array(104);
  ktx.set([0xab, 0x4b, 0x54, 0x58, 0x20, 0x32, 0x30, 0xbb, 0x0d, 0x0a, 0x1a, 0x0a]);
  const view = new DataView(ktx.buffer);
  view.setUint32(12, VK_FORMAT.bc7, true);
  view.setUint32(20, 4, true);
  view.setUint32(24, 4, true);
  view.setUint32(40, 1, true);
  view.setBigUint64(80, 104n, true);
  view.setBigUint64(88, 16n, true);
  const blocks = new Uint8Array(16).fill(7);
  const jpeg = new Uint8Array([0xff, 0xd8, 1, 2]);
  const whole = new Uint8Array(ktx.length + blocks.length);
  whole.set(ktx);
  whole.set(blocks, ktx.length);
  const file = new Uint8Array(8 + whole.length + jpeg.length);
  file.set([0x4f, 0x43, 0x46, 0x47]);
  new DataView(file.buffer).setUint32(4, whole.length, true);
  file.set(whole, 8);
  file.set(jpeg, 8 + whole.length);
  return { file, jpeg };
}

test("a packed texture keeps the KTX2 and the bitmap fallback", () => {
  const { file, jpeg } = makePacked();
  const split = splitPacked(file);
  assert.deepEqual([...split.fallback], [...jpeg]);
  const info = readKtx2(split.ktx);
  assert.equal(info.vkFormat, VK_FORMAT.bc7);
  assert.equal(info.width, 4);
  assert.equal(info.levels.length, 1);
  assert.equal(info.levels[0].data.length, 16);
  assert.equal(formatFor(info.vkFormat, { bptc: true }), 36492);
  assert.equal(formatFor(info.vkFormat, {}), 0);
  assert.equal(formatFor(0, { bptc: true }), 0);
  assert.equal(formatFor(167, { astc: true }), 37813);
  assert.equal(minFilterFor(1, 256, 256), 1006);
  assert.equal(minFilterFor(9, 256, 256), 1008);
  assert.equal(gpuTexturesEnabled("", null), true);
  assert.equal(gpuTexturesEnabled("?textures=bitmap", "1"), false);
  assert.equal(gpuTexturesEnabled("", "0"), false);
  assert.equal(gpuTexturesEnabled("?gpuTextures=1", "0"), true);
});

test("WebGPU caps choose compressed BC7 without a WebGL probe", () => {
  const { file } = makePacked();
  const choice = chooseTexture(file, {
    enabled: true,
    caps: { bptc: true },
    gl: {}, // GPUCanvasContext has no compressedTexImage2D
  });
  assert.equal(choice.kind, "compressed");
  assert.equal(choice.format, 36492);
  assert.equal(choice.levels.length, 1);
});

test("a canvas context without extensions reports no compressed caps", () => {
  assert.deepEqual(textureSupport({}), { s3tc: false, bptc: false, astc: false });
});

test("textureSupport reads WebGPU compression features from the renderer backend", () => {
  const renderer = {
    isWebGPURenderer: true,
    backend: {
      isWebGPUBackend: true,
      hasFeature(name) {
        return name === "texture-compression-bc";
      },
    },
  };
  assert.deepEqual(textureSupport({}, renderer), { s3tc: true, bptc: true, astc: false, webgpu: true });
});


test("releaseTextureCpu drops mip and image buffers in place and is idempotent", () => {
  const data = new Uint8Array(16).fill(7);
  const image = { width: 4, height: 4, data: new Uint8Array(8).fill(3) };
  const level = { width: 4, height: 4, data };
  const mipmaps = [level];
  const texture = {
    userData: {},
    source: { userData: {}, data: null },
    mipmaps,
    image,
    needsUpdate: false,
  };
  assert.equal(releaseTextureCpu(texture), true);
  assert.equal(texture.mipmaps, mipmaps);
  assert.equal(texture.mipmaps[0], level);
  assert.equal(texture.image, image);
  assert.equal(texture.mipmaps[0].data.byteLength, 0);
  assert.equal(texture.image.data.byteLength, 0);
  assert.equal(texture.needsUpdate, false);
  assert.equal(texture.generateMipmaps, false);
  assert.equal(texture.userData.forgeCpuReleased, true);
  assert.equal(texture.source.userData.forgeCpuReleased, true);
  assert.equal(data.byteLength, 16);
  assert.equal(releaseTextureCpu(texture), false);
});

test("repairCompressedTexture does not dirty a texture after CPU release", () => {
  const texture = {
    isCompressedTexture: true,
    userData: { forgeCpuReleased: true },
    mipmaps: [{ width: 4, height: 4, data: new Uint8Array(0) }],
    image: { width: 4, height: 4 },
    needsUpdate: false,
  };
  repairCompressedTexture(texture);
  assert.equal(texture.needsUpdate, false);
});

test("parse settles without handing non-basis bytes to the Basis loader", async () => {
  const THREE = {
    SRGBColorSpace: "srgb",
    CompressedTexture: class {
      constructor(mipmaps, width, height, format) {
        this.isTexture = true;
        this.isCompressedTexture = true;
        this.mipmaps = mipmaps;
        this.image = { width, height };
        this.format = format;
      }
    },
    Texture: class {
      constructor(image) {
        this.isTexture = true;
        this.image = image;
      }
    },
  };
  const { file } = makePacked();
  let basisCalls = 0;
  const loader = createGpuTextureLoader({
    THREE,
    gl: null,
    caps: { s3tc: true, bptc: true, astc: false },
    enabled: () => true,
    basisParse: () => { basisCalls += 1; },
  });
  const texture = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("parse did not settle")), 1000);
    loader.parse(file.buffer, (value) => { clearTimeout(timer); resolve(value); }, (error) => { clearTimeout(timer); reject(error); });
  });
  assert.equal(texture.isCompressedTexture, true);
  assert.equal(texture.format, 36492);
  assert.equal(basisCalls, 0);

  const raw = splitPacked(file).ktx;
  const rawBuffer = raw.slice().buffer;
  const fallbackLoader = createGpuTextureLoader({
    THREE,
    gl: null,
    caps: {},
    enabled: () => true,
    basisParse: () => { basisCalls += 1; },
  });
  const originalBitmap = globalThis.createImageBitmap;
  delete globalThis.createImageBitmap;
  const warns = [];
  const originalWarn = console.warn;
  console.warn = (message) => warns.push(String(message));
  try {
    const result = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("bitmap fallback parse did not settle")), 1000);
      fallbackLoader.parse(rawBuffer, (texture) => {
        clearTimeout(timer);
        resolve(texture);
      }, (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
    assert.equal(result.userData.forgePlainFallback, true);
    assert.equal(warns.length, 1);
    assert.match(warns[0], /no bitmap fallback/);
    await new Promise((resolve, reject) => {
      fallbackLoader.parse(rawBuffer, resolve, reject);
    });
    assert.equal(warns.length, 1);
  } finally {
    console.warn = originalWarn;
    if (originalBitmap) globalThis.createImageBitmap = originalBitmap;
  }
  assert.equal(basisCalls, 0);
});

test("a jpeg or png manifest file decodes and does not reject", async () => {
  const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);
  const choice = chooseTexture(jpeg, { enabled: true, caps: { bptc: true }, gl: {} });
  assert.equal(choice.kind, "bitmap");
  assert.deepEqual([...choice.bytes], [...jpeg]);
  const THREE = {
    SRGBColorSpace: "srgb",
    Texture: class {
      constructor(image) {
        this.isTexture = true;
        this.image = image;
      }
    },
    DataTexture: class {
      constructor(data, width, height) {
        this.isTexture = true;
        this.image = { data, width, height };
        this.userData = {};
      }
    },
  };
  const warns = [];
  const originalWarn = console.warn;
  console.warn = (message) => warns.push(String(message));
  const originalBitmap = globalThis.createImageBitmap;
  const originalFetch = globalThis.fetch;
  globalThis.createImageBitmap = async (blob) => {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    assert.equal(bytes[0], 0xff);
    assert.equal(bytes[1], 0xd8);
    return { width: 6, height: 2 };
  };
  globalThis.fetch = async (url) => ({
    ok: true,
    arrayBuffer: async () => (String(url).endsWith(".jpg") ? jpeg.buffer : jpeg.slice().buffer),
  });
  try {
    const loader = createGpuTextureLoader({
      THREE,
      gl: null,
      caps: {},
      enabled: () => true,
      basisParse() { throw new Error("basis"); },
    });
    const loaded = await new Promise((resolve, reject) => {
      loader.load("/api/model-textures/abc.jpg", resolve, undefined, reject);
    });
    assert.equal(loaded.isTexture, true);
    assert.equal(loaded.image.width, 6);
    assert.equal(loaded.userData && loaded.userData.forgePlainFallback, undefined);
    assert.equal(warns.length, 0);
    const again = await new Promise((resolve, reject) => {
      loader.load("/api/model-textures/abc.jpg", resolve, undefined, reject);
    });
    assert.equal(again.image.width, 6);
    assert.equal(warns.length, 0);
  } finally {
    console.warn = originalWarn;
    globalThis.fetch = originalFetch;
    if (originalBitmap) globalThis.createImageBitmap = originalBitmap;
    else delete globalThis.createImageBitmap;
  }
});

test("releaseTextureCpu leaves a size stub after closing an ImageBitmap so mipLevelCount stays finite", () => {
  const image = {
    width: 64,
    height: 32,
    close() {
      this.width = 0;
      this.height = 0;
    },
  };
  const source = { userData: {}, data: image };
  const texture = {
    userData: {},
    source,
    get image() { return this.source.data; },
    generateMipmaps: true,
    needsUpdate: false,
    version: 1,
    mipmaps: [],
  };
  assert.equal(releaseTextureCpu(texture), true);
  assert.equal(image.width, 0);
  assert.equal(texture.image.width, 64);
  assert.equal(texture.image.height, 32);
  assert.equal(texture.generateMipmaps, false);
  assert.equal(texture.needsUpdate, false);
  assert.equal(texture.version, 1);
  assert.equal(source.dataReady, false);
  const levels = Math.floor(Math.log2(Math.max(texture.image.width, texture.image.height))) + 1;
  assert.equal(Number.isFinite(levels) && levels >= 1, true);
  assert.equal(levels, 7);
});

test("texture bytes stay until the GPU image exists", () => {
  const map = { isTexture: true };
  assert.equal(textureGpuReady(null, map), false);
  assert.equal(textureGpuReady({ get: () => ({}) }, map), false);
  assert.equal(textureGpuReady({ get: () => ({ texture: {}, isDefaultTexture: true }) }, map), false);
  assert.equal(textureGpuReady({ get: () => ({ texture: { id: 1 } }) }, map), true);
});
