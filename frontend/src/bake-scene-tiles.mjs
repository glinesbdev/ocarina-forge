/**
 * Bake one cell of static scenery into a meshopt glb plus a coarser HLOD.
 *
 * The glTF stays in the editor's Y-up coordinates. The tileset sets
 * `gltfUpAxis` to `z` so 3d-tiles-renderer does not apply its Y-up
 * correction (that correction is a +90° X rotation). Content URIs are
 * relative to the tileset's directory.
 *
 *   node frontend/src/bake-scene-tiles.mjs job.json
 */
import { readFileSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { pathToFileURL } from "node:url";

import { Document, NodeIO } from "@gltf-transform/core";
import {
  EXTMeshGPUInstancing,
  EXTMeshoptCompression,
  KHRMaterialsUnlit,
} from "@gltf-transform/extensions";
import {
  copyToDocument,
  dedup,
  instance,
  join as joinMeshes,
  meshopt,
  prune,
  simplify,
  unpartition,
} from "@gltf-transform/functions";
import { MeshoptDecoder } from "meshoptimizer/decoder";
import { MeshoptEncoder } from "meshoptimizer/encoder";
import { MeshoptSimplifier } from "meshoptimizer/simplifier";
import * as THREE from "three";

await Promise.all([
  MeshoptDecoder.ready,
  MeshoptEncoder.ready,
  MeshoptSimplifier.ready,
]);

const io = new NodeIO()
  .registerExtensions([EXTMeshGPUInstancing, EXTMeshoptCompression, KHRMaterialsUnlit])
  .registerDependencies({
    "meshopt.decoder": MeshoptDecoder,
    "meshopt.encoder": MeshoptEncoder,
    "meshopt.simplifier": MeshoptSimplifier,
  });

const loaded = new Map();

export async function readSource(path) {
  const key = String(path);
  if (!loaded.has(key)) loaded.set(key, io.read(key));
  return loaded.get(key);
}

export function clearSourceCache() {
  loaded.clear();
}

function localMatrix(node) {
  const t = node.getTranslation();
  const r = node.getRotation();
  const s = node.getScale();
  return new THREE.Matrix4().compose(
    new THREE.Vector3(t[0], t[1], t[2]),
    new THREE.Quaternion(r[0], r[1], r[2], r[3]),
    new THREE.Vector3(s[0] || 1, s[1] || 1, s[2] || 1),
  );
}

function nodeWorld(node) {
  const chain = [];
  const seen = new Set();
  for (let current = node; current && !seen.has(current); current = current.getParentNode()) {
    seen.add(current);
    chain.push(localMatrix(current));
  }
  const world = new THREE.Matrix4();
  for (let index = chain.length - 1; index >= 0; index -= 1) world.multiply(chain[index]);
  return world;
}

function actorMatrix(actor) {
  const t = actor.translation || [0, 0, 0];
  const r = actor.rotation || [0, 0, 0];
  const s = actor.scale || [1, 1, 1];
  return new THREE.Matrix4().compose(
    new THREE.Vector3(Number(t[0]) || 0, Number(t[1]) || 0, Number(t[2]) || 0),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(Number(r[0]) || 0, Number(r[1]) || 0, Number(r[2]) || 0, "ZYX")),
    new THREE.Vector3(Number(s[0]) || 1, Number(s[1]) || 1, Number(s[2]) || 1),
  );
}

function lodOf(node) {
  const seen = new Set();
  for (let current = node; current && !seen.has(current); current = current.getParentNode()) {
    seen.add(current);
    const match = /^lod(\d+):/.exec(current.getName() || "");
    if (match) return Number(match[1]);
  }
  return 0;
}

function familyOf(name) {
  const match = /^lod\d+:(.*)$/.exec(name || "");
  return match ? match[1] : (name || "");
}

function positionKey(x, y, z) {
  return `${Math.round(x * 1000)}:${Math.round(y * 1000)}:${Math.round(z * 1000)}`;
}

