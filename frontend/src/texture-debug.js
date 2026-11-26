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
  ].join(" ");
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
  return [line, ...samples].join("\n");
}
