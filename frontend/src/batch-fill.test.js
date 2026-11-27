import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import * as THREE from "three";

import { createBatchFiller, doubledCapacity, simulateBatchArrival } from "./batch-fill.js";

function geom(id, verts = 10, indices = 12) {
  return {
    id,
    getAttribute(name) {
      return name === "position" ? { count: verts } : null;
    },
    getIndex() {
      return { count: indices };
    },
  };
}

function fakeMesh() {
  let nextGeo = 0;
  const mesh = {
    _instanceInfo: [],
    maxInstanceCount: 0,
    maxVertexCount: 0,
    maxIndexCount: 0,
    setInstanceCount(count) {
      this.maxInstanceCount = count;
    },
    setGeometrySize(vertices, indices) {
      this.maxVertexCount = vertices;
      this.maxIndexCount = indices;
    },
    addGeometry() {
      const id = nextGeo;
      nextGeo += 1;
      return id;
    },
    addInstance() {
      const id = this._instanceInfo.length;
      this._instanceInfo.push({ active: true });
      return id;
    },
    deleteInstance(id) {
      this._instanceInfo[id].active = false;
    },
  };
  return mesh;
}

function fillerFrom(createMesh) {
  return createBatchFiller({
    createMesh(spec) {
      const mesh = createMesh(spec);
      mesh.maxInstanceCount = spec.instances;
      mesh.maxVertexCount = spec.vertices;
      mesh.maxIndexCount = spec.indices;
      return mesh;
    },
  });
}

test("capacity doubles until the next add fits", () => {
  assert.equal(doubledCapacity(8, 8), 8);
  assert.equal(doubledCapacity(8, 9), 16);
  assert.equal(doubledCapacity(64, 100000), 131072);
  assert.equal(doubledCapacity(0, 3), 4);
});

test("staged arrivals used to build one mesh per key per arrival", () => {
  const keys = Array.from({ length: 50 }, (_, index) => `key-${index}`);
  const arrivals = Array.from({ length: 400 }, (_, index) => [keys[index % 50], keys[(index + 7) % 50]]);
  const simulated = simulateBatchArrival(arrivals);
  assert.equal(simulated.before, 800);
  assert.equal(simulated.after, 50);
  assert.equal(simulated.keys, 50);
});

test("one batch key constructs one mesh and further copies append", () => {
  let constructed = 0;
  const filler = fillerFrom(() => {
    constructed += 1;
    return fakeMesh();
  });
  const roof = geom("roof");
  const wall = geom("wall");
  const first = filler.append("albedo", null, [
    { geometry: roof },
    { geometry: roof },
    { geometry: wall },
  ]);
  assert.equal(constructed, 1);
  assert.equal(first.batch.mesh.maxInstanceCount, 8);
  assert.equal(filler.stats().batches, 1);
  const again = filler.append("albedo", null, [
    { geometry: roof },
    { geometry: wall },
    { geometry: roof },
  ]);
  assert.equal(constructed, 1);
  assert.equal(again.batch.mesh, first.batch.mesh);
  assert.equal(again.fresh, false);
  assert.equal(filler.get("albedo").active, 6);
  const other = filler.append("cutout", null, [{ geometry: roof }]);
  assert.equal(constructed, 2);
  assert.equal(first.batch.mesh !== other.batch.mesh, true);
  assert.equal(filler.stats().keys, 2);
});

test("a grow that throws opens one overflow mesh and leaves the original", () => {
  let constructed = 0;
  const filler = fillerFrom(() => {
    constructed += 1;
    const mesh = fakeMesh();
    mesh.setInstanceCount = () => {
      throw new Error("Maximum item count reached");
    };
    return mesh;
  });
  const geometry = geom("prop");
  const first = filler.append("shared", null, [{ geometry }]);
  assert.equal(constructed, 1);
  assert.equal(first.batch.instanceCap, 8);
  const overflow = filler.append("shared", null, Array.from({ length: 8 }, () => ({ geometry })));
  assert.equal(overflow.overflow, true);
  assert.equal(constructed, 2);
  assert.equal(filler.get("shared").mesh, first.batch.mesh);
  assert.equal(filler.get("shared").active, 1);
  assert.equal(filler.stats().overflow, 1);
  assert.equal(filler.stats().batches, 2);
});

