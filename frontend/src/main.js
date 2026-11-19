import { api } from "./api.js";
import { createViewport } from "./viewport.js";

const $ = (id) => document.getElementById(id);

const state = {
  status: null,
  scenes: [],
  modified: new Set(),
  recipes: new Set(),
  projectOnly: [],
  banc: null,
  view: null,
  selected: null,
  history: { undo: [], redo: [] },
  showHidden: false,
  showLabels: true,
  nav: "orbit",
  gizmo: "translate",
  tab: "outliner",
  addMode: "add",
};

const viewport = createViewport($("viewport"), {
  onSelect: (id) => {
    state.selected = id;
    viewport.select(id);
    renderOutliner();
    renderProps();
    if (id && window.matchMedia("(max-width: 980px)").matches) showTab("props");
  },
  onPreview: (id, translate, rotate) => {
    if (id !== state.selected) return;
    fillNumber("tx", translate[0]);
    fillNumber("ty", translate[1]);
    fillNumber("tz", translate[2]);
    fillNumber("rx", deg(rotate[0]));
    fillNumber("ry", deg(rotate[1]));
    fillNumber("rz", deg(rotate[2]));
  },
  onTransform: (id, translate, rotate) => {
    edit({ command: "transform", id, translate: translate.map(round4), rotate: rotate.map(round4) });
  },
});

$("undo").addEventListener("click", () => undo());
$("redo").addEventListener("click", () => redo());
$("build-open").addEventListener("click", () => $("build-dialog").showModal());
$("project-chip").addEventListener("click", openProjectDialog);
$("open-scenes").addEventListener("click", () => togglePane("scene-pane"));
$("open-side").addEventListener("click", () => togglePane("side-pane"));
$("scene-search").addEventListener("input", renderScenes);
$("actor-search").addEventListener("input", renderOutliner);
$("add-actor").addEventListener("click", () => openAdd("add"));
$("show-hidden").addEventListener("change", (event) => {
  state.showHidden = event.target.checked;
  viewport.setFlags({ hidden: state.showHidden });
  renderOutliner();
});
$("show-labels").addEventListener("change", (event) => {
  state.showLabels = event.target.checked;
  viewport.setFlags({ labels: state.showLabels });
});
$("frame").addEventListener("click", () => viewport.frame(state.selected));
$("tab-outliner").addEventListener("click", () => showTab("outliner"));
$("tab-props").addEventListener("click", () => showTab("props"));
document.querySelectorAll("[data-nav]").forEach((button) => {
  button.addEventListener("click", () => setNav(button.dataset.nav));
});
document.querySelectorAll("[data-gizmo]").forEach((button) => {
  button.addEventListener("click", () => setGizmo(button.dataset.gizmo));
});
document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});
$("build-run").addEventListener("click", runBuild);
$("add-search").addEventListener("input", () => loadActorNames($("add-search").value));
$("create-project").addEventListener("click", createProject);
$("open-project").addEventListener("click", openProject);
$("open-ato").addEventListener("click", () => {
  const root = state.status && state.status.repo;
  if (!root) return;
  $("open-dir").value = `${root}/examples/after-the-ocarina`;
  openProject();
});
$("import-run").addEventListener("click", () => importAto($("import-path").value));
$("import-example").addEventListener("click", () => {
  const root = state.status && state.status.repo;
  if (!root) return;
  importAto(`${root}/examples/ato-scenes`);
});
$("save-dump").addEventListener("click", saveDump);
$("use-sample").addEventListener("click", useSample);

window.addEventListener("keydown", (event) => {
  if (isTyping(event)) return;
  const meta = event.metaKey || event.ctrlKey;
  if (meta && event.key.toLowerCase() === "z" && !event.shiftKey) {
    event.preventDefault();
    undo();
  } else if (meta && (event.key.toLowerCase() === "y" || (event.shiftKey && event.key.toLowerCase() === "z"))) {
    event.preventDefault();
    redo();
  } else if (event.key === "f") {
    viewport.frame(state.selected);
  } else if (event.key === "g") {
    setGizmo("translate");
  } else if (event.key === "r") {
    setGizmo("rotate");
  } else if (event.key === "h" && state.selected) {
    const actor = selectedActor();
    edit({ command: actor && actor.status === "hidden" ? "unhide" : "hide", id: state.selected });
  } else if (event.key === "Delete" && state.selected) {
    edit({ command: "remove", id: state.selected });
  } else if (event.key === "Escape") {
    state.selected = null;
    viewport.select(null);
    renderOutliner();
    renderProps();
  }
});

