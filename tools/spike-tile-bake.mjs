/**
 * SPIKE. Not a product path. Times a gltf-transform tile bake on synthetic
 * scenery shaped like one Field cell. Writes nothing under the repo. No game
 * files. Run: node tools/spike-tile-bake.mjs
 */
import { spawnSync } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const root = await modules();
const { Document, NodeIO } = await import(pathToFileURL(join(root, "@gltf-transform/core/dist/index.modern.js")).href);
const { EXTMeshGPUInstancing, EXTMeshoptCompression, KHRMaterialsUnlit } = await import(pathToFileURL(join(root, "@gltf-transform/extensions/dist/index.modern.js")).href);
const { dedup, instance, join: joinMeshes, meshopt, prune, simplify } = await import(pathToFileURL(join(root, "@gltf-transform/functions/dist/functions.modern.js")).href);
const meshoptMod = await import(pathToFileURL(join(root, "meshoptimizer/meshopt_decoder.mjs")).href);
const encoderMod = await import(pathToFileURL(join(root, "meshoptimizer/meshopt_encoder.js")).href);
const simplifierMod = await import(pathToFileURL(join(root, "meshoptimizer/meshopt_simplifier.js")).href);
await Promise.all([
  meshoptMod.MeshoptDecoder.ready,
  encoderMod.MeshoptEncoder.ready,
  simplifierMod.MeshoptSimplifier.ready,
]);

const io = new NodeIO()
  .registerExtensions([EXTMeshGPUInstancing, EXTMeshoptCompression, KHRMaterialsUnlit])
  .registerDependencies({
    "meshopt.decoder": meshoptMod.MeshoptDecoder,
    "meshopt.encoder": encoderMod.MeshoptEncoder,
    "meshopt.simplifier": simplifierMod.MeshoptSimplifier,
  });

const cells = [
  { name: "sparse", unique: 24, uniqueTris: 800, materials: 4, props: 2, copies: 20, propTris: 200 },
  { name: "dense", unique: 80, uniqueTris: 2500, materials: 12, props: 4, copies: 30, propTris: 400 },
];

const rows = [];
for (const cell of cells) {
  rows.push(await bakeCell(cell));
}
const rebake = await bakeCell(cells[1]);
rebake.name = "dense-rebake";
rows.push(rebake);

const bc7 = timeBc7();
const report = {
  spike: "tile-bake",
  note: "Synthetic cells. This machine has no game dump, so these are not Field tiles.",
  rows,
  bc7,
};
console.log(JSON.stringify(report, null, 2));

async function bakeCell(spec) {
  const started = performance.now();
  const doc = new Document();
  const buffer = doc.createBuffer();
  const scene = doc.createScene();
  doc.getRoot().setDefaultScene(scene);
  const unlit = doc.createExtension(KHRMaterialsUnlit);
  const materials = [];
  for (let index = 0; index < spec.materials; index += 1) {
    materials.push(doc.createMaterial(`mat-${index}`)
      .setBaseColorFactor([0.2 + index * 0.05, 0.55, 0.3, 1])
      .setExtension("KHR_materials_unlit", unlit.createUnlit())
      .setExtras({ forge: index === 0 ? "moss" : "static" }));
  }

  let actor = 1;
  for (let index = 0; index < spec.unique; index += 1) {
    const material = materials[index % materials.length];
    const mesh = gridMesh(doc, buffer, material, spec.uniqueTris, actor, index * 3, 0);
    scene.addChild(doc.createNode(`actor-${actor}`).setMesh(mesh).setTranslation([index % 16, 0, Math.floor(index / 16)]));
    actor += 1;
  }

  const propIds = [];
  for (let prop = 0; prop < spec.props; prop += 1) {
    const material = materials[prop % materials.length];
    const mesh = gridMesh(doc, buffer, material, spec.propTris, 0, 0, 0);
    const ids = [];
    for (let copy = 0; copy < spec.copies; copy += 1) {
      ids.push(actor);
      scene.addChild(doc.createNode(`prop-${prop}-${copy}`)
        .setMesh(mesh)
        .setTranslation([copy * 1.5, 0, 40 + prop * 2]));
      actor += 1;
    }
    propIds.push({ mesh, ids });
  }

  const built = performance.now();
  // Instance repeated props before join. Join bakes every copy into the
  // material mesh, and then nothing is left to instance.
  await doc.transform(dedup(), instance({ min: 5 }));
  const instanced = doc.getRoot().listNodes().filter((node) => node.getExtension("EXT_mesh_gpu_instancing"));
  for (const node of instanced) {
    const batch = node.getExtension("EXT_mesh_gpu_instancing");
    const count = batch.getAttribute("TRANSLATION")?.getCount() || 0;
    const ids = new Float32Array(count);
    for (let index = 0; index < count; index += 1) ids[index] = 1000 + index;
    batch.setAttribute("_FEATURE_ID_0", doc.createAccessor()
      .setType("SCALAR")
      .setArray(ids)
      .setBuffer(buffer));
  }
  await doc.transform(joinMeshes({ keepMeshes: false }), prune());
  const joined = performance.now();
  const instancedAfterJoin = doc.getRoot().listNodes().filter((node) => node.getExtension("EXT_mesh_gpu_instancing")).length;

  const featureValues = new Set();
  let featureAccessors = 0;
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) {
      const ids = prim.getAttribute("_FEATURE_ID_0");
      if (!ids) continue;
      featureAccessors += 1;
      const array = ids.getArray();
      for (let index = 0; index < array.length; index += 64) featureValues.add(array[index]);
    }
  }

  const beforeSimplify = countTris(doc);
  await doc.transform(simplify({ simplifier: simplifierMod.MeshoptSimplifier, ratio: 0.08, error: 1 }));
  const hlodTris = countTris(doc);
  const simplified = performance.now();
  await doc.transform(meshopt({ encoder: encoderMod.MeshoptEncoder, level: "medium" }));
  const binary = await io.writeBinary(doc);
  const done = performance.now();
  const readBack = await io.readBinary(binary);
  let featureAfterMeshopt = null;
  const readPrim = readBack.getRoot().listMeshes()[0]?.listPrimitives()[0];
  const readIds = readPrim?.getAttribute("_FEATURE_ID_0")?.getArray();
  if (readIds && readIds.length) featureAfterMeshopt = readIds[0];
  return {
    name: spec.name,
    actors: actor - 1,
    inputTris: spec.unique * spec.uniqueTris + spec.props * spec.copies * spec.propTris,
    meshesAfterJoin: doc.getRoot().listMeshes().length,
    instancedNodes: instanced.length,
    instancedAfterJoin,
    featureAccessors,
    distinctFeatureSamples: featureValues.size,
    hlodTris,
    beforeSimplify,
    bytes: binary.byteLength,
    featureAfterMeshopt,
    ms: {
      build: round(built - started),
      joinInstance: round(joined - built),
      simplify: round(simplified - joined),
      meshoptAndWrite: round(done - simplified),
      total: round(done - started),
    },
  };
}

