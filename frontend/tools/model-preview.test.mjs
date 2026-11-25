import assert from "node:assert/strict";
import test from "node:test";
import { Document, NodeIO } from "@gltf-transform/core";
import { KHRMaterialsUnlit } from "@gltf-transform/extensions";
import { MeshoptSimplifier } from "meshoptimizer";
import { BufferAttribute, BufferGeometry, Matrix4, Mesh, Raycaster, Vector3 } from "three";
import { buildCorrectedTerrain, confirmedTerrainOverlap, deformTerrainPrimitive, rasterizeCutout, sampleAuthoredDetail } from "./terrain-cutout.mjs";

import { appendModelPreview, buildModelPreview } from "./model-preview.mjs";

await MeshoptSimplifier.ready;

test("selects one LOD per family and writes compact texture-free geometry", async () => {
  const source = new Document();
  const scene = source.createScene();
  const buffer = source.createBuffer();
  const texture = source.createTexture().setMimeType("image/png").setImage(new Uint8Array(8192));
  const material = source.createMaterial().setBaseColorFactor([0.5, 0.4, 0.3, 1]).setBaseColorTexture(texture);
  const positions = source.createAccessor().setType("VEC3").setBuffer(buffer)
    .setArray(new Float32Array([0, 0, 0, 1, 0, 0, 9, 9, 9, 0, 1, 0]));
  const indices = source.createAccessor().setType("SCALAR").setBuffer(buffer)
    .setArray(new Uint16Array([0, 1, 3]));
  for (const name of ["lod0:house", "lod1:house", "lod0:door"]) {
    const primitive = source.createPrimitive().setAttribute("POSITION", positions)
      .setIndices(indices).setMaterial(material);
    scene.addChild(source.createNode(name).setTranslation([3, 4, 5])
      .setMesh(source.createMesh().addPrimitive(primitive)));
  }

  const preview = buildModelPreview(source);
  const io = new NodeIO().registerExtensions([KHRMaterialsUnlit]);
  const bytes = await io.writeBinary(preview);
  const restored = (await io.readBinary(bytes)).getRoot();
  assert.deepEqual(restored.listNodes().map((node) => node.getName()), ["lod1:house", "lod0:door"]);
  assert.deepEqual(restored.listNodes()[0].getTranslation(), [3, 4, 5]);
  assert.equal(restored.listTextures().length, 0);
  assert.ok(bytes.length < 8192);
  for (const mesh of restored.listMeshes()) {
    const [primitive] = mesh.listPrimitives();
    assert.equal(primitive.getAttribute("POSITION").getCount(), 3);
    assert.deepEqual([...primitive.getIndices().getArray()], [0, 1, 2]);
  }
  assert.equal(source.getRoot().listNodes().length, 3);
  assert.equal(source.getRoot().listTextures().length, 1);
});

test("an empty model has no preview", () => {
  assert.equal(buildModelPreview(new Document()), null);
});


test("scene terrain excludes the elevated sub layer before simplifying", () => {
  const source = new Document();
  const scene = source.createScene();
  const buffer = source.createBuffer();
  const accessor = (type, data) => source.createAccessor().setType(type).setArray(data).setBuffer(buffer);
  const primitive = source.createPrimitive()
    .setAttribute("POSITION", accessor("VEC3", new Float32Array([
      0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 20, 0, 1, 20, 0, 0, 20, 1,
    ])))
    .setAttribute("TEXCOORD_3", accessor("VEC2", new Float32Array([
      0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 1,
    ])))
    .setIndices(accessor("SCALAR", new Uint16Array([0, 1, 2, 3, 4, 5])));
  scene.addChild(source.createNode("terrain").setMesh(source.createMesh().addPrimitive(primitive)));
  const preview = new Document();
  const out = preview.createScene();
  appendModelPreview(source, preview, out, preview.createBuffer(), true);
  const mesh = out.listChildren()[0].getMesh().listPrimitives()[0];
  assert.deepEqual([...mesh.getIndices().getArray()], [0, 1, 2]);
  assert.deepEqual([...mesh.getAttribute("POSITION").getArray()], [0, 0, 0, 1, 0, 0, 0, 0, 1]);
});


test("authored detail uses the finest covering grid and the coarse mesh's triangle diagonal", () => {
  const tile = (bounds, centre) => {
    const grid = Buffer.alloc(257 * 257 * 4);
    grid.writeFloatLE(centre, (257 + 1) * 4);
    return { bounds, grid: grid.toString("base64"), step: 2 };
  };
  const sample = sampleAuthoredDetail([tile([0, 0, 256, 256], 4), tile([0, 0, 512, 512], 100)], 2);
  assert.equal(sample(1, 1), 8);
  assert.equal(sample(1.25, 1.25), 4);
  assert.equal(sample(300, 300), 0);
  assert.equal(sample(-1, 0), 0);
  const fallback = sampleAuthoredDetail([tile([0, 0, 256, 256], NaN), tile([-1, -1, 511, 511], 3)], 1);
  assert.equal(fallback(1, 1), 3);
});

