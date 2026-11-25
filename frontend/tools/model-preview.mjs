import { Document, Primitive } from "@gltf-transform/core";
import { KHRMaterialsUnlit } from "@gltf-transform/extensions";
import { MeshoptSimplifier } from "meshoptimizer";

const targetIndices = 6000;

export function buildModelPreview(source) {
  const preview = new Document();
  const scene = preview.createScene();
  preview.getRoot().setDefaultScene(scene);
  appendModelPreview(source, preview, scene, preview.createBuffer());
  return scene.listChildren().length ? preview : null;
}

export function appendModelPreview(source, preview, parent, buffer, terrain = false, modelBudget = 0) {
  const root = source.getRoot();
  const activeScene = root.getDefaultScene() || root.listScenes()[0];
  const parts = new Map();
  activeScene?.traverse((node) => {
    if (!node.getMesh()) return;
    const lod = /^lod(\d+):(.+)$/.exec(node.getName());
    const family = lod ? lod[2] : node;
    const level = lod ? Number(lod[1]) : 0;
    const current = parts.get(family);
    if (!current || level > current.level) parts.set(family, { level, nodes: [node] });
    else if (level === current.level) current.nodes.push(node);
  });

  const unlit = preview.createExtension(KHRMaterialsUnlit);
  const materials = new Map();
  const primitiveCount = [...parts.values()].reduce((sum, part) => sum + part.nodes.reduce(
    (total, node) => total + node.getMesh().listPrimitives().length, 0), 0);
  const budget = modelBudget && primitiveCount && !terrain
    ? Math.max(24, Math.floor(modelBudget / primitiveCount / 3) * 3)
    : targetIndices;

  function previewMaterial(sourceMaterial) {
    if (!materials.has(sourceMaterial)) {
      const material = preview.createMaterial()
        .setBaseColorFactor(sourceMaterial?.getBaseColorFactor() || [0.65, 0.65, 0.65, 1])
        .setDoubleSided(sourceMaterial?.getDoubleSided() || false)
        .setExtension("KHR_materials_unlit", unlit.createUnlit());
      if (sourceMaterial?.getAlphaMode() === "BLEND") material.setAlphaMode("BLEND");
      materials.set(sourceMaterial, material);
    }
    return materials.get(sourceMaterial);
  }

  for (const { nodes } of parts.values()) {
    for (const node of nodes) {
      const primitives = [];
      for (const primitive of node.getMesh().listPrimitives()) {
        const previewPart = previewPrimitive(preview, buffer, primitive, terrain, budget);
        if (!previewPart) continue;
        previewPart.setMaterial(previewMaterial(primitive.getMaterial()));
        primitives.push(previewPart);
      }
      if (!primitives.length) continue;
      const mesh = preview.createMesh();
      for (const primitive of primitives) mesh.addPrimitive(primitive);
      parent.addChild(preview.createNode(node.getName()).setMatrix(node.getWorldMatrix()).setMesh(mesh));
    }
  }
}

function previewPrimitive(document, buffer, primitive, terrain, budget) {
  if (primitive.getMode() !== Primitive.Mode.TRIANGLES) return null;
  const positions = primitive.getAttribute("POSITION")?.getArray();
  if (!(positions instanceof Float32Array)) return null;
  const count = positions.length / 3;
  let indices = Uint32Array.from(primitive.getIndices()?.getArray() || { length: count },
    (index, offset) => index ?? offset);
  if (terrain) {
    const marks = primitive.getAttribute("TEXCOORD_3")?.getArray();
    if (marks) {
      const main = new Uint32Array(indices.length);
      let length = 0;
      for (let index = 0; index + 2 < indices.length; index += 3) {
        if (marks[indices[index] * 2 + 1] > 0.5
            || marks[indices[index + 1] * 2 + 1] > 0.5
            || marks[indices[index + 2] * 2 + 1] > 0.5) continue;
        main.set(indices.subarray(index, index + 3), length);
        length += 3;
      }
      indices = main.subarray(0, length);
    }
  }
  if (indices.length < 3 || indices.length % 3) return null;
  if (indices.length > budget) {
    const [reduced] = terrain
      ? MeshoptSimplifier.simplify(indices, positions, 3, budget, 0.001, ["LockBorder"])
      : MeshoptSimplifier.simplifySloppy(indices, positions, 3, null, budget, 0.1);
    if (reduced.length >= 3 && reduced.length < indices.length) indices = reduced;
  }
  const [remap, unique] = MeshoptSimplifier.compactMesh(indices);
  const compact = new Float32Array(unique * 3);
  for (let vertex = 0; vertex < remap.length; vertex += 1) {
    if (remap[vertex] === 0xffffffff) continue;
    compact.set(positions.subarray(vertex * 3, vertex * 3 + 3), remap[vertex] * 3);
  }
  return document.createPrimitive()
    .setAttribute("POSITION", document.createAccessor().setType("VEC3").setArray(compact).setBuffer(buffer))
    .setIndices(document.createAccessor().setType("SCALAR")
      .setArray(unique <= 65536 ? Uint16Array.from(indices) : indices).setBuffer(buffer));
}
