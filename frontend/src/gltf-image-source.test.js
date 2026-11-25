import assert from "node:assert/strict";
import test from "node:test";

import { VK_FORMAT } from "./gpu-texture.js";
import {
  isKtx2OrPacked,
  loadForgeImageSource,
  loadTextureFromBytes,
  shouldUseGpuLoader,
  toBytes,
} from "./gltf-image-source.js";

function makeKtx2() {
  const ktx = new Uint8Array(120);
  ktx.set([0xab, 0x4b, 0x54, 0x58, 0x20, 0x32, 0x30, 0xbb, 0x0d, 0x0a, 0x1a, 0x0a]);
  const view = new DataView(ktx.buffer);
  view.setUint32(12, VK_FORMAT.bc7, true);
  view.setUint32(20, 4, true);
  view.setUint32(24, 4, true);
  view.setUint32(40, 1, true);
  view.setBigUint64(80, 104n, true);
  view.setBigUint64(88, 16n, true);
  ktx.fill(7, 104);
  return ktx;
}

function makePacked() {
  const ktx = makeKtx2();
  const jpeg = new Uint8Array([0xff, 0xd8, 1, 2]);
  const file = new Uint8Array(8 + ktx.length + jpeg.length);
  file.set([0x4f, 0x43, 0x46, 0x47]);
  new DataView(file.buffer).setUint32(4, ktx.length, true);
  file.set(ktx, 8);
  file.set(jpeg, 8 + ktx.length);
  return file;
}

const THREE = {
  Texture: class {
    constructor(image) {
      this.image = image;
      this.isTexture = true;
      this.needsUpdate = false;
    }
  },
};

test("isKtx2OrPacked detects KTX2 and OCFG, not a JPEG", () => {
  assert.equal(isKtx2OrPacked(makeKtx2()), true);
  assert.equal(isKtx2OrPacked(makePacked()), true);
  assert.equal(isKtx2OrPacked(new Uint8Array([0xff, 0xd8, 0xff, 0xe0])), false);
});

test("shouldUseGpuLoader follows image/ktx2 mime or packed bytes", () => {
  assert.equal(shouldUseGpuLoader({ mimeType: "image/ktx2" }), true);
  assert.equal(shouldUseGpuLoader({ mimeType: "image/png" }, makePacked()), true);
  assert.equal(shouldUseGpuLoader({ mimeType: "image/png" }, new Uint8Array([0xff, 0xd8, 1])), false);
});

test("loadTextureFromBytes hands a GPU loader the exact bufferView bytes, not a blob URL", async () => {
  const packed = makePacked();
  // The view sits inside a larger GLB-like buffer, the way a real bufferView does.
  const glb = new Uint8Array(32 + packed.length);
  glb.set(packed, 32);
  const view = glb.subarray(32);
  let seen;
  const loader = {
    parse(buffer, onLoad) {
      seen = toBytes(buffer);
      onLoad({ isTexture: true, name: "gpu", byteLength: seen.byteLength });
    },
  };
  const created = [];
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = (blob) => {
    created.push(blob);
    return "blob:http://127.0.0.1:47241/test";
  };
  URL.revokeObjectURL = () => {};
  try {
    const texture = await loadTextureFromBytes(loader, view, "image/ktx2", THREE);
    assert.equal(texture.name, "gpu");
    assert.equal(seen.byteLength, packed.byteLength);
    assert.deepEqual([...seen.slice(0, 4)], [0x4f, 0x43, 0x46, 0x47]);
    assert.equal(created.length, 0);
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});

test("loadTextureFromBytes revokes a fallback object URL only after the loader settles", async () => {
  const events = [];
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const originalBitmap = globalThis.createImageBitmap;
  delete globalThis.createImageBitmap;
  URL.createObjectURL = () => {
    events.push("create");
    return "blob:http://127.0.0.1:47241/late";
  };
  URL.revokeObjectURL = (url) => {
    events.push(`revoke:${url}`);
  };
  const loader = {
    load(url, onLoad) {
      events.push(`start:${url}`);
      setTimeout(() => onLoad({ isTexture: true, from: url }), 0);
    },
  };
  try {
    const texture = await loadTextureFromBytes(loader, new Uint8Array([1, 2, 3]), "image/png", THREE);
    assert.equal(texture.from, "blob:http://127.0.0.1:47241/late");
    assert.deepEqual(events, ["create", "start:blob:http://127.0.0.1:47241/late", "revoke:blob:http://127.0.0.1:47241/late"]);
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    if (originalBitmap) globalThis.createImageBitmap = originalBitmap;
  }
});

test("loadForgeImageSource passes the GPU loader to the stock path for a ktx2 bufferView", async () => {
  const seen = [];
  const gpuLoader = { parse() {}, load() {} };
  const imageLoader = { load() { throw new Error("ImageBitmapLoader should not load a ktx2 bufferView"); } };
  const parser = { json: { images: [{ bufferView: 0, mimeType: "image/ktx2" }] } };
  const texture = await loadForgeImageSource({
    parser,
    sourceIndex: 0,
    imageLoader,
    gpuLoader,
    original(index, loader) {
      seen.push({ index, isGpu: loader === gpuLoader });
      return Promise.resolve({ isTexture: true, ok: true });
    },
  });
  assert.deepEqual(seen, [{ index: 0, isGpu: true }]);
  assert.equal(texture.ok, true);
});

test("loadForgeImageSource keeps the image loader for a PNG bufferView", async () => {
  const seen = [];
  const gpuLoader = { parse() {}, load() {} };
  const imageLoader = { load() {} };
  const parser = { json: { images: [{ bufferView: 0, mimeType: "image/png" }] } };
  await loadForgeImageSource({
    parser,
    sourceIndex: 0,
    imageLoader,
    gpuLoader,
    original(index, loader) {
      seen.push(loader === imageLoader);
      return Promise.resolve({ isTexture: true });
    },
  });
  assert.deepEqual(seen, [true]);
});
