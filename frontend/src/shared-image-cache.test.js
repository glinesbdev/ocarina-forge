import assert from "node:assert/strict";
import test from "node:test";

import { createSharedImageCache, textureCpuReleased } from "./shared-image-cache.js";

test("a closed bitmap or emptied mips count as released", () => {
  assert.equal(textureCpuReleased({ image: { width: 0, height: 0, close() {} } }), true);
  assert.equal(textureCpuReleased({ image: { width: 4, height: 4, close() {} } }), false);
  assert.equal(textureCpuReleased({ isCompressedTexture: true, mipmaps: [{ data: new Uint8Array(0) }] }), true);
  assert.equal(textureCpuReleased({ isCompressedTexture: true, mipmaps: [{ data: new Uint8Array(16) }] }), false);
  assert.equal(textureCpuReleased({ userData: {}, source: { userData: { forgeCpuReleased: true } } }), true);
});

test("a released cached texture is reloaded instead of cloned", async () => {
  const cache = createSharedImageCache();
  let loads = 0;
  const load = async () => {
    loads += 1;
    return { id: loads, userData: {}, source: { userData: {} }, image: { width: 8, height: 8, close() {} } };
  };
  const first = await cache.get("/api/model-textures/a", load);
  assert.equal((await cache.get("/api/model-textures/a", load)).id, 1);
  // The resident budget dropped the CPU copy.
  first.source.userData.forgeCpuReleased = true;
  first.image.width = 0;
  first.image.height = 0;
  const again = await cache.get("/api/model-textures/a", load);
  assert.equal(again.id, 2);
  assert.equal(loads, 2);
  assert.equal(textureCpuReleased(again), false);
});

test("a failed load is not cached", async () => {
  const cache = createSharedImageCache();
  await assert.rejects(cache.get("u", async () => { throw new Error("nope"); }));
  assert.equal(cache.entries.has("u"), false);
});