boot();

async function boot() {
  try {
    state.status = await api.status();
  } catch (error) {
    $("gate").hidden = false;
    $("gate").replaceChildren(gateMessage("Bancwright could not reach its server.", error.message));
    return;
  }
  renderChrome();
  if (!dumpReady()) {
    showGate();
    return;
  }
  $("gate").hidden = true;
  $("workspace").hidden = false;
  viewport.resize();
  syncPanes();
  await refreshScenes();
}

const mobileQuery = window.matchMedia("(max-width: 980px)");
mobileQuery.addEventListener("change", syncPanes);

function syncPanes() {
  const mobile = mobileQuery.matches;
  $("scene-pane").classList.toggle("shut", mobile);
  $("side-pane").classList.toggle("shut", mobile);
  viewport.resize();
}

function togglePane(id) {
  const pane = $(id);
  const other = $(id === "scene-pane" ? "side-pane" : "scene-pane");
  const willOpen = pane.classList.contains("shut");
  pane.classList.toggle("shut", !willOpen);
  if (willOpen) other.classList.add("shut");
  viewport.resize();
}

function dumpReady() {
  return Boolean(state.status && state.status.dump && !state.status.dump.error);
}

function projectReady() {
  return Boolean(state.status && state.status.project && !state.status.project.error);
}

function renderChrome() {
  const dump = state.status.dump;
  const project = state.status.project;
  const banner = $("banner");
  banner.replaceChildren();
  const notes = [];
  if (dump && dump.synthetic) notes.push("Sample dump. These actors are stand-ins, not the game.");
  if (dump && !dump.error && !projectReady()) {
    notes.push("No project is open. You can look around. Edits need a project.");
  }
  if (dump && dump.error) notes.push(dump.message);
  if (project && project.error) notes.push(project.message);
  banner.hidden = notes.length === 0;
  if (!banner.hidden) {
    banner.append(document.createTextNode(notes.join(" ")));
    if (!projectReady() && dumpReady()) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = "Open a project";
      button.addEventListener("click", openProjectDialog);
      banner.append(button);
    }
  }
  $("workspace").classList.toggle("with-banner", !banner.hidden);
  const chip = $("project-chip");
  chip.hidden = false;
  if (projectReady()) {
    $("project-title").textContent = project.manifest.title || project.manifest.name;
    $("project-name").textContent = project.manifest.name;
    $("build-open").hidden = false;
  } else {
    $("project-title").textContent = dumpReady() ? "No project" : "Set up Bancwright";
    $("project-name").textContent = dump && dump.root ? dump.root : "";
    $("build-open").hidden = true;
  }
  renderProjectContents();
}

function showGate() {
  $("workspace").hidden = true;
  $("gate").hidden = false;
  const card = document.createElement("div");
  card.className = "gate-card";
  const title = document.createElement("h1");
  title.textContent = "Point Bancwright at your dump";
  const lede = document.createElement("p");
  lede.className = "lede";
  lede.textContent = "The dump is the Longshot folder that contains romfs. Bancwright only reads it. Edits go into a project folder, and Build writes a mod Longshot can load.";
  const path = document.createElement("input");
  path.placeholder = "~/Games/Longshot/rc/v3.0.0";
  path.value = (state.status.config && state.status.config.dump) || "";
  const error = document.createElement("p");
  error.className = "muted";
  if (state.status.dump && state.status.dump.error) error.textContent = state.status.dump.message;
  const row = document.createElement("div");
  row.className = "row";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "primary";
  save.textContent = "Use this dump";
  save.addEventListener("click", async () => {
    try {
      state.status = await api.saveConfig({ dump: path.value.trim() });
      renderChrome();
      if (dumpReady()) {
        $("gate").hidden = true;
        $("workspace").hidden = false;
        viewport.resize();
        await refreshScenes();
        if (!projectReady()) openProjectDialog();
      } else {
        showGate();
      }
    } catch (err) {
      error.textContent = err.message;
    }
  });
  const sample = document.createElement("button");
  sample.type = "button";
  sample.className = "ghost";
  sample.textContent = "Use the bundled sample dump";
  sample.addEventListener("click", useSample);
  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = "The sample is a tiny Kokiri Forest stand-in with no Nintendo data, so you can learn the editor before pointing it at the game. On a real install the folder is usually ~/Games/Longshot/rc/v3.0.0.";
  row.append(save, sample);
  const stack = document.createElement("div");
  stack.className = "stack";
  stack.append(path, row, error, note);
  card.append(title, lede, stack);
  $("gate").replaceChildren(card);
}

