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
  scene: (banc) => request(`/api/scene?banc=${encodeURIComponent(banc)}`),
  sceneChanges: (banc) => request(`/api/scene/changes?banc=${encodeURIComponent(banc)}`),
  problems: (banc) => request(`/api/scene/problems?banc=${encodeURIComponent(banc)}`),
  meshAudit: (banc) => request(`/api/scene/mesh-audit?banc=${encodeURIComponent(banc)}`),
  hotReload: () => request("/api/hot-reload"),
  saveHotReload: (body) => request("/api/hot-reload", { method: "PUT", body: JSON.stringify(body) }),
  projectChanges: () => request("/api/changes"),
  edit: (banc, command) => request("/api/scene/edit", { method: "POST", body: JSON.stringify({ banc, command }) }),
  putDiff: (banc, actors) => request("/api/scene/diff", { method: "PUT", body: JSON.stringify({ banc, actors }) }),
  putRecipes: (banc, recipes) => request("/api/scene/recipes", { method: "PUT", body: JSON.stringify({ banc, recipes }) }),
  actors: (q) => request(`/api/actors?q=${encodeURIComponent(q)}`),
  behavior: ({ banc = "", id = "", gyaml = "" } = {}) =>
    request(`/api/behavior?banc=${encodeURIComponent(banc)}&id=${encodeURIComponent(id)}&gyaml=${encodeURIComponent(gyaml)}`),
  setBehaviorField: (body) => request("/api/behavior/field", { method: "POST", body: JSON.stringify(body) }),
  putBehaviorOps: (body) => request("/api/behavior/ops", { method: "PUT", body: JSON.stringify(body) }),
  exportBehavior: (gyaml) => request("/api/behavior/export", { method: "POST", body: JSON.stringify({ gyaml }) }),
  flags: (q) => request(`/api/flags?q=${encodeURIComponent(q)}`),
  model: (gyaml, options) => request(`/api/models/${encodeURIComponent(gyaml)}`, options),
  models: (names, prefetch = true) =>
    request("/api/models/batch", { method: "POST", body: JSON.stringify({ names, prefetch }) }).then((data) => data.models || {}),
  terrain: (banc, refine = false) => request(`/api/terrain?banc=${encodeURIComponent(banc)}${refine ? "&refine=true" : ""}`),
  ground: (banc, x, z, exclude) => request(`/api/ground?banc=${encodeURIComponent(banc)}&x=${encodeURIComponent(x)}&z=${encodeURIComponent(z)}${exclude ? `&exclude=${encodeURIComponent(exclude)}` : ""}`),
  lighting: (banc) => request(`/api/lighting?banc=${encodeURIComponent(banc)}`),
  groundSurface: (banc) => `/api/ground/surface?banc=${encodeURIComponent(banc)}`,
  exportMod: (body) => request("/api/export", { method: "POST", body: JSON.stringify(body) }),
  importAto: (path) => request("/api/import/ato", { method: "POST", body: JSON.stringify({ path }) }),
};
