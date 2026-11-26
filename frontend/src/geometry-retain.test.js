import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { createCpuPark } from "./cpu-park.js";
import { ensureGeometryCpu, geometryAttributeView, noteGeometryBatch, parkGeometryAttributes, releaseGeometryCpu, unparkGeometryAttributes } from "./geometry-retain.js";

function triangle() {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 1, 2, 3, 4, 5, 6, 7, 8]), 3));
  geometry.setIndex([0, 1, 2]);
  geometry.computeBoundingSphere();
  return geometry;
}

test("a part copy is released only after a batch owns it, then restored", () => {
  const geometry = triangle();
  const before = geometry.getAttribute("position").array.byteLength + geometry.getIndex().array.byteLength;
  assert.equal(releaseGeometryCpu(geometry), 0);
  assert.equal(geometry.getAttribute("position").count, 3);

  const mesh = new THREE.BatchedMesh(1, 3, 3, new THREE.MeshBasicMaterial());
  const id = mesh.addGeometry(geometry);
  noteGeometryBatch(geometry, mesh, id);
  const released = releaseGeometryCpu(geometry);
  assert.equal(released, before);
  assert.equal(geometry.getAttribute("position").count, 0);
  assert.equal(geometry.getIndex().count, 0);
  assert.deepEqual(geometryAttributeView(geometry).attributes.position, { itemSize: 3, count: 3, normalized: false });
  assert.equal(geometryAttributeView(geometry).indexCount, 3);

  assert.equal(ensureGeometryCpu(geometry), true);
  assert.deepEqual(Array.from(geometry.getAttribute("position").array), [0, 1, 2, 3, 4, 5, 6, 7, 8]);
  assert.deepEqual(Array.from(geometry.getIndex().array), [0, 1, 2]);
  assert.equal(geometry.userData.forgeCpuReleased, false);
  mesh.dispose();
});

test("water and other keep-cpu geometry stays resident", () => {
  const geometry = triangle();
  geometry.userData.forgeKeepCpu = true;
  const mesh = new THREE.BatchedMesh(1, 3, 3, new THREE.MeshBasicMaterial());
  noteGeometryBatch(geometry, mesh, mesh.addGeometry(geometry));
  assert.equal(releaseGeometryCpu(geometry), 0);
  assert.equal(geometry.getAttribute("position").count, 3);
  mesh.dispose();
});

test("a second geometry in the same batch restores its own range", () => {
  const first = triangle();
  const second = new THREE.BufferGeometry();
  second.setAttribute("position", new THREE.BufferAttribute(new Float32Array([9, 8, 7, 6, 5, 4, 3, 2, 1]), 3));
  second.setIndex([2, 0, 1]);
  const mesh = new THREE.BatchedMesh(2, 6, 6, new THREE.MeshBasicMaterial());
  noteGeometryBatch(first, mesh, mesh.addGeometry(first));
  noteGeometryBatch(second, mesh, mesh.addGeometry(second));
  releaseGeometryCpu(first);
  releaseGeometryCpu(second);
  assert.equal(ensureGeometryCpu(second), true);
  assert.deepEqual(Array.from(second.getAttribute("position").array), [9, 8, 7, 6, 5, 4, 3, 2, 1]);
  assert.deepEqual(Array.from(second.getIndex().array), [2, 0, 1]);
  mesh.dispose();
});

test("parked normals and uvs leave the geometry and come back", async () => {
  const geometry = triangle();
  geometry.setAttribute("normal", new THREE.BufferAttribute(new Float32Array(9).fill(1), 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 0, 1]), 2));
  const stored = new Map();
  const park = {
    park(id, arrays) {
      stored.set(id, arrays);
      return Promise.resolve(0);
    },
    take(id) {
      const arrays = stored.get(id) || null;
      stored.delete(id);
      return Promise.resolve(arrays);
    },
  };
  const bytes = await parkGeometryAttributes(geometry, park, { ready: () => true });
  assert.ok(bytes > 0);
  assert.equal(geometry.getAttribute("position").array.length, 9);
  assert.equal(geometry.getAttribute("uv").array.length, 0);
  assert.equal(geometry.getAttribute("normal").array.length, 0);
  assert.equal(await unparkGeometryAttributes(geometry, park), true);
  assert.deepEqual(Array.from(geometry.getAttribute("uv").array), [0, 0, 1, 0, 0, 1]);
  assert.equal(geometry.getAttribute("normal").array.length, 9);
  assert.equal(geometry.userData.forgeParked, null);
});