function gateMessage(titleText, detail) {
  const card = document.createElement("div");
  card.className = "gate-card";
  const title = document.createElement("h1");
  title.textContent = titleText;
  const p = document.createElement("p");
  p.textContent = detail;
  card.append(title, p);
  return card;
}

async function useSample() {
  try {
    state.status = await api.sample();
    closeDialogs();
    renderChrome();
    $("gate").hidden = true;
    $("workspace").hidden = false;
    viewport.resize();
    state.banc = null;
    await refreshScenes();
    toast("Sample dump and sample project are open.");
  } catch (error) {
    toast(error.message);
  }
}

async function saveDump() {
  try {
    state.status = await api.saveConfig({ dump: $("dump-path").value.trim() });
    closeDialogs();
    renderChrome();
    if (!dumpReady()) {
      showGate();
      return;
    }
    $("gate").hidden = true;
    $("workspace").hidden = false;
    viewport.resize();
    state.banc = null;
    state.view = null;
    await refreshScenes();
  } catch (error) {
    toast(error.message);
  }
}

function openProjectDialog() {
  const project = state.status.project;
  const summary = $("project-summary");
  summary.replaceChildren();
  if (projectReady()) {
    summary.textContent = `${project.manifest.title} · ${project.manifest.name} · ${project.root}`;
  } else if (project && project.error) {
    summary.textContent = project.message;
  } else {
    summary.textContent = "No project is open.";
  }
  if (state.status.dump && state.status.dump.root) $("dump-path").value = state.status.dump.root;
  if (state.status.repo) $("import-path").placeholder = `${state.status.repo}/examples/ato-scenes`;
  const catalog = $("project-catalog");
  catalog.replaceChildren();
  if (projectReady()) catalog.append(catalogList(project.catalog));
  $("project-dialog").showModal();
}

