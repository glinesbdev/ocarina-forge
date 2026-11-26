/**
 * One console line for a textured draw that still looks flat.
 * Enable with `?texdebug=1` or `localStorage.ocarinaforge.texdebug = "1"`.
 * Profile `tex` counts maps on the drawn batch materials. `gpu N tex` is
 * `renderer.info.memory.textures`, an object count, not bytes.
 */

export function textureDebugEnabled(search, stored) {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  if (params.get("texdebug") === "1") return true;
  return stored === "1";
}

let firstPipelineError = "";
const warnedPipelineErrors = new Set();

/** One console warning for a dropped texture. Later copies of the same message stay quiet. */
export function noteTexturePipelineError(error) {
  const message = String((error && error.message) || error || "").trim();
  if (!message) return "";
  if (!firstPipelineError) firstPipelineError = message;
  if (!warnedPipelineErrors.has(message)) {
    warnedPipelineErrors.add(message);
    console.warn(`Texture pipeline: ${message}`);
  }
  return message;
}

export function texturePipelineError() {
  return firstPipelineError;
}

export function resetTexturePipelineError() {
  firstPipelineError = "";
  warnedPipelineErrors.clear();
}

export function formatTextureDebug(report) {
  const row = report || {};
  const line = [
    "forge texdebug",
    `shown=${row.shown ? 1 : 0}`,
    `batches=${Number(row.batches) || 0}`,
    `meshMaps=${Number(row.meshMaps) || 0}`,
    `partMaps=${Number(row.partMaps) || 0}`,
    `litOnly=${Number(row.litOnly) || 0}`,
    `missingUv=${Number(row.missingUv) || 0}`,
    `foreign=${Number(row.foreign) || 0}`,
    `uploaded=${Number(row.uploaded) || 0}`,
    `profileTex=${Number(row.profileTex) || 0}`,
    `allow=${row.allow ? 1 : 0}`,
    `requested=${Number(row.requested) || 0}`,
    `fetched=${Number(row.fetched) || 0}`,
    `decoded=${Number(row.decoded) || 0}`,
    `assigned=${Number(row.assigned) || 0}`,
  ];
  if (row.error) line.push(`error=${row.error}`);
  const text = line.join(" ");
  const samples = [];
  for (const sample of row.samples || []) {
    samples.push(
      [
        sample.name || "batch",
        `meshMap=${sample.meshMap ? 1 : 0}`,
        `partMap=${sample.partMap ? 1 : 0}`,
        `uv=${sample.uv ? 1 : 0}`,
        `ch=${Number.isInteger(sample.channel) ? sample.channel : 0}`,
        `compressed=${sample.compressed ? 1 : 0}`,
        `foreign=${sample.foreign ? 1 : 0}`,
        `uploaded=${sample.uploaded ? 1 : 0}`,
        `same=${sample.sameMaterial ? 1 : 0}`,
      ].join(" "),
    );
  }
  return [text, ...samples].join("\n");
}
