import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { Document, NodeIO } from "@gltf-transform/core";
import { EXTMeshGPUInstancing, EXTMeshoptCompression, KHRMaterialsUnlit } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer/decoder";

import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { bakeJob, clearSourceCache, localBakePath } from "./bake-scene-tiles.mjs";
import {
  actorKeyFromFeature,
  cellId,
  featureIdOnHit,
  shouldRequestTileBake,
  tileCounters,
  tileEligible,
  tileHides,
  tilesEnabled,
} from "./scene-tiles.js";

await MeshoptDecoder.ready;

async function writeTriangle(path, name) {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const material = doc.createMaterial(name);
  const prim = doc.createPrimitive()
    .setMaterial(material)
    .setAttribute("POSITION", doc.createAccessor().setType("VEC3").setArray(new Float32Array([
      0, 0, 0, 1, 0, 0, 0, 1, 0,
    ])).setBuffer(buffer))
    .setIndices(doc.createAccessor().setType("SCALAR").setArray(new Uint32Array([0, 1, 2])).setBuffer(buffer));
  const mesh = doc.createMesh("Tree").addPrimitive(prim);
  const scene = doc.createScene();
  scene.addChild(doc.createNode("lod0:Body:0").setMesh(mesh));
  scene.addChild(doc.createNode("lod1:Body:0").setMesh(mesh));
  doc.getRoot().setDefaultScene(scene);
  const io = new NodeIO();
  await io.write(path, doc);
}

function actor(featureId, x, glb) {
  return {
    featureId,
    gyaml: "Obj_Tree",
    glb,
    translation: [x, 0, 0],
    rotation: [0, 0, 0],
    scale: [1, 1, 1],
    parts: [{ family: "Body:0", image: "Tree_Alb", alpha: "opaque" }],
  };
}

test("static scenery is eligible and water, dynamic, and terrain are not", () => {
  const tree = { gyaml: "Obj_Tree", banc: "Banc/HyruleField_Child_Static.bcett.byml.zs", layer: "models", category: "other" };
  assert.equal(tileEligible(tree), true);
  assert.equal(tileEligible({ ...tree, banc: "Banc/HyruleField_Child_Dynamic.bcett.byml.zs" }), false);
  assert.equal(tileEligible({ ...tree, layer: "water" }), false);
  assert.equal(tileEligible({ ...tree, gyaml: "Obj_LakeHrl_WaterPlane" }), false);
  assert.equal(tileEligible({ ...tree, layer: "lights" }), false);
  assert.equal(tileEligible({ ...tree, category: "system" }), false);
  assert.equal(tileEligible({ ...tree, volume: true }), false);
  assert.equal(tilesEnabled({ query: "?tiles=1" }), true);
  assert.equal(tilesEnabled({ query: "" }), false);
  assert.equal(tilesEnabled({ setting: true }), true);
});

test("a tile hides its batched copy only after that tile has loaded", () => {
  assert.equal(tileHides({ tilesOn: true, held: true, opened: false, live: false }), false);
  assert.equal(tileHides({ tilesOn: false, held: true, opened: false, live: true }), false);
  assert.equal(tileHides({ tilesOn: true, held: true, opened: false, live: true }), true);
  assert.equal(tileHides({ tilesOn: true, held: true, opened: true, live: true }), false);
  assert.equal(tileHides({ tilesOn: true, held: false, opened: false, live: true }), false);
  assert.equal(cellId(10, -10), "0_-1");
  assert.equal(cellId(256, 256), "1_1");
});

function unboundConstructions(source) {
  const bound = new Set();
  for (const match of source.matchAll(/import\s*\{([^}]+)\}/g)) {
    for (const part of match[1].split(",")) {
      const name = part.split(/\s+as\s+/).pop().trim();
      if (name) bound.add(name);
    }
  }
  for (const match of source.matchAll(/(?:const|let|var)\s*\{([^}]+)\}/g)) {
    for (const part of match[1].split(",")) {
      const name = part.split(/\s*:\s*/).pop().trim();
      if (/^[A-Za-z_$][\w$]*$/.test(name)) bound.add(name);
    }
  }
  const missing = [];
  for (const match of source.matchAll(/\bnew\s+([A-Z][A-Za-z0-9_]*)/g)) {
    if (!bound.has(match[1])) missing.push(match[1]);
  }
  return missing;
}

test("the tile runtime has no unbound constructor names", () => {
  const source = readFileSync(new URL("./scene-tiles-runtime.js", import.meta.url), "utf8");
  assert.deepEqual(unboundConstructions(source), []);
});

