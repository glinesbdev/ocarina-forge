import assert from "node:assert/strict";
import test from "node:test";

import {
  PICK_FRAGMENT_LIB,
  PICK_FRAGMENT_OUT,
  PICK_VERTEX_WORLD,
  PICK_LAYER,
  PREVIEW_PICK_LAYER,
  TERRAIN_PICK_LAYER,
  WEBGPU_PICK_SPAN,
  decodeId24,
  decodeUnit24,
  encodeId24,
  encodeUnit24,
  groupsFromPick,
  layerIntersects,
  ndcFromClient,
  paddedPickWidth,
  pickLayerMask,
  pickMask,
  pickStrip,
  pickTextureSize,
  resolvePickTexture,
  pixelFromNdc,
  pointFromDistance,
  quantizeUnorm8,
  readPickTexel,
  uniqueIdsFromBuffer,
  writePickTexel,
} from "./gpu-pick.js";

test("actor ids survive an 8-bit unorm round trip", () => {
  for (let i = 0; i < 256; i += 1) assert.equal(quantizeUnorm8(i), i);
  for (const id of [0, 1, 255, 256, 65535, 0x010203, 16777215]) {
    const [r, g, b, a] = encodeId24(id);
    assert.deepEqual([quantizeUnorm8(r), quantizeUnorm8(g), quantizeUnorm8(b), quantizeUnorm8(a)], [r, g, b, a]);
    assert.equal(decodeId24(r, g, b, a), id);
  }
  assert.equal(decodeId24(1, 2, 3, 0), 0);
});

test("distance packing is little-endian and a clear pixel is a miss", () => {
  const [r, g, b, a] = encodeUnit24(1);
  assert.deepEqual([r, g, b, a], [255, 255, 255, 255]);
  assert.equal(decodeUnit24(0, 0, 0, 0), null);
  assert.equal(decodeUnit24(...encodeUnit24(0)), 0);
  const mid = decodeUnit24(...encodeUnit24(0.5));
  assert.ok(Math.abs(mid - 0.5) < 1 / 16777215);
});

test("instance ids are stored one per texel", () => {
  const count = 3;
  const size = pickTextureSize(count);
  const data = new Float32Array(size * size * 4);
  writePickTexel(data, size, 0, 4);
  writePickTexel(data, size, 2, 9);
  assert.equal(readPickTexel(data, size, 0), 4);
  assert.equal(readPickTexel(data, size, 1), 0);
  assert.equal(readPickTexel(data, size, 2), 9);
});

test("the cursor pixel is inside a readback strip that fits the drawing buffer", () => {
  const wide = pickStrip(100, 1920, WEBGPU_PICK_SPAN);
  assert.equal(wide.span, 64);
  assert.equal(wide.origin, 100);
  assert.equal(wide.localX, 0);
  const edge = pickStrip(1900, 1920, 64);
  assert.equal(edge.origin + edge.span <= 1920, true);
  assert.equal(edge.origin + edge.localX, 1900);
  const narrow = pickStrip(10, 30, 64);
  assert.equal(narrow.span, 30);
  assert.equal(narrow.localX, 10);
  assert.equal(paddedPickWidth(narrow.span, true), 64);
  assert.equal(paddedPickWidth(1, false), 1);
});

test("ndc and the view offset use the same top-left pixel", () => {
  const ndc = ndcFromClient(75, 25, { left: 0, top: 0, width: 100, height: 50 });
  assert.equal(ndc.x, 0.5);
  assert.equal(ndc.y, 0);
  const pixel = pixelFromNdc(ndc.x, ndc.y, 100, 50);
  assert.equal(pixel.x, 75);
  assert.equal(pixel.yFromTop, 25);
});

test("a marquee buffer collapses to unique actor ids in reading order", () => {
  const bytes = new Uint8Array(8 * 4);
  bytes.set(encodeId24(2), 0);
  bytes.set(encodeId24(2), 4);
  bytes.set(encodeId24(0), 8);
  bytes.set(encodeId24(7), 12);
  assert.deepEqual(uniqueIdsFromBuffer(bytes, 2, 2), [2, 7]);
});

test("gpu hits stay in front-to-back order and volume edges stay on the cpu list", () => {
  const groups = groupsFromPick(["tree", "sign"], [
    { id: "area", edge: true, distance: 4 },
    { id: "tree", edge: false, distance: 1 },
  ]);
  assert.deepEqual(groups.solids, ["tree", "sign"]);
  assert.deepEqual(groups.volumes, ["area"]);
  assert.deepEqual(groups.edges, ["area"]);
});

test("the gizmo's layer is outside every pick camera", () => {
  const gizmo = 1 << 0;
  for (const mask of [
    pickLayerMask(),
    pickLayerMask({ preview: true }),
    pickLayerMask({ terrain: true, preview: true }),
  ]) {
    assert.equal(layerIntersects(gizmo, mask), false);
  }
  assert.equal(layerIntersects(1 << PICK_LAYER, pickLayerMask()), true);
  assert.equal(layerIntersects(1 << PREVIEW_PICK_LAYER, pickLayerMask()), false);
  assert.equal(layerIntersects(1 << PREVIEW_PICK_LAYER, pickLayerMask({ preview: true })), true);
  assert.equal(layerIntersects(1 << TERRAIN_PICK_LAYER, pickLayerMask({ terrain: true })), true);
});

test("a pick mask is a three.js texture, including a render target's color texture", () => {
  const texture = { isTexture: true, name: "albedo" };
  const alpha = { isTexture: true, name: "alpha" };
  const target = { isRenderTarget: true, texture };
  assert.equal(resolvePickTexture(texture), texture);
  assert.equal(resolvePickTexture(target), texture);
  assert.equal(resolvePickTexture(null), null);
  assert.equal(resolvePickTexture({ texture: {} }), null);
  assert.deepEqual(pickMask({ alphaTest: 0.4, map: target }), { alphaTest: 0.4, map: texture, channel: "a" });
  assert.deepEqual(pickMask({ alphaTest: 0.4, alphaMap: alpha }), { alphaTest: 0.4, map: alpha, channel: "g" });
  assert.deepEqual(pickMask({ alphaTest: 0, map: texture }), { alphaTest: 0, map: null, channel: "a" });
  assert.deepEqual(pickMask({ alphaTest: 0.4 }), { alphaTest: 0.4, map: null, channel: "a" });
  assert.deepEqual(pickMask({ alphaTest: 0.4, map: { url: "placeholder" } }), { alphaTest: 0.4, map: null, channel: "a" });
});

test("the id shader packs little-endian and discards a miss", () => {
  const shader = `${PICK_VERTEX_WORLD}\n${PICK_FRAGMENT_LIB}\n${PICK_FRAGMENT_OUT}`;
  assert.match(shader, /n & 255/);
  assert.match(shader, /n >> 8/);
  assert.match(shader, /n >> 16/);
  assert.match(shader, /discard/);
  assert.match(shader, /getIndirectIndex\( gl_DrawID \)/);
  assert.match(shader, /gl_InstanceID/);
  assert.match(shader, /vForgeInstance >= 0\.0/);
});

test("a camera distance rebuilds the world point", () => {
  const origin = { x: 1, y: 2, z: 3 };
  const direction = { x: 0, y: -1, z: 0 };
  const target = { x: 0, y: 0, z: 0, copy(value) { this.x = value.x; this.y = value.y; this.z = value.z; return this; }, addScaledVector(vector, scale) { this.x += vector.x * scale; this.y += vector.y * scale; this.z += vector.z * scale; return this; } };
  const point = pointFromDistance(origin, direction, 5, target);
  assert.equal(point.y, -3);
});
