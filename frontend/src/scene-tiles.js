/**
 * Gates for the experimental Field tileset. The renderer is not imported
 * here, so a page with the toggle off never constructs one.
 *
 * Static scenery is a model or decoration on a Static banc. Dynamic bancs,
 * water, terrain, lights, collision, effects, areas, and system actors stay
 * on the current batches. The selected actor is still baked; the view hides
 * that tile and shows the actor's own objects.
 */

export const TILE_CELL_METRES = 256;

const TILE_LAYERS = new Set(["models", "decorations"]);

export function tilesEnabled({ query = "", setting = false } = {}) {
  const params = typeof query === "string" ? new URLSearchParams(query) : query;
  return params.get("tiles") === "1" || setting === true;
}

export function tileEligible(actor) {
  if (!actor || actor.volume || actor.region) return false;
  const banc = String(actor.banc || "");
  if (!banc.includes("_Static")) return false;
  if (banc.includes("_Dynamic")) return false;
  const layer = String(actor.layer || "");
  if (!TILE_LAYERS.has(layer)) return false;
  const category = String(actor.category || "");
  if (category === "area" || category === "system") return false;
  const gyaml = String(actor.gyaml || "");
  if (!gyaml || /water|lake|river|ocean|pond|waterfall/i.test(gyaml)) return false;
  return true;
}

export function cellId(x, z, cell = TILE_CELL_METRES) {
  const cx = Math.floor(Number(x) / cell);
  const cz = Math.floor(Number(z) / cell);
  return `${cx}_${cz}`;
}

/**
 * Hide the batched copy only after that actor's tile has loaded.
 * `live` stays false until a model for this actor is on screen, so a loader
 * error leaves the current batches visible.
 */
export function tileHides({ tilesOn = false, held = false, opened = false, live = false } = {}) {
  return Boolean(tilesOn) && Boolean(live) && Boolean(held) && !opened;
}

/**
 * Actor id stored on a baked mesh. glTF loads the accessor as `_feature_id_0`.
 * An instanced copy keeps one id per instance; a joined mesh keeps one per vertex.
 */
export function featureIdOnHit(hit) {
  const geometry = hit && hit.object && hit.object.geometry;
  const attr = geometry && typeof geometry.getAttribute === "function"
    ? geometry.getAttribute("_feature_id_0")
    : null;
  if (!attr || typeof attr.getX !== "function") return 0;
  let index = 0;
  if (attr.isInstancedBufferAttribute && Number.isInteger(hit.instanceId)) {
    index = hit.instanceId;
  } else if (hit.face && geometry.index && typeof geometry.index.getX === "function") {
    index = geometry.index.getX(hit.face.a);
  } else if (Number.isInteger(hit.faceIndex) && geometry.index) {
    index = geometry.index.getX(hit.faceIndex * 3);
  }
  const value = Number(attr.getX(index));
  if (!Number.isFinite(value) || value <= 0) return 0;
  return Math.round(value);
}

/** The pick id is the feature id. `keyFor` is the session map from gpu-pick. */
export function actorKeyFromFeature(featureId, keyFor) {
  const id = Math.round(Number(featureId));
  if (!id || typeof keyFor !== "function") return null;
  return keyFor(id) || null;
}

/** A failed bake is not sent again until the checkbox or a save asks. */
export function shouldRequestTileBake({ failed = false, reason = "settle" } = {}) {
  if (reason === "toggle" || reason === "save") return true;
  return !failed;
}

export function tileCounters({ enabled = false, loaded = 0, visible = 0, cachedBytes = 0, bakeMs = 0 } = {}) {
  return {
    tilesLoaded: enabled ? Number(loaded) || 0 : 0,
    tilesVisible: enabled ? Number(visible) || 0 : 0,
    tilesBytes: enabled ? Number(cachedBytes) || 0 : 0,
    bakeMs: Number(bakeMs) || 0,
  };
}
