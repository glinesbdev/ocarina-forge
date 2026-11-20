/**
 * One JSON request per line on stdin, one JSON response per line on stdout.
 * The Python converter keeps this process alive so a scene does not start Node once per mesh.
 */
import readline from "node:readline";
import { NodeIO } from "@gltf-transform/core";
import { EXTMeshoptCompression, KHRMaterialsUnlit } from "@gltf-transform/extensions";
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from "meshoptimizer";

await MeshoptSimplifier.ready;
await MeshoptEncoder.ready;
await MeshoptDecoder.ready;

const readIO = new NodeIO()
  .registerExtensions([EXTMeshoptCompression, KHRMaterialsUnlit])
  .registerDependencies({ "meshopt.decoder": MeshoptDecoder });
const writeIO = new NodeIO()
  .registerExtensions([EXTMeshoptCompression, KHRMaterialsUnlit])
  .registerDependencies({ "meshopt.encoder": MeshoptEncoder, "meshopt.decoder": MeshoptDecoder });
const plainIO = new NodeIO().registerExtensions([KHRMaterialsUnlit]);

// KTX2 is not applied. The current Basis encoder writes files that three's
// transcoder turns into an empty image, so a cached texture would draw black.
// Geometry is compressed without quantizing, which keeps actor positions put.

async function pack(body) {
  const glb = Buffer.from(body.glb, "base64");
  const document = await readIO.readBinary(glb);
  document
    .createExtension(EXTMeshoptCompression)
    .setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  const out = Buffer.from(await writeIO.writeBinary(document));
  return { ok: true, glb: out.toString("base64") };
}

async function plain(body) {
  const glb = Buffer.from(body.glb, "base64");
  const document = await readIO.readBinary(glb);
  document.createExtension(EXTMeshoptCompression).dispose();
  const out = Buffer.from(await plainIO.writeBinary(document));
  return { ok: true, glb: out.toString("base64") };
}

function typedFrom(value, Type) {
  const buf = Buffer.from(value, "base64");
  const copy = new Uint8Array(buf.length);
  copy.set(buf);
  return new Type(copy.buffer);
}

function floatsFrom(value) {
  return typedFrom(value, Float32Array);
}

function intsFrom(value) {
  return typedFrom(value, Uint32Array);
}

function bytesOf(view) {
  return Buffer.from(view.buffer, view.byteOffset, view.byteLength).toString("base64");
}

function simplify(body) {
  const positions = floatsFrom(body.positions);
  const source = intsFrom(body.indices);
  const vertexCount = positions.length / 3;
  if (source.length < 3 || vertexCount < 3) return { ok: false, reason: "too small" };
  const ratio = Number(body.ratio) > 0 ? Number(body.ratio) : 0.35;
  const error = Number(body.error) > 0 ? Number(body.error) : 0.05;
  let target = Math.floor(source.length * ratio);
  target -= target % 3;
  if (target < 3) target = 3;
  const indices = new Uint32Array(source);
  let simplified;
  let deviation;
  if (body.uvs) {
    const uvs = floatsFrom(body.uvs);
    [simplified, deviation] = MeshoptSimplifier.simplifyWithAttributes(
      indices,
      positions,
      3,
      uvs,
      2,
      [0.01, 0.01],
      null,
      target,
      error,
      ["LockBorder"]
    );
  } else {
    [simplified, deviation] = MeshoptSimplifier.simplify(indices, positions, 3, target, error, ["LockBorder"]);
  }
  if (!simplified || simplified.length < 3 || simplified.length >= source.length * 0.85) {
    return { ok: false, reason: "not smaller", error: deviation ?? null };
  }
  const compacted = new Uint32Array(simplified);
  const [remap, unique] = MeshoptSimplifier.compactMesh(compacted);
  if (!unique || unique < 3) return { ok: false, reason: "empty" };
  const outPos = new Float32Array(unique * 3);
  const uvs = body.uvs ? floatsFrom(body.uvs) : null;
  const outUv = uvs ? new Float32Array(unique * 2) : null;
  for (let old = 0; old < remap.length; old += 1) {
    const slot = remap[old];
    if (slot >= unique) continue;
    outPos[slot * 3] = positions[old * 3];
    outPos[slot * 3 + 1] = positions[old * 3 + 1];
    outPos[slot * 3 + 2] = positions[old * 3 + 2];
    if (outUv && old * 2 + 1 < uvs.length) {
      outUv[slot * 2] = uvs[old * 2];
      outUv[slot * 2 + 1] = uvs[old * 2 + 1];
    }
  }
  return {
    ok: true,
    positions: bytesOf(outPos),
    indices: bytesOf(compacted),
    uvs: outUv ? bytesOf(outUv) : null,
    vertices: unique,
    triangles: compacted.length / 3,
    error: deviation,
  };
}

const lines = readline.createInterface({ input: process.stdin });
let pending = Promise.resolve();
lines.on("line", (line) => {
  pending = pending.then(async () => {
    let response;
    try {
      const body = JSON.parse(line);
      if (body.op === "simplify") response = simplify(body);
      else if (body.op === "pack") response = await pack(body);
      else if (body.op === "plain") response = await plain(body);
      else response = { ok: false, reason: "unknown op" };
    } catch (error) {
      response = { ok: false, reason: error.message || "meshopt failed" };
    }
    process.stdout.write(`${JSON.stringify(response)}\n`);
  });
});