function renderProjectContents() {
  const box = $("project-contents");
  const body = $("project-contents-body");
  if (!box || !body) return;
  body.replaceChildren();
  const project = state.status && state.status.project;
  if (!projectReady() || !project.catalog) {
    box.hidden = true;
    return;
  }
  const catalog = project.catalog;
  const count = (catalog.text || []).length + (catalog.events || []).length + (catalog.quests || []).length;
  if (!count && !(catalog.scenes || []).length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  body.append(catalogList(catalog));
}

function catalogList(catalog) {
  const root = document.createElement("div");
  root.className = "catalog";
  const sections = [
    ["Quests", (catalog.quests || []).map((item) => `${item.name} · ${item.kind}`)],
    ["Text", (catalog.text || []).map((item) => item.label || item.file)],
    ["Events", (catalog.events || []).map((item) => `${item.title} · ${item.kind}`)],
    ["Tables", (catalog.tables || []).map((item) => item.file)],
    ["Game data", (catalog.gamedata || []).map((item) => (item.names || []).join(", ") || item.file)],
    ["UI", (catalog.ui || []).map((item) => item.name)],
    ["Scene recipes", (catalog.scenes || []).map((item) => item.summary)],
  ];
  for (const [title, rows] of sections) {
    if (!rows.length) continue;
    const block = document.createElement("section");
    const heading = document.createElement("h3");
    heading.textContent = title;
    const list = document.createElement("ul");
    for (const row of rows) {
      const item = document.createElement("li");
      item.textContent = row;
      list.append(item);
    }
    block.append(heading, list);
    root.append(block);
  }
  if (!root.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "This project has no text, events, or quests yet.";
    root.append(empty);
  }
  return root;
}

async function createProject() {
  try {
    await api.createProject({
      directory: $("new-dir").value.trim(),
      name: $("new-name").value.trim(),
      title: $("new-title").value.trim(),
      author: $("new-author").value.trim(),
      version: $("new-version").value.trim() || "0.1.0",
    });
    state.status = await api.status();
    closeDialogs();
    renderChrome();
    await refreshScenes();
  } catch (error) {
    toast(error.message);
  }
}

async function openProject() {
  try {
    await api.openProject($("open-dir").value.trim());
    state.status = await api.status();
    closeDialogs();
    renderChrome();
    await refreshScenes();
  } catch (error) {
    toast(error.message);
  }
}

async function importAto(path) {
  if (!projectReady()) {
    toast("Open a project before importing.");
    return;
  }
  const log = $("import-log");
  log.textContent = "Importing…";
  try {
    const result = await api.importAto(path);
    log.textContent = (result.messages || []).join("\n") || "Imported.";
    state.status = await api.status();
    renderChrome();
    await refreshScenes();
    if (state.banc) await openScene(state.banc, { keepHistory: true });
  } catch (error) {
    log.textContent = error.message;
    toast(error.message);
  }
}

async function refreshScenes() {
  const data = await api.scenes();
  state.scenes = data.scenes;
  state.modified = new Set(data.modified || []);
  state.recipes = new Set(data.recipes || []);
  state.projectOnly = data.projectOnly || [];
  $("scene-count").textContent = `${state.scenes.length} scenes`;
  renderScenes();
  const preferred = [...state.modified][0] || (state.scenes[0] && state.scenes[0].path);
  if (!state.banc && preferred) await openScene(preferred, { frame: true });
}

function renderScenes() {
  const query = $("scene-search").value.trim().toLowerCase();
  const list = $("scene-list");
  list.replaceChildren();
  const groups = new Map();
  for (const scene of state.scenes) {
    const hay = `${scene.area} ${scene.name} ${scene.path}`.toLowerCase();
    if (query && !hay.includes(query)) continue;
    if (!groups.has(scene.area)) groups.set(scene.area, []);
    groups.get(scene.area).push(scene);
  }
  if (groups.size === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.scenes.length ? "No scenes match that search." : "This dump has no placement files under romfs/Banc.";
    list.append(empty);
  }
  for (const [area, scenes] of groups) {
    const heading = document.createElement("div");
    heading.className = "area";
    heading.textContent = pretty(area);
    list.append(heading);
    for (const scene of scenes) list.append(sceneButton(scene));
  }
  if (state.projectOnly.length) {
    const heading = document.createElement("div");
    heading.className = "area";
    heading.textContent = "In the project, missing from the dump";
    list.append(heading);
    for (const path of state.projectOnly) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "scene-item";
      button.textContent = path;
      button.addEventListener("click", () => toast(`${path} is in the project, but this dump does not have that placement file.`));
      list.append(button);
    }
  }
}

function sceneButton(scene) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "scene-item";
  button.setAttribute("aria-current", scene.path === state.banc ? "true" : "false");
  const text = document.createElement("span");
  text.className = "grow";
  const strong = document.createElement("strong");
  strong.textContent = pretty(scene.name);
  const small = document.createElement("small");
  small.textContent = scene.path;
  text.append(strong, small);
  button.append(text);
  if (state.modified.has(scene.path)) {
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.title = "This scene has actor edits in the project";
    button.append(dot);
  } else if (state.recipes.has(scene.path)) {
    const dot = document.createElement("i");
    dot.className = "dot recipe";
    dot.title = "An advanced patch in this project rewrites this scene at build time";
    button.append(dot);
  }
  button.addEventListener("click", () => {
    openScene(scene.path, { frame: true });
    if (window.matchMedia("(max-width: 980px)").matches) $("scene-pane").classList.add("shut");
  });
  return button;
}

async function openScene(banc, options = {}) {
  $("viewport-empty").hidden = true;
  $("viewport").classList.add("is-live");
  try {
    const view = await api.scene(banc);
    state.banc = banc;
    if (!options.keepHistory) state.history = { undo: [], redo: [] };
    const interesting = view.actors.find((actor) => actor.status === "edited" || actor.status === "added");
    applyView(view, { frame: Boolean(options.frame), select: interesting ? interesting.id : null });
    const scene = state.scenes.find((item) => item.path === banc);
    $("scene-name").textContent = scene ? pretty(scene.name) : pretty(banc.split("/").pop() || banc);
    renderScenes();
    updateHistoryButtons();
  } catch (error) {
    toast(error.message);
    $("warnings").hidden = false;
    $("warnings").textContent = error.message;
  }
}

