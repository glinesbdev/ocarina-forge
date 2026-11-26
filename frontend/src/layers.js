/**
 * Load filters. The ids match `ocarina_forge/layers.py`.
 *
 * A load filter skips the fetch. A visibility flag only hides what is already
 * loaded. `textures` is not an actor layer. Geometry is one glb. Images are a
 * separate manifest. Turning textures on or off does not fetch that glb.
 * Save still sends the stored diff for every layer that is off.
 */

export const LAYERS = [
  "models",
  "terrain",
  "water",
  "collision",
  "behavior",
  "effects",
  "decorations",
  "lights",
];

export const LAYER_LABELS = {
  models: "Models",
  terrain: "Terrain",
  water: "Water",
  collision: "Collision",
  behavior: "Behavior",
  effects: "Effects",
  decorations: "Decorations",
  lights: "Lights",
  textures: "Textures",
};

const WATER = new Set(["water", "lake", "waterfall", "ocean", "pond", "sea", "river", "stream", "moat", "aqua", "mizu", "brook"]);
const LIGHTS = new Set(["light", "lamp", "torch", "candle", "lantern", "chandelier"]);
const DECORATIONS = new Set(["grass", "flower", "bush", "weed", "ivy", "fern", "mushroom", "moss", "vine", "shrub", "plant", "clover", "herb", "petal"]);
const COLLISION = ["airwall", "collision", "collider", "navmesh"];

export const PRESETS = {
  full: { layers: LAYERS, textures: true, label: "Full" },
  visual: {
    layers: ["models", "terrain", "water", "effects", "decorations", "lights"],
    textures: true,
    label: "Visual only",
  },
  layout: {
    layers: ["models", "terrain", "water", "collision", "decorations", "lights"],
    textures: false,
    label: "Layout",
  },
  "terrain-water": { layers: ["terrain", "water"], textures: true, label: "Terrain + water" },
  logic: { layers: ["behavior", "collision"], textures: false, label: "Logic" },
};

function tokens(name) {
  const spaced = String(name || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2");
  return new Set(spaced.toLowerCase().match(/[a-z0-9]+/g) || []);
}

export function actorLayer(gyaml) {
  const name = gyaml || "";
  if (/^(Npc_|Enemy_|Animal_|Shop_)/.test(name)) return "models";
  if (name.startsWith("Eft")) return "effects";
  const words = tokens(name);
  if (!words.has("npc") && [...words].some((word) => WATER.has(word))) return "water";
  const folded = name.toLowerCase();
  if ([...words].some((word) => COLLISION.includes(word)) || COLLISION.some((word) => folded.includes(word))) return "collision";
  if ([...words].some((word) => LIGHTS.has(word))) return "lights";
  if ([...words].some((word) => DECORATIONS.has(word))) return "decorations";
  return "models";
}

export function recordLayer(record) {
  if (!record) return "models";
  return actorLayer(record.gyaml || record.actor || "");
}

function layerMap(ids, on) {
  const set = new Set(ids);
  return Object.fromEntries(LAYERS.map((id) => [id, on ? set.has(id) : false]));
}

function visibleMap(source) {
  const next = {};
  for (const id of LAYERS) next[id] = !source || source[id] !== false;
  return next;
}

export function fullLoad() {
  return {
    preset: "full",
    textures: true,
    layers: layerMap(LAYERS, true),
    visible: { ...visibleMap(), terrain: false },
  };
}

export function presetLoad(name, visible) {
  const preset = PRESETS[name];
  if (!preset) return fullLoad();
  return {
    preset: name,
    textures: preset.textures,
    layers: layerMap(preset.layers, true),
    visible: visibleMap(visible),
  };
}

function matchPreset(layers, textures) {
  for (const [name, preset] of Object.entries(PRESETS)) {
    const same = LAYERS.every((id) => Boolean(layers[id]) === preset.layers.includes(id));
    if (same && Boolean(textures) === preset.textures) return name;
  }
  return "custom";
}

export function loadFromLayers(layers, textures, visible) {
  const normalized = {};
  for (const id of LAYERS) normalized[id] = Boolean(layers && layers[id]);
  const flag = textures !== false;
  return {
    preset: matchPreset(normalized, flag),
    textures: flag,
    layers: normalized,
    visible: visibleMap(visible),
  };
}

export function parseLoadParam(text, textures) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  const forced = textures == null || textures === ""
    ? null
    : !["0", "false", "off", "no"].includes(String(textures).toLowerCase());
  if (!raw.includes(",") && PRESETS[raw]) {
    const preset = presetLoad(raw);
    if (forced == null || forced === preset.textures) return preset;
    return loadFromLayers(preset.layers, forced, preset.visible);
  }
  const layers = {};
  for (const id of LAYERS) layers[id] = false;
  for (const token of raw.split(",")) {
    const name = token.trim().toLowerCase();
    if (LAYERS.includes(name)) layers[name] = true;
  }
  const texturesOn = forced == null
    ? Boolean(layers.models || layers.water || layers.decorations || layers.lights)
    : forced;
  return loadFromLayers(layers, texturesOn);
}