function materialFor(doc, unlitExt, materials, spec) {
  const image = spec && spec.image ? String(spec.image) : "";
  const family = spec && spec.family ? String(spec.family) : "";
  const alpha = spec && spec.alpha ? String(spec.alpha) : "opaque";
  const gyaml = spec && spec.gyaml ? String(spec.gyaml) : "";
  const key = image ? `${image}|${alpha}` : `${gyaml}|${family}|${alpha}`;
  let material = materials.get(key);
  if (material) return material;
  material = doc.createMaterial(image || key)
    .setDoubleSided(alpha === "blend")
    .setExtension("KHR_materials_unlit", unlitExt.createUnlit())
    .setExtras({ forge: { image, family, gyaml, alpha } });
  if (alpha === "blend") material.setAlphaMode("BLEND");
  else if (alpha === "mask") material.setAlphaMode("MASK").setAlphaCutoff(0.5);
  materials.set(key, material);
  return material;
}

function stampVertices(doc, buffer, mesh, featureId) {
  for (const prim of mesh.listPrimitives()) {
    const position = prim.getAttribute("POSITION");
    if (!position) continue;
    const ids = new Float32Array(position.getCount());
    ids.fill(featureId);
    prim.setAttribute("_FEATURE_ID_0", doc.createAccessor().setType("SCALAR").setArray(ids).setBuffer(buffer));
  }
}

function stampInstances(doc, buffer, node, queues) {
  const batch = node.getExtension("EXT_mesh_gpu_instancing");
  const translation = batch && batch.getAttribute("TRANSLATION");
  if (!translation) return 0;
  const array = translation.getArray();
  const count = translation.getCount();
  const ids = new Float32Array(count);
  for (let index = 0; index < count; index += 1) {
    const key = positionKey(array[index * 3], array[index * 3 + 1], array[index * 3 + 2]);
    const queue = queues.get(key);
    ids[index] = queue && queue.length ? queue.shift() : 0;
  }
  batch.setAttribute("_FEATURE_ID_0", doc.createAccessor().setType("SCALAR").setArray(ids).setBuffer(buffer));
  return count;
}

async function placeActor(doc, scene, buffer, unlitExt, materials, actor) {
  const source = await readSource(actor.glb);
  const placement = actorMatrix(actor);
  const featureId = Number(actor.featureId) || 0;
  const parts = Array.isArray(actor.parts) ? actor.parts : [];
  let placed = 0;
  for (const sourceNode of source.getRoot().listNodes()) {
    if (!sourceNode.getMesh() || lodOf(sourceNode) > 0) continue;
    const family = familyOf(sourceNode.getName());
    const part = parts.find((item) => item && item.family === family) || null;
    const copied = copyToDocument(doc, source, [sourceNode.getMesh()]);
    const mesh = copied.get(sourceNode.getMesh());
    if (!mesh) continue;
    const spec = part
      ? { image: part.image || "", family, alpha: part.alpha || "opaque", gyaml: actor.gyaml || "" }
      : null;
    if (spec) {
      const shared = materialFor(doc, unlitExt, materials, spec);
      for (const prim of mesh.listPrimitives()) prim.setMaterial(shared);
    }
    const world = placement.clone().multiply(nodeWorld(sourceNode));
    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const scale = new THREE.Vector3();
    world.decompose(position, quaternion, scale);
    scene.addChild(doc.createNode(`actor-${featureId}-${placed}`)
      .setMesh(mesh)
      .setTranslation([position.x, position.y, position.z])
      .setRotation([quaternion.x, quaternion.y, quaternion.z, quaternion.w])
      .setScale([scale.x, scale.y, scale.z])
      .setExtras({ featureId }));
    placed += 1;
  }
  return placed;
}

function countTris(doc) {
  let tris = 0;
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) tris += (prim.getIndices()?.getCount() || 0) / 3;
  }
  return Math.round(tris);
}

function boundsOf(doc) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  const point = new THREE.Vector3();
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const matrix = localMatrix(node);
    const batch = node.getExtension("EXT_mesh_gpu_instancing");
    const translations = batch?.getAttribute("TRANSLATION")?.getArray();
    const copies = translations ? translations.length / 3 : 1;
    for (const prim of mesh.listPrimitives()) {
      const pos = prim.getAttribute("POSITION")?.getArray();
      if (!pos) continue;
      for (let copy = 0; copy < copies; copy += 1) {
        for (let index = 0; index < pos.length; index += 3) {
          point.set(pos[index], pos[index + 1], pos[index + 2]);
          if (translations) {
            point.x += translations[copy * 3];
            point.y += translations[copy * 3 + 1];
            point.z += translations[copy * 3 + 2];
          } else {
            point.applyMatrix4(matrix);
          }
          min[0] = Math.min(min[0], point.x);
          min[1] = Math.min(min[1], point.y);
          min[2] = Math.min(min[2], point.z);
          max[0] = Math.max(max[0], point.x);
          max[1] = Math.max(max[1], point.y);
          max[2] = Math.max(max[2], point.z);
        }
      }
    }
  }
  if (!Number.isFinite(min[0])) return { min: [0, 0, 0], max: [0, 0, 0] };
  return { min, max };
}

