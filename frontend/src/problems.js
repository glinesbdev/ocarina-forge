/** Group browser model failures so Problems can list the actors, not one line per copy. */

export function groupModelErrors(entries, actors) {
  const groups = new Map();
  const seen = new Set();
  for (const entry of entries || []) {
    const gyaml = entry && entry.gyaml;
    const message = (entry && entry.message) || "The glTF did not load.";
    if (!gyaml || seen.has(gyaml)) continue;
    seen.add(gyaml);
    const hits = (actors || []).filter((actor) => actor && actor.gyaml === gyaml);
    const rows = hits.length
      ? hits.map((actor) => ({ id: actor.id || "", gyaml, name: actor.name || gyaml }))
      : [{ id: "", gyaml, name: gyaml }];
    if (!groups.has(message)) groups.set(message, []);
    groups.get(message).push(...rows);
  }
  return [...groups.entries()].map(([message, rows]) => ({ message, actors: rows, count: rows.length }));
}

/** Texture slot for an overlay map. -1 when the glb does not have that image. */
export function resolveTextureIndex(json, index, imageName) {
  const textures = (json && json.textures) || [];
  const images = (json && json.images) || [];
  if (imageName) {
    const image = images.findIndex((item) => item && item.name === imageName);
    if (image >= 0) {
      const found = textures.findIndex((item) => item && item.source === image);
      if (found >= 0) return found;
    }
  }
  if (Number.isInteger(index) && index >= 0 && index < textures.length) return index;
  return -1;
}
