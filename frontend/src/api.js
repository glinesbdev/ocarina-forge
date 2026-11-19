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
  status: () => request("/api/status"),
  saveConfig: (body) => request("/api/config", { method: "PUT", body: JSON.stringify(body) }),
  sample: () => request("/api/setup/sample", { method: "POST", body: "{}" }),
  createProject: (body) => request("/api/project", { method: "POST", body: JSON.stringify(body) }),
  openProject: (directory) => request("/api/project/open", { method: "POST", body: JSON.stringify({ directory }) }),
  scenes: () => request("/api/scenes"),
  scene: (banc) => request(`/api/scene?banc=${encodeURIComponent(banc)}`),
  sceneChanges: (banc) => request(`/api/scene/changes?banc=${encodeURIComponent(banc)}`),
  projectChanges: () => request("/api/changes"),
  edit: (banc, command) => request("/api/scene/edit", { method: "POST", body: JSON.stringify({ banc, command }) }),
  putDiff: (banc, actors) => request("/api/scene/diff", { method: "PUT", body: JSON.stringify({ banc, actors }) }),
  actors: (q) => request(`/api/actors?q=${encodeURIComponent(q)}`),
  model: (gyaml) => request(`/api/models/${encodeURIComponent(gyaml)}`),
  terrain: (banc) => request(`/api/terrain?banc=${encodeURIComponent(banc)}`),
  exportMod: (body) => request("/api/export", { method: "POST", body: JSON.stringify(body) }),
  importAto: (path) => request("/api/import/ato", { method: "POST", body: JSON.stringify({ path }) }),
};
