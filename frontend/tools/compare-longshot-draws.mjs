import { createReadStream } from "node:fs";
import { readFile } from "node:fs/promises";
import { createInterface } from "node:readline";

async function compare(auditFile, logFile, program) {
  const { viewport } = JSON.parse(await readFile(auditFile, "utf8"));
  if (!viewport?.camera || !viewport.selected) {
    throw new Error("The Forge mesh audit needs a camera and a selected actor.");
  }
  const { selected, camera } = viewport;
  const surfaces = [...selected.detail.map((surface) => ({ ...surface, kind: "detail" })),
    ...selected.preview.map((surface) => ({ ...surface, kind: "preview" }))];
  if (!surfaces.length) throw new Error("The selected actor had no mesh surfaces when the audit started.");

  const byIndexCount = new Map();
  for (const { indices } of surfaces) if (indices > 0) byIndexCount.set(indices, []);
  let drawRecords = 0;
  const lines = createInterface({ input: createReadStream(logFile), crlfDelay: Infinity });
  for await (const line of lines) {
    const row = /vk: dump-draw (\d+) prog ([a-f0-9]{16}) prim (\d+) count (\d+) first (\d+) inst (\d+) bv (-?\d+) bi (\d+) idx (\d+)\/(\d+) ibuf (\S+)(.*)$/i.exec(line);
    if (!row) continue;
    drawRecords += 1;
    const [, step, key, topology, count, first, instances, baseVertex, baseInstance, indexed, indexType, , details] = row;
    // Triangle lists only. Strip conversion, indirect and meshlet draws can
    // change the counts and need the original draw resources for comparison.
    if (topology !== "4" || indexed !== "1" || /^ ind /.test(details) || (program && key.toLowerCase() !== program)) continue;
    const candidates = byIndexCount.get(Number(count));
    if (!candidates) continue;
    const attributes = [...details.matchAll(/\bva(\d+) f(\d+)\+(\d+) s(\d+)\/(\d+)/g)]
      .map(([, location, format, offset, stream, stride]) => ({
        location: Number(location), format: Number(format), offset: Number(offset), stream: Number(stream), stride: Number(stride),
      }));
    candidates.push({ step: Number(step), program: key, first: Number(first), instances: Number(instances),
      baseVertex: Number(baseVertex), baseInstance: Number(baseInstance), indexType: Number(indexType), attributes });
  }
  if (!drawRecords) throw new Error("No Longshot RX_VK_DUMP_ARGS draw records found in this log.");
  return {
    camera, actor: { id: selected.id, gyaml: selected.gyaml }, drawRecords,
    criterion: "Equal index count on a direct indexed triangle-list draw. Candidate identity requires independent verification.",
    surfaces: surfaces.map((surface) => ({ ...surface, candidates: byIndexCount.get(surface.indices) ?? [] })),
  };
}

const [audit, log, program] = process.argv.slice(2);
try {
  if (!audit || !log || (program && !/^[a-f0-9]{16}$/i.test(program))) {
    throw new Error("Usage: node compare-longshot-draws.mjs <mesh-audit.json> <Longshot log> [16-digit program key]");
  }
  console.log(JSON.stringify(await compare(audit, log, program?.toLowerCase()), null, 2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