function applyView(view, { frame = false, select } = {}) {
  state.view = view;
  if (select !== undefined) state.selected = select;
  else if (view.focus) state.selected = view.focus;
  else if (state.selected && !view.actors.some((actor) => actor.id === state.selected)) state.selected = null;
  viewport.setActors(view.actors, { frame });
  viewport.select(state.selected);
  viewport.setFlags({ hidden: state.showHidden, labels: state.showLabels });
  const warnings = $("warnings");
  warnings.replaceChildren();
  warnings.hidden = !view.warnings || view.warnings.length === 0;
  for (const message of view.warnings || []) {
    const p = document.createElement("p");
    p.textContent = message;
    warnings.append(p);
  }
  renderOutliner();
  renderProps();
  if (state.banc) state.modified.add(state.banc);
  if (view.diff && Object.keys(view.diff.actors || {}).length === 0) state.modified.delete(state.banc);
}

async function edit(command) {
  if (!state.banc) return;
  if (!projectReady()) {
    toast("Open a project first. Looking around does not write anything.");
    openProjectDialog();
    return;
  }
  const before = structuredClone((state.view && state.view.diff && state.view.diff.actors) || {});
  state.history.undo.push(before);
  state.history.redo = [];
  try {
    const view = await api.edit(state.banc, command);
    applyView(view, { select: view.focus || state.selected });
    renderScenes();
  } catch (error) {
    state.history.undo.pop();
    toast(error.message);
    if (state.view) viewport.setActors(state.view.actors, { frame: false });
    viewport.select(state.selected);
  }
  updateHistoryButtons();
}

async function undo() {
  if (!state.history.undo.length || !state.banc) return;
  state.history.redo.push(structuredClone(state.view.diff.actors || {}));
  const previous = state.history.undo.pop();
  await restore(previous);
}

async function redo() {
  if (!state.history.redo.length || !state.banc) return;
  state.history.undo.push(structuredClone(state.view.diff.actors || {}));
  const next = state.history.redo.pop();
  await restore(next);
}

async function restore(actors) {
  try {
    const view = await api.putDiff(state.banc, actors);
    applyView(view, { select: state.selected });
    renderScenes();
  } catch (error) {
    toast(error.message);
  }
  updateHistoryButtons();
}

function updateHistoryButtons() {
  $("undo").disabled = state.history.undo.length === 0;
  $("redo").disabled = state.history.redo.length === 0;
}

function renderOutliner() {
  const list = $("outliner");
  list.replaceChildren();
  if (!state.view) {
    list.append(empty("Open a scene to see its actors."));
    return;
  }
  const query = $("actor-search").value.trim().toLowerCase();
  const actors = state.view.actors.filter((actor) => {
    const hay = `${actor.gyaml} ${actor.name || ""} ${actor.id || ""} ${actor.category}`.toLowerCase();
    return !query || hay.includes(query);
  });
  const shown = actors.slice(0, 400);
  if (!shown.length) list.append(empty(state.view.actors.length ? "No actors match." : "This scene has no actors."));
  for (const actor of shown) list.append(actorRow(actor));
  if (actors.length > shown.length) list.append(empty(`Showing 400 of ${actors.length}. Search to narrow the list.`));
  for (const removed of state.view.removed || []) {
    const row = document.createElement("div");
    row.className = "actor-row";
    const text = document.createElement("span");
    text.className = "grow";
    text.textContent = removed.gyaml || "Removed";
    const badge = document.createElement("span");
    badge.className = "badge removed";
    badge.textContent = "removed";
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "ghost";
    restore.textContent = "Restore";
    restore.addEventListener("click", () => edit({ command: "revert", id: removed.id }));
    row.append(text, badge, restore);
    list.append(row);
  }
}

function actorRow(actor) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "actor-row";
  button.setAttribute("role", "option");
  button.setAttribute("aria-selected", actor.id && actor.id === state.selected ? "true" : "false");
  const swatch = document.createElement("i");
  swatch.className = `swatch ${actor.category}`;
  const text = document.createElement("span");
  text.className = "grow";
  const strong = document.createElement("strong");
  strong.textContent = actor.name || actor.gyaml;
  const small = document.createElement("small");
  small.textContent = actor.name ? actor.gyaml : actor.category;
  text.append(strong, small);
  button.append(swatch, text);
  if (actor.status && actor.status !== "vanilla") {
    const badge = document.createElement("span");
    badge.className = `badge ${actor.status}`;
    badge.textContent = actor.status;
    button.append(badge);
  }
  button.addEventListener("click", () => {
    state.selected = actor.id;
    viewport.select(actor.id);
    renderOutliner();
    renderProps();
    showTab("props");
  });
  button.addEventListener("dblclick", () => viewport.frame(actor.id));
  return button;
}

