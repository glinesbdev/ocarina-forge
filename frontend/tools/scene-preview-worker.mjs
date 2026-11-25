import { readFile } from "node:fs/promises";
import { Document, NodeIO } from "@gltf-transform/core";
import { EXTMeshoptCompression, KHRMaterialsUnlit, KHRTextureBasisu } from "@gltf-transform/extensions";
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from "meshoptimizer";

import { appendModelPreview } from "./model-preview.mjs";
import { buildCorrectedTerrain } from "./terrain-cutout.mjs";

await Promise.all([MeshoptDecoder.ready, MeshoptEncoder.ready, MeshoptSimplifier.ready]);

const input = [];
for await (const chunk of process.stdin) input.push(chunk);
const request = JSON.parse(Buffer.concat(input).toString("utf8"));
const io = new NodeIO().registerExtensions([EXTMeshoptCompression, KHRMaterialsUnlit, KHRTextureBasisu])
  .registerDependencies({ "meshopt.decoder": MeshoptDecoder, "meshopt.encoder": MeshoptEncoder });

if (request.cutout) {
  const terrain = await buildCorrectedTerrain(io, request.terrain, request.cutout, request.actors,
    request.heightTiles, request.heightScale, request.entrances, request.entranceStarts, request.collision, request.pond);
  if (terrain) process.stdout.write(Buffer.from(await io.writeBinary(terrain)));
} else {
  await packPreview(request);
}

async function packPreview(models) {
  const document = new Document();
  const scene = document.createScene();
  document.getRoot().setDefaultScene(scene);
  const buffer = document.createBuffer();
  // Large scenes keep every actor type, with a smaller geometry budget per type.
  const modelBudget = models.length > 64 ? Math.min(3600, Math.floor(600000 / models.length)) : 0;
  for (const model of models) {
    const source = await io.readBinary(await readFile(model.path));
    const group = document.createNode(model.name).setExtras({ forgePreview: model.name });
    appendModelPreview(source, document, group, buffer, model.name === "@terrain", modelBudget);
    if (group.listChildren().length) scene.addChild(group);
    else group.dispose();
  }
  if (!scene.listChildren().length) throw new Error("No scene preview triangles.");
  document.createExtension(EXTMeshoptCompression)
    .setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  process.stdout.write(Buffer.from(await io.writeBinary(document)));
}