function gridMesh(doc, buffer, material, triangles, actorId, originX, originZ) {
  const cols = Math.max(1, Math.round(Math.sqrt(triangles / 2)));
  const rows = Math.max(1, Math.ceil(triangles / (cols * 2)));
  const positions = [];
  const ids = [];
  const indices = [];
  const stride = cols + 1;
  for (let y = 0; y <= rows; y += 1) {
    for (let x = 0; x <= cols; x += 1) {
      positions.push(originX + x * 0.4, 0, originZ + y * 0.4);
      ids.push(actorId);
    }
  }
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < cols; x += 1) {
      const a = y * stride + x;
      indices.push(a, a + 1, a + stride, a + 1, a + stride + 1, a + stride);
    }
  }
  const prim = doc.createPrimitive()
    .setMaterial(material)
    .setAttribute("POSITION", doc.createAccessor().setType("VEC3").setArray(new Float32Array(positions)).setBuffer(buffer))
    .setAttribute("_FEATURE_ID_0", doc.createAccessor().setType("SCALAR").setArray(new Float32Array(ids)).setBuffer(buffer))
    .setIndices(doc.createAccessor().setType("SCALAR").setArray(new Uint32Array(indices)).setBuffer(buffer));
  return doc.createMesh().addPrimitive(prim);
}

function countTris(doc) {
  let tris = 0;
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) tris += (prim.getIndices()?.getCount() || 0) / 3;
  }
  return Math.round(tris);
}

function timeBc7() {
  const script = `
import sys, time
sys.path.insert(0, ${JSON.stringify("/agent/repos/ocarina-forge")})
from ocarina_forge.formats.gpu import encode_bc7
out = {}
for size in (64, 256):
    rgba = bytes([40, 120, 50, 255]) * (size * size)
    t = time.perf_counter()
    blocks = encode_bc7(size, size, rgba)
    out[str(size)] = {"ms": round((time.perf_counter() - t) * 1000, 1), "bytes": len(blocks)}
print(out)
`;
  const run = spawnSync("python3", ["-c", script], { encoding: "utf8" });
  if (run.status !== 0) return { error: (run.stderr || run.stdout || "python failed").slice(0, 400) };
  try {
    return JSON.parse(run.stdout.replaceAll("'", "\""));
  } catch {
    return { raw: run.stdout.slice(0, 400) };
  }
}

function round(value) {
  return Math.round(value * 10) / 10;
}

async function modules() {
  const home = join(tmpdir(), "spike-tiles");
  const dir = join(home, "node_modules");
  const probe = spawnSync(process.execPath, ["-e", "import('three')"], { cwd: home, encoding: "utf8" });
  if (probe.status === 0) return dir;
  const installDir = await mkdtemp(join(tmpdir(), "spike-tiles-"));
  const install = spawnSync("npm", ["install", "--silent", "three@0.187.0", "3d-tiles-renderer@0.5.4", "@gltf-transform/core@4.2.1", "@gltf-transform/extensions@4.2.1", "@gltf-transform/functions@4.2.1", "meshoptimizer@1.3.0"], {
    cwd: installDir,
    encoding: "utf8",
  });
  if (install.status !== 0) throw new Error(install.stderr || "npm install failed");
  return join(installDir, "node_modules");
}