function sharedGeometry() {
  const verts = 1024;
  const pos = verts * 3;
  const nrm = verts * 3;
  const uv = verts * 2;
  const buffer = new ArrayBuffer((pos + nrm + uv) * 4);
  const geometry = new THREE.BufferGeometry();
  const position = new Float32Array(buffer, 0, pos);
  const normal = new Float32Array(buffer, pos * 4, nrm);
  const coords = new Float32Array(buffer, (pos + nrm) * 4, uv);
  position.fill(1);
  normal.fill(2);
  coords.fill(3);
  geometry.setAttribute("position", new THREE.BufferAttribute(position, 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(coords, 2));
  geometry.setIndex([0, 1, 2]);
  return { geometry, sharedBytes: buffer.byteLength, normal, coords };
}

test("parking a shared vertex buffer keeps one copy of each attribute", async () => {
  const { geometry, sharedBytes, normal, coords } = sharedGeometry();
  const park = createCpuPark({ worker: false });
  const bytes = await parkGeometryAttributes(geometry, park, { ready: () => true });
  const parked = park.retainedBytes();
  const position = geometry.getAttribute("position").array;
  assert.equal(bytes, normal.byteLength + coords.byteLength);
  assert.equal(parked, normal.byteLength + coords.byteLength);
  assert.equal(position.byteLength + parked, sharedBytes);
  assert.equal(geometry.getAttribute("normal").array.byteLength, 0);
  assert.equal(geometry.getAttribute("uv").array.byteLength, 0);
  assert.notEqual(position.buffer, normal.buffer);
  assert.equal(position[0], 1);
  assert.equal(geometry.getAttribute("position").count, 1024);
  const start = position.byteLength + geometry.getIndex().array.byteLength;
  for (let i = 0; i < 4; i += 1) {
    await unparkGeometryAttributes(geometry, park);
    await parkGeometryAttributes(geometry, park, { ready: () => true });
    assert.equal(park.retainedBytes(), normal.byteLength + coords.byteLength);
    assert.equal(geometry.getAttribute("position").array.byteLength + park.retainedBytes(), sharedBytes);
  }
  await unparkGeometryAttributes(geometry, park);
  assert.equal(park.retainedBytes(), 0);
  const live = ["position", "normal", "uv"].reduce((sum, name) => sum + geometry.getAttribute(name).array.byteLength, 0);
  assert.equal(live, sharedBytes);
  assert.equal(geometry.getAttribute("position").array.byteLength + geometry.getIndex().array.byteLength, start);
  assert.equal(geometry.getAttribute("normal").array[0], 2);
  assert.equal(geometry.getAttribute("uv").array[0], 3);
});

test("an owned attribute is moved, not copied, into the park", async () => {
  const geometry = triangle();
  const normal = new Float32Array(9).fill(4);
  const uv = new Float32Array([0, 0, 1, 0, 0, 1]);
  geometry.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  const park = createCpuPark({ worker: false });
  await parkGeometryAttributes(geometry, park, { ready: () => true });
  assert.equal(park.retainedBytes(), normal.byteLength + uv.byteLength);
  const back = await park.take(geometry.userData.forgeParkId);
  assert.equal(back[0], normal);
  assert.equal(back[1], uv);
});

test("releasing an interleaved part drops the shared buffer once", () => {
  const data = new Float32Array(600);
  const buffer = new THREE.InterleavedBuffer(data, 6);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.InterleavedBufferAttribute(buffer, 3, 0));
  geometry.setAttribute("normal", new THREE.InterleavedBufferAttribute(buffer, 3, 3));
  geometry.userData.forgeBatch = { mesh: {}, geometryId: 0 };
  const released = releaseGeometryCpu(geometry);
  assert.equal(buffer.array.byteLength, 0);
  assert.equal(released, data.byteLength);
});
