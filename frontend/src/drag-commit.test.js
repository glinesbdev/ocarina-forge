import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";
import { readFileSync } from "node:fs";
import { commitTranslateDrag } from "./drag-commit.js";

function treeBatch(y) {
  const geometry = new THREE.ConeGeometry(0.4, 1.6, 4);
  geometry.translate(0, 0.8, 0);
  const mesh = new THREE.BatchedMesh(1, geometry.getAttribute("position").count, geometry.getIndex().count, new THREE.MeshBasicMaterial());
  const geometryId = mesh.addGeometry(geometry);
  const index = mesh.addInstance(geometryId);
  const matrix = new THREE.Matrix4().setPosition(0, y, 0);
  mesh.setMatrixAt(index, matrix);
  const slotMatrix = matrix.clone();
  return {
    mesh,
    slots: [{ actor: "tree", mesh, index, shown: true, matrix: slotMatrix }],
  };
}

function readY(mesh, index) {
  const matrix = new THREE.Matrix4();
  mesh.getMatrixAt(index, matrix);
  return matrix.elements[13];
}

test("a snap that drops the pivot also writes the batched instance before returning", () => {
  const { mesh, slots } = treeBatch(2);
  const position = { x: 1, y: 2, z: -4 };
  const matrix = new THREE.Matrix4();
  // The ground reply is intentionally unused. The old bug updated the pivot
  // and left the instance at the lifted height until the next Y drag.
  const posed = commitTranslateDrag({
    position,
    axis: "XZ",
    phase: "release",
    snapOn: true,
    surfaceY: 0,
    slots,
    actorKey: "tree",
    matrix,
  });
  assert.equal(posed.snapped, true);
  assert.equal(position.y, 0);
  assert.equal(readY(mesh, slots[0].index), 0);
  assert.equal(slots[0].matrix.elements[13], 0);
  assert.equal(posed.written, 1);
});

test("an explicit Y lift keeps the height and still writes that height into the instance", () => {
  const { mesh, slots } = treeBatch(0);
  const position = { x: 0, y: 1.25, z: -6 };
  const matrix = new THREE.Matrix4();
  const posed = commitTranslateDrag({
    position,
    axis: "Y",
    phase: "release",
    snapOn: true,
    surfaceY: 0,
    slots,
    actorKey: "tree",
    matrix,
  });
  assert.equal(posed.hold, true);
  assert.equal(posed.snapped, false);
  assert.equal(position.y, 1.25);
  assert.equal(readY(mesh, slots[0].index), 1.25);
});

test("a hidden batched slot still receives the snapped matrix", () => {
  const { mesh, slots } = treeBatch(2);
  slots[0].shown = false;
  const position = { x: 0, y: 2, z: 0 };
  const matrix = new THREE.Matrix4();
  const posed = commitTranslateDrag({
    position,
    axis: "X",
    phase: "move",
    snapOn: true,
    surfaceY: -0.2,
    slots,
    actorKey: "tree",
    matrix,
  });
  assert.equal(posed.snapped, true);
  assert.equal(position.y, -0.2);
  assert.ok(Math.abs(slots[0].matrix.elements[13] + 0.2) < 1e-5);
  assert.ok(Math.abs(readY(mesh, slots[0].index) + 0.2) < 1e-5);
  assert.equal(posed.written, 1);
});

test("the viewport commits the instance before it waits on a ground reply", () => {
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const body = viewport.slice(viewport.indexOf("function maybeSnap"), viewport.indexOf("function spawnPoint"));
  assert.match(body, /commitTranslateDrag\(/);
  const commitAt = body.indexOf("commitTranslateDrag(");
  const groundAt = body.indexOf("return requestGround");
  assert.ok(commitAt !== -1 && groundAt !== -1 && commitAt < groundAt);
  const release = viewport.slice(
    viewport.indexOf('transform.addEventListener("dragging-changed"'),
    viewport.indexOf("let keyWindow"),
  );
  assert.match(release, /maybeSnap\(entry, "release", axis\)/);
  assert.match(release, /applySelectedActorTransform\(entry, \{ commit: false \}\)/);
  const snapAt = release.indexOf('maybeSnap(entry, "release", axis)');
  const syncAt = release.indexOf("applySelectedActorTransform(entry, { commit: false })");
  const waitAt = release.indexOf("typeof pending.then");
  assert.ok(snapAt < syncAt && syncAt < waitAt);
});