test("an entrance restores an authored depression in coarse ground", async () => {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const accessor = (type, data) => doc.createAccessor().setType(type).setArray(data).setBuffer(buffer);
  const primitive = doc.createPrimitive()
    .setAttribute("POSITION", accessor("VEC3", Float32Array.of(0, 6, 0, 0, 6, 16, 16, 6, 0, 16, 6, 16)))
    .setAttribute("TEXCOORD_3", accessor("VEC2", new Float32Array(8)))
    .setIndices(accessor("SCALAR", Uint16Array.of(0, 1, 2, 2, 1, 3)));
  doc.createScene().addChild(doc.createNode().setMesh(doc.createMesh().addPrimitive(primitive)));
  const grid = Buffer.alloc(257 * 257 * 4);
  for (let z = 0; z <= 256; z += 1) {
    for (let x = 0; x <= 256; x += 1) {
      grid.writeFloatLE(x >= 96 && x <= 160 && z >= 96 && z <= 160 ? 1 : 6, (z * 257 + x) * 4);
    }
  }
  const tiles = [{ bounds: [0, 0, 16, 16], grid: grid.toString("base64"), step: 256 }];
  await buildCorrectedTerrain({ read: () => doc }, "base", {}, [], tiles, 1, [[3, 3, 13, 13], [4, 4, 12, 12]]);
  const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(primitive.getAttribute("POSITION").getArray(), 3));
  geometry.setIndex([...primitive.getIndices().getArray()]);
  const mesh = new Mesh(geometry);
  const height = (x, z) => new Raycaster(new Vector3(x, 10, z), new Vector3(0, -1, 0)).intersectObject(mesh)[0].point.y;
  assert.equal(height(8, 8), 1);
  assert.equal(height(14, 8), 6);
});

test("automatic fit requires a touching floor and a contiguous nearby overlap", () => {
  const rectangle = (x, z, width, height, y) => [
    [x, y, z, x, y, z + height, x + width, y, z],
    [x + width, y, z, x, y, z + height, x + width, y, z + height],
  ];
  const floor = rectangle(0, 0, 8, 6, 4);
  assert.equal(confirmedTerrainOverlap(floor, () => 4).length, 0);
  assert.equal(confirmedTerrainOverlap(floor, () => 0).length, 0);
  assert.equal(confirmedTerrainOverlap(floor, () => 5).length, 0);
  assert.equal(confirmedTerrainOverlap(floor, (x, z) => x < 2 && z < 1 ? 5 : 4).length, 0);
  const remote = [...floor, ...rectangle(30, 0, 8, 6, 4)];
  const fits = confirmedTerrainOverlap(remote, (x) => x < 3 || x > 20 ? 5 : 4);
  assert.ok(fits.length > 0 && fits.flat().every((triangle) => triangle[0] < 8));
  assert.equal(confirmedTerrainOverlap(remote, (x) => (x < 3 || (x >= 30 && x < 33)) ? 5 : 4).length, 2);
});

test("collision-backed entrances clear buried ground while excluding disconnected, unmatched, and lower surfaces", async () => {
  const surface = (doc, name, left, right, height) => {
    const buffer = doc.getRoot().listBuffers()[0] || doc.createBuffer();
    const attr = (type, data) => doc.createAccessor().setType(type).setArray(data).setBuffer(buffer);
    const primitive = doc.createPrimitive()
      .setAttribute("POSITION", attr("VEC3", Float32Array.of(left, height, 0, left, height, 12, right, height, 0, right, height, 12)))
      .setAttribute("TEXCOORD_3", attr("VEC2", new Float32Array(8)))
      .setIndices(attr("SCALAR", Uint16Array.of(0, 1, 2, 2, 1, 3)));
    const node = doc.createNode(name).setMesh(doc.createMesh().addPrimitive(primitive));
    const scene = doc.getRoot().listScenes()[0] || doc.createScene();
    scene.addChild(node);
    return primitive;
  };
  const base = new Document(), model = new Document();
  const ground = surface(base, "ground", -12, 14, 6);
  surface(model, "lod0:Platform__Material:0", 0, 6, 0);
  surface(model, "lod0:Platform__Material:1", 0, 6, -5);
  surface(model, "lod0:Wall__Material:0", 9, 12, 0);
  surface(model, "lod0:VisualOnly", -8, -5, 0);
  const collisionSurface = (left, right, y) => [
    [left, y, 0, left, y, 12, right, y, 0], [right, y, 0, left, y, 12, right, y, 12],
  ];
  const collision = { Land_Test: [
    ...collisionSurface(0, 6, 0.01), ...collisionSurface(0, 6, -5),
    ...collisionSurface(9, 12, 0), ...collisionSurface(-8, -5, -1),
  ] };
  const actor = { gyaml: "Land_Test", translate: [0, 2, 0], rotate: [0, 0, 0], scale: [1, 1, 1],
    entranceIndices: [0] };
  const result = await buildCorrectedTerrain({ read: (path) => path === "ground" ? base : model }, "ground",
    { Land_Test: "model" }, [actor], [], 1, [[-12, -9, 18, 21]], [[3, 2, 6]], collision);
  assert.ok(result === base);
  const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(ground.getAttribute("POSITION").getArray(), 3));
  geometry.setIndex([...ground.getIndices().getArray()]);
  const mesh = new Mesh(geometry);
  const height = (x) => new Raycaster(new Vector3(x, 10, 6), new Vector3(0, -1, 0)).intersectObject(mesh)[0].point.y;
  assert.ok(Math.abs(height(3) - 1.98) < 0.005);
  assert.equal(height(11), 6);
  assert.equal(height(-6), 6);
});