function renderProps() {
  const panel = $("props-panel");
  panel.replaceChildren();
  const actor = selectedActor();
  if (!actor) {
    panel.append(empty("Select an actor in the view or the list. Move and Rotate use the gizmo. G moves, R rotates, F frames."));
    return;
  }
  const head = document.createElement("div");
  head.className = "identity";
  const title = document.createElement("h3");
  title.textContent = actor.name || actor.gyaml;
  const gyaml = document.createElement("div");
  gyaml.textContent = actor.gyaml;
  const hash = document.createElement("div");
  hash.className = "hash";
  hash.textContent = actor.id || "No instance hash. This actor cannot be edited until the file has a Hash.";
  const meta = document.createElement("div");
  const cat = document.createElement("span");
  cat.className = "badge";
  cat.textContent = actor.category;
  meta.append(cat);
  if (actor.status !== "vanilla") {
    const status = document.createElement("span");
    status.className = `badge ${actor.status}`;
    status.textContent = actor.status;
    meta.append(" ", status);
  }
  head.append(title, gyaml, hash, meta);
  panel.append(head);

  if (!actor.id || actor.status === "locked") return;

  panel.append(vectorFields("Position (metres)", "t", actor.translate));
  panel.append(vectorFields("Rotation (degrees, Y is yaw)", "r", actor.rotate.map(deg)));
  panel.append(vectorFields("Scale", "s", actor.scale));

  const actions = document.createElement("div");
  actions.className = "actions";
  actions.append(
    action("Duplicate", () => edit({ command: "duplicate", id: actor.id })),
    action(actor.status === "hidden" ? "Show" : "Hide", () => edit({ command: actor.status === "hidden" ? "unhide" : "hide", id: actor.id })),
    action("Delete", () => edit({ command: "remove", id: actor.id })),
    action("Change actor", () => openAdd("replace")),
  );
  if (actor.status !== "vanilla") actions.append(action("Revert", () => edit({ command: "revert", id: actor.id })));
  panel.append(actions);

  const nameField = document.createElement("div");
  nameField.className = "field";
  const nameLabel = document.createElement("label");
  nameLabel.textContent = "Instance name";
  const nameInput = document.createElement("input");
  nameInput.value = actor.name || "";
  nameInput.addEventListener("change", () => edit({ command: "set_name", id: actor.id, name: nameInput.value }));
  nameLabel.append(nameInput);
  nameField.append(nameLabel);
  panel.append(nameField);

  const paramsTitle = document.createElement("h3");
  paramsTitle.textContent = "Parameters";
  panel.append(paramsTitle);
  const params = actor.params;
  if (!params || params[0] !== "dict") {
    panel.append(empty("No extra parameters."));
  } else {
    const entries = Object.entries(params[1]);
    if (!entries.length) panel.append(empty("No extra parameters."));
    for (const [key, value] of entries) panel.append(paramBlock(key, value));
  }
  panel.append(addParamRow(actor.id));
  if (actor.srtHash) {
    const srt = document.createElement("p");
    srt.className = "muted";
    srt.textContent = `SRT hash ${actor.srtHash}. The instance id is left alone; the project uses it as the key.`;
    panel.append(srt);
  }
}

function vectorFields(label, prefix, values) {
  const wrap = document.createElement("div");
  const name = document.createElement("div");
  name.className = "axis-label";
  name.textContent = label;
  const grid = document.createElement("div");
  grid.className = "axes";
  ["X", "Y", "Z"].forEach((axis, index) => {
    const input = document.createElement("input");
    input.id = prefix + axis.toLowerCase();
    input.type = "number";
    input.step = "any";
    input.value = formatNum(values[index]);
    input.setAttribute("aria-label", `${label} ${axis}`);
    input.addEventListener("change", () => commitTransform());
    grid.append(input);
  });
  wrap.append(name, grid);
  return wrap;
}

function commitTransform() {
  const actor = selectedActor();
  if (!actor) return;
  const read = (id) => Number($(id).value);
  if ([..."xyz"].some((axis) => !Number.isFinite(read("t" + axis)) || !Number.isFinite(read("r" + axis)) || !Number.isFinite(read("s" + axis)))) {
    toast("Position, rotation and scale need numbers.");
    return;
  }
  edit({
    command: "transform",
    id: actor.id,
    translate: [read("tx"), read("ty"), read("tz")],
    rotate: [rad(read("rx")), rad(read("ry")), rad(read("rz"))],
    scale: [read("sx"), read("sy"), read("sz")],
  });
}