test("moving one key builds one new mesh and leaves the others", () => {
  let constructed = 0;
  const filler = fillerFrom(() => {
    constructed += 1;
    return fakeMesh();
  });
  const geometries = ["a", "b", "c", "d"].map((name) => geom(name));
  const meshes = [];
  for (const geometry of geometries) {
    const added = filler.append(geometry.id, null, [{ geometry }]);
    meshes.push(added.batch.mesh);
  }
  assert.equal(constructed, 4);
  const moved = filler.get("a").mesh.addInstance;
  const instanceId = filler.get("a").active === 1 ? 0 : -1;
  filler.removeInstance("a", instanceId);
  const emptied = filler.dropIfEmpty("a");
  assert.equal(emptied.mesh, meshes[0]);
  const next = filler.append("e", null, [{ geometry: geometries[0] }]);
  assert.equal(constructed, 5);
  assert.equal(next.batch.mesh !== meshes[1], true);
  assert.equal(filler.get("b").mesh, meshes[1]);
  assert.equal(filler.get("c").mesh, meshes[2]);
  assert.equal(filler.get("d").mesh, meshes[3]);
  assert.equal(filler.get("a"), null);
  assert.equal(typeof moved, "function");
});

function slab(vertices) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(vertices * 3), 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(new Float32Array(vertices * 3), 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(vertices * 2), 2));
  const index = new Uint32Array(vertices);
  for (let i = 0; i < vertices; i += 1) index[i] = i;
  geometry.setIndex(new THREE.BufferAttribute(index, 1));
  return geometry;
}

test("a real BatchedMesh grows by doubling and one key stays one mesh", () => {
  let constructed = 0;
  const filler = createBatchFiller({
    createMesh({ instances, vertices, indices, material }) {
      constructed += 1;
      return new THREE.BatchedMesh(instances, vertices, Math.max(indices, 1), material);
    },
  });
  const material = new THREE.MeshBasicMaterial();
  const box = new THREE.BoxGeometry(1, 1, 1);
  const actors = 80;
  const beforeStarted = performance.now();
  const separate = [];
  for (let i = 0; i < actors; i += 1) {
    separate.push(new THREE.BatchedMesh(1, 64, 64, material));
    const id = separate[i].addGeometry(box);
    separate[i].addInstance(id);
  }
  const beforeMs = performance.now() - beforeStarted;
  for (const mesh of separate) mesh.dispose();

  const afterStarted = performance.now();
  for (let i = 0; i < actors; i += 1) {
    filler.append("house", material, [{ geometry: box }]);
  }
  const afterMs = performance.now() - afterStarted;
  assert.equal(constructed, 1);
  assert.equal(filler.stats().batches, 1);
  assert.equal(filler.get("house").active, actors);
  assert.equal(filler.get("house").mesh.maxInstanceCount >= actors, true);
  assert.equal(filler.get("house").mesh.maxInstanceCount <= actors * 2, true);
  const grown = filler.get("house").mesh.maxInstanceCount;

  const wide = slab(40000);
  const beforeWide = constructed;
  const wideResult = filler.append("house", material, [{ geometry: wide }]);
  assert.ok(constructed === beforeWide || constructed === beforeWide + 1);
  assert.equal(filler.get("house").active, wideResult.overflow ? actors : actors + 1);
  const wideVertices = wideResult.overflow ? null : wideResult.batch.mesh._maxVertexCount;
  if (!wideResult.overflow) assert.equal(wideVertices > 65535, true);
  for (const batch of filler.list()) batch.mesh.dispose();
  wide.dispose();
  box.dispose();
  material.dispose();
  assert.ok(beforeMs >= 0 && afterMs >= 0);
  assert.equal(grown, 128);
  assert.equal(wideVertices, 65536);
});

test("texture arrival restages the affected actors instead of every batch", () => {
  const source = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const start = source.indexOf("function finishTextureEdit");
  const end = source.indexOf("function loadOwnedTexture");
  const body = source.slice(start, end);
  assert.equal(body.includes("queueRebuild("), false);
  assert.match(body, /rebuildBatches\(affected\)/);
  const rebuildStart = source.indexOf("function rebuildBatches");
  const rebuildEnd = source.indexOf("function queueRebuild");
  const rebuild = source.slice(rebuildStart, rebuildEnd);
  assert.equal(rebuild.includes("clearBatches("), false);
  const attached = rebuild.indexOf("placeBatchMesh(mesh, batch.key)");
  assert.ok(attached >= 0 && attached < rebuild.lastIndexOf("removeFromParent"));
  assert.equal(source.includes("function syncPickIds"), false);
  assert.match(source, /prepareGpuPick\(\)/);
});
