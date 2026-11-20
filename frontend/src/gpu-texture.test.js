import assert from "node:assert/strict";
import test from "node:test";

import { formatFor, gpuTexturesEnabled, minFilterFor, readKtx2, splitPacked, VK_FORMAT } from "./gpu-texture.js";

test("a packed texture keeps the KTX2 and the bitmap fallback", () => {
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
