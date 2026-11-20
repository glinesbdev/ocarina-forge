import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

// Banc Rotate and BFRES Euler flag 0x1000 are extrinsic XYZ: R = Rz · Ry · Rx.
// three.js names that order ZYX. Order XYZ is intrinsic and lays the ivy flat.

test("ivy rotate hangs +Z down and round-trips through the gizmo quaternion", () => {
  const samples = [
    [1.5708, -1.29154, 0],
    [1.5708, -1.309, 0],
  ];
  for (const [rx, ry, rz] of samples) {
    const pivot = new THREE.Object3D();
    pivot.position.set(29.5, 109.0, -21.75);
    pivot.rotation.set(rx, ry, rz, "ZYX");
    pivot.updateMatrix();
    const tip = new THREE.Vector3(0, 0, 3).applyMatrix4(pivot.matrix);
    assert.ok(Math.abs(tip.y - (109.0 - 3)) < 0.02, `tip y ${tip.y}`);
    assert.ok(Math.abs(tip.x - 29.5) < 0.02, `tip x ${tip.x}`);
    assert.ok(Math.abs(tip.z - -21.75) < 0.02, `tip z ${tip.z}`);
    const normal = new THREE.Vector3(0, 1, 0).applyQuaternion(pivot.quaternion);
    assert.ok(Math.abs(normal.y) < 0.02);
    assert.ok(Math.abs(normal.x - Math.sin(ry)) < 0.02);
    assert.ok(Math.abs(normal.z - Math.cos(ry)) < 0.02);

    const edited = new THREE.Object3D();
    edited.rotation.order = "ZYX";
    edited.quaternion.copy(pivot.quaternion);
    const back = edited.rotation;
    assert.ok(Math.abs(back.x - rx) < 1e-5);
    assert.ok(Math.abs(back.y - ry) < 1e-5);
    assert.ok(Math.abs(back.z - rz) < 1e-5);
  }
});

test("a yaw-only gate rotate is the same in XYZ and ZYX", () => {
  const yaw = -1.13506;
  const zyx = new THREE.Object3D();
  zyx.rotation.set(0, yaw, 0, "ZYX");
  const xyz = new THREE.Object3D();
  xyz.rotation.set(0, yaw, 0, "XYZ");
  const a = new THREE.Vector3(1, 0, 0).applyQuaternion(zyx.quaternion);
  const b = new THREE.Vector3(1, 0, 0).applyQuaternion(xyz.quaternion);
  assert.ok(a.distanceTo(b) < 1e-5);
});