test("scene preparation fits an ordinary modeled floor without a map-specific override", async () => {
  const surface = (left, right) => {
    const doc = new Document();
    const buffer = doc.createBuffer();
    const values = (type, array) => doc.createAccessor().setBuffer(buffer).setType(type).setArray(array);
    const part = doc.createPrimitive()
      .setAttribute("POSITION", values("VEC3", new Float32Array([
        0, left, 0, 0, left, 8, 8, right, 0, 8, right, 8,
      ])))
      .setAttribute("TEXCOORD_3", values("VEC2", new Float32Array(8)))
      .setIndices(values("SCALAR", new Uint16Array([0, 1, 2, 2, 1, 3])));
    const node = doc.createNode("lod0:floor").setMesh(doc.createMesh().addPrimitive(part));
    doc.createScene().addChild(node);
    return doc;
  };
  const base = surface(5, 4);
  const land = { gyaml: "Land_Test_Floor", translate: [0, 0, 0], rotate: [0, 0, 0], scale: [1, 1, 1] };
  const io = { read: (path) => path === "base" ? base : surface(4, 4) };
  const fitted = await buildCorrectedTerrain(io, "base", { [land.gyaml]: "model" }, [land]);
  const primitive = fitted.getRoot().listMeshes()[0].listPrimitives()[0];
  const positions = primitive.getAttribute("POSITION").getArray();
  const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(positions, 3));
  geometry.setIndex([...primitive.getIndices().getArray()]);
  const ray = new Raycaster(new Vector3(2.5, 10, 3.5), new Vector3(0, -1, 0));
  assert.ok(ray.intersectObject(new Mesh(geometry))[0].point.y < 4.05);
});

test("clearance under model geometry tapers back into the source terrain without a wall at the footprint edge", () => {
  const clip = rasterizeCutout([[0, 0, 0, 0, 0, 2, 2, 0, 0]]);
  let previous = clip.sample(0.1, 8, 0.5);
  assert.ok(previous <= 0.01);
  for (let x = 0.225; x < 12; x += 0.125) {
    const height = clip.sample(x, 8, 0.5);
    assert.ok(height >= previous - 1e-6 && height - previous <= 0.3, `height jumped at x=${x}`);
    previous = height;
  }
  assert.equal(previous, 8);
});

test("rolling-rock ground moves below overlapping scenery without opening holes or moving sub terrain", () => {
  const clip = rasterizeCutout([
    [0.5, 2, 0.5, 0.5, 2, 1.5, 1.5, 2, 0.5],
    [0.5, 4, 0.5, 0.5, 4, 1, 1, 4, 0.5],
  ]);
  const doc = new Document();
  const buffer = doc.createBuffer();
  const accessor = (type, data) => doc.createAccessor().setType(type).setArray(data).setBuffer(buffer);
  const part = doc.createPrimitive()
    .setAttribute("POSITION", accessor("VEC3", new Float32Array([
      0, 5, 0, 0, 5, 8, 8, 5, 0, 8, 5, 8,
      0, 9, 0, 0, 9, 8, 8, 9, 0,
    ])))
    .setAttribute("TEXCOORD_3", accessor("VEC2", new Float32Array([
      0.25, 0, 0.25, 0, 0.25, 0, 0.25, 0, 0.75, 1, 0.75, 1, 0.75, 1,
    ])))
    .setIndices(accessor("SCALAR", new Uint16Array([0, 1, 2, 2, 1, 3, 4, 5, 6])));
  deformTerrainPrimitive(part, new Matrix4(), clip);
  const positions = part.getAttribute("POSITION").getArray();
  const indices = [...part.getIndices().getArray()];
  assert.deepEqual(indices.splice(-3), [4, 5, 6]);
  assert.deepEqual([...positions.slice(12, 21)], [0, 9, 0, 0, 9, 8, 8, 9, 0]);
  assert.deepEqual([...new Set(part.getAttribute("TEXCOORD_3").getArray())].sort(), [0, 0.25, 0.75, 1]);
  const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(positions, 3)).setIndex(indices);
  const mesh = new Mesh(geometry);
  function groundAt(x, z) {
    const ray = new Raycaster(new Vector3(x, 10, z), new Vector3(0, -1, 0));
    return ray.intersectObject(mesh)[0]?.point.y;
  }
  assert.ok(groundAt(0.7, 0.7) < 2);
  assert.ok(groundAt(1.3, 1.3) < 3);
  assert.equal(groundAt(7, 7), 5);
  for (let z = 0.05; z < 7.99; z += 0.2) {
    for (let x = 0.05; x < 7.99; x += 0.2) assert.ok(Number.isFinite(groundAt(x, z)));
  }
});
