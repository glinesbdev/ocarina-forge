export async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { message: text };
    }
  }
  if (!response.ok) {
    const error = new Error(data.message || `Request failed (${response.status}).`);
    error.code = data.error || "error";
    throw error;
  }
  return data;
}

export const api = {
  health: () => request("/api/health"),
  status: () => request("/api/status"),
  stream: () => request("/api/stream"),
  saveConfig: (body) => request("/api/config", { method: "PUT", body: JSON.stringify(body) }),
  sample: () => request("/api/setup/sample", { method: "POST", body: "{}" }),
  createProject: (body) => request("/api/project", { method: "POST", body: JSON.stringify(body) }),
  projects: () => request("/api/projects"),
  localMods: () => request("/api/local-mods"),
  openLocalMod: (id) => request("/api/local-mods/open", { method: "POST", body: JSON.stringify({ id }) }),
  openProject: (directory) => request("/api/project/open", { method: "POST", body: JSON.stringify({ directory }) }),
  exportLongshot: (body) => request("/api/mod-export", { method: "POST", body: JSON.stringify(body) }),
  scenes: () => request("/api/scenes"),
  scene: (banc, load) => {
    const params = new URLSearchParams({ banc });
    if (load && load.query) params.set("load", load.query);
    if (load && load.textures != null && load.textures !== "") params.set("textures", String(load.textures));
    return request(`/api/scene?${params}`);
  },
  sceneChanges: (banc) => request(`/api/scene/changes?banc=${encodeURIComponent(banc)}`),
  problems: (banc) => request(`/api/scene/problems?banc=${encodeURIComponent(banc)}`),
  meshAudit: (banc) => request(`/api/scene/mesh-audit?banc=${encodeURIComponent(banc)}`),
  hotReload: () => request("/api/hot-reload"),
  saveHotReload: (body) => request("/api/hot-reload", { method: "PUT", body: JSON.stringify(body) }),
  projectChanges: () => request("/api/changes"),
  edit: (banc, command, load) => request("/api/scene/edit", {
    method: "POST",
    body: JSON.stringify({ banc, command, ...(load || {}) }),
  }),
  putDiff: (banc, actors, load) => request("/api/scene/diff", {
    method: "PUT",
    body: JSON.stringify({ banc, actors, ...(load || {}) }),
  }),
  putRecipes: (banc, recipes) => request("/api/scene/recipes", { method: "PUT", body: JSON.stringify({ banc, recipes }) }),
  loadFilters: () => request("/api/load-filters"),
  putLoadFilters: (body) => request("/api/load-filters", { method: "PUT", body: JSON.stringify(body) }),
  actors: (q) => request(`/api/actors?q=${encodeURIComponent(q)}`),
  behavior: ({ banc = "", id = "", gyaml = "", load = "" } = {}) => {
    const params = new URLSearchParams({ banc, id, gyaml });
    if (load) params.set("load", load);
    return request(`/api/behavior?${params}`);
  },
  setBehaviorField: (body) => request("/api/behavior/field", { method: "POST", body: JSON.stringify(body) }),
  putBehaviorOps: (body) => request("/api/behavior/ops", { method: "PUT", body: JSON.stringify(body) }),
  exportBehavior: (gyaml) => request("/api/behavior/export", { method: "POST", body: JSON.stringify({ gyaml }) }),
  flags: (q) => request(`/api/flags?q=${encodeURIComponent(q)}`),
  actorTextures: (gyaml, options = {}) => {
    const params = new URLSearchParams();
    if (options.load) params.set("load", options.load);
    const query = params.toString();
    return request(`/api/models/${encodeURIComponent(gyaml)}/textures${query ? `?${query}` : ""}`);
  },
  model: (gyaml, options = {}) => {
    const params = new URLSearchParams();
    if (options.load) params.set("load", options.load);
    if (options.textures != null && options.textures !== "") params.set("textures", String(options.textures));
    if (options.flat) params.set("flat", "1");
    const query = params.toString();
    const init = {};
    if (options.priority) init.priority = options.priority;
    return request(`/api/models/${encodeURIComponent(gyaml)}${query ? `?${query}` : ""}`, init);
  },
  models: (names, prefetch = true, options = {}) =>
    request("/api/models/batch", {
      method: "POST",
      body: JSON.stringify({
        names,
        prefetch,
        load: options.load || "",
        textures: options.textures,
        flat: options.flat ? 1 : 0,
      }),
    }).then((data) => data.models || {}),
  terrain: (banc, refine = false, options = {}) => {
    const params = new URLSearchParams({ banc });
    if (refine) params.set("refine", "true");
    if (options.load) params.set("load", options.load);
    if (options.textures != null && options.textures !== "") params.set("textures", String(options.textures));
    return request(`/api/terrain?${params}`);
  },
  ground: (banc, x, z, exclude, options = {}) => {
    const params = new URLSearchParams({ banc, x: String(x), z: String(z) });
    if (exclude) params.set("exclude", exclude);
    if (options.load) params.set("load", options.load);
    return request(`/api/ground?${params}`);
  },
  groundSurface: (banc, options = {}) => {
    const params = new URLSearchParams({ banc });
    if (options.load) params.set("load", options.load);
    return `/api/ground/surface?${params}`;
  },
  exportMod: (body) => request("/api/export", { method: "POST", body: JSON.stringify(body) }),
  importAto: (path) => request("/api/import/ato", { method: "POST", body: JSON.stringify({ path }) }),
};