test("a new tile cache does not sort with a null comparator", async () => {
  const { tileCache } = await import("./scene-tiles-runtime.js");
  const { LRUCache } = await import("3d-tiles-renderer/core");
  const fresh = new LRUCache();
  assert.equal(fresh.unloadPriorityCallback, null);
  assert.throws(() => [2, 1].sort(fresh.unloadPriorityCallback), /comparison function/);
  const cache = tileCache(LRUCache, fresh);
  assert.equal(typeof cache.unloadPriorityCallback, "function");
  assert.deepEqual([2, 1].sort(cache.unloadPriorityCallback), [2, 1]);
  const ranked = new LRUCache();
  ranked.unloadPriorityCallback = (a, b) => a.rank - b.rank;
  const copied = tileCache(LRUCache, ranked);
  assert.deepEqual(
    [{ rank: 2 }, { rank: 1 }].sort(copied.unloadPriorityCallback),
    [{ rank: 1 }, { rank: 2 }],
  );
});

test("the tile runtime constructs GLTFLoader from its own imports", async () => {
  const { installTileLoaders } = await import("./scene-tiles-runtime.js");
  const { LoadingManager } = await import("three");
  const { MeshoptDecoder } = await import("meshoptimizer/decoder");
  await MeshoptDecoder.ready;
  const manager = new LoadingManager();
  let added = null;
  const original = manager.addHandler.bind(manager);
  manager.addHandler = (pattern, loader) => {
    added = loader;
    return original(pattern, loader);
  };
  const release = installTileLoaders({ manager }, null);
  assert.equal(typeof release, "function");
  assert.equal(added && added.constructor && added.constructor.name, "GLTFLoader");
  release();
  assert.equal(manager.getHandler("cell.glb"), null);
});

test("a failed bake is not requested again until the checkbox or a save", () => {
  assert.equal(shouldRequestTileBake({ failed: true, reason: "settle" }), false);
  assert.equal(shouldRequestTileBake({ failed: true, reason: "benchmark" }), false);
  assert.equal(shouldRequestTileBake({ failed: true, reason: "toggle" }), true);
  assert.equal(shouldRequestTileBake({ failed: true, reason: "save" }), true);
  assert.equal(shouldRequestTileBake({ failed: false, reason: "settle" }), true);
});

test("a Windows drive path is not joined onto the cwd or turned into a file URL", () => {
  const windows = localBakePath("C:\\Users\\gline\\src\\ocarina-forge-gh\\cache\\ab\\file.glb");
  assert.equal(windows.startsWith("file:"), false);
  assert.equal(windows.includes("/agent/"), false);
  assert.match(windows, /^C:\\Users\\gline\\src\\ocarina-forge-gh\\cache\\ab\\file\.glb$/);
});

test("the baker selftest prints its own result", () => {
  const script = fileURLToPath(new URL("../tools/bake-scene-tiles.mjs", import.meta.url));
  const run = spawnSync(process.execPath, [script, "--selftest"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    encoding: "utf8",
    timeout: 60000,
  });
  assert.equal(run.status, 0, run.stderr || run.stdout);
  const line = run.stdout.trim().split("\n").pop();
  const report = JSON.parse(line);
  assert.equal(report.ok, true);
  assert.equal(report.sources, 2);
  assert.deepEqual(report.featureIds, [7, 11]);
  assert.equal(report.buffers.leaf, 1);
  assert.ok(report.buffers.hlod <= 1);
});

test("a hit on _feature_id_0 resolves to that actor", () => {
  const values = [7, 11, 11];
  const hit = {
    face: { a: 1 },
    object: {
      geometry: {
        index: { getX: (index) => index },
        getAttribute(name) {
          if (name !== "_feature_id_0") return null;
          return { getX: (index) => values[index] };
        },
      },
    },
  };
  assert.equal(featureIdOnHit(hit), 11);
  const keys = new Map([[11, "tree-b"]]);
  assert.equal(actorKeyFromFeature(featureIdOnHit(hit), (id) => keys.get(id)), "tree-b");
  assert.deepEqual(tileCounters({ enabled: false, loaded: 4, bakeMs: 3 }), {
    tilesLoaded: 0,
    tilesVisible: 0,
    tilesBytes: 0,
    bakeMs: 3,
  });
});

