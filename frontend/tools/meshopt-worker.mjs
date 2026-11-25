/**
 * Length-prefixed JSON requests on stdin and responses on stdout.
 * The Python converter keeps this process alive so a scene does not start Node once per mesh.
 */
import { NodeIO } from "@gltf-transform/core";
import { EXTMeshoptCompression, KHRMaterialsUnlit, KHRTextureBasisu } from "@gltf-transform/extensions";
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from "meshoptimizer";
import { LengthPrefixedFrameReader } from "./length-prefixed.mjs";

await MeshoptSimplifier.ready;
await MeshoptEncoder.ready;
await MeshoptDecoder.ready;

const textureExtensions = [EXTMeshoptCompression, KHRMaterialsUnlit, KHRTextureBasisu];
const readIO = new NodeIO()
  .registerExtensions(textureExtensions)
  .registerDependencies({ "meshopt.decoder": MeshoptDecoder });
const writeIO = new NodeIO()
  .registerExtensions(textureExtensions)
  .registerDependencies({ "meshopt.encoder": MeshoptEncoder, "meshopt.decoder": MeshoptDecoder });
const plainIO = new NodeIO().registerExtensions([KHRMaterialsUnlit, KHRTextureBasisu]);

// Albedo images may be image/ktx2. The bytes are a KTX2 of BC1, BC3, or BC7
// with a JPEG or PNG after it (OCFG). They are copied through. Do not transcode
// them: a Basis file (vkFormat 0) decoded empty in three r170.
// Geometry is compressed without quantizing. Positions and TEXCOORD stay
// float32, including UVs outside 0..1. Do not run quantize(): a normalized
// uint16 texcoord squashes a tiled chart (a roof at -3..5) into the unit square.

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
  const colors = body.colors ? floatsFrom(body.colors) : null;
  const outColor = colors ? new Float32Array(unique * 4) : null;
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
    if (outColor && old * 4 + 3 < colors.length) {
      outColor[slot * 4] = colors[old * 4];
      outColor[slot * 4 + 1] = colors[old * 4 + 1];
      outColor[slot * 4 + 2] = colors[old * 4 + 2];
      outColor[slot * 4 + 3] = colors[old * 4 + 3];
    }
  }
  return {
    ok: true,
    positions: bytesOf(outPos),
    indices: bytesOf(compacted),
    uvs: outUv ? bytesOf(outUv) : null,
    colors: outColor ? bytesOf(outColor) : null,
    vertices: unique,
    triangles: compacted.length / 3,
    error: deviation,
  };
}

// Length-prefixed frames. A newline JSON reader dies, or recurses until it
// crashes, once a base64 glb is larger than its line buffer. The process used
// to exit on that, and Python then flushed a dead stdin pipe.
function writeFrame(obj) {
  const json = Buffer.from(JSON.stringify(obj));
  const head = Buffer.alloc(4);
  head.writeUInt32LE(json.length, 0);
  process.stdout.write(Buffer.concat([head, json]));
}

process.stdout.on("error", (error) => {
  console.error(`stdout: ${error.message}`);
});
process.on("uncaughtException", (error) => {
  console.error(error && error.stack ? error.stack : error);
});
process.on("unhandledRejection", (error) => {
  console.error(error && error.stack ? error.stack : error);
});

let pending = Promise.resolve();

const frameReader = new LengthPrefixedFrameReader(512 * 1024 * 1024, (frame) => {
  const text = frame.toString("utf8");
  pending = pending.then(async () => {
    let response;
    try {
      const body = JSON.parse(text);
      if (body.op === "simplify") response = simplify(body);
      else if (body.op === "pack") response = await pack(body);
      else if (body.op === "plain") response = await plain(body);
      else response = { ok: false, reason: "unknown op" };
    } catch (error) {
      response = { ok: false, reason: error.message || "meshopt failed" };
    }
    try {
      writeFrame(response);
    } catch (error) {
      console.error(error && error.stack ? error.stack : error);
    }
  });
});

process.stdin.on("data", (chunk) => {
  try {
    frameReader.push(chunk);
  } catch (error) {
    console.error(`meshopt frame: ${error.message}`);
    process.exit(1);
  }
});
process.stdin.on("error", (error) => {
  console.error(`stdin: ${error.message}`);
});
process.stdin.on("end", () => {
  pending.then(() => process.exit(0));
});
process.stdin.resume();
writeFrame({ ok: true, ready: true });