function paramBlock(path, tagged) {
  const block = document.createElement("div");
  block.className = "param";
  if (!Array.isArray(tagged)) {
    block.textContent = path;
    return block;
  }
  const [kind, payload] = tagged;
  if (kind === "dict" || kind === "array" || kind === "h32" || kind === "h64") {
    const details = document.createElement("details");
    details.open = path === "Dynamic";
    const summary = document.createElement("summary");
    summary.textContent = `${path} · ${kind === "dict" ? "group" : kind}`;
    details.append(summary);
    if (kind === "h32" || kind === "h64") {
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = "Hash maps are kept on export. Editing their keys is not in this version.";
      details.append(note);
      for (const [key, value] of Object.entries(payload || {})) {
        const row = document.createElement("p");
        row.className = "hash";
        row.textContent = `${key}: ${preview(value)}`;
        details.append(row);
      }
    } else if (kind === "array") {
      payload.forEach((item, index) => {
        details.append(paramBlock(`${path}[${index}]`, item));
      });
    } else {
      for (const [key, value] of Object.entries(payload)) details.append(paramBlock(`${path}.${key}`, value));
    }
    block.append(details);
    return block;
  }
  const label = document.createElement("label");
  label.textContent = `${path} · ${kind}`;
  const locked = path === "Hash" || path === "SRTHash" || path.endsWith("Placement.ID");
  if (kind === "bool") {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(payload);
    input.disabled = locked;
    input.addEventListener("change", () => edit({ command: "set", id: state.selected, path, value: ["bool", input.checked] }));
    label.append(input);
  } else if (kind === "bin") {
    const input = document.createElement("input");
    input.value = payload || "";
    input.disabled = locked;
    input.addEventListener("change", () => edit({ command: "set", id: state.selected, path, value: ["bin", input.value.trim()] }));
    label.append(input);
  } else {
    const input = document.createElement("input");
    input.value = payload === null || payload === undefined ? "" : String(payload);
    input.disabled = locked;
    if (kind === "f32" || kind === "f64" || kind === "s32" || kind === "u32") input.inputMode = "decimal";
    input.addEventListener("change", () => {
      let value = input.value;
      if (kind === "f32" || kind === "f64" || kind === "s32" || kind === "u32") {
        if (value.trim() === "" || Number.isNaN(Number(value))) {
          toast(`${path} needs a number.`);
          return;
        }
        value = kind === "s32" || kind === "u32" ? Number.parseInt(value, 10) : Number(value);
      }
      edit({ command: "set", id: state.selected, path, value: [kind, value] });
    });
    label.append(input);
  }
  block.append(label);
  return block;
}

function addParamRow(id) {
  const row = document.createElement("div");
  row.className = "field";
  const path = document.createElement("input");
  path.placeholder = "Dynamic.NewFlag";
  const type = document.createElement("select");
  for (const kind of ["str", "bool", "s32", "u32", "f32", "u64", "s64"]) {
    const option = document.createElement("option");
    option.value = kind;
    option.textContent = kind;
    type.append(option);
  }
  const value = document.createElement("input");
  value.placeholder = "value";
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Add parameter";
  button.addEventListener("click", () => {
    const kind = type.value;
    let payload = value.value;
    if (kind === "bool") payload = payload === "true" || payload === "1";
    else if (kind === "s32" || kind === "u32" || kind === "f32") payload = Number(payload || 0);
    else if (kind === "u64" || kind === "s64") payload = payload || "0";
    edit({ command: "set", id, path: path.value.trim(), value: [kind, payload] });
  });
  row.append(path, type, value, button);
  return row;
}

