import assert from "node:assert/strict";
import test from "node:test";

import { sceneResultCurrent, unloadSceneResources } from "./scene-unload.js";

function disposed() {
  return { disposed: false, dispose() { this.disposed = true; } };
}

test("unload drops per-map objects and keeps shared ones", () => {
  const geometry = disposed();
  geometry.userData = { forgeBatch: null, boundsTree: { id: "bvh" } };
  geometry.disposeBoundsTree = function disposeBoundsTree() { this.boundsFreed = true; };
  const material = disposed();
  material.map = disposed();
  material.userData = { mossMap: disposed(), forgeLit: { map: disposed() } };
  const shared = disposed();
  shared.userData = { forgeViewportShared: true };
  const mesh = disposed();
  mesh.geometry = disposed();
  mesh.material = material;
  mesh.userData = { forgePickTexture: disposed(), forgeLinkedGeometries: [geometry] };
  mesh.removeFromParent = function removeFromParent() { this.removed = true; };
  geometry.userData.forgeBatch = { mesh, geometryId: 1 };
  const terrain = {
    removed: false,
    removeFromParent() { this.removed = true; },
    traverse(visit) {
      visit({ geometry: disposed(), material: shared, userData: {} });
      visit({ geometry: disposed(), material: disposed(), userData: { forgePickTexture: disposed() } });
    },
  };
  const target = disposed();
  target.texture = disposed();
  const surface = { freed: false, free() { this.freed = true; } };
  const worker = { terminated: false, terminate() { this.terminated = true; } };
  let listened = 0;
  const actors = new Map([["kokiri", { id: "kokiri" }]]);
  const templates = new Map([["/api/models/Obj_Tree/model.glb", { parts: [geometry] }]]);
  const hits = [{ x: 1, z: 2, y: 3 }];
  const selection = { current: "kokiri" };
  const gizmo = { detached: false, detach() { this.detached = true; } };
  const picker = { resetCount: 0, reset() { this.resetCount += 1; } };
  const overlay = { removed: false, remove() { this.removed = true; } };
  const keptMaterial = disposed();
  const scene = {
    generation: 4,
    controller: new AbortController(),
    keep: new Set([keptMaterial]),
    workers: [worker],
    listeners: [() => { listened += 1; }],
    meshes: [mesh],
    geometries: [geometry],
    materials: [material, keptMaterial],
    textures: [material.map],
    targets: [target],
    objects: [terrain],
    linkedGeometries: [geometry],
    surface,
    picker,
    gizmo,
    selection,
    maps: [actors, templates],
    lists: [hits],
    overlays: [overlay],
  };
  const previous = scene.controller;
  const stale = scene.generation;

  unloadSceneResources(scene);

  assert.equal(sceneResultCurrent(scene, stale), false);
  assert.equal(sceneResultCurrent(scene, scene.generation), true);
  assert.equal(previous.signal.aborted, true);
  assert.equal(scene.controller.signal.aborted, false);
  assert.equal(worker.terminated, true);
  assert.equal(listened, 1);
  assert.equal(mesh.disposed, true);
  assert.equal(mesh.removed, true);
  assert.equal(mesh.geometry.disposed, true);
  assert.equal(mesh.userData.forgePickTexture, null);
  assert.equal(geometry.userData.forgeBatch, null);
  assert.equal(geometry.disposed, true);
  assert.equal(geometry.boundsFreed, true);
  assert.equal(geometry.boundsTree, undefined);
  assert.equal(material.disposed, true);
  assert.equal(material.map.disposed, true);
  assert.equal(material.userData.mossMap.disposed, true);
  assert.equal(material.userData.forgeLit.map.disposed, true);
  assert.equal(keptMaterial.disposed, false);
  assert.equal(shared.disposed, false);
  assert.equal(target.disposed, true);
  assert.equal(target.texture.disposed, true);
  assert.equal(terrain.removed, true);
  assert.equal(surface.freed, true);
  assert.equal(scene.surface, null);
  assert.equal(picker.resetCount, 1);
  assert.equal(gizmo.detached, true);
  assert.equal(selection.current, null);
  assert.equal(actors.size, 0);
  assert.equal(templates.size, 0);
  assert.deepEqual(hits, []);
  assert.equal(overlay.removed, true);
  assert.deepEqual(scene.meshes, []);
  assert.deepEqual(scene.geometries, []);
  assert.deepEqual(scene.materials, []);
  assert.deepEqual(scene.textures, []);
  assert.deepEqual(scene.targets, []);
  assert.deepEqual(scene.objects, []);
  assert.deepEqual(scene.linkedGeometries, []);
  assert.deepEqual(scene.workers, []);
  assert.deepEqual(scene.listeners, []);
  assert.equal(scene.overlays.length, 0);
});

test("a late result from the previous generation is not current", () => {
  const scene = { generation: 1, controller: new AbortController() };
  const token = scene.generation;
  assert.equal(sceneResultCurrent(scene, token), true);
  unloadSceneResources(scene);
  assert.equal(sceneResultCurrent(scene, token), false);
  const next = scene.generation;
  assert.equal(sceneResultCurrent(scene, next), true);
  scene.controller.abort();
  assert.equal(sceneResultCurrent(scene, next), false);
});
