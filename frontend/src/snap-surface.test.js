import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { chooseSnap, selectSnapHits, translateAfterSnap } from "./snap.js";
import { columnHits } from "./snap-surface.js";

function floor(y, width = 8) {
  const geometry = new THREE.PlaneGeometry(width, width);
  geometry.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  mesh.position.y = y;
  mesh.updateWorldMatrix(true, false);
  return mesh;
}

function roomBatch() {
  const geometry = new THREE.PlaneGeometry(8, 8);
  geometry.rotateX(-Math.PI / 2);
  const mesh = new THREE.BatchedMesh(
    2,
    geometry.getAttribute("position").count,
    geometry.getIndex().count,
    new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }),
  );
  const geometryId = mesh.addGeometry(geometry);
  const floorId = mesh.addInstance(geometryId);
  const propId = mesh.addInstance(geometryId);
  mesh.setMatrixAt(floorId, new THREE.Matrix4().setPosition(0, 2.5, 0));
  mesh.setMatrixAt(propId, new THREE.Matrix4().setPosition(0, 6, 0));
  mesh.matrix.identity();
  mesh.matrixWorld.identity();
  mesh.updateWorldMatrix(true, false);
  return { mesh, floorId, propId };
}

test("a room floor that is not y=0 is the snap, and a miss does not become 0", () => {
  const mesh = floor(2.5);
  const hits = columnHits([mesh], 0, 6, 0);
  assert.ok(hits.length >= 1);
  assert.ok(hits.every((hit) => Math.abs(hit.point.y - 2.5) < 1e-4));
  assert.equal(chooseSnap(6, hits.map((hit) => ({ y: hit.point.y }))), 2.5);
  assert.equal(chooseSnap(6, columnHits([], 0, 6, 0)), null);
  const missed = translateAfterSnap([0, 6, 0], null, { axis: "X", phase: "release", snapOn: true });
  assert.equal(missed.snapped, false);
  assert.equal(missed.translate[1], 6);
});

test("a BatchedMesh floor is hit per instance, and the dragged instance is excluded", () => {
  const { mesh, floorId, propId } = roomBatch();
  const hits = columnHits([mesh], 0, 8, 0, (hit) => hit.batchId !== propId);
  assert.ok(hits.some((hit) => hit.batchId === floorId));
  assert.equal(hits.some((hit) => hit.batchId === propId), false);
  const y = chooseSnap(8, hits.map((hit) => ({ y: hit.point.y })));
  assert.ok(Math.abs(y - 2.5) < 1e-3);
});

test("a static map mesh whose raycast was disabled still hits", () => {
  const mesh = floor(4);
  mesh.raycast = () => {};
  const hits = columnHits([mesh], 1, 9, -1);
  assert.ok(hits.length >= 1);
  assert.ok(hits.every((hit) => Math.abs(hit.point.y - 4) < 1e-3));
});

test("collision wins over the room model only when the ground missed", () => {
  assert.equal(selectSnapHits({
    ground: [{ y: -0.05 }],
    collision: [{ y: 3 }],
    models: [{ y: 2.5 }],
  })[0].y, -0.05);
  assert.equal(selectSnapHits({
    ground: [],
    collision: [{ y: 3 }],
    models: [{ y: 2.5 }],
  })[0].y, 3);
  assert.equal(selectSnapHits({
    ground: [],
    collision: [],
    models: [{ y: 2.5 }],
  })[0].y, 2.5);
  assert.equal(selectSnapHits({ ground: [{ y: Number.NaN }], collision: [], models: [] }).length, 0);
});

test("an actor below every surface snaps up to the nearest one", () => {
  assert.equal(chooseSnap(0, [{ y: 2.5 }, { y: 8 }]), 2.5);
});