export function normalizeLoad(data) {
  if (!data || typeof data !== "object") return fullLoad();
  if (data.layers && typeof data.layers === "object") {
    return loadFromLayers(data.layers, data.textures !== false, data.visible);
  }
  if (data.preset && PRESETS[data.preset]) return presetLoad(data.preset, data.visible);
  return fullLoad();
}

export function enabledLayers(load) {
  return LAYERS.filter((id) => load && load.layers && load.layers[id]);
}

export function layersOff(load) {
  const off = LAYERS.filter((id) => !load || !load.layers || !load.layers[id]);
  const meshes = load && (load.layers.models || load.layers.water || load.layers.decorations || load.layers.lights);
  if (meshes && load.textures === false) off.push("textures");
  return off;
}

/**
 * Banner copy when this project will fetch meshes without images.
 * Empty when textures are on, or when no mesh layer is fetched (Logic).
 * The stage-bar Textures checkbox only shows or hides images already loaded.
 */
export function textureFilterNotice(load) {
  if (!load || load.textures !== false) return "";
  const layers = load.layers || {};
  const meshes = layers.models || layers.terrain || layers.water || layers.decorations || layers.lights;
  if (!meshes) return "";
  return "Load textures is off for this project, so models are fetched without images. That can look like a hang. Turn it on with Load textures in the Actors panel. The Textures checkbox on the stage bar only shows or hides images that are already loaded. The first time you check that box while this filter is off, it turns Load textures on.";
}

export function loadQuery(load) {
  if (!load) return "";
  if (load.preset && load.preset !== "custom" && PRESETS[load.preset]) return load.preset;
  return enabledLayers(load).join(",");
}

export function wantsMesh(actor) {
  if (!actor || actor.logicOnly || actor.fetchModel === false) return false;
  const layer = actor.layer || actorLayer(actor.gyaml);
  return layer !== "collision" && layer !== "behavior";
}

export function filterActors(actors, load) {
  const loaded = new Set(enabledLayers(load));
  const rows = actors || [];
  const keep = new Set();
  rows.forEach((actor, index) => {
    if (!actor) return;
    const layer = actor.layer || actorLayer(actor.gyaml);
    if (loaded.has(layer) || (actor.logicOnly && loaded.has("behavior"))) keep.add(index);
  });
  if (loaded.has("behavior")) {
    const byHash = new Map();
    rows.forEach((actor, index) => {
      const hash = actor.placementId || String(actor.id || "").split("::").pop();
      if (hash) byHash.set(hash, index);
    });
    rows.forEach((actor, index) => {
      if (!actor || !(actor.links || []).length) return;
      keep.add(index);
      for (const target of actor.links) {
        const hit = byHash.get(target);
        if (hit != null) keep.add(hit);
      }
    });
  }
  return rows.filter((actor, index) => keep.has(index)).map((actor) => {
    const layer = actor.layer || actorLayer(actor.gyaml);
    if (loaded.has(layer) || actor.logicOnly) return actor;
    return { ...actor, logicOnly: true, fetchModel: false, sourceLayer: layer, layer: "behavior" };
  });
}

export function linkPairs(actors) {
  const byHash = new Map();
  for (const actor of actors || []) {
    const hash = actor.placementId || String(actor.id || "").split("::").pop();
    if (hash) byHash.set(hash, actor.id);
  }
  const pairs = [];
  const seen = new Set();
  for (const actor of actors || []) {
    for (const target of actor.links || []) {
      const other = byHash.get(target);
      if (!other || other === actor.id) continue;
      const key = [actor.id, other].sort().join(">");
      if (seen.has(key)) continue;
      seen.add(key);
      pairs.push([actor.id, other]);
    }
  }
  return pairs;
}

/**
 * Records for layers that are off stay the stored objects. A save that never
 * received those actors cannot replace or drop them.
 */
export function mergeStoredDiff(stored, incoming, loadedLayers) {
  const loaded = new Set(loadedLayers || []);
  const kept = {};
  const prior = stored || {};
  for (const [id, record] of Object.entries(incoming || {})) {
    if (!record || typeof record !== "object") continue;
    if (!loaded.has(recordLayer(record))) {
      if (prior[id]) kept[id] = prior[id];
      continue;
    }
    kept[id] = record;
  }
  for (const [id, record] of Object.entries(prior)) {
    if (kept[id] || !record) continue;
    if (!loaded.has(recordLayer(record))) kept[id] = record;
  }
  return kept;
}

export function fullVisual(load) {
  if (!load || !load.textures) return false;
  return ["models", "terrain", "water", "effects", "decorations", "lights"].every((id) => load.layers[id]);
}

/**
 * Requests for one actor type. Geometry and textures are different fetches.
 * A side that is already resident is not requested again. Textures are
 * requested only when the mesh is wanted or already in memory.
 */
export function planAssetFetches({
  wantGeometry = false,
  geometryResident = false,
  wantTextures = false,
  texturesResident = false,
} = {}) {
  const geometry = Boolean(wantGeometry);
  const resident = Boolean(geometryResident);
  return {
    models: geometry && !resident ? 1 : 0,
    textures: Boolean(wantTextures) && (geometry || resident) && !texturesResident ? 1 : 0,
  };
}
