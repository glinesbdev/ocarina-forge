/** Fuzzy command palette. Actors, scenes, and editor commands share one list.
 * A query matches a name, an actor class, a hash, a scene path, or a command. */

export const EDITOR_COMMANDS = [
  { id: "undo", label: "Undo", detail: "Ctrl+Z", fields: ["undo"] },
  { id: "redo", label: "Redo", detail: "Ctrl+Shift+Z", fields: ["redo"] },
  { id: "frame", label: "Frame selection", detail: "F", fields: ["frame", "focus", "camera"] },
  { id: "snap", label: "Toggle snap to ground", detail: "V", fields: ["snap", "ground", "collision", "surface"] },
  { id: "add", label: "Add an actor", detail: "", fields: ["add", "spawn", "place", "new actor"] },
  { id: "hide", label: "Hide or show selection", detail: "H", fields: ["hide", "unhide", "show selection"] },
  { id: "duplicate", label: "Duplicate selection", detail: "", fields: ["duplicate", "copy"] },
  { id: "delete", label: "Delete selection", detail: "Delete", fields: ["delete", "remove"] },
  { id: "hidden", label: "Toggle hidden actors", detail: "", fields: ["hidden", "show hidden"] },
  { id: "changes", label: "Show changes", detail: "", fields: ["changes", "diff", "history"] },
  { id: "problems", label: "Show problems", detail: "", fields: ["problems", "validation", "diagnostics", "errors"] },
  { id: "project", label: "Project settings", detail: "", fields: ["project", "dump", "open"] },
  { id: "build", label: "Build the mod", detail: "", fields: ["build", "export", "install"] },
  { id: "ground", label: "Toggle ground", detail: "", fields: ["ground", "terrain", "heightmap"] },
  { id: "stats", label: "Toggle stats", detail: "", fields: ["stats", "fps", "frame time"] },
];

export function fuzzyScore(query, text) {
  const needle = String(query || "").trim().toLowerCase();
  const hay = String(text || "").toLowerCase();
  if (!needle) return 1;
  if (!hay) return 0;
  if (hay === needle) return 1000;
  if (hay.startsWith(needle)) return 820 - Math.min(hay.length, 200);
  const word = hay.indexOf(needle);
  if (word >= 0) return 640 - Math.min(word, 200);
  let from = 0;
  let streak = 0;
  let score = 120;
  for (const char of needle) {
    const at = hay.indexOf(char, from);
    if (at < 0) return 0;
    streak = at === from ? streak + 1 : 1;
    score += 6 + streak * 3 - Math.min(at - from, 12);
    from = at + 1;
  }
  return score;
}

export function bestScore(query, fields) {
  const tokens = String(query || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (!tokens.length) return 1;
  let total = 0;
  for (const token of tokens) {
    let best = 0;
    for (const field of fields || []) {
      const score = fuzzyScore(token, field);
      if (score > best) best = score;
    }
    if (best <= 0) return 0;
    total += best;
  }
  return total;
}

export function paletteRows({ actors = [], scenes = [], commands = EDITOR_COMMANDS, query = "", limit = 40 } = {}) {
  const items = [];
  for (const actor of actors) {
    const label = actor.name || actor.gyaml || "Actor";
    items.push({
      kind: "actor",
      id: actor.id || "",
      label,
      detail: [actor.gyaml, actor.id].filter(Boolean).join(" · "),
      fields: [actor.name, actor.gyaml, actor.id, actor.category, actor.companionLabel],
    });
  }
  for (const scene of scenes) {
    items.push({
      kind: "scene",
      id: scene.path || "",
      label: scene.name || scene.path || "Scene",
      detail: scene.area ? `${scene.area} · ${scene.path}` : scene.path || "",
      fields: [scene.name, scene.area, scene.path],
    });
  }
  for (const command of commands) {
    items.push({
      kind: "command",
      id: command.id,
      label: command.label,
      detail: command.detail || "Command",
      fields: [command.label, command.detail, ...(command.fields || [])],
    });
  }
  const needle = String(query || "").trim();
  const ranked = [];
  for (const item of items) {
    const score = bestScore(needle, item.fields);
    if (needle && score <= 0) continue;
    ranked.push({ item, score });
  }
  if (!needle) {
    const order = { command: 0, actor: 1, scene: 2 };
    ranked.sort((a, b) => order[a.item.kind] - order[b.item.kind] || a.item.label.localeCompare(b.item.label));
  } else {
    ranked.sort((a, b) => b.score - a.score || a.item.label.localeCompare(b.item.label));
  }
  return ranked.slice(0, limit).map((row) => row.item);
}