function action(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function openAdd(mode) {
  if (!projectReady()) {
    toast("Open a project first.");
    openProjectDialog();
    return;
  }
  state.addMode = mode;
  $("add-title").textContent = mode === "replace" ? "Change actor" : "Add an actor";
  $("add-help").textContent = mode === "replace"
    ? "The instance id stays. Only the actor name (Gyaml) changes."
    : "Names come from Pack/Actor in the dump. The new actor is placed at the camera target.";
  $("add-search").value = "";
  $("add-dialog").showModal();
  loadActorNames("");
  $("add-search").focus();
}

async function loadActorNames(query) {
  const box = $("add-results");
  box.replaceChildren(empty("Searching…"));
  try {
    const data = await api.actors(query);
    box.replaceChildren();
    if (!data.names.length) box.append(empty(data.total ? "No actor names match." : "This dump has no Pack/Actor list."));
    for (const name of data.names) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "name-row";
      button.textContent = name;
      button.addEventListener("click", () => chooseActor(name));
      box.append(button);
    }
    if (data.matched > data.names.length) box.append(empty(`${data.matched} matches. Keep typing to narrow them.`));
  } catch (error) {
    box.replaceChildren(empty(error.message));
  }
}

function chooseActor(name) {
  $("add-dialog").close();
  if (state.addMode === "replace") {
    if (!state.selected) return;
    edit({ command: "set_gyaml", id: state.selected, gyaml: name });
    return;
  }
  edit({ command: "add", actor: name, translate: viewport.spawnPoint() });
}

async function runBuild() {
  const log = $("build-log");
  log.replaceChildren();
  const line = document.createElement("li");
  line.textContent = "Building…";
  log.append(line);
  $("build-download").hidden = true;
  try {
    const result = await api.exportMod({ install: $("build-install").checked, zip: $("build-zip").checked });
    log.replaceChildren();
    for (const entry of result.log || []) {
      const item = document.createElement("li");
      item.className = entry.level;
      item.textContent = entry.message;
      log.append(item);
    }
    if (result.zip) $("build-download").hidden = false;
  } catch (error) {
    log.replaceChildren();
    const item = document.createElement("li");
    item.className = "error";
    item.textContent = error.message;
    log.append(item);
  }
}

function selectedActor() {
  if (!state.view || !state.selected) return null;
  return state.view.actors.find((actor) => actor.id === state.selected) || null;
}

function showTab(name) {
  state.tab = name;
  $("outliner-panel").hidden = name !== "outliner";
  $("props-panel").hidden = name !== "props";
  $("tab-outliner").classList.toggle("is-on", name === "outliner");
  $("tab-props").classList.toggle("is-on", name === "props");
  $("tab-outliner").setAttribute("aria-selected", name === "outliner" ? "true" : "false");
  $("tab-props").setAttribute("aria-selected", name === "props" ? "true" : "false");
  if (mobileQuery.matches) {
    $("side-pane").classList.remove("shut");
    $("scene-pane").classList.add("shut");
  }
}

function setNav(mode) {
  state.nav = mode;
  viewport.setNav(mode);
  document.querySelectorAll("[data-nav]").forEach((button) => button.classList.toggle("is-on", button.dataset.nav === mode));
  $("stage-hint").textContent = mode === "fly"
    ? "WASD moves. Q and E go down and up. Right-drag looks. Shift is faster."
    : "Left drag orbits. Right drag pans. Scroll zooms. Click a box to select.";
}

function setGizmo(mode) {
  state.gizmo = mode;
  viewport.setGizmo(mode);
  document.querySelectorAll("[data-gizmo]").forEach((button) => button.classList.toggle("is-on", button.dataset.gizmo === mode));
}

function fillNumber(id, value) {
  const input = $(id);
  if (!input || document.activeElement === input) return;
  input.value = formatNum(value);
}

function formatNum(value) {
  if (!Number.isFinite(Number(value))) return "";
  const rounded = Math.round(Number(value) * 10000) / 10000;
  return String(rounded);
}

function round4(value) {
  return Math.round(value * 10000) / 10000;
}

function deg(radians) {
  return radians * 180 / Math.PI;
}

function rad(degrees) {
  return degrees * Math.PI / 180;
}

function pretty(name) {
  return String(name).replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function preview(tagged) {
  if (!Array.isArray(tagged)) return "";
  const [kind, payload] = tagged;
  if (kind === "dict" || kind === "array" || kind === "h32" || kind === "h64") return kind;
  return `${kind} ${payload}`;
}

function empty(text) {
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = text;
  return p;
}

function toast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  $("toasts").append(node);
  setTimeout(() => node.remove(), 5200);
}

function closeDialogs() {
  for (const dialog of document.querySelectorAll("dialog")) {
    if (dialog.open) dialog.close();
  }
}

function isTyping(event) {
  const tag = event.target && event.target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (event.target && event.target.isContentEditable);
}