test("two source documents bake to one buffer in the cell and the HLOD", async () => {
  const root = await mkdtemp(join(tmpdir(), "forge-tiles-"));
  try {
    const tree = join(root, "tree.glb");
    const rock = join(root, "rock.glb");
    await writeTriangle(tree, "bark");
    await writeTriangle(rock, "stone");
    clearSourceCache();
    const job = {
      cells: [{
        id: "0_0",
        leaf: join(root, "leaf.glb"),
        hlod: join(root, "hlod.glb"),
        meta: join(root, "meta.json"),
        actors: [
          { ...actor(7, 0, tree), gyaml: "Obj_Tree" },
          { ...actor(11, 8, rock), gyaml: "Obj_Rock" },
        ],
      }],
    };
    const report = await bakeJob(job);
    assert.deepEqual(report.cells[0].actors, [7, 11]);
    const reader = new NodeIO()
      .registerExtensions([EXTMeshGPUInstancing, EXTMeshoptCompression, KHRMaterialsUnlit])
      .registerDependencies({ "meshopt.decoder": MeshoptDecoder });
    const leaf = await reader.readBinary(await readFile(job.cells[0].leaf));
    const hlod = await reader.readBinary(await readFile(job.cells[0].hlod));
    assert.equal(leaf.getRoot().listBuffers().length, 1);
    assert.ok(hlod.getRoot().listBuffers().length <= 1);
    const ids = new Set();
    for (const node of leaf.getRoot().listNodes()) {
      const batch = node.getExtension("EXT_mesh_gpu_instancing");
      const instancedIds = batch?.getAttribute("_FEATURE_ID_0")?.getArray();
      if (instancedIds) for (const value of instancedIds) ids.add(Math.round(value));
      const mesh = node.getMesh();
      if (!mesh) continue;
      for (const prim of mesh.listPrimitives()) {
        const array = prim.getAttribute("_FEATURE_ID_0")?.getArray();
        if (!array) continue;
        for (let index = 0; index < array.length; index += 1) ids.add(Math.round(array[index]));
      }
    }
    assert.ok(ids.has(7) && ids.has(11), `feature ids ${[...ids].join(",")}`);
  } finally {
    clearSourceCache();
    await rm(root, { recursive: true, force: true });
  }
});

test("two bakes of the same actors are byte-identical and keep feature ids", async () => {
  const root = await mkdtemp(join(tmpdir(), "forge-tiles-"));
  try {
    const glb = join(root, "tree.glb");
    await writeTriangle(glb, "bark");
    clearSourceCache();
    async function once(dir) {
      const job = {
        cells: [{
          id: "0_0",
          leaf: join(dir, "leaf.glb"),
          hlod: join(dir, "hlod.glb"),
          meta: join(dir, "meta.json"),
          actors: [actor(11, 10, glb), actor(7, 0, glb)],
        }],
      };
      const report = await bakeJob(job);
      const leaf = await readFile(job.cells[0].leaf);
      const meta = JSON.parse(await readFile(job.cells[0].meta, "utf8"));
      return { leaf, meta, report };
    }
    const first = await once(join(root, "a"));
    clearSourceCache();
    const second = await once(join(root, "b"));
    assert.deepEqual(Buffer.from(first.leaf), Buffer.from(second.leaf));
    assert.deepEqual(first.meta.actors, [7, 11]);
    assert.ok(first.meta.instanced >= 2);
    assert.ok(first.report.ms >= 0);

    const reader = new NodeIO()
      .registerExtensions([EXTMeshGPUInstancing, EXTMeshoptCompression, KHRMaterialsUnlit])
      .registerDependencies({ "meshopt.decoder": MeshoptDecoder });
    const doc = await reader.readBinary(first.leaf);
    const ids = new Set();
    for (const node of doc.getRoot().listNodes()) {
      const batch = node.getExtension("EXT_mesh_gpu_instancing");
      const instancedIds = batch?.getAttribute("_FEATURE_ID_0")?.getArray();
      if (instancedIds) for (const value of instancedIds) ids.add(Math.round(value));
      const mesh = node.getMesh();
      if (!mesh) continue;
      for (const prim of mesh.listPrimitives()) {
        const array = prim.getAttribute("_FEATURE_ID_0")?.getArray();
        if (!array) continue;
        for (let index = 0; index < array.length; index += 1) ids.add(Math.round(array[index]));
      }
    }
    assert.ok(ids.has(7), `feature ids ${[...ids].join(",")}`);
    assert.ok(ids.has(11), `feature ids ${[...ids].join(",")}`);
    const hlod = await readFile(join(root, "a", "hlod.glb"));
    assert.ok(hlod.byteLength > 0);
    assert.ok(doc.getRoot().listBuffers().length <= 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