export function tilesetBox(min, max) {
  const cx = (min[0] + max[0]) / 2;
  const cy = (min[1] + max[1]) / 2;
  const cz = (min[2] + max[2]) / 2;
  const hx = Math.max((max[0] - min[0]) / 2, 1);
  const hy = Math.max((max[1] - min[1]) / 2, 1);
  const hz = Math.max((max[2] - min[2]) / 2, 1);
  return [cx, cy, cz, hx, 0, 0, 0, hy, 0, 0, 0, hz];
}

async function bakeCell(cell) {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const scene = doc.createScene("cell");
  doc.getRoot().setDefaultScene(scene);
  const asset = doc.getRoot().getAsset();
  asset.generator = "ocarina-forge-tiles";
  const unlitExt = doc.createExtension(KHRMaterialsUnlit);
  const materials = new Map();
  const actors = (cell.actors || []).slice().sort((a, b) => Number(a.featureId) - Number(b.featureId));
  for (const actor of actors) {
    await placeActor(doc, scene, buffer, unlitExt, materials, actor);
  }
  await doc.transform(dedup(), prune());
  const queues = new Map();
  for (const node of doc.getRoot().listNodes()) {
    if (!node.getMesh()) continue;
    const extras = node.getExtras() || {};
    const id = Number(extras.featureId) || 0;
    if (!id) continue;
    const t = node.getTranslation();
    const key = positionKey(t[0], t[1], t[2]);
    const queue = queues.get(key) || [];
    queue.push(id);
    queues.set(key, queue);
  }
  await doc.transform(instance({ min: 2 }));
  let instanced = 0;
  for (const node of doc.getRoot().listNodes()) {
    if (node.getExtension("EXT_mesh_gpu_instancing")) {
      instanced += stampInstances(doc, buffer, node, queues);
      continue;
    }
    const id = Number((node.getExtras() || {}).featureId) || 0;
    if (id && node.getMesh()) stampVertices(doc, buffer, node.getMesh(), id);
  }
  await doc.transform(joinMeshes(), prune());
  const triangles = countTris(doc);
  const bounds = boundsOf(doc);
  const plain = await io.writeBinary(doc);
  await doc.transform(unpartition(), meshopt({ encoder: MeshoptEncoder, level: "medium" }));
  await mkdir(dirname(cell.leaf), { recursive: true });
  await io.write(cell.leaf, doc);
  let hlodTriangles = triangles;
  try {
    const hlod = await io.readBinary(plain);
    await hlod.transform(
      simplify({ simplifier: MeshoptSimplifier, ratio: 0.1, error: 1 }),
      unpartition(),
      meshopt({ encoder: MeshoptEncoder, level: "medium" }),
    );
    hlodTriangles = countTris(hlod);
    await mkdir(dirname(cell.hlod), { recursive: true });
    await io.write(cell.hlod, hlod);
  } catch {
    await mkdir(dirname(cell.hlod), { recursive: true });
    writeFileSync(cell.hlod, Buffer.from(plain));
  }
  const meta = {
    id: cell.id,
    bounds,
    box: tilesetBox(bounds.min, bounds.max),
    triangles,
    hlodTriangles,
    instanced,
    meshes: doc.getRoot().listMeshes().length,
    actors: actors.map((actor) => Number(actor.featureId)),
  };
  writeFileSync(cell.meta, JSON.stringify(meta));
  return meta;
}

export async function bakeJob(job) {
  const started = performance.now();
  const cells = [];
  for (const cell of job.cells || []) cells.push(await bakeCell(cell));
  return {
    cells,
    ms: Math.round(performance.now() - started),
  };
}

async function main() {
  const jobPath = process.argv[2];
  if (!jobPath) throw new Error("Pass the bake job JSON.");
  const job = JSON.parse(readFileSync(jobPath, "utf8"));
  const report = await bakeJob(job);
  if (job.report) writeFileSync(job.report, JSON.stringify(report));
  else console.log(JSON.stringify(report));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
