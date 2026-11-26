import "dockview-core/dist/styles/dockview.css";

import { api } from "./api.js";
import { dismissWebgpuNotice, webgpuNoticeVisible } from "./backend.js";
import { buildNoticeId, buildNoticeVisible, describeBuildMismatch, dismissBuildNotice, frontendBuild } from "./build-info.js";
import { planActorRefresh } from "./actors.js";
import {
  buildOutlinerRows,
  followSelection,
  planViewportReveal,
  revealHint,
  shouldVirtualize,
  virtualRevealTop,
  virtualSlice,
} from "./outliner.js";
import { graphHistory, historyStep, renderBehavior } from "./behavior.js";
import { clearGraphCameras, releaseDisconnectedGraphs } from "./graph-view.js";
import { createDock, panelIdForTab, tabForPanel } from "./dock.js";
import { blankHistory, editLabel, historyRows, popRedo, popUndo, pushEdit } from "./history.js";
import { modelLine } from "./inspector.js";
import { paletteRows } from "./palette.js";
import {
  addPermission,
  catalogRows,
  declarationFromSelection,
  isNetOrigin,
  nextMode,
  parseNetLines,
  selectionFromDeclaration,
  validateClient,
} from "./permissions.js";
import { sceneEntries } from "./scene-list.js";
import { groupModelErrors, isMarker } from "./problems.js";
import { SHORTCUT_GROUPS } from "./shortcuts.js";
import { createViewport } from "./viewport.js";
import {
  followersFor,
  groupPanel,
  resolveDecorationGroups,
} from "./decoration-groups.js";
import {
  LAYERS,
  LAYER_LABELS,
  PRESETS,
  enabledLayers,
  filterActors,
  fullLoad,
  fullVisual,
  layersOff,
  linkPairs,
  loadFromLayers,
  loadQuery,
  normalizeLoad,
  parseLoadParam,
  presetLoad,
} from "./layers.js";

function $(id) {
  const local = document.getElementById(id);
  if (local) return local;
  if (typeof globalThis.__ocarinaFind === "function") {
    const parked = globalThis.__ocarinaFind(id);
    if (parked) return parked;
  }
  const dockApi = globalThis.__ocarinaDock;
  if (!dockApi || !dockApi.groups) return null;
  for (const group of dockApi.groups) {
    const popup = group.api.getWindow();
    if (!popup || popup === window) continue;
    const found = popup.document.getElementById(id);
    if (found) return found;
  }
  return null;
}

let onShortcut = () => {};
let viewport = null;
let sceneOpenToken = 0;
let sceneFetch = new AbortController();
let sceneVisualLoad = 0;
let sceneVisualBoxes = 0;
let sceneVisualDeferred = 0;
let sceneVisualResidentCapped = false;
let sceneActorsReady = false;
let sceneTerrainReady = true;
const dock = createDock(document.getElementById("dock"), {
  onWindow(popup) {
    popup.addEventListener("keydown", (event) => onShortcut(event));
    if (viewport) viewport.resize();
  },
  onChange() {
    renderWindowMenu();
    if (viewport) viewport.resize();
  },
});

const state = {
  status: null,
  scenes: [],
  modified: new Set(),
  recipes: new Set(),
  projectOnly: [],
  changeCounts: {},
  changes: null,
  changeToken: 0,
  problems: null,
  problemToken: 0,
  meshAudit: null,
  modelErrors: [],
  terrainNotes: [],
  behavior: null,
  behaviorKey: "",
  behaviorLayer: "curated",
  behaviorNode: "",
  behaviorFile: "",
  behaviorEntry: "",
  behaviorQuery: "",
  behaviorError: "",
  behaviorLoading: false,
  behaviorTrail: [],
  behaviorAhead: [],
  behaviorFrame: "",
  showProblemNotes: false,
  hot: null,
  hotToken: 0,
  banc: null,
  view: null,
  selected: null,
  history: blankHistory(null),
  showHidden: false,
  showLabels: true,
  showCompanions: true,
  companionPick: true,
  categories: { area: "show", system: "show" },
  hideVolumes: false,
  outlinerCollapsed: {},
  showGround: false,
  showWater: true,
  showSub: false,
  terrainDebug: false,
  helperBoxes: null,
  terrain: null,
  gizmo: "translate",
  tab: "outliner",
  addMode: "add",
  load: fullLoad(),
};

function loadRequest() {
  const load = state.load || fullLoad();
  return { load: loadQuery(load), textures: load.textures ? "1" : "0" };
}

// Geometry only. The URL does not change when textures toggle, so a texture
// switch cannot be a second model fetch. Images come from fetchTextures.
function geometryRequest() {
  const load = state.load || fullLoad();
  return { load: loadQuery(load), flat: "1" };
}

viewport = await createViewport($("viewport"), {
  fetchModel: (gyaml, options) => api.model(gyaml, { ...(options || {}), ...geometryRequest() }),
  fetchModels: (names, prefetch, options) => api.models(names, prefetch, { ...geometryRequest(), signal: options && options.signal }),
  fetchTextures: (gyaml, signal) => api.actorTextures(gyaml, { load: loadQuery(state.load || fullLoad()), signal }),
  fetchTerrain: (banc, refine, signal) => api.terrain(banc, refine, { ...loadRequest(), signal }),
  fetchGround: (banc, x, z, exclude, signal) => api.ground(banc, x, z, exclude, { ...loadRequest(), signal }),
  fetchSurface: (banc) => api.groundSurface(banc, loadRequest()),
  onProgress: (info) => {
    const box = $("model-progress");
    if (!box) return;
    if (sceneVisualLoad) {
      updateSceneViewerProgress(info);
      box.hidden = true;
      return;
    }
    if (!info || !info.total) {
      box.hidden = true;
      return;
    }
    const extra = info.boxes ? ` · ${info.boxes} still boxes` : "";
    $("model-progress-text").textContent = `${info.label}${extra}`;
    $("model-progress-bar").style.width = `${Math.round((100 * info.done) / Math.max(info.total, 1))}%`;
    box.hidden = Boolean(info.finished);
  },
  onModelsReady: (result) => finishSceneViewerLoad(result),
  onUnload: () => {
    state.view = null;
    state.raw = null;
    state.selected = null;
    state.behavior = null;
    state.behaviorKey = "";
    state.behaviorNode = "";
    state.behaviorFile = "";
    state.behaviorEntry = "";
    state.behaviorQuery = "";
    state.behaviorError = "";
    state.behaviorTrail = [];
    state.behaviorAhead = [];
    state.behaviorFrame = "";
    state.behaviorLoading = false;
    const host = $("behavior-panel");
    if (host) {
      host.__fitGraph = null;
      host.__clearGraph = null;
      host.__spotlight = null;
      host.replaceChildren();
      releaseDisconnectedGraphs();
      clearGraphCameras();
    }
    renderOutliner();
  },
  onModels: () => {
    renderProps();
    renderOutliner();
  },
  onModelError: (gyaml, message) => {
    noteModelError(gyaml, message);
  },
  onTerrainWarn: (message) => {
    noteTerrain(message);
  },
  onTerrain: (meta) => {
    state.terrain = meta;
    const proxy = Boolean(meta && typeof meta.reason === "string" && /terrain proxy/i.test(meta.reason));
    const heightmap = Boolean(meta && typeof meta.reason === "string" && /terrain heightmap/i.test(meta.reason));
    const sparse = Boolean(meta && meta.sparse);
    const show = Boolean(meta && meta.status === "mesh" && !proxy && !sparse);
    state.showGround = show;
    const box = $("show-ground");
    if (box) {
      box.checked = show;
      const title = proxy
        ? "The static map has no albedo. On draws a neutral grid on y = 0 instead of the green stand-in."
        : sparse
          ? "Sparse heightmap: most in-clip tiles have no file, so Ground starts off. Turn it on to see the sheet."
          : heightmap
            ? "Show the terrain heightmap. The untextured stand-in stays off."
            : "Show the static ground mesh";
      box.title = title;
      if (box.parentElement) box.parentElement.title = title;
    }
    viewport.setFlags({ ground: show });
  },
  onTerrainReady: () => {
    if (!sceneVisualLoad) return;
    sceneTerrainReady = true;
    if (sceneActorsReady) finishSceneViewerLoad();
  },
  onSelect: (id) => {
    state.selected = id;
    viewport.select(id);
    renderOutliner({ reveal: "viewport" });
    renderProps();
    noteCompanion();
    if (id && window.matchMedia("(max-width: 980px)").matches) showTab("props");
  },
  onPreview: (id, translate, rotate, scale) => {
    if (id !== state.selected) return;
    fillNumber("tx", translate[0]);
    fillNumber("ty", translate[1]);
    fillNumber("tz", translate[2]);
    fillNumber("rx", deg(rotate[0]));
    fillNumber("ry", deg(rotate[1]));
    fillNumber("rz", deg(rotate[2]));
    if (scale) {
      fillNumber("sx", scale[0]);
      fillNumber("sy", scale[1]);
      fillNumber("sz", scale[2]);
    }
  },
  onDeviceLost: (message) => toast(message, { persistent: true }),
  onFrame() {
    refreshStreamLog();
  },
  decorationFollowers: (id) => followersFor(id, currentDecorationGroups()),
  onTransform: (id, translate, rotate, scale, followers) => {
    const pose = {
      translate: translate.map(round4),
      rotate: rotate.map(round4),
      scale: (scale || [1, 1, 1]).map(round4),
    };
    if (!followers || !followers.length) {
      edit({ command: "transform", id, ...pose });
      return;
    }
    moveWithDecorations(id, pose, followers);
  },
});

async function openSettings() {
  try {
    const status = await api.status();
    const workers = status.workers || { count: 1, max: 1, source: "default" };
    const range = $("worker-range");
    const number = $("worker-count");
    const max = String(workers.max || 1);
    range.max = max;
    number.max = max;
    range.min = "1";
    number.min = "1";
    range.value = String(workers.count || 1);
    number.value = range.value;
    const locked = workers.source === "env";
    range.disabled = locked;
    number.disabled = locked;
    $("worker-note").textContent = locked
      ? `OCARINA_FORGE_WORKERS is ${workers.count}, so this slider is saved but not used until that variable is unset. This machine has ${workers.max} cores.`
      : `Using ${workers.count} of ${workers.max}. The default leaves one core free. The pool is rebuilt between converts.`;
    const textures = $("gpu-textures");
    const forced = new URLSearchParams(window.location.search).has("textures") || new URLSearchParams(window.location.search).has("gpuTextures");
    textures.checked = gpuTexturesOn();
    textures.disabled = forced;
    $("worker-save").disabled = locked && forced;
    $("gpu-texture-note").textContent = forced
      ? "This page's URL chooses the texture path. Take ?textures= off the address to use the checkbox."
      : "Albedo uploads as BC blocks. Turn this off, or open ?textures=bitmap, to use the JPEG and PNG instead. Saving reloads the view.";
    $("settings-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

function syncWorkerCount(value) {
  const range = $("worker-range");
  const number = $("worker-count");
  const max = Number(range.max) || 1;
  const next = Math.min(max, Math.max(1, Number(value) || 1));
  range.value = String(next);
  number.value = String(next);
}

$("worker-range").addEventListener("input", () => syncWorkerCount($("worker-range").value));
$("worker-count").addEventListener("input", () => syncWorkerCount($("worker-count").value));
function gpuTexturesOn() {
  const params = new URLSearchParams(window.location.search);
  const flag = params.get("textures") || params.get("gpuTextures");
  if (flag === "bitmap" || flag === "0") return false;
  if (flag === "gpu" || flag === "1") return true;
  return localStorage.getItem("ocarinaforge.gpuTextures") !== "0";
}

$("settings-form").addEventListener("submit", async (event) => {
  if (event.submitter && event.submitter.value !== "save") return;
  event.preventDefault();
  try {
    const previous = localStorage.getItem("ocarinaforge.gpuTextures") === "0" ? "0" : "1";
    const next = $("gpu-textures").checked ? "1" : "0";
    localStorage.setItem("ocarinaforge.gpuTextures", next);
    const workers = Number($("worker-count").value);
    await api.saveConfig({ workers });
    viewport.setConvertSlots(workers);
    if (previous !== next && !$("gpu-textures").disabled) {
      window.location.reload();
      return;
    }
    $("settings-dialog").close();
  } catch (error) {
    toast(error.message);
  }
});

let streamAt = 0;
function refreshStreamLog() {
  const now = performance.now();
  if (now - streamAt < 1000) return;
  streamAt = now;
  api.stream()
    .then((data) => {
      if (data.warm) viewport.setWarm(data.warm);
      const warm = data.warm && data.warm.total
        ? ` · warm ${data.warm.phase} ${data.warm.done || 0}/${data.warm.total}${data.warm.failed ? ` · ${data.warm.failed} failed` : ""}${data.warm.paused ? " paused" : ""}`
        : "";
      const head = `server ${data.misses || 0} converted · ${data.hits || 0} cache hits · conv ${data.convertMs || 0} ms · tex ${data.textureMs || 0} ms${warm}`;
      const lines = (data.lines || []).slice(-8);
      if (viewport.setServerStatus) viewport.setServerStatus({ head, lines });
    })
    .catch(() => {});
}

$("settings-open").addEventListener("click", () => openSettings());
$("undo").addEventListener("click", () => undo());
$("redo").addEventListener("click", () => redo());
$("build-open").addEventListener("click", () => $("build-dialog").showModal());
$("permissions-open").addEventListener("click", () => openPermissions());
$("build-permissions").addEventListener("click", () => openPermissions());
$("project-permissions").addEventListener("click", () => openPermissions());
$("permissions-save").addEventListener("click", () => savePermissions());
$("permissions-grant").addEventListener("click", () => grantPermissions());
function refreshPermissionDraft() {
  if (!$("permissions-dialog").open || !permissionsUi.view) return;
  permissionsUi.view = { ...permissionsUi.view, errors: [] };
  renderPermissions({ keepFields: true });
}
$("permissions-net").addEventListener("input", refreshPermissionDraft);
$("permissions-multiplayer").addEventListener("change", refreshPermissionDraft);
$("project-chip").addEventListener("click", openProjectDialog);
$("open-scenes").addEventListener("click", () => showTab("scenes"));
$("open-side").addEventListener("click", () => showTab("outliner"));
document.addEventListener("click", (event) => {
  const root = $("window-menu-root");
  if (!root || !root.open || root.contains(event.target)) return;
  root.open = false;
});
$("scene-search").addEventListener("input", renderScenes);
$("actor-search").addEventListener("input", () => renderOutliner());
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
$("active-bank").addEventListener("change", (event) => {
  const active = event.target;
  const other = (state.raw?.companions || []).find((item) => bancKind(item.banc) === active.value);
  active.value = bancKind(state.banc);
  if (other) openScene(other.banc);
});
$("show-companions").addEventListener("change", (event) => {
  state.showCompanions = event.target.checked;
  if (state.raw) applyView(state.raw, { select: state.selected });
});
$("companion-pick").addEventListener("change", (event) => {
  state.companionPick = event.target.checked;
  if (state.raw) applyView(state.raw, { select: state.selected });
});
showRendererNotice();
watchBuildSkew();
const profileBox = $("show-profile");
if (profileBox) {
  const fromQuery = new URLSearchParams(window.location.search).get("profile") === "1";
  const profileOn = fromQuery || storedFlag("ocarinaforge.profile", false);
  profileBox.checked = profileOn;
  viewport.setShowProfile(profileOn);
  profileBox.addEventListener("change", () => {
    viewport.setShowProfile(profileBox.checked);
    try {
      localStorage.setItem("ocarinaforge.profile", profileBox.checked ? "1" : "0");
    } catch {
      /* private mode */
    }
  });
}
$("draw-distance").addEventListener("change", (event) => {
  viewport.setFlags({ distance: Number(event.target.value) || 0 });
});
$("show-ground").addEventListener("change", (event) => {
  state.showGround = event.target.checked;
  if (state.load) state.load.visible.terrain = event.target.checked;
  viewport.setFlags({ ground: state.showGround, visibleLayers: state.load && state.load.visible });
  persistLoad();
});
$("show-water").addEventListener("change", (event) => {
  state.showWater = event.target.checked;
  if (state.load) state.load.visible.water = event.target.checked;
  viewport.setFlags({ water: state.showWater, visibleLayers: state.load && state.load.visible });
  persistLoad();
});
const showTextures = $("show-textures");
if (showTextures) {
  showTextures.addEventListener("change", () => {
    if (showTextures.checked && state.load && state.load.textures === false) {
      void applyLoadChange(loadFromLayers(state.load.layers, true, state.load.visible));
      return;
    }
    if (viewport.setTextureShown) viewport.setTextureShown(showTextures.checked);
  });
}
for (const [id, flag, fallback] of [
  ["show-shadows", "shadows", false],
  ["show-ao", "ao", true],
  ["show-effect-gizmos", "effectGizmos", true],
  ["show-editor-lighting", "editorDefaults", false],
]) {
  const box = $(id);
  if (!box) continue;
  const on = storedFlag(`ocarinaforge.${flag}`, fallback);
  box.checked = on;
  viewport.setFlags({ [flag]: on });
  box.addEventListener("change", () => {
    viewport.setFlags({ [flag]: box.checked });
    if (flag === "editorDefaults") {
      const note = $("lighting-note");
      if (note) note.textContent = viewport.lightingStatus();
    }
    try {
      localStorage.setItem(`ocarinaforge.${flag}`, box.checked ? "1" : "0");
    } catch {
      /* private mode */
    }
  });
}
$("show-sub").addEventListener("change", (event) => {
  state.showSub = event.target.checked;
  viewport.setFlags({ subTerrain: state.showSub });
});
$("terrain-debug").addEventListener("change", (event) => {
  state.terrainDebug = event.target.checked;
  viewport.setFlags({ terrainDebug: state.terrainDebug });
});
$("blend-mask").addEventListener("change", (event) => {
  viewport.setFlags({ blendMask: event.target.checked });
});
$("frame").addEventListener("click", () => viewport.frame(state.selected));
const snapBox = $("snap-surface");
if (snapBox) {
  snapBox.addEventListener("change", () => viewport.setSnap(snapBox.checked));
}
document.querySelectorAll("[data-cat]").forEach((button) => {
  button.addEventListener("click", () => {
    setCategory(button.dataset.cat, button.dataset.mode);
    renderOutliner();
  });
});
renderWindowMenu();
$("shortcuts-open").addEventListener("click", () => openShortcuts());
const shortcutsDialog = $("shortcuts-dialog");
if (shortcutsDialog) {
  shortcutsDialog.addEventListener("click", (event) => {
    if (event.target === shortcutsDialog) shortcutsDialog.close();
  });
}
document.querySelectorAll("[data-gizmo]").forEach((button) => {
  button.addEventListener("click", () => setGizmo(button.dataset.gizmo));
});
document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});
$("build-run").addEventListener("click", runBuild);
$("mod-export").addEventListener("click", runModExport);
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
$("hot-save").addEventListener("click", saveHotReload);
$("use-sample").addEventListener("click", useSample);
$("palette-open").addEventListener("click", () => openPalette());
$("palette-search").addEventListener("input", () => {
  paletteIndex = 0;
  renderPalette();
});
$("palette-search").addEventListener("keydown", onPaletteKey);
$("palette-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runPalette(paletteCache[paletteIndex]);
});

onShortcut = (event) => {
  const meta = event.metaKey || event.ctrlKey;
  if (meta && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openPalette();
    return;
  }
  if (isTyping(event)) return;
  if (event.altKey && !meta && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
    const behavior = document.getElementById("behavior-panel");
    if (behavior && (behavior.matches(":hover") || behavior.contains(document.activeElement))) {
      event.preventDefault();
      stepBehavior(event.key === "ArrowLeft" ? -1 : 1);
      return;
    }
  }
  if (event.key === "?") {
    event.preventDefault();
    openShortcuts();
    return;
  }
  if (meta && event.key.toLowerCase() === "z" && !event.shiftKey) {
    event.preventDefault();
    undo();
  } else if (meta && (event.key.toLowerCase() === "y" || (event.shiftKey && event.key.toLowerCase() === "z"))) {
    event.preventDefault();
    redo();
  } else if (event.key === "f" || event.key === "F") {
    const behavior = document.getElementById("behavior-panel");
    if (behavior && behavior.__fitGraph && behavior.matches(":hover")) {
      event.preventDefault();
      behavior.__fitGraph();
    } else viewport.frame(state.selected);
  } else if (event.key === "g") {
    setGizmo("translate");
  } else if (event.key === "r") {
    setGizmo("rotate");
  } else if (event.key === "b" && state.selected) {
    showTab("behavior");
  } else if (event.key === "h" && state.selected) {
    const actor = selectedActor();
    if (!actor) return;
    if (actor.status === "recipe") removeRecipe(actor);
    else edit({ command: actor.status === "hidden" ? "unhide" : "hide", id: state.selected });
  } else if (event.key === "Delete" && state.selected) {
    const actor = selectedActor();
    if (!actor) return;
    if (actor.status === "recipe") removeRecipe(actor);
    else edit({ command: "remove", id: state.selected });
  } else if (event.key.toLowerCase() === "v" && !meta) {
    toggleSnap();
  } else if (!meta && !event.repeat && event.key.toLowerCase() === "p") {
    if (document.querySelector("dialog[open]")) return;
    event.preventDefault();
    toggleProfile();
  } else if (event.key === "Escape") {
    if (document.querySelector("dialog[open]")) return;
    state.selected = null;
    viewport.select(null);
    renderOutliner();
    renderProps();
  }
};
window.addEventListener("keydown", (event) => onShortcut(event));

boot();

async function boot() {
  let startupError = "";
  try {
    state.status = await api.status();
  } catch (error) {
    $("gate").hidden = false;
    $("gate").replaceChildren(gateMessage("Ocarina Forge could not reach its server.", error.message));
    return;
  }
  const params = new URLSearchParams(window.location.search);
  const selectedMod = params.get("mod");
  if (selectedMod) {
    params.delete("mod");
    const search = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`);
    try {
      await api.openLocalMod(selectedMod);
      state.status = await api.status();
    } catch (error) {
      startupError = error.code === "mod_import"
        ? `Not editable in Forge: ${error.message}`
        : error.message;
    }
  }
  renderChrome();
  viewport.setConvertSlots(state.status.workers && state.status.workers.count);
  refreshHotReload();
  if (!dumpReady()) {
    showGate();
    if (startupError) toast(startupError);
    return;
  }
  $("gate").hidden = true;
  $("workspace").hidden = false;
  dock.fit();
  viewport.resize();
  syncPanes();
  if (startupError) toast(startupError);
  await loadFiltersFromProject();
  await refreshScenes();
}

const mobileQuery = window.matchMedia("(max-width: 980px)");
mobileQuery.addEventListener("change", syncPanes);

function syncPanes() {
  if (viewport) viewport.resize();
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
    $("permissions-open").hidden = false;
  } else {
    $("project-title").textContent = dumpReady() ? "No project" : "Set up Ocarina Forge";
    $("project-name").textContent = dump && dump.root ? dump.root : "";
    $("build-open").hidden = true;
    $("permissions-open").hidden = true;
  }
  renderProjectContents();
}

function showGate() {
  $("workspace").hidden = true;
  $("gate").hidden = false;
  const card = document.createElement("div");
  card.className = "gate-card";
  const title = document.createElement("h1");
  title.textContent = "Point Ocarina Forge at your dump";
  const lede = document.createElement("p");
  lede.className = "lede";
  lede.textContent = "The dump is the Longshot folder that contains romfs. Ocarina Forge only reads it. Edits go into a project folder, and Build writes a mod Longshot can load.";
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
    await loadFiltersFromProject();
    await refreshScenes();
    toast("Sample dump and sample project are open.");
  } catch (error) {
    toast(error.message);
  }
}

async function refreshHotReload() {
  try {
    state.hot = await api.hotReload();
  } catch (error) {
    const missing = /404/.test(error.message || "");
    state.hot = {
      enabled: false,
      status: "error",
      message: missing ? "Restart the editor (./build.sh or build.bat) so the API can run." : error.message,
    };
  }
  renderHotStatus();
  fillHotReload();
}

function fillHotReload() {
  const hot = state.hot;
  const path = $("hot-path");
  const enabled = $("hot-enabled");
  if (!hot || !path || !enabled) return;
  enabled.checked = Boolean(hot.enabled);
  if (document.activeElement !== path) path.value = hot.path || "";
}

function renderHotStatus() {
  const node = $("hot-status");
  if (!node) return;
  const hot = state.hot;
  const show = Boolean(hot && (hot.enabled || hot.status === "error"));
  node.hidden = !show;
  node.textContent = show ? hot.message || "" : "";
  node.classList.toggle("is-error", Boolean(hot && hot.status === "error"));
}

async function saveHotReload() {
  try {
    state.hot = await api.saveHotReload({
      enabled: $("hot-enabled").checked,
      path: $("hot-path").value.trim(),
    });
    renderHotStatus();
    toast(state.hot.message || "Hot reload saved.");
  } catch (error) {
    toast(error.message);
  }
}

function noteHotReload() {
  if (!state.hot || !state.hot.enabled) return;
  const token = ++state.hotToken;
  const delays = [800, 2000, 5000, 12000];
  (async () => {
    for (const delay of delays) {
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (token !== state.hotToken) return;
      try {
        state.hot = await api.hotReload();
      } catch (error) {
        state.hot = { ...(state.hot || {}), status: "error", message: error.message };
      }
      renderHotStatus();
      if (!state.hot || state.hot.status !== "pending") return;
    }
  })();
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
  fillHotReload();
  if (state.status.repo) $("import-path").placeholder = `${state.status.repo}/examples/ato-scenes`;
  const catalog = $("project-catalog");
  catalog.replaceChildren();
  if (projectReady()) catalog.append(catalogList(project.catalog));
  fillProjectChanges();
  fillLibrary();
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
  box.hidden = false;
  const all = document.createElement("button");
  all.type = "button";
  all.className = "ghost";
  all.textContent = "All map changes";
  all.addEventListener("click", () => openProjectDialog());
  const list = catalogList(project.catalog);
  body.append(all);
  if (list.querySelector("section")) body.append(list);
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

async function fillLibrary() {
  const host = $("project-library");
  if (!host) return;
  host.replaceChildren(empty("Loading projects…"));
  try {
    const [data, local] = await Promise.all([api.projects(), api.localMods()]);
    host.replaceChildren();
    const note = document.createElement("p");
    note.className = "muted tight";
    note.textContent = data.root;
    host.append(note);
    if (!data.projects.length) {
      host.append(empty("No Forge projects there yet. Leave the folder blank to create one in it."));
    } else {
      for (const item of data.projects) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "name-row";
        button.textContent = `${item.title} · ${item.modId}`;
        button.addEventListener("click", () => {
          $("open-dir").value = item.root;
          openProject();
        });
        host.append(button);
      }
    }
    const heading = document.createElement("h3");
    heading.textContent = "Local game mods";
    host.append(heading);
    const localRoot = document.createElement("p");
    localRoot.className = "muted tight";
    localRoot.textContent = local.root;
    host.append(localRoot);
    if (!local.mods.length) {
      host.append(empty("No local mods found."));
    }
    for (const item of local.mods) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "name-row";
      button.disabled = !item.editable;
      const action = item.action === "open_project" ? "Open Forge project" : "Import and open";
      button.textContent = item.editable
        ? `${item.title} · ${item.id} · ${action}`
        : `${item.title} · ${item.id} · Not editable`;
      button.title = item.reason || "";
      if (item.editable) button.addEventListener("click", () => openLocalMod(item.id));
      host.append(button);
    }
  } catch (error) {
    host.replaceChildren(empty(error.message));
  }
}

async function createProject() {
  try {
    const directory = $("new-dir").value.trim();
    const body = {
      name: $("new-name").value.trim(),
      title: $("new-title").value.trim(),
      author: $("new-author").value.trim(),
      description: ($("new-description") && $("new-description").value.trim()) || "",
      version: $("new-version").value.trim() || "0.1.0",
    };
    if (directory) body.directory = directory;
    await api.createProject(body);
    state.status = await api.status();
    closeDialogs();
    renderChrome();
    await loadFiltersFromProject();
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
    await loadFiltersFromProject();
    await refreshScenes();
  } catch (error) {
    toast(error.message);
  }
}

async function openLocalMod(id) {
  try {
    await api.openLocalMod(id);
    state.status = await api.status();
    closeDialogs();
    renderChrome();
    if (dumpReady()) {
      await loadFiltersFromProject();
      await refreshScenes();
    } else showGate();
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
  state.scenes = sceneEntries(data.scenes);
  state.modified = new Set(data.modified || []);
  state.recipes = new Set(data.recipes || []);
  state.projectOnly = data.projectOnly || [];
  state.changeCounts = data.changes || {};
  $("scene-count").textContent = `${state.scenes.length} scenes`;
  renderScenes();
  const preferred = state.scenes.flatMap((scene) => scene.banks).find((bank) => state.modified.has(bank)) || state.scenes[0]?.path;
  if (!state.banc && preferred) await openScene(preferred, { frame: true });
}

function renderScenes() {
  const query = $("scene-search").value.trim().toLowerCase();
  const list = $("scene-list");
  list.replaceChildren();
  const groups = new Map();
  for (const scene of state.scenes) {
    const hay = `${scene.area} ${scene.name} ${scene.banks.join(" ")}`.toLowerCase();
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
  button.setAttribute("aria-current", scene.banks.includes(state.banc) ? "true" : "false");
  const text = document.createElement("span");
  text.className = "grow";
  const strong = document.createElement("strong");
  strong.textContent = pretty(scene.name);
  const small = document.createElement("small");
  small.textContent = scene.banks.length > 1 ? "Static + Dynamic" : scene.path;
  small.title = scene.banks.join("\n");
  text.append(strong, small);
  button.append(text);
  const counts = {};
  for (const bank of scene.banks) {
    for (const [key, count] of Object.entries(state.changeCounts[bank] || {})) {
      counts[key] = (counts[key] || 0) + count;
    }
  }
  if (counts.total) {
    const dot = document.createElement("i");
    dot.className = actorChange(counts) ? "dot" : "dot recipe";
    dot.title = changeSummary(counts);
    button.append(dot);
  } else if (scene.banks.some((bank) => state.modified.has(bank))) {
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.title = "This scene has actor edits in the project";
    button.append(dot);
  } else if (scene.banks.some((bank) => state.recipes.has(bank))) {
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

function setScenePreparation(title, message, done = 0, total = 0) {
  const box = $("scene-prepare");
  if (!box) return;
  $("scene-prepare-title").textContent = title;
  $("scene-prepare-text").textContent = message;
  $("scene-prepare-bar").style.width = `${total ? Math.round((100 * done) / total) : 0}%`;
  box.hidden = false;
}

function clearScenePreparation() {
  const box = $("scene-prepare");
  if (box) {
    box.hidden = true;
  }
}

function updateSceneViewerProgress(info) {
  if (!info) return;
  sceneVisualBoxes = info.boxes || 0;
  sceneVisualDeferred = info.deferred || 0;
  sceneVisualResidentCapped = Boolean(info.residentCapped);
  const extra = info.boxes ? ` · ${info.boxes} box actors` : "";
  setScenePreparation(
    "Loading scene in viewer",
    `${info.done} of ${info.total} scene models loaded${extra}`,
    info.done,
    info.total,
  );
}

function finishSceneViewerLoad(result = {}) {
  const token = sceneVisualLoad;
  if (!token || token !== sceneOpenToken) return;
  if (result.batchesBuilt === false) {
    setScenePreparation("Scene assembly failed", "The previous view is still visible. Choose another scene or retry.", 1, 1);
    return;
  }
  sceneActorsReady = true;
  if (!sceneTerrainReady) {
    setScenePreparation("Loading terrain", "Preparing the terrain mesh…", 0, 1);
    return;
  }
  let message = "All scene models are loaded.";
  if (sceneVisualResidentCapped) message = "Visible detail is ready. Distant models load as you move.";
  else if (sceneVisualDeferred) message = "Visible detail is ready. The rest of the scene is loading in the background.";
  else if (sceneVisualBoxes) message = `Scene loaded with ${sceneVisualBoxes} box actor${sceneVisualBoxes === 1 ? "" : "s"}.`;
  setScenePreparation("Scene ready", message, 1, 1);
  window.setTimeout(() => {
    if (sceneVisualLoad !== token || sceneOpenToken !== token) return;
    sceneVisualLoad = 0;
    clearScenePreparation();
  }, 350);
}

function viewForLoad(view) {
  if (!view) return view;
  const load = state.load || fullLoad();
  const next = { ...view, actors: filterActors(view.actors, load) };
  if (Array.isArray(view.companions)) {
    next.companions = view.companions.map((extra) => ({
      ...extra,
      actors: filterActors(extra.actors, load),
    }));
  }
  return next;
}

function mergeActor(current, incoming) {
  if (!current) return incoming;
  if (incoming && incoming.logicOnly && !current.logicOnly) {
    return { ...current, links: incoming.links || current.links };
  }
  return incoming || current;
}

function mergeViews(current, incoming) {
  const byId = new Map();
  for (const actor of (current && current.actors) || []) byId.set(actor.id, actor);
  for (const actor of (incoming && incoming.actors) || []) byId.set(actor.id, mergeActor(byId.get(actor.id), actor));
  const companions = ((current && current.companions) || []).map((extra) => {
    const add = ((incoming && incoming.companions) || []).find((item) => item.banc === extra.banc);
    if (!add) return extra;
    const rows = new Map((extra.actors || []).map((actor) => [actor.id, actor]));
    for (const actor of add.actors || []) rows.set(actor.id, mergeActor(rows.get(actor.id), actor));
    return { ...extra, actors: [...rows.values()] };
  });
  return { ...(current || {}), actors: [...byId.values()], companions, diff: (current && current.diff) || (incoming && incoming.diff) };
}

let appliedTextureFilter = null;
function syncTextureSwitch(load) {
  const allow = Boolean(load && load.textures !== false);
  const box = $("show-textures");
  if (appliedTextureFilter !== allow) {
    appliedTextureFilter = allow;
    if (box) box.checked = allow;
    if (viewport.setTextureShown) viewport.setTextureShown(allow);
  }
  if (viewport.setTextureLoad) viewport.setTextureLoad(allow);
}

function publishLoad() {
  const load = state.load || fullLoad();
  viewport.setLoadState({ off: layersOff(load) });
  syncTextureSwitch(load);
  const ground = $("show-ground");
  const water = $("show-water");
  state.showGround = Boolean(load.layers.terrain && load.visible.terrain !== false);
  state.showWater = Boolean(load.layers.water && load.visible.water !== false);
  if (ground) {
    ground.checked = state.showGround;
    ground.disabled = !load.layers.terrain;
  }
  if (water) {
    water.checked = state.showWater;
    water.disabled = !load.layers.water;
  }
  viewport.setFlags({ ground: state.showGround, water: state.showWater, visibleLayers: load.visible });
  renderLoadFilters();
  refreshLinkGizmos();
}

function writeLoadUrl(load) {
  const url = new URL(window.location.href);
  if (!load || load.preset === "full") {
    url.searchParams.delete("load");
    url.searchParams.delete("textures");
  } else if (load.preset && load.preset !== "custom" && PRESETS[load.preset]) {
    url.searchParams.set("load", load.preset);
    url.searchParams.delete("textures");
  } else {
    url.searchParams.set("load", enabledLayers(load).join(","));
    if (load.textures === false) url.searchParams.set("textures", "0");
    else url.searchParams.delete("textures");
  }
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

let loadSave = 0;
function persistLoad() {
  const token = ++loadSave;
  const body = state.load;
  if (!projectReady() || !body) return;
  api.putLoadFilters(body).catch(() => {
    if (token === loadSave) toast("Load filters were not saved.");
  });
}

async function loadFiltersFromProject() {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = parseLoadParam(params.get("load"), params.get("textures"));
  let saved = null;
  if (projectReady()) {
    try {
      saved = normalizeLoad(await api.loadFilters());
    } catch {
      saved = fullLoad();
    }
  }
  const base = saved || fullLoad();
  state.load = fromUrl ? { ...fromUrl, visible: base.visible } : base;
  publishLoad();
  if (fromUrl && projectReady()) {
    try {
      await api.putLoadFilters(state.load);
    } catch {
      /* the session still has the URL filters */
    }
  }
}

function refreshLinkGizmos() {
  const load = state.load || fullLoad();
  if (!load.layers.behavior) {
    viewport.setLinks([]);
    return;
  }
  viewport.setLinks(linkPairs((state.view && state.view.actors) || []));
}

function renderLoadFilters() {
  const host = $("load-filters");
  if (!host) return;
  const load = state.load || fullLoad();
  host.replaceChildren();
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Load preset");
  for (const [id, preset] of Object.entries(PRESETS)) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = preset.label;
    select.append(option);
  }
  if (load.preset === "custom") {
    const option = document.createElement("option");
    option.value = "custom";
    option.textContent = "Custom";
    select.append(option);
  }
  select.value = PRESETS[load.preset] || load.preset === "custom" ? load.preset : "full";
  select.addEventListener("change", () => {
    if (!PRESETS[select.value]) return;
    void applyLoadChange(presetLoad(select.value, load.visible));
  });
  host.append(select);
  for (const id of LAYERS) host.append(loadRow(id, load));
  host.append(textureRow(load));
}

function loadRow(id, load) {
  const row = document.createElement("div");
  row.className = "load-row";
  const label = document.createElement("label");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = Boolean(load.layers[id]);
  box.addEventListener("change", () => {
    const layers = { ...load.layers, [id]: box.checked };
    void applyLoadChange(loadFromLayers(layers, load.textures, load.visible));
  });
  const name = document.createElement("span");
  name.textContent = LAYER_LABELS[id];
  label.append(box, name);
  const eye = document.createElement("button");
  eye.type = "button";
  eye.className = "eye";
  const shown = load.visible[id] !== false;
  eye.textContent = shown ? "Hide" : "Show";
  eye.disabled = !load.layers[id];
  eye.title = shown ? "Hide this layer" : "Show this layer";
  eye.addEventListener("click", () => {
    setLayerVisible(id, load.visible[id] === false);
  });
  row.append(label, eye);
  return row;
}

function textureRow(load) {
  const row = document.createElement("label");
  row.className = "load-row";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = load.textures !== false;
  const meshes = load.layers.models || load.layers.water || load.layers.decorations || load.layers.lights;
  box.disabled = !meshes;
  box.addEventListener("change", () => {
    void applyLoadChange(loadFromLayers(load.layers, box.checked, load.visible));
  });
  const name = document.createElement("span");
  name.textContent = "Load textures";
  box.title = "Download images after the mesh. Layout and textures=0 leave this off. The Textures checkbox on the stage bar shows or hides them, and does not fetch again.";
  row.append(box, name);
  return row;
}

function setLayerVisible(id, on) {
  state.load = { ...state.load, visible: { ...state.load.visible, [id]: on } };
  publishLoad();
  persistLoad();
}

async function applyLoadChange(next) {
  const previous = state.load || fullLoad();
  state.load = next;
  publishLoad();
  writeLoadUrl(next);
  persistLoad();
  if (!state.raw || !state.banc) return;
  const gained = LAYERS.filter((id) => next.layers[id] && !previous.layers[id]);
  const lost = LAYERS.filter((id) => previous.layers[id] && !next.layers[id]);
  if (lost.includes("terrain")) viewport.dropTerrain();
  if (gained.includes("terrain")) viewport.ensureTerrain();
  // Textures are applied onto the resident materials in publishLoad. A
  // texture change is not a scene reload and not a model fetch.
  if (!gained.length && !lost.length) return;
  if (!gained.length) {
    applyView(viewForLoad(state.raw));
    return;
  }
  const partial = loadFromLayers(
    Object.fromEntries(LAYERS.map((id) => [id, gained.includes(id)])),
    next.textures,
    next.visible,
  );
  try {
    const incoming = await api.scene(state.banc, { query: loadQuery(partial), textures: next.textures ? "1" : "0" });
    applyView(mergeViews(state.raw, incoming));
  } catch (error) {
    toast(error.message);
  }
}

async function openScene(banc, options = {}) {
  sceneFetch.abort();
  sceneFetch = new AbortController();
  const signal = sceneFetch.signal;
  const token = ++sceneOpenToken;
  const terrainAlreadyReady = state.banc === banc && sceneTerrainReady;
  if (!options.keepHistory) state.history = blankHistory(null);
  if (viewport) viewport.unloadScene();
  sceneVisualLoad = 0;
  sceneVisualBoxes = 0;
  sceneVisualDeferred = 0;
  sceneVisualResidentCapped = false;
  sceneActorsReady = false;
  sceneTerrainReady = (state.load && state.load.layers.terrain === false) ? true : terrainAlreadyReady;
  setScenePreparation(`Preparing ${banc.split("/").pop()}`, "Reading scene and companion assets…");
  try {
    const view = await api.scene(banc, { query: loadQuery(state.load), textures: state.load.textures ? "1" : "0", signal });
    if (signal.aborted || token !== sceneOpenToken) return;
    sceneVisualLoad = token;
    setScenePreparation("Loading scene in viewer", "Preparing and streaming scene models…", 0, 1);
    $("viewport-empty").hidden = true;
    $("viewport").classList.add("is-live");
    state.banc = banc;
    if (!options.keepHistory) state.history = blankHistory(null);
    const interesting = view.actors.find((actor) => actor.status === "edited" || actor.status === "added");
    applyView(view, { frame: Boolean(options.frame), select: interesting ? interesting.id : null, forceSceneLoad: true });
    if (!options.keepHistory) state.history = blankHistory(captureShot());
    renderScenes();
    updateHistoryButtons();
    renderChanges();
  } catch (error) {
    if (error && error.name === "AbortError") return;
    if (token === sceneOpenToken) {
      sceneVisualLoad = 0;
      clearScenePreparation();
      toast(error.message);
    }
  }
}

function bancKind(banc) {
  const name = (banc || "").split("/").pop() || "";
  if (name.includes("_Dynamic")) return "Dynamic";
  if (name.includes("_Static")) return "Static";
  return "companion";
}

function composeView(view) {
  if (!view) return view;
  const host = view.banc || state.banc;
  const actors = (view.actors || [])
    .filter((actor) => !actor.companion)
    .map((actor) => ({ ...actor, banc: actor.banc || host, companion: false }));
  if (state.showCompanions !== false) {
    for (const extra of view.companions || []) {
      const label = bancKind(extra.banc);
      for (const actor of extra.actors || []) {
        if (!actor.id) continue;
        actors.push({
          ...actor,
          companion: true,
          banc: extra.banc,
          placementId: actor.placementId || actor.id,
          id: String(actor.id).includes("::") ? actor.id : `${extra.banc}::${actor.id}`,
          companionLabel: label,
          pickable: state.companionPick !== false,
        });
      }
    }
  }
  return { ...view, actors };
}

function commandTarget(command) {
  const id = command.id || state.selected;
  const actor = actorRecord(id);
  const forced = command.banc || "";
  const rest = { ...command };
  delete rest.banc;
  const banc = forced || (actor && actor.banc) || state.banc;
  const placement = actor && actor.placementId;
  return {
    banc,
    command: placement ? { ...rest, id: placement } : rest,
    select: id,
  };
}

function actorRecord(id) {
  return ((state.view && state.view.actors) || []).find((actor) => actor.id === id) || null;
}

function diffTouched(diff) {
  if (!diff) return false;
  if (Object.keys(diff.actors || {}).length) return true;
  const groups = diff.decorationGroups || {};
  return Object.keys(groups.include || {}).length > 0 || (groups.exclude || []).length > 0;
}

function placementHash(actor) {
  return (actor && (actor.placementId || actor.id)) || null;
}

function findViewId(hash) {
  const actors = (state.view && state.view.actors) || [];
  const found = actors.find((actor) => actor.id === hash || actor.placementId === hash);
  return found ? found.id : hash;
}

function storedDecorationGroups() {
  const include = {};
  const exclude = new Set();
  const raw = state.raw || {};
  const absorb = (diff, prefix) => {
    const groups = (diff && diff.decorationGroups) || {};
    for (const [child, parent] of Object.entries(groups.include || {})) include[prefix + child] = findViewId(parent);
    for (const child of groups.exclude || []) exclude.add(prefix + child);
  };
  absorb(raw.diff, "");
  for (const extra of raw.companions || []) absorb(extra.diff, `${extra.banc}::`);
  return { include, exclude };
}

function currentDecorationGroups() {
  const actors = (state.view && state.view.actors) || [];
  const stored = storedDecorationGroups();
  return resolveDecorationGroups(actors, {
    boundsFor: (actor) => {
      const info = viewport && viewport.modelInfo(actor.id);
      return info && info.bounds ? info.bounds : null;
    },
    include: stored.include,
    exclude: stored.exclude,
  });
}

let decorationSelection = new Set();

function syncDecorationSelection() {
  const ids = state.view ? followersFor(state.selected, currentDecorationGroups()) : [];
  decorationSelection = new Set(ids);
  if (viewport && viewport.setGroupHighlight) viewport.setGroupHighlight(ids);
}

function moveWithDecorations(id, pose, followers) {
  const parent = actorRecord(id);
  const parentBanc = (parent && parent.banc) || state.banc;
  const parentHash = parent ? placementHash(parent) : id;
  const rows = [{
    banc: parentBanc,
    id: parentHash,
    translate: pose.translate,
    rotate: pose.rotate,
    scale: pose.scale,
  }];
  for (const child of followers) {
    const actor = actorRecord(child.id) || actorRecord(child.key);
    rows.push({
      banc: (actor && actor.banc) || parentBanc,
      id: actor ? placementHash(actor) : child.id,
      translate: (child.translate || []).map(round4),
      rotate: (child.rotate || []).map(round4),
    });
  }
  const byBanc = new Map();
  for (const row of rows) {
    const list = byBanc.get(row.banc) || [];
    list.push(row);
    byBanc.set(row.banc, list);
  }
  const count = followers.length;
  const name = (parent && (parent.name || parent.gyaml)) || "piece";
  const label = `Move ${name} · ${count} decoration${count === 1 ? "" : "s"}`;
  const commands = [...byBanc.entries()].map(([banc, actors]) => ({
    banc,
    command: {
      command: "transform_set",
      id: actors.some((row) => row.id === parentHash) ? parentHash : actors[0].id,
      actors: actors.map((row) => {
        const next = { id: row.id, translate: row.translate, rotate: row.rotate };
        if (row.scale) next.scale = row.scale;
        return next;
      }),
    },
  }));
  editBundle(commands, label, id);
}

function setDecorationParent(childIds, parentViewId) {
  const parent = parentViewId ? actorRecord(parentViewId) : null;
  const parentHash = parent ? placementHash(parent) : null;
  const byBanc = new Map();
  const ordered = [];
  for (const childId of childIds) {
    const child = actorRecord(childId);
    if (!child) continue;
    ordered.push(child);
    const banc = child.banc || state.banc;
    const list = byBanc.get(banc) || [];
    list.push(placementHash(child));
    byBanc.set(banc, list);
  }
  if (!ordered.length) return;
  const childName = ordered[0].name || ordered[0].gyaml || "decoration";
  const parentName = parent ? (parent.name || parent.gyaml) : "";
  const label = parent
    ? (ordered.length > 1 ? `Group ${ordered.length} with ${parentName}` : `Group ${childName} with ${parentName}`)
    : (ordered.length > 1 ? `Ungroup ${ordered.length}` : `Ungroup ${childName}`);
  const commands = [...byBanc.entries()].map(([banc, ids]) => ({
    banc,
    command: { command: "decoration_group", id: ids[0], ids, parent: parentHash },
  }));
  if (commands.length === 1) edit({ ...commands[0].command, banc: commands[0].banc }, label);
  else editBundle(commands, label, state.selected);
}

function noteCompanion() {
  const scene = state.scenes.find((item) => item.banks.includes(state.banc));
  const base = scene ? pretty(scene.name) : pretty((state.banc || "").split("/").pop() || "");
  const extras = (state.raw && state.raw.companions) || [];
  const kinds = extras.map((item) => bancKind(item.banc));
  const actor = actorRecord(state.selected);
  let suffix = "";
  if (scene?.banks.length > 1 && kinds.length) {
    suffix = state.showCompanions !== false ? " · Static + Dynamic" : ` · ${bancKind(state.banc)} only`;
  } else if (state.showCompanions !== false && kinds.length) {
    suffix = ` · with ${kinds.join(", ")}`;
  }
  if (actor && actor.companion) suffix += ` · editing ${actor.companionLabel}`;
  const node = $("scene-name");
  if (node) node.textContent = base + suffix;
  const active = $("active-bank");
  active.hidden = !(scene?.banks.length > 1 && kinds.length);
  active.value = bancKind(state.banc);
}

let lightingBanc = "";

function loadLighting(banc) {
  if (!banc || banc === lightingBanc) return;
  lightingBanc = banc;
  const note = $("lighting-note");
  const signal = sceneFetch.signal;
  api.lighting(banc, { signal }).then((data) => {
    if (signal.aborted || banc !== lightingBanc) return;
    const text = viewport.applyLighting(data);
    if (note) note.textContent = text || "";
  }).catch((error) => {
    if (error && error.name === "AbortError") return;
    if (signal.aborted || banc !== lightingBanc) return;
    if (note) note.textContent = viewport.applyLighting(null) || "";
  });
}

function applyView(view, { frame = false, select, forceSceneLoad = false } = {}) {
  const previousActors = state.view ? state.view.actors : null;
  const filtered = viewForLoad(view);
  state.raw = filtered;
  const composed = composeView(filtered);
  state.view = composed;
  if (select !== undefined) state.selected = select;
  else if (filtered.focus) state.selected = filtered.focus;
  if (state.selected && !composed.actors.some((actor) => actor.id === state.selected)) state.selected = null;
  const plan = frame ? { mode: "rebuild" } : planActorRefresh(previousActors, composed.actors);
  const patched = plan.mode === "keep" || (plan.mode === "patch" && viewport.patchActors(plan.actors));
  const previewUrl = fullVisual(state.load) ? filtered.previewUrl : null;
  const terrain = Boolean(state.load && state.load.layers.terrain);
  if (forceSceneLoad || (!patched && (frame || !viewport.reconcileActors(composed.actors)))) {
    state.modelErrors = [];
    state.meshAudit = null;
    state.terrainNotes = [];
    viewport.setActors(composed.actors, { frame, banc: filtered.banc, previewUrl, terrain });
  }
  viewport.select(state.selected);
  viewport.setFlags({ hidden: state.showHidden, labels: state.showLabels, visibleLayers: state.load && state.load.visible });
  refreshLinkGizmos();
  loadLighting(view.banc || state.banc);
  renderOutliner();
  renderProps();
  loadChanges(view.banc || state.banc);
  loadProblems(view.banc || state.banc);
  if (state.banc) state.modified.add(state.banc);
  if (!diffTouched(view.diff)) state.modified.delete(state.banc);
  for (const extra of view.companions || []) {
    if (diffTouched(extra.diff)) state.modified.add(extra.banc);
    else state.modified.delete(extra.banc);
  }
  noteCompanion();
}

function snapshot() {
  const raw = state.raw || state.view || {};
  const actors = structuredClone((raw.diff && raw.diff.actors) || {});
  const recipes = ((state.view && state.view.actors) || [])
    .filter((actor) => actor.status === "recipe" && !actor.companion)
    .map((actor) => ({
      id: actor.id,
      gyaml: actor.gyaml,
      name: actor.name,
      translate: actor.translate,
      rotate: actor.rotate,
      scale: actor.scale,
      params: actor.params,
      recipe: actor.recipe,
    }));
  return {
    actors,
    decorationGroups: structuredClone((raw.diff && raw.diff.decorationGroups) || { include: {}, exclude: [] }),
    recipes,
    companions: (raw.companions || []).map((item) => ({
      banc: item.banc,
      actors: structuredClone((item.diff && item.diff.actors) || {}),
      decorationGroups: structuredClone((item.diff && item.diff.decorationGroups) || { include: {}, exclude: [] }),
      recipes: (item.actors || [])
        .filter((actor) => actor.status === "recipe")
        .map((actor) => ({
          id: actor.placementId || actor.id,
          gyaml: actor.gyaml,
          name: actor.name,
          translate: actor.translate,
          rotate: actor.rotate,
          scale: actor.scale,
          params: actor.params,
          recipe: actor.recipe,
        })),
    })),
  };
}

function captureShot() {
  const file = snapshot();
  return {
    ...file,
    select: state.selected,
    viewActors: structuredClone((state.view && state.view.actors) || []),
  };
}

let persistChain = Promise.resolve();
let persistToken = 0;

function persistShot(shot, rollback) {
  const token = ++persistToken;
  const banc = state.banc;
  const leaving = rollback && rollback.shot;
  const writeRecipes = shotHasRecipes(shot) || shotHasRecipes(leaving);
  persistChain = persistChain.then(async () => {
    try {
      await writeShot(banc, shot, writeRecipes);
    } catch (error) {
      if (token !== persistToken) return;
      toast(error.message || "The project file did not save.");
      if (!rollback) return;
      state.history = rollback.history;
      if (rollback.shot) applyLocal(rollback.shot);
      updateHistoryButtons();
    }
  });
}

function shotHasRecipes(shot) {
  if (!shot) return false;
  if (shot.recipes && shot.recipes.length) return true;
  return (shot.companions || []).some((item) => item.recipes && item.recipes.length);
}

async function writeShot(banc, shot, writeRecipes) {
  if (!banc || !shot) return;
  const loaded = { loaded: enabledLayers(state.load), ...loadRequest() };
  await api.putDiff(banc, shot.actors || {}, { ...loaded, decorationGroups: shot.decorationGroups || { include: {}, exclude: [] } });
  if (writeRecipes) await api.putRecipes(banc, shot.recipes || []);
  for (const item of shot.companions || []) {
    if (!item.banc || item.banc === banc) continue;
    await api.putDiff(item.banc, item.actors || {}, {
      ...loaded,
      decorationGroups: item.decorationGroups || { include: {}, exclude: [] },
    });
    if (writeRecipes && Array.isArray(item.recipes)) await api.putRecipes(item.banc, item.recipes);
  }
  if (state.banc === banc) {
    loadChanges(banc);
    loadProblems(banc);
    noteHotReload();
  }
}

function applyLocal(shot) {
  if (!shot || !state.raw) return;
  const previousActors = state.view ? state.view.actors : null;
  const nextActors = structuredClone(shot.viewActors || []);
  const hostActors = nextActors.filter((actor) => !actor.companion).map((actor) => {
    const copy = { ...actor };
    delete copy.companion;
    delete copy.companionLabel;
    delete copy.banc;
    return copy;
  });
  const companionActors = new Map();
  for (const actor of nextActors) {
    if (!actor.companion) continue;
    const list = companionActors.get(actor.banc) || [];
    const copy = { ...actor, id: actor.placementId || String(actor.id).split("::").pop() };
    delete copy.companion;
    delete copy.companionLabel;
    delete copy.pickable;
    list.push(copy);
    companionActors.set(actor.banc, list);
  }
  state.raw = {
    ...state.raw,
    actors: hostActors,
    diff: {
      ...(state.raw.diff || {}),
      actors: structuredClone(shot.actors || {}),
      decorationGroups: structuredClone(shot.decorationGroups || { include: {}, exclude: [] }),
    },
    recipes: structuredClone(shot.recipes || []),
    companions: (state.raw.companions || []).map((item) => {
      const saved = (shot.companions || []).find((entry) => entry.banc === item.banc);
      const listed = companionActors.get(item.banc);
      return {
        ...item,
        ...(saved ? {
          diff: {
            ...(item.diff || {}),
            actors: structuredClone(saved.actors || {}),
            decorationGroups: structuredClone(saved.decorationGroups || { include: {}, exclude: [] }),
          },
        } : {}),
        ...(listed ? { actors: listed } : {}),
      };
    }),
  };
  state.view = { ...(state.view || {}), actors: nextActors };
  const plan = planActorRefresh(previousActors, nextActors);
  const patched = plan.mode === "keep" || (plan.mode === "patch" && viewport.patchActors(plan.actors));
  if (!patched && !viewport.reconcileActors(nextActors)) {
    viewport.setActors(nextActors, { frame: false, banc: state.banc });
  }
  state.selected = shot.select && nextActors.some((actor) => actor.id === shot.select) ? shot.select : null;
  viewport.select(state.selected);
  if (state.banc) {
    if (diffTouched(state.raw.diff)) state.modified.add(state.banc);
    else state.modified.delete(state.banc);
  }
  renderOutliner();
  renderProps();
  renderScenes();
  renderChanges();
  noteCompanion();
}

async function edit(command, labelOverride) {
  if (!state.banc) return;
  if (!projectReady()) {
    toast("Open a project first. Looking around does not write anything.");
    openProjectDialog();
    return;
  }
  const target = commandTarget(command);
  const subject = actorRecord(command.id) || { gyaml: command.actor || command.gyaml || "" };
  const label = labelOverride || editLabel(command, subject, { gizmo: state.gizmo });
  await persistChain;
  try {
    const view = await api.edit(target.banc, target.command, { load: loadQuery(state.load), textures: state.load.textures ? "1" : "0" });
    if (target.banc !== state.banc && state.raw) {
      const companions = (state.raw.companions || []).map((item) =>
        item.banc === target.banc
          ? { banc: item.banc, actors: view.actors, removed: view.removed, diff: view.diff }
          : item,
      );
      const focus = view.focus ? `${target.banc}::${view.focus}` : target.select;
      applyView({ ...state.raw, companions }, { select: focus });
    } else {
      applyView(
        { ...view, companions: (state.raw && state.raw.companions) || [] },
        { select: view.focus || state.selected },
      );
    }
    state.history = pushEdit(state.history, { label, shot: captureShot() });
    renderScenes();
    renderChanges();
    noteHotReload();
  } catch (error) {
    toast(error.message);
    if (state.view && !viewport.patchActors(state.view.actors)) {
      viewport.setActors(state.view.actors, { frame: false, banc: state.banc });
    }
    viewport.select(state.selected);
  }
  updateHistoryButtons();
}

async function editBundle(commands, label, selectId) {
  if (!state.banc) return;
  if (!projectReady()) {
    toast("Open a project first. Looking around does not write anything.");
    openProjectDialog();
    return;
  }
  await persistChain;
  const before = captureShot();
  try {
    let hostView = null;
    const patched = new Map();
    for (const item of commands) {
      const view = await api.edit(item.banc, item.command, { load: loadQuery(state.load), textures: state.load.textures ? "1" : "0" });
      if (item.banc === state.banc) hostView = view;
      else patched.set(item.banc, view);
    }
    const base = hostView || state.raw;
    if (base) {
      const source = hostView ? ((state.raw && state.raw.companions) || []) : (base.companions || []);
      const companions = source.map((item) => {
        const next = patched.get(item.banc);
        return next ? { banc: item.banc, actors: next.actors, removed: next.removed, diff: next.diff } : item;
      });
      applyView(hostView ? { ...hostView, companions } : { ...base, companions }, { select: selectId });
    }
    state.history = pushEdit(state.history, { label, shot: captureShot() });
    renderScenes();
    renderChanges();
    noteHotReload();
  } catch (error) {
    toast(error.message);
    applyLocal(before);
    persistShot(before);
    viewport.select(state.selected);
  }
  updateHistoryButtons();
}

function undo() {
  if (!state.banc) return;
  const previousHistory = state.history;
  const step = popUndo(state.history);
  if (!step) return;
  state.history = step.history;
  updateHistoryButtons();
  if (step.entry.behavior) {
    restoreBehavior(step.entry.gyaml, step.entry.before, previousHistory);
    return;
  }
  if (step.entry.local) {
    setCategory(step.entry.category, step.entry.before, { record: false });
    renderOutliner();
    renderChanges();
    return;
  }
  if (!step.scene) return;
  applyLocal(step.scene);
  persistShot(step.scene, { history: previousHistory, shot: step.entry.shot });
}

function redo() {
  if (!state.banc) return;
  const previousHistory = state.history;
  const step = popRedo(state.history);
  if (!step) return;
  state.history = step.history;
  updateHistoryButtons();
  if (step.entry.behavior) {
    restoreBehavior(step.entry.gyaml, step.entry.after, previousHistory);
    return;
  }
  if (step.entry.local) {
    setCategory(step.entry.category, step.entry.after, { record: false });
    renderOutliner();
    renderChanges();
    return;
  }
  applyLocal(step.entry.shot);
  persistShot(step.entry.shot, { history: previousHistory, shot: step.scene });
}

async function restoreBehavior(gyaml, ops, previousHistory) {
  try {
    const graph = await api.putBehaviorOps({ gyaml, ops: ops || [] });
    if (state.behavior && state.behavior.gyaml === gyaml) {
      state.behavior = graph;
      drawBehavior();
    }
    renderChanges();
  } catch (error) {
    state.history = previousHistory;
    updateHistoryButtons();
    toast(error.message);
  }
}

function removeRecipe(actor) {
  const file = (actor.recipe && actor.recipe.file) || "the recipe";
  const label = actor.name || actor.gyaml;
  if (!window.confirm(`Remove ${label} from ${file}? The build will no longer place it.`)) return;
  edit({ command: "remove", id: actor.id });
}

const CATEGORY_FILTERS = [
  ["npc", "NPC"],
  ["enemy", "Enemy"],
  ["object", "Object"],
  ["land", "Land"],
  ["effect", "Effect"],
  ["animal", "Animal"],
  ["area", "Area"],
  ["system", "System"],
  ["other", "Other"],
];

const HELPER_DEFAULTS = {
  npc: true,
  enemy: true,
  object: true,
  land: true,
  effect: true,
  animal: true,
  area: false,
  system: false,
  other: true,
};

function loadHelperBoxes() {
  const next = { ...HELPER_DEFAULTS };
  try {
    const raw = localStorage.getItem("ocarinaforge.helperBoxes");
    if (!raw) return next;
    const parsed = JSON.parse(raw);
    for (const key of Object.keys(HELPER_DEFAULTS)) {
      if (parsed && typeof parsed[key] === "boolean") next[key] = parsed[key];
    }
  } catch {
    /* a bad saved value keeps the defaults */
  }
  return next;
}

function eyeMarkup(on) {
  const slash = on ? "" : '<path d="M2.2 13.6 13.8 2.4" fill="none" stroke="currentColor" stroke-width="1.4"/>';
  return `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M8 3.2C4.2 3.2 1.6 8 1.6 8S4.2 12.8 8 12.8 14.4 8 14.4 8 11.8 3.2 8 3.2zm0 7.6A2.8 2.8 0 1 1 8 5.2a2.8 2.8 0 0 1 0 5.6z"/>${slash}</svg>`;
}

function renderLegend() {
  const host = document.querySelector(".legend");
  if (!host || !state.helperBoxes) return;
  host.replaceChildren();
  for (const [id, label] of CATEGORY_FILTERS) {
    if (id === "other") continue;
    const on = state.helperBoxes[id] !== false;
    const button = document.createElement("button");
    button.type = "button";
    button.className = on ? "legend-toggle" : "legend-toggle is-off";
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.title = on
      ? `Hide ${label} helper boxes and labels. Models stay. The selected or hovered actor still shows its box.`
      : `Show ${label} helper boxes for actors without a model, and their labels.`;
    const swatch = document.createElement("i");
    swatch.className = `swatch ${id}`;
    const name = document.createElement("span");
    name.textContent = label;
    const eye = document.createElement("i");
    eye.className = "eye";
    eye.innerHTML = eyeMarkup(on);
    button.append(swatch, name, eye);
    button.addEventListener("click", () => {
      state.helperBoxes[id] = !on;
      localStorage.setItem("ocarinaforge.helperBoxes", JSON.stringify(state.helperBoxes));
      viewport.setFlags({ boxes: { [id]: state.helperBoxes[id] } });
      renderLegend();
    });
    host.append(button);
  }
}

state.helperBoxes = loadHelperBoxes();
viewport.setFlags({ boxes: state.helperBoxes });
renderLegend();

function setCategory(cat, mode, options = {}) {
  const previous = state.categories[cat] || "show";
  if (previous === mode) return;
  state.categories[cat] = mode;
  document.querySelectorAll(`[data-cat="${cat}"]`).forEach((item) => {
    item.classList.toggle("is-on", item.dataset.mode === mode);
  });
  viewport.setFlags({ categories: { [cat]: mode } });
  if (options.record === false || !state.banc) return;
  const name = (CATEGORY_FILTERS.find(([id]) => id === cat) || [cat, cat])[1];
  const label = mode === "lock" ? `Lock ${name}` : mode === "hide" ? `Hide ${name}` : `Show ${name}`;
  state.history = pushEdit(state.history, { label, local: true, category: cat, before: previous, after: mode });
  updateHistoryButtons();
  renderChanges();
}

function updateHistoryButtons() {
  $("undo").disabled = state.history.undo.length === 0;
  $("redo").disabled = state.history.redo.length === 0;
}

let outlinerVirtualRows = null;
let outlinerScrollLocked = false;
let outlinerScrollFrame = 0;
let outlinerScrollToken = 0;

function outlinerScrollBehavior() {
  try {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return "auto";
  } catch {
    /* smooth is the default */
  }
  return "smooth";
}

function setOutlinerHint(text) {
  const hint = $("outliner-hint");
  if (!hint) return;
  const next = text || "";
  if (hint.textContent === next && hint.hidden === !next) return;
  hint.textContent = next;
  hint.hidden = !next;
}

function bindOutlinerScroll(list) {
  if (!list || list.dataset.outlinerScroll === "1") return;
  list.dataset.outlinerScroll = "1";
  list.addEventListener("scroll", onOutlinerScroll, { passive: true });
}

function onOutlinerScroll() {
  if (outlinerScrollLocked || !outlinerVirtualRows) return;
  const token = outlinerScrollToken;
  if (outlinerScrollFrame) return;
  outlinerScrollFrame = requestAnimationFrame(() => {
    outlinerScrollFrame = 0;
    if (token !== outlinerScrollToken || outlinerScrollLocked || !outlinerVirtualRows) return;
    const list = $("outliner");
    if (list) paintOutlinerVirtual(list, outlinerVirtualRows);
  });
}

function renderOutliner({ reveal = null } = {}) {
  renderFilters();
  const list = $("outliner");
  if (!list) return;
  bindOutlinerScroll(list);
  if (reveal === "viewport" && state.selected && dock.isOpen(panelIdForTab("outliner"))) {
    dock.reveal(panelIdForTab("outliner"));
  }
  const savedScroll = list.scrollTop || 0;
  const clientHeight = list.clientHeight || 0;
  outlinerScrollToken += 1;
  if (!state.view) {
    outlinerVirtualRows = null;
    decorationSelection = new Set();
    if (viewport && viewport.setGroupHighlight) viewport.setGroupHighlight([]);
    list.replaceChildren(empty("Open a scene to see its actors."));
    setOutlinerHint("");
    return;
  }
  syncDecorationSelection();
  const query = $("actor-search").value.trim().toLowerCase();
  if (!state.outlinerCollapsed) state.outlinerCollapsed = {};
  const actors = state.view.actors || [];
  let plan = { action: "none" };
  if (reveal === "viewport") {
    plan = planViewportReveal({
      actors,
      selectedId: state.selected,
      source: "viewport",
      query,
      groups: CATEGORY_FILTERS,
      collapsed: state.outlinerCollapsed,
      scrollTop: savedScroll,
      clientHeight,
      behavior: outlinerScrollBehavior(),
    });
    for (const id of plan.expand || []) delete state.outlinerCollapsed[id];
  }
  const rows = plan.rows || buildOutlinerRows({
    actors,
    query,
    groups: CATEGORY_FILTERS,
    collapsed: state.outlinerCollapsed,
  });
  const selected = actors.find((actor) => actor && actor.id === state.selected) || null;
  setOutlinerHint(plan.action === "hint" ? plan.hint : revealHint(selected, query));
  outlinerScrollLocked = true;
  try {
    if (shouldVirtualize(rows)) {
      outlinerVirtualRows = rows;
      list.replaceChildren();
      const host = document.createElement("div");
      host.className = "outliner-virtual";
      list.append(host);
      appendOutlinerExtras(list);
      // Height has to exist before scrollTop, or the browser clamps the reveal to 0.
      paintOutlinerVirtual(list, rows);
      list.scrollTop = savedScroll;
      paintOutlinerVirtual(list, rows);
    } else {
      outlinerVirtualRows = null;
      list.replaceChildren();
      if (!rows.length) list.append(empty(actors.length ? "No actors match." : "This scene has no actors."));
      for (const row of rows) list.append(row.kind === "group" ? groupRow(row) : actorRow(row.actor));
      appendOutlinerExtras(list);
      list.scrollTop = savedScroll;
    }
  } finally {
    outlinerScrollLocked = false;
  }
  if (plan.action === "reveal") scheduleOutlinerReveal(plan, rows);
}

function scheduleOutlinerReveal(plan, rows, attempt = 0) {
  const token = outlinerScrollToken;
  requestAnimationFrame(() => {
    if (token !== outlinerScrollToken) return;
    const list = $("outliner");
    if (!list) return;
    if (!(list.clientHeight > 0) && attempt < 4) {
      scheduleOutlinerReveal(plan, rows, attempt + 1);
      return;
    }
    outlinerScrollLocked = true;
    try {
      if (plan.virtual) {
        const top = virtualRevealTop(rows, plan.index, {
          scrollTop: list.scrollTop || 0,
          clientHeight: list.clientHeight || 0,
        });
        followSelection(list, { ...plan, scrollTop: top }, null);
        paintOutlinerVirtual(list, rows);
      } else {
        followSelection(list, plan, findOutlinerActor(list, plan.actorId));
      }
    } finally {
      outlinerScrollLocked = false;
    }
  });
}

function paintOutlinerVirtual(list, rows) {
  const host = list.querySelector(":scope > .outliner-virtual");
  if (!host) return;
  const slice = virtualSlice(rows, { scrollTop: list.scrollTop || 0, clientHeight: list.clientHeight || 0 });
  host.style.height = `${slice.total}px`;
  const fragment = document.createDocumentFragment();
  for (const item of slice.items) {
    const el = item.row.kind === "group" ? groupRow(item.row) : actorRow(item.row.actor);
    el.style.top = `${item.top}px`;
    el.style.height = `${item.height}px`;
    fragment.append(el);
  }
  host.replaceChildren(fragment);
}

function findOutlinerActor(list, id) {
  if (!list || id == null) return null;
  const want = String(id);
  for (const node of list.querySelectorAll("[data-actor-id]")) {
    if (node.getAttribute("data-actor-id") === want) return node;
  }
  return null;
}

function appendOutlinerExtras(list) {
  for (const removed of (state.view && state.view.removed) || []) {
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
  for (const extra of (state.raw && state.raw.companions) || []) {
    if (state.showCompanions === false) continue;
    const label = bancKind(extra.banc);
    for (const removed of extra.removed || []) {
      const row = document.createElement("div");
      row.className = "actor-row";
      const text = document.createElement("span");
      text.className = "grow";
      text.textContent = `${removed.gyaml || "Removed"} · ${label}`;
      const badge = document.createElement("span");
      badge.className = "badge removed";
      badge.textContent = "removed";
      const restore = document.createElement("button");
      restore.type = "button";
      restore.className = "ghost";
      restore.textContent = "Restore";
      restore.addEventListener("click", () => edit({ command: "revert", id: removed.id, banc: extra.banc }));
      row.append(text, badge, restore);
      list.append(row);
    }
  }
}

function actorRow(actor) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "actor-row";
  button.setAttribute("role", "option");
  button.setAttribute("aria-selected", actor.id && actor.id === state.selected ? "true" : "false");
  if (actor.id) button.setAttribute("data-actor-id", String(actor.id));
  if (decorationSelection.has(actor.id)) button.classList.add("is-grouped");
  const swatch = document.createElement("i");
  swatch.className = `swatch ${actor.category}`;
  const text = document.createElement("span");
  text.className = "grow";
  const strong = document.createElement("strong");
  strong.textContent = actor.companion
    ? `${actor.name || actor.gyaml} · ${actor.companionLabel || "companion"}`
    : actor.name || actor.gyaml;
  const small = document.createElement("small");
  small.textContent = actor.status === "recipe" ? (actor.recipe && actor.recipe.note) || "added by build" : actor.name ? actor.gyaml : actor.category;
  text.append(strong, small);
  button.append(swatch, text);
  if (actor.status && actor.status !== "vanilla") {
    const badge = document.createElement("span");
    badge.className = `badge ${actor.status}`;
    badge.textContent = actor.status === "recipe" ? "build" : actor.status;
    button.append(badge);
  }
  button.addEventListener("click", () => {
    state.selected = actor.id;
    viewport.select(actor.id);
    renderOutliner({ reveal: "panel" });
    renderProps();
    noteCompanion();
    showTab("props");
  });
  button.addEventListener("dblclick", () => viewport.frame(actor.id));
  return button;
}

function groupRow(group) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = group.collapsed ? "outliner-group is-shut" : "outliner-group";
  button.setAttribute("aria-expanded", group.collapsed ? "false" : "true");
  button.dataset.group = group.id;
  button.title = group.collapsed ? `Expand ${group.label}` : `Collapse ${group.label}`;
  const caret = document.createElement("i");
  caret.className = "outliner-caret";
  caret.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = group.label;
  const count = document.createElement("span");
  count.className = "count";
  count.textContent = String(group.count);
  button.append(caret, label, count);
  button.addEventListener("click", () => {
    if (!state.outlinerCollapsed) state.outlinerCollapsed = {};
    if (group.collapsed) delete state.outlinerCollapsed[group.id];
    else state.outlinerCollapsed[group.id] = true;
    renderOutliner({ reveal: "panel" });
  });
  return button;
}

function renderFilters() {
  const host = $("outliner-filters");
  if (!host) return;
  host.replaceChildren();
  if (!state.view) return;
  const volumes = document.createElement("button");
  volumes.type = "button";
  volumes.className = state.hideVolumes ? "is-off" : "";
  volumes.textContent = "Volumes";
  volumes.title = state.hideVolumes ? "Show volume outlines" : "Hide every volume outline";
  volumes.addEventListener("click", () => {
    state.hideVolumes = !state.hideVolumes;
    viewport.setFlags({ volumes: state.hideVolumes });
    renderOutliner();
  });
  host.append(volumes);
  const present = new Set(state.view.actors.map((actor) => actor.category));
  for (const [id, label] of CATEGORY_FILTERS) {
    if (!present.has(id) && state.categories[id] !== "hide") continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = state.categories[id] === "hide" ? "is-off" : "";
    button.textContent = label;
    const hidden = state.categories[id] === "hide";
    button.title = hidden ? `Show ${label}` : `Hide ${label}`;
    button.addEventListener("click", () => {
      if (!state.categoryMemory) state.categoryMemory = {};
      if (hidden) setCategory(id, state.categoryMemory[id] || "show");
      else {
        state.categoryMemory[id] = state.categories[id] || "show";
        setCategory(id, "hide");
      }
      renderOutliner();
    });
    host.append(button);
  }
}

function renderDecorationGroup(panel, actor) {
  const info = groupPanel(actor, (state.view && state.view.actors) || [], currentDecorationGroups());
  if (!info) return;
  const box = document.createElement("section");
  box.className = "decoration-group";
  const title = document.createElement("h3");
  title.textContent = "Decorations";
  const note = document.createElement("p");
  note.className = "muted";
  box.append(title, note);
  if (info.role === "decoration") {
    title.textContent = "Moves with";
    if (info.parentId) {
      note.textContent = `${info.parentName}. Dragging that piece moves this one. Dragging this one leaves the piece where it is.`;
      const row = document.createElement("div");
      row.className = "actions";
      row.append(action("Remove from group", () => setDecorationParent([actor.id], null)));
      box.append(row);
    } else {
      note.textContent = "This decoration moves on its own. Group it with a nearby piece to drag them together.";
    }
    for (const parent of info.alternatives.slice(0, 8)) {
      const row = document.createElement("div");
      row.className = "actions";
      row.append(action(`Group with ${parent.name || parent.gyaml}`, () => setDecorationParent([actor.id], parent.id)));
      box.append(row);
    }
    panel.append(box);
    return;
  }
  const count = info.members.length;
  note.textContent = count
    ? `${count} decoration${count === 1 ? "" : "s"} move and turn with this piece. Scale does not. The Decorations layer has to be loaded.`
    : "No decoration is grouped with this piece. Nearby ones can be added. The Decorations layer has to be loaded.";
  const shown = info.members.slice(0, 24);
  for (const member of shown) {
    const row = document.createElement("div");
    row.className = "actions";
    const label = document.createElement("span");
    label.textContent = member.name || member.gyaml;
    row.append(label, action("Remove", () => setDecorationParent([member.id], null)));
    box.append(row);
  }
  if (count > shown.length) {
    const more = document.createElement("p");
    more.className = "muted";
    more.textContent = `${count - shown.length} more. Remove all clears every one.`;
    box.append(more);
  }
  if (count) {
    const row = document.createElement("div");
    row.className = "actions";
    row.append(action("Remove all", () => setDecorationParent(info.members.map((member) => member.id), null)));
    box.append(row);
  }
  for (const candidate of info.candidates.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = "actions";
    row.append(action(`Add ${candidate.name || candidate.gyaml}`, () => setDecorationParent([candidate.id], actor.id)));
    box.append(row);
  }
  panel.append(box);
}

function renderProps() {
  if (dock.isOpen("behavior")) refreshBehavior();
  renderDetails();
  syncDecorationSelection();
  const panel = $("props-panel");
  panel.replaceChildren();
  const actor = selectedActor();
  if (!actor) {
    panel.append(empty("Select an actor in the view or the list. Move, Rotate, and Scale use the gizmo. G moves, R rotates, F frames."));
    return;
  }
  const head = document.createElement("div");
  head.className = "identity";
  const title = document.createElement("h3");
  title.textContent = actor.name || actor.gyaml;
  const info = viewport.modelInfo(actor.id);
  const line = document.createElement("p");
  line.className = "model-line";
  line.textContent = modelLine(info);
  line.title = (info && info.source) || line.textContent;
  const meta = document.createElement("div");
  const cat = document.createElement("span");
  cat.className = "badge";
  cat.textContent = actor.category;
  meta.append(cat);
  if (actor.status !== "vanilla") {
    const status = document.createElement("span");
    status.className = `badge ${actor.status}`;
    status.textContent = actor.status === "recipe" ? "build" : actor.status;
    meta.append(" ", status);
  }
  head.append(
    title,
    line,
    identityRow("Gyaml", actor.gyaml),
    identityRow("Hash", actor.id || "none. This actor cannot be edited until the file has a Hash.", true),
    meta,
  );
  panel.append(head);
  const jumps = document.createElement("div");
  jumps.className = "actions";
  jumps.append(action("Behavior", () => showTab("behavior")), action("Details", () => showTab("details")));
  panel.append(jumps);
  const notes = actorProblems(actor);
  if (notes.length) panel.append(problemNotes(notes));
  if (actor.status === "recipe") panel.append(recipeNote(actor));

  if (!actor.id || actor.status === "locked") return;

  panel.append(vectorFields("Position (metres)", "t", actor.translate));
  panel.append(vectorFields("Rotation (degrees, X then Y then Z)", "r", actor.rotate.map(deg)));
  panel.append(vectorFields("Scale", "s", actor.scale));
  if (actor.scaleInFile === false) {
    const scaleNote = document.createElement("p");
    scaleNote.className = "muted";
    scaleNote.textContent = "This actor has no Scale in the banc. Saving a different scale writes an add. The patch grows the file, and the game applies it on the next process start.";
    panel.append(scaleNote);
  }

  const actions = document.createElement("div");
  actions.className = "actions";
  if (actor.status === "recipe") {
    actions.append(
      action("Duplicate", () => edit({ command: "duplicate", id: actor.id })),
      action("Hide", () => removeRecipe(actor)),
      action("Delete", () => removeRecipe(actor)),
      action("Revert", () => edit({ command: "revert", id: actor.id })),
    );
  } else {
    actions.append(
      action("Duplicate", () => edit({ command: "duplicate", id: actor.id })),
      action(actor.status === "hidden" ? "Show" : "Hide", () => edit({ command: actor.status === "hidden" ? "unhide" : "hide", id: actor.id })),
      action("Delete", () => edit({ command: "remove", id: actor.id })),
      action("Change actor", () => openAdd("replace")),
    );
    if (actor.status !== "vanilla") actions.append(action("Revert", () => edit({ command: "revert", id: actor.id })));
  }
  panel.append(actions);
  renderDecorationGroup(panel, actor);

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

function identityRow(caption, value, mono = false) {
  const row = document.createElement("div");
  row.className = mono ? "id-row hash" : "id-row";
  const label = document.createElement("span");
  label.textContent = caption;
  row.append(label, document.createTextNode(value));
  return row;
}

function recipeNote(actor) {
  const wrap = document.createElement("div");
  wrap.className = "model-note";
  const note = document.createElement("p");
  note.textContent = (actor.recipe && actor.recipe.note) || "added by build";
  const file = document.createElement("p");
  file.className = "muted";
  file.textContent = (actor.recipe && actor.recipe.file) || "";
  wrap.append(note, file);
  if (actor.recipe && actor.recipe.flag) {
    const flag = document.createElement("p");
    flag.className = "muted";
    flag.textContent = `Present while ${actor.recipe.flag} is set. Edits are written to the recipe file. The vanilla banc stays as it is until you build.`;
    wrap.append(flag);
  } else {
    const flag = document.createElement("p");
    flag.className = "muted";
    flag.textContent = "Edits are written to the recipe file. The vanilla banc stays as it is until you build.";
    wrap.append(flag);
  }
  return wrap;
}

function renderDetails() {
  const panel = $("details-panel");
  if (!panel) return;
  panel.replaceChildren();
  const actor = selectedActor();
  if (!actor) {
    panel.append(empty("Select an actor to read its model."));
    return;
  }
  const title = document.createElement("h3");
  title.textContent = actor.name || actor.gyaml;
  panel.append(title, modelBlock(actor));
  const materials = materialBlock(viewport.modelInfo(actor.id));
  if (materials) panel.append(materials);
}

function modelBlock(actor) {
  const wrap = document.createElement("div");
  wrap.className = "model-note";
  const info = viewport.modelInfo(actor.id) || { status: "loading", reason: "Waiting to convert" };
  const title = document.createElement("div");
  title.className = "axis-label";
  title.textContent = "Model";
  const body = document.createElement("p");
  if (info.status === "mesh") {
    const bits = [`${info.triangles || 0} triangles`];
    if (info.textures) bits.push(info.textures === 1 ? "1 base color" : `${info.textures} base colors`);
    if (info.source) bits.push(info.source);
    body.textContent = bits.join(" · ");
  } else if (info.status === "loading" || info.status === "queued") {
    body.textContent = "Waiting to convert";
  } else {
    wrap.classList.add("is-box");
    body.textContent = info.reason || "This actor stays a labeled box.";
  }
  wrap.append(title, body);
  if (info.status === "mesh" && info.reason) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = info.reason;
    wrap.append(note);
  } else if (info.status !== "mesh" && info.source) {
    const source = document.createElement("p");
    source.className = "muted";
    source.textContent = info.source;
    wrap.append(source);
  }
  if (info.textureNote) {
    const textures = document.createElement("p");
    textures.className = "muted";
    textures.textContent = info.textureNote;
    wrap.append(textures);
  }
  return wrap;
}

function materialBlock(info) {
  const rows = (info && info.textureLog) || [];
  const useful = rows.filter((row) => row && (row.material || (row.roles && row.roles.length) || (row.params && row.params.length)));
  if (!useful.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "model-note";
  const title = document.createElement("div");
  title.className = "axis-label";
  title.textContent = "Materials";
  wrap.append(title);
  for (const row of useful.slice(0, 8)) {
    const body = document.createElement("p");
    body.className = "material-dump";
    body.textContent = materialText(row);
    wrap.append(body);
  }
  if (useful.length > 8) {
    const more = document.createElement("p");
    more.className = "muted";
    more.textContent = `${useful.length - 8} more. python -m ocarina_forge material-debug prints every material.`;
    wrap.append(more);
  }
  return wrap;
}

function materialText(row) {
  const lines = [row.material || "material"];
  const shader = [row.archive, row.shader].filter(Boolean).join(" / ");
  lines.push(shader || "no shader assign");
  if (row.hints && row.hints.length) lines.push("name hint " + row.hints.join(", "));
  const attributes = row.attributes || {};
  for (const key of Object.keys(attributes)) lines.push(`${key} = ${attributes[key]}`);
  const projection = row.projection || {};
  for (const key of Object.keys(projection)) lines.push(`${key} = ${projection[key]}`);
  for (const role of row.roles || []) {
    const slot = role[0] || "?";
    const texture = role[1] || "?";
    const kind = role[2] || "other";
    const uv = role[3] ? `uv ${role[3]}` : "uv (not in the shader assign)";
    const srt = role[4] ? `srt ${role[4]}` : "srt (none named for this sampler)";
    lines.push(`${slot} = ${texture}  ${kind}  ${uv}  ${srt}`);
  }
  if (row.alpha) lines.push(String(row.alpha));
  const options = row.options || {};
  for (const key of Object.keys(options)) lines.push(`${key} = ${options[key]}`);
  for (const param of row.params || []) lines.push(paramText(param));
  return lines.join("\n");
}

function paramText(param) {
  if (!param) return "";
  if (param.kind === "texsrt") {
    const scale = (param.scale || []).join(" ");
    const translate = (param.translate || []).join(" ");
    return `${param.name}  texsrt ${param.mode}  scale ${scale}  rotate ${param.rotate}  translate ${translate}`;
  }
  const value = Array.isArray(param.value) ? param.value.join(" ") : param.value ?? "";
  return `${param.name}  ${param.kind || ""} ${value}`.trim();
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

const permissionsUi = {
  catalog: null,
  rows: [],
  selection: new Map(),
  view: null,
};

function closeOpenDialogs() {
  for (const dialog of document.querySelectorAll("dialog")) {
    if (dialog.open) dialog.close();
  }
}

function currentPermissionDeclaration() {
  return declarationFromSelection(permissionsUi.selection, {
    net: parseNetLines($("permissions-net").value),
    multiplayer: $("permissions-multiplayer").value,
  });
}

function renderPermissions({ keepFields = false } = {}) {
  const view = permissionsUi.view || {};
  const declaration = view.declaration || {};
  $("permissions-mod").textContent = view.modId ? `Mod id ${view.modId}` : "";
  $("permissions-restart").textContent = (permissionsUi.catalog && permissionsUi.catalog.restartNote) || view.restartNote || "";
  if (!keepFields) {
    $("permissions-multiplayer").value = declaration.multiplayer || "local";
    $("permissions-net").value = (declaration.net || []).join("\n");
  }
  if (!$("permissions-game").value && view.suggestedGame) $("permissions-game").value = view.suggestedGame;

  const draft = currentPermissionDeclaration();
  const local = validateClient(draft, {
    content: view.content || "",
    runtime: view.runtime || "",
    system: Boolean(declaration.system),
    rows: permissionsUi.rows,
  });
  const errors = $("permissions-errors");
  errors.replaceChildren();
  for (const message of [...(view.errors || []), ...local.errors.filter((item) => !(view.errors || []).includes(item))]) {
    const line = document.createElement("p");
    line.textContent = message;
    errors.append(line);
  }
  for (const message of local.warnings || []) {
    const line = document.createElement("p");
    line.className = "warn";
    line.textContent = message;
    errors.append(line);
  }

  const scan = $("permissions-scan");
  scan.replaceChildren();
  const report = view.scan || {};
  for (const item of report.dynamicImports || []) {
    const line = document.createElement("p");
    line.className = "warn";
    line.textContent = `${item.file}:${item.line} uses import(${item.specifier ? `"${item.specifier}"` : ""}). A mod's modules are its static imports. import() is not available.`;
    scan.append(line);
  }
  for (const origin of draft.net) {
    if (!isNetOrigin(origin)) continue;
    const id = origin === "*" ? "net:*" : `net:${origin}`;
    if (permissionsUi.selection.has(id)) continue;
    const line = document.createElement("p");
    const text = document.createElement("span");
    text.textContent = `${origin} needs ${id}. `;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Add";
    button.addEventListener("click", () => {
      permissionsUi.selection.set(id, "required");
      permissionsUi.view = { ...permissionsUi.view, errors: [] };
      renderPermissions({ keepFields: true });
    });
    line.append(text, button);
    scan.append(line);
  }
  for (const name of report.missing || []) {
    const line = document.createElement("p");
    const text = document.createElement("span");
    text.textContent = `${name} is used by the scripts and is not declared. `;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Add";
    button.addEventListener("click", () => {
      const next = addPermission(currentPermissionDeclaration(), name);
      permissionsUi.selection = selectionFromDeclaration(next);
      permissionsUi.view = { ...view, declaration: { ...declaration, ...next }, errors: [] };
      $("permissions-net").value = next.net.join("\n");
      renderPermissions({ keepFields: true });
    });
    line.append(text, button);
    scan.append(line);
  }
  if ((report.unused || []).length) {
    const line = document.createElement("p");
    line.className = "warn";
    line.textContent = `Declared and not seen in a call: ${report.unused.join(", ")}.`;
    scan.append(line);
  }
  for (const hint of (report.hints || []).slice(0, 6)) {
    const line = document.createElement("p");
    line.className = "warn";
    line.textContent = `${hint.file}:${hint.line} ${hint.message}`;
    scan.append(line);
  }

  const list = $("permissions-list");
  list.replaceChildren();
  let lastGroup = "";
  for (const row of permissionsUi.rows) {
    if (row.group !== lastGroup) {
      lastGroup = row.group;
      const heading = document.createElement("h3");
      heading.textContent = row.groupLabel;
      const section = document.createElement("section");
      section.className = "perm-group";
      section.append(heading);
      list.append(section);
    }
    const section = list.lastElementChild;
    const item = document.createElement("div");
    item.className = "perm-row";
    const mode = permissionsUi.selection.get(row.id) || "off";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mode";
    button.textContent = mode === "required" ? "Required" : mode === "optional" ? "Optional" : "Off";
    button.disabled = Boolean(row.systemOnly);
    button.title = row.systemOnly ? "Only a Longshot system mod can ask for this." : "Required, optional, or off";
    button.addEventListener("click", () => {
      const next = nextMode(mode);
      if (next === "off") permissionsUi.selection.delete(row.id);
      else permissionsUi.selection.set(row.id, next);
      permissionsUi.view = { ...permissionsUi.view, errors: [] };
      renderPermissions({ keepFields: true });
    });
    const copy = document.createElement("div");
    const player = document.createElement("span");
    player.className = "player";
    player.textContent = row.player;
    const features = document.createElement("span");
    features.className = "features";
    features.textContent = row.systemOnly ? `${row.features} Only a Longshot system mod can ask for this.` : row.features;
    copy.append(player, features);
    const badge = document.createElement("span");
    badge.className = `badge ${row.risk}`;
    badge.textContent = row.risk;
    if (row.systemOnly) item.classList.add("disabled");
    item.append(button, copy, badge);
    section.append(item);
  }
  const extras = [...permissionsUi.selection.keys()].filter((id) => !permissionsUi.rows.some((row) => row.id === id));
  if (extras.length) {
    const section = document.createElement("section");
    section.className = "perm-group";
    const heading = document.createElement("h3");
    heading.textContent = "Named origins";
    section.append(heading);
    for (const id of extras) {
      const mode = permissionsUi.selection.get(id);
      const item = document.createElement("div");
      item.className = "perm-row";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mode";
      button.textContent = mode === "optional" ? "Optional" : "Required";
      button.addEventListener("click", () => {
        const next = nextMode(mode);
        if (next === "off") permissionsUi.selection.delete(id);
        else permissionsUi.selection.set(id, next);
        renderPermissions({ keepFields: true });
      });
      const copy = document.createElement("div");
      copy.textContent = id.startsWith("net:") ? `Can talk to ${id.slice(4)}` : id;
      const badge = document.createElement("span");
      badge.className = "badge medium";
      badge.textContent = id === "net:*" ? "high" : "medium";
      item.append(button, copy, badge);
      section.append(item);
    }
    list.append(section);
  }
}

async function openPermissions() {
  if (!projectReady()) {
    toast("Open a project first.");
    return;
  }
  closeOpenDialogs();
  try {
    const [catalog, view] = await Promise.all([api.permissions(), api.projectPermissions()]);
    permissionsUi.catalog = catalog;
    permissionsUi.rows = catalogRows(catalog);
    permissionsUi.view = view;
    permissionsUi.selection = selectionFromDeclaration(view.declaration || {});
    renderPermissions();
    $("permissions-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

async function savePermissions({ quiet = false } = {}) {
  const body = currentPermissionDeclaration();
  try {
    const view = await api.savePermissions(body);
    permissionsUi.view = { ...permissionsUi.view, ...view };
    permissionsUi.selection = selectionFromDeclaration(view.declaration || {});
    renderPermissions();
    if (!quiet) toast(view.errors && view.errors.length ? "Saved. The manifest still has errors." : "Permissions saved.");
    return view;
  } catch (error) {
    toast(error.message);
    throw error;
  }
}

async function grantPermissions() {
  const game = $("permissions-game").value.trim();
  const note = $("permissions-grant-note");
  if (!game) {
    note.textContent = "Point at the game folder first. Nothing was written.";
    return;
  }
  note.textContent = "Writing mods.json…";
  try {
    const view = await savePermissions({ quiet: true });
    if (view.errors && view.errors.length) {
      note.textContent = "Fix the permission errors before writing mods.json. The game folder was not changed.";
      return;
    }
    const result = await api.grantPermissions({ game });
    const backup = result.backup ? ` Backup: ${result.backup}.` : " A new file was created, so there was nothing to back up.";
    note.textContent = `Granted ${result.granted.join(", ") || "nothing"} in ${result.path}.${backup} ${result.restartNote || ""} ${(result.warnings || []).join(" ")}`;
  } catch (error) {
    note.textContent = error.message;
  }
}

async function runModExport() {
  const log = $("build-log");
  log.replaceChildren();
  const line = document.createElement("li");
  line.textContent = "Writing the Longshot mod…";
  log.append(line);
  try {
    const result = await api.exportLongshot({ install: true });
    log.replaceChildren();
    for (const entry of result.log || []) {
      const item = document.createElement("li");
      item.className = entry.level;
      item.textContent = entry.message;
      log.append(item);
    }
  } catch (error) {
    log.replaceChildren();
    const item = document.createElement("li");
    item.className = "error";
    item.textContent = error.message;
    log.append(item);
  }
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

const CHANGE_LABELS = [
  ["added", "added"],
  ["removed", "removed or hidden"],
  ["moved", "moved"],
  ["turned", "rotated or scaled"],
  ["params", "parameters"],
  ["events", "events"],
  ["logic", "logic"],
  ["schedule", "schedules"],
  ["text", "text"],
];

function actorProblems(actor) {
  const rows = (state.problems && state.problems.diagnostics) || [];
  if (!actor) return [];
  return rows.filter((row) => row.actor && row.actor === actor.id);
}

function problemClass(severity) {
  if (severity === "info") return "problem-info";
  if (severity === "warning") return "problem-warning";
  return "problem-error";
}

function problemNotes(rows) {
  const list = document.createElement("div");
  list.className = "problem-notes";
  for (const row of rows) {
    if (row.severity === "info" && !state.showProblemNotes) continue;
    const line = document.createElement("p");
    line.className = problemClass(row.severity);
    line.textContent = row.message;
    list.append(line);
  }
  return list;
}

function noteModelError(gyaml, message) {
  if (!gyaml || isMarker(gyaml)) return;
  const text = message || "The glTF did not load.";
  if (state.modelErrors.some((item) => item.gyaml === gyaml)) return;
  state.modelErrors = [...state.modelErrors, { gyaml, message: text }];
  console.error(`${gyaml}: ${text}`);
  renderProblems();
}

function noteTerrain(message) {
  const text = message || "The terrain did not load.";
  if (state.terrainNotes.includes(text)) return;
  state.terrainNotes = [...state.terrainNotes, text];
  renderProblems();
}

function modelGroups() {
  const actors = (state.view && state.view.actors) || [];
  return groupModelErrors(state.modelErrors, actors);
}

function sceneWarnings() {
  return ((state.view && state.view.warnings) || []).filter((message) => message);
}

function problemsMarkdown(report) {
  const rows = (report && report.diagnostics) || [];
  const groups = modelGroups();
  const notes = [...state.terrainNotes, ...sceneWarnings()];
  const counts = { error: groups.reduce((sum, group) => sum + group.count, 0), warning: notes.length, info: 0 };
  for (const row of rows) counts[row.severity] = (counts[row.severity] || 0) + 1;
  const summary = ["error", "warning", "info"].filter((key) => counts[key]).map((key) => `${counts[key]} ${key}`).join(", ");
  const lines = [`# Problems for ${(report && report.banc) || state.banc || "scene"}`, "", summary || "No problems.", ""];
  for (const group of groups) {
    lines.push(`- **error** ${group.message} (${group.count})`);
    for (const actor of group.actors) lines.push(`  - ${actor.name || actor.gyaml}${actor.id ? ` \`${actor.id}\`` : ""}`);
  }
  for (const message of notes) lines.push(`- **warning** ${message}`);
  for (const row of rows) {
    lines.push(`- **${row.severity}** \`${row.code}\` ${row.message}`);
    const where = [row.file, row.pointer || row.actor].filter(Boolean).join(" · ");
    if (where) lines.push(`  ${where}`);
  }
  return `${lines.join("\n")}\n`;
}

function downloadText(filename, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function loadProblems(banc) {
  const token = ++state.problemToken;
  if (!banc || !projectReady()) {
    state.problems = { banc, diagnostics: [] };
    renderProblems();
    return;
  }
  if (!state.problems || state.problems.banc !== banc) {
    state.problems = null;
    renderProblems();
  }
  try {
    const report = await api.problems(banc);
    if (token !== state.problemToken || state.banc !== banc) return;
    state.problems = report;
  } catch (error) {
    if (token !== state.problemToken) return;
    const missing = /404/.test(error.message || "");
    state.problems = {
      banc,
      diagnostics: [],
      error: missing ? "Restart the editor (./build.sh or build.bat) so validation can run." : error.message,
    };
  }
  renderProblems();
  if (state.selected) renderProps();
}

async function runMeshAudit() {
  const audit = { banc: state.banc, loading: true };
  const view = {
    backend: viewport.backend,
    placements: viewport.auditPlacements(),
    shaderErrors: viewport.shaderDiagnostics(),
    ...viewport.auditView(),
  };
  audit.viewport = view;
  state.meshAudit = audit;
  renderProblems();
  try {
    audit.report = await api.meshAudit(audit.banc);
    if (state.meshAudit === audit && state.banc === audit.banc) {
      audit.report.viewport = view;
    }
  } catch (error) {
    audit.error = error.message;
  } finally {
    audit.loading = false;
    if (state.meshAudit === audit && state.banc === audit.banc) renderProblems();
  }
}

function renderMeshAudit(panel) {
  const audit = state.meshAudit?.banc === state.banc ? state.meshAudit : null;
  const tools = document.createElement("div");
  tools.className = "problem-tools";
  const run = document.createElement("button");
  run.type = "button";
  run.className = "ghost";
  run.textContent = audit?.loading ? "Checking meshes…" : "Audit meshes";
  run.disabled = Boolean(audit?.loading);
  run.addEventListener("click", runMeshAudit);
  tools.append(run);
  panel.append(tools);
  if (audit?.error) panel.append(empty(audit.error));
  const report = audit?.report;
  const center = audit?.viewport?.centerSurface;
  if (center) {
    const { queued, loading } = audit.viewport.streaming;
    const stage = center.stage === "preview"
      ? queued || loading ? "startup preview" : "startup preview still visible while model requests are idle"
      : `model mesh${center.lod == null ? "" : ` (LOD ${center.lod})`}`;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "change-row";
    row.disabled = !center.id || !state.view?.actors.some(({ id }) => id === center.id);
    const content = document.createElement("span");
    content.className = "grow";
    const name = document.createElement("strong");
    name.textContent = `Center of viewport · ${center.gyaml}`;
    const detail = document.createElement("small");
    detail.textContent = [stage, center.surface, `${Math.round(center.distance)} m`, center.stage === "preview" && center.reason]
      .filter(Boolean).join(" · ");
    detail.title = center.source;
    content.append(name, detail);
    row.append(content);
    row.addEventListener("click", () => focusActor(center.id, { frame: false }));
    panel.append(row);
  }
  if (!report) return;

  const json = document.createElement("button");
  json.type = "button";
  json.className = "ghost";
  json.textContent = "Mesh audit JSON";
  json.addEventListener("click", () => downloadText("mesh-audit.json", `${JSON.stringify(report, null, 2)}\n`, "application/json"));
  tools.append(json);
  const labels = {
    ready: "validated", no_mesh: "no mesh expected", uncached: "uncached",
    unsupported: "unsupported geometry", partial: "partially decoded",
    missing: "missing file", failed: "conversion failed", invalid: "invalid glTF",
  };
  const summary = Object.entries(report.counts).map(([status, count]) => `${count} ${labels[status]}`);
  panel.append(empty(summary.join(" · ") || "No model placements to audit."));
  const misplaced = (report.viewport?.placements || []).filter((placement) => placement.mismatched);
  if (misplaced.length) panel.append(empty(`${misplaced.length} viewport placement${misplaced.length === 1 ? "" : "s"} differ from the actor transform. See Mesh audit JSON.`));
  const shaderErrors = report.viewport?.shaderErrors?.length || 0;
  if (shaderErrors) panel.append(empty(`${shaderErrors} WGSL compiler errors in this viewport session. See Mesh audit JSON.`));
  const list = document.createElement("div");
  list.className = "change-list";
  const visible = new Set((state.view?.actors || []).map((actor) => actor.id));
  for (const row of report.models) {
    if (["ready", "no_mesh", "uncached"].includes(row.status)) continue;
    const actor = row.actors.find((item) => visible.has(item.id));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "change-row";
    button.disabled = !actor;
    const text = document.createElement("span");
    text.className = "grow";
    const name = document.createElement("strong");
    name.textContent = `${row.gyaml} · ${labels[row.status]} (${row.actors.length})`;
    const detail = document.createElement("small");
    detail.textContent = row.reason;
    detail.title = row.source;
    text.append(name, detail);
    button.append(text);
    if (actor) button.addEventListener("click", () => focusActor(actor.id));
    list.append(button);
  }
  if (list.childElementCount) panel.append(list);
}

function renderProblems() {
  const panel = $("problems-panel");
  const badge = $("problem-count");
  if (!panel || !badge) return;
  panel.replaceChildren();
  const report = state.problems;
  const rows = (report && report.diagnostics) || [];
  const groups = modelGroups();
  const loadErrors = groups.reduce((sum, group) => sum + group.count, 0);
  const sceneNotes = [...state.terrainNotes, ...sceneWarnings()];
  const errors = rows.filter((row) => row.severity === "error").length + loadErrors;
  const warnings = rows.filter((row) => row.severity === "warning").length + sceneNotes.length;
  const notes = rows.filter((row) => row.severity === "info").length;
  badge.hidden = !(errors || warnings);
  badge.textContent = errors ? String(errors) : String(warnings);
  badge.classList.toggle("removed", errors > 0);
  syncDockTitles();
  if (!state.banc) {
    panel.append(empty("Open a scene to check it."));
    return;
  }
  renderMeshAudit(panel);
  if (!report) {
    if (groups.length || sceneNotes.length) panel.append(modelProblemList(groups, sceneNotes));
    else panel.append(empty("Checking this scene…"));
    return;
  }
  if (report.error) {
    panel.append(empty(report.error));
    if (groups.length || sceneNotes.length) panel.append(modelProblemList(groups, sceneNotes));
    return;
  }
  const tools = document.createElement("div");
  tools.className = "problem-tools";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "ghost";
  copy.textContent = "Copy";
  copy.addEventListener("click", async () => {
    const text = problemsMarkdown(report);
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied the problem list.");
    } catch {
      downloadText("problems.md", text, "text/markdown");
      toast("Clipboard is blocked here, so the list was downloaded instead.");
    }
  });
  const asJson = document.createElement("button");
  asJson.type = "button";
  asJson.className = "ghost";
  asJson.textContent = "JSON";
  asJson.addEventListener("click", () => downloadText("problems.json", `${JSON.stringify(report, null, 2)}\n`, "application/json"));
  const asMarkdown = document.createElement("button");
  asMarkdown.type = "button";
  asMarkdown.className = "ghost";
  asMarkdown.textContent = "Markdown";
  asMarkdown.addEventListener("click", () => downloadText("problems.md", problemsMarkdown(report), "text/markdown"));
  tools.append(copy, asJson, asMarkdown);
  if (notes) {
    const label = document.createElement("label");
    label.className = "check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.showProblemNotes;
    box.addEventListener("change", () => {
      state.showProblemNotes = box.checked;
      renderProblems();
      renderProps();
    });
    label.append(box, document.createTextNode(`Vanilla notes (${notes})`));
    tools.append(label);
  }
  panel.append(tools);
  const visible = rows.filter((row) => row.severity !== "info" || state.showProblemNotes);
  if (!visible.length && !groups.length && !sceneNotes.length) {
    panel.append(empty(notes ? "No errors. Vanilla notes are hidden." : "No problems in this scene."));
    return;
  }
  if (groups.length || sceneNotes.length) panel.append(modelProblemList(groups, sceneNotes));
  if (!visible.length) return;
  const list = document.createElement("div");
  list.className = "change-list";
  for (const row of visible) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "change-row";
    const mark = document.createElement("span");
    mark.className = row.severity === "error" ? "badge removed" : "badge";
    mark.textContent = row.severity === "error" ? "error" : row.severity === "info" ? "note" : "warn";
    const text = document.createElement("span");
    text.className = "grow";
    const strong = document.createElement("strong");
    strong.textContent = row.message;
    const small = document.createElement("small");
    const where = [row.code, row.file, row.pointer || row.actor].filter(Boolean).join(" · ");
    small.textContent = where;
    text.append(strong, small);
    button.append(mark, text);
    button.addEventListener("click", () => focusProblem(row));
    list.append(button);
  }
  panel.append(list);
}

function modelProblemList(groups, notes) {
  const root = document.createElement("div");
  for (const group of groups) {
    const block = document.createElement("section");
    block.className = "problem-group";
    const head = document.createElement("p");
    head.className = "problem-error";
    head.textContent = `${group.message} (${group.count})`;
    block.append(head);
    const list = document.createElement("div");
    list.className = "change-list";
    for (const actor of group.actors) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "change-row";
      const mark = document.createElement("span");
      mark.className = "badge removed";
      mark.textContent = "model";
      const text = document.createElement("span");
      text.className = "grow";
      const strong = document.createElement("strong");
      strong.textContent = actor.name || actor.gyaml;
      const small = document.createElement("small");
      small.textContent = [actor.name && actor.name !== actor.gyaml ? actor.gyaml : "", actor.id].filter(Boolean).join(" · ");
      text.append(strong, small);
      button.append(mark, text);
      button.addEventListener("click", () => focusActor(actor.id));
      list.append(button);
    }
    block.append(list);
    root.append(block);
  }
  for (const message of notes) {
    const line = document.createElement("p");
    line.className = "problem-warning";
    line.textContent = message;
    root.append(line);
  }
  return root;
}

function focusActor(id, { frame = true } = {}) {
  if (!id || !state.view) {
    toast("That actor is not in the open scene.");
    return;
  }
  const actor = state.view.actors.find((item) => item.id === id);
  if (!actor) {
    toast("That actor is not in the open scene.");
    return;
  }
  state.selected = actor.id;
  viewport.select(actor.id);
  if (frame) viewport.frame(actor.id);
  renderOutliner();
  renderProps();
  noteCompanion();
  showTab("props");
}

function focusProblem(row) {
  if (!row.actor || !state.view) return;
  const actor = state.view.actors.find((item) => item.id === row.actor);
  if (!actor) {
    toast("That actor is not in the open scene.");
    return;
  }
  state.selected = actor.id;
  viewport.select(actor.id);
  viewport.frame(actor.id);
  renderOutliner();
  renderProps();
  noteCompanion();
  showTab("props");
}

function changeSummary(counts) {
  if (!counts || !counts.total) return "";
  return CHANGE_LABELS.filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} ${label}`).join(", ");
}

function actorChange(counts) {
  return Boolean(counts && (counts.added || counts.removed || counts.moved || counts.turned || counts.params));
}

async function loadChanges(banc) {
  const token = ++state.changeToken;
  if (!banc || !projectReady()) {
    state.changes = { banc, groups: [], counts: { total: 0 } };
    renderChanges();
    return;
  }
  state.changes = null;
  renderChanges();
  try {
    const report = await api.sceneChanges(banc);
    if (token !== state.changeToken || state.banc !== banc) return;
    state.changes = report;
    state.changeCounts[banc] = report.counts || {};
    renderChanges();
    renderScenes();
  } catch (error) {
    if (token !== state.changeToken) return;
    state.changes = { error: error.message, groups: [], counts: { total: 0 } };
    renderChanges();
  }
}

function renderChanges() {
  const panel = $("changes-panel");
  const badge = $("change-count");
  if (!panel || !badge) return;
  panel.replaceChildren();
  const report = state.changes;
  const total = report && report.counts ? report.counts.total : 0;
  badge.hidden = !total;
  badge.textContent = total ? String(total) : "";
  syncDockTitles();
  panel.append(sessionBlock());
  if (!state.banc) {
    panel.append(empty("Open a scene to see what the project changed."));
    return;
  }
  if (!report) {
    panel.append(empty("Loading the project diff…"));
    return;
  }
  if (report.error) {
    panel.append(empty(report.error));
    return;
  }
  if (!total) {
    panel.append(empty("Nothing in this project changes this map."));
    return;
  }
  panel.append(changeGroups(report.groups || []));
}

function sessionBlock() {
  const root = document.createElement("div");
  root.className = "change-list";
  const heading = document.createElement("div");
  heading.className = "area";
  heading.textContent = "This session";
  root.append(heading);
  const rows = historyRows(state.history);
  if (!rows.length) {
    root.append(empty("No edits this sitting. Ctrl+Z steps back through a move, a property, add, delete, duplicate, hide, and lock."));
    return root;
  }
  for (const row of rows) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = row.place === "redo" ? "change-row is-redo" : "change-row";
    if (row.latest) button.classList.add("is-current");
    const text = document.createElement("span");
    text.className = "grow";
    const strong = document.createElement("strong");
    strong.textContent = row.entry.label;
    const small = document.createElement("small");
    small.textContent = row.place === "redo" ? "Redo" : row.latest ? "Latest · Ctrl+Z" : "Undo";
    text.append(strong, small);
    button.append(text);
    button.addEventListener("click", () => focusHistory(row.entry));
    root.append(button);
  }
  return root;
}

function focusHistory(entry) {
  const id = entry.shot && entry.shot.select;
  if (!id || !state.view || !state.view.actors.some((actor) => actor.id === id)) return;
  state.selected = id;
  viewport.select(id);
  viewport.frame(id);
  renderOutliner();
  renderProps();
}

function changeGroups(groups) {
  const root = document.createElement("div");
  root.className = "change-list";
  for (const group of groups) {
    const heading = document.createElement("div");
    heading.className = "area";
    heading.textContent = group.title;
    root.append(heading);
    for (const entry of group.entries || []) root.append(changeRow(entry));
  }
  return root;
}

function changeRow(entry) {
  const row = document.createElement("div");
  row.className = "change-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "change-row";
  const text = document.createElement("span");
  text.className = "grow";
  const strong = document.createElement("strong");
  const who = entry.label || entry.gyaml || entry.title;
  strong.textContent = entry.title && entry.title !== who ? `${who} · ${entry.title}` : entry.title || who;
  const delta = document.createElement("small");
  delta.textContent = `${entry.before} → ${entry.after}`;
  const source = document.createElement("small");
  source.textContent = `${entry.sourceLabel || "Project"} · ${entry.file || "project"}`;
  text.append(strong, delta, source);
  button.append(text);
  button.addEventListener("click", () => focusChange(entry));
  row.append(button);
  if (entry.revertable && entry.actorId) {
    const revert = document.createElement("button");
    revert.type = "button";
    revert.className = "ghost";
    revert.textContent = "Revert";
    revert.addEventListener("click", async () => {
      const command = { command: "revert_part", id: entry.actorId, part: entry.part, path: entry.path || "" };
      const open = document.querySelector("dialog[open]");
      if (open) open.close();
      if (entry.banc && entry.banc !== state.banc) await openScene(entry.banc, { keepHistory: true });
      showTab("changes");
      edit(command);
    });
    row.append(revert);
  }
  return row;
}

async function focusChange(entry) {
  const open = document.querySelector("dialog[open]");
  if (open) open.close();
  if (entry.banc && entry.banc !== state.banc) {
    await openScene(entry.banc, { frame: false, keepHistory: true });
  }
  showTab("changes");
  if (!entry.actorId || !state.view) {
    toast(entry.source === "patch"
      ? "This patch is applied when you build. It does not select an actor in the view."
      : "That actor is not in the view. Restore it to frame it.");
    return;
  }
  const actor = state.view.actors.find((item) => item.id === entry.actorId);
  if (!actor) {
    toast("That actor is not in the view. Restore it to frame it.");
    return;
  }
  if (actor.status === "hidden" && !state.showHidden) {
    state.showHidden = true;
    $("show-hidden").checked = true;
    viewport.setFlags({ hidden: true });
  }
  state.selected = entry.actorId;
  viewport.select(entry.actorId);
  viewport.frame(entry.actorId);
  renderOutliner();
  renderProps();
}

async function fillProjectChanges() {
  const host = $("project-changes");
  if (!host) return;
  host.hidden = false;
  host.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = "Changes";
  if (!projectReady()) {
    host.append(heading, empty("Open a project to see map changes."));
    return;
  }
  host.append(heading, empty("Loading changes…"));
  try {
    const report = await api.projectChanges();
    if (!$("project-dialog").open) return;
    host.replaceChildren(heading);
    host.append(projectChangeList(report));
  } catch (error) {
    host.replaceChildren(heading, empty(error.message));
  }
}

function projectChangeList(report) {
  const root = document.createElement("div");
  const maps = report.maps || [];
  if (!maps.length) {
    root.append(empty("No map in this project has edits yet."));
    return root;
  }
  const intro = document.createElement("p");
  intro.className = "muted";
  intro.textContent = `${maps.length} ${maps.length === 1 ? "map" : "maps"} · ${changeSummary(report.totals)}`;
  root.append(intro);
  for (const map of maps) {
    const details = document.createElement("details");
    details.className = "map-change";
    const summary = document.createElement("summary");
    summary.textContent = `${pretty(map.name)} · ${changeSummary(map.counts)}`;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "ghost";
    open.textContent = "Open map";
    open.addEventListener("click", () => {
      closeDialogs();
      openScene(map.banc, { frame: true, keepHistory: true });
      showTab("changes");
    });
    details.append(summary, open, changeGroups(map.groups || []));
    root.append(details);
  }
  return root;
}

let paletteIndex = 0;
let paletteCache = [];

function openPalette() {
  paletteIndex = 0;
  const dialog = $("palette-dialog");
  const input = $("palette-search");
  if (!dialog || !input) return;
  input.value = "";
  renderPalette();
  if (!dialog.open) dialog.showModal();
  input.focus();
  input.select();
}

function renderPalette() {
  const host = $("palette-results");
  const input = $("palette-search");
  if (!host || !input) return;
  paletteCache = paletteRows({
    actors: paletteActors(),
    scenes: state.scenes || [],
    query: input.value,
  });
  host.replaceChildren();
  if (!paletteCache.length) {
    host.append(empty("Nothing matches. Try a name, an actor class, a hash, a scene, or a command."));
    return;
  }
  if (paletteIndex >= paletteCache.length) paletteIndex = paletteCache.length - 1;
  paletteCache.forEach((row, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = index === paletteIndex ? "palette-row is-on" : "palette-row";
    button.setAttribute("role", "option");
    const kind = document.createElement("span");
    kind.className = "palette-kind";
    kind.textContent = row.kind;
    const text = document.createElement("span");
    text.className = "grow";
    const strong = document.createElement("strong");
    strong.textContent = row.label;
    const small = document.createElement("small");
    small.textContent = row.detail;
    text.append(strong, small);
    button.append(kind, text);
    button.addEventListener("click", () => runPalette(row));
    button.addEventListener("mouseenter", () => {
      paletteIndex = index;
      host.querySelectorAll(".palette-row").forEach((item, itemIndex) => item.classList.toggle("is-on", itemIndex === index));
    });
    host.append(button);
  });
  const current = host.querySelector(".is-on");
  if (current && current.scrollIntoView) current.scrollIntoView({ block: "nearest" });
}

function onPaletteKey(event) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    paletteIndex = Math.min(paletteIndex + 1, Math.max(paletteCache.length - 1, 0));
    renderPalette();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    paletteIndex = Math.max(paletteIndex - 1, 0);
    renderPalette();
  } else if (event.key === "Enter") {
    event.preventDefault();
    runPalette(paletteCache[paletteIndex]);
  }
}

function paletteActors() {
  const rows = [...((state.view && state.view.actors) || [])];
  if (state.showCompanions === false && state.raw) {
    const seen = new Set(rows.map((actor) => actor.id).filter(Boolean));
    for (const extra of state.raw.companions || []) {
      for (const actor of extra.actors || []) {
        const id = actor.id && !String(actor.id).includes("::") ? `${extra.banc}::${actor.id}` : actor.id;
        if (id && seen.has(id)) continue;
        rows.push({ ...actor, id, companionLabel: bancKind(extra.banc) });
        if (id) seen.add(id);
      }
    }
  }
  return rows;
}

function runPalette(row) {
  const dialog = $("palette-dialog");
  if (dialog && dialog.open) dialog.close();
  if (!row) return;
  if (row.kind === "actor") {
    if (!row.id) {
      toast("That actor has no hash, so it cannot be selected.");
      return;
    }
    state.selected = row.id;
    viewport.select(row.id);
    viewport.frame(row.id);
    renderOutliner();
    renderProps();
    noteCompanion();
    showTab("props");
    return;
  }
  if (row.kind === "scene") {
    openScene(row.id, { frame: true });
    return;
  }
  runCommand(row.id);
}

function toggleProfile() {
  const box = $("show-profile");
  if (!box) return;
  box.checked = !box.checked;
  box.dispatchEvent(new Event("change"));
}

function toggleSnap() {
  const box = $("snap-surface");
  if (!box) return;
  box.checked = !box.checked;
  viewport.setSnap(box.checked);
  toast(box.checked ? "Snap to ground and collision is on." : "Snap is off.");
}

function runCommand(id) {
  if (id === "undo") undo();
  else if (id === "redo") redo();
  else if (id === "frame") viewport.frame(state.selected);
  else if (id === "snap") toggleSnap();
  else if (id === "add") openAdd("add");
  else if (id === "hide") {
    const actor = selectedActor();
    if (!actor) toast("Select an actor first.");
    else if (actor.status === "recipe") removeRecipe(actor);
    else edit({ command: actor.status === "hidden" ? "unhide" : "hide", id: actor.id });
  } else if (id === "duplicate") {
    const actor = selectedActor();
    if (!actor || !actor.id) toast("Select an actor first.");
    else edit({ command: "duplicate", id: actor.id });
  } else if (id === "delete") {
    const actor = selectedActor();
    if (!actor || !actor.id) toast("Select an actor first.");
    else if (actor.status === "recipe") removeRecipe(actor);
    else edit({ command: "remove", id: actor.id });
  } else if (id === "hidden") {
    const box = $("show-hidden");
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change"));
  } else if (id === "changes") showTab("changes");
  else if (id === "problems") showTab("problems");
  else if (id === "behavior") showTab("behavior");
  else if (id === "details") showTab("details");
  else if (id === "project") openProjectDialog();
  else if (id === "build") $("build-dialog").showModal();
  else if (id === "permissions") openPermissions();
  else if (id === "ground") {
    const box = $("show-ground");
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change"));
  } else if (id === "profile") {
    toggleProfile();
  }
}

function showTab(name) {
  state.tab = name;
  dock.reveal(panelIdForTab(name));
  if (name === "behavior") refreshBehavior();
  if (name === "details") renderDetails();
  if (viewport) viewport.resize();
}

function syncDockTitles() {
  const problems = $("problem-count");
  const changes = $("change-count");
  const problemText = problems && !problems.hidden && problems.textContent ? `Problems ${problems.textContent}` : "Problems";
  const changeText = changes && !changes.hidden && changes.textContent ? `Edits ${changes.textContent}` : "Edits";
  dock.setTitle("problems", problemText);
  dock.setTitle("edits", changeText);
}

function renderWindowMenu() {
  const menu = $("window-menu");
  if (!menu) return;
  menu.replaceChildren();
  for (const panel of dock.panels) {
    const open = dock.isOpen(panel.id);
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", open ? "true" : "false");
    button.textContent = `${open ? "Hide" : "Show"} ${panel.title}`;
    button.addEventListener("click", () => {
      if (open) dock.close(panel.id);
      else showTab(tabForPanel(panel.id));
    });
    menu.append(button);
  }
  const rule = () => {
    const line = document.createElement("hr");
    return line;
  };
  const command = (label, run) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => {
      const root = $("window-menu-root");
      if (root) root.open = false;
      run();
    });
    return button;
  };
  menu.append(rule(), command("Pop out Behavior", () => popOutPanel("behavior")), command("Pop out Details", () => popOutPanel("details")), command("Pop out Inspector", () => popOutPanel("inspector")), rule(), command("Reset layout", () => {
    dock.reset();
    toast("Layout reset. The behavior graph is under the view.");
  }));
}

async function popOutPanel(id) {
  showTab(tabForPanel(id));
  const opened = await dock.popOut(id);
  if (!opened) toast("The browser blocked the window. Allow pop-ups for this site, then try Pop out again.");
  if (viewport) viewport.resize();
}

function refreshBehavior() {
  const host = $("behavior-panel");
  if (!host) return;
  if (!state.load || !state.load.layers.behavior) {
    host.replaceChildren();
    releaseDisconnectedGraphs();
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "Behavior is not loaded. Turn the Behavior layer on to read the graph.";
    host.append(note);
    return;
  }
  const actor = selectedActor();
  const banc = actor?.banc || state.banc || "";
  const id = actor?.placementId || actor?.id || "";
  const key = actor ? `${banc}|${id}|${actor.gyaml || ""}` : "";
  if (key !== state.behaviorKey) {
    state.behaviorKey = key;
    state.behavior = null;
    state.behaviorNode = "";
    state.behaviorFile = "";
    state.behaviorEntry = "";
    state.behaviorQuery = "";
    state.behaviorError = "";
    state.behaviorTrail = [];
    state.behaviorAhead = [];
    state.behaviorFrame = "";
    state.behaviorLoading = Boolean(actor);
    if (actor) {
      const requestKey = key;
      api
        .behavior({ banc, id, gyaml: actor.gyaml || "", load: loadQuery(state.load), signal: sceneFetch.signal })
        .then((graph) => {
          if (state.behaviorKey !== requestKey) return;
          state.behavior = graph;
          state.behaviorLoading = false;
          drawBehavior();
        })
        .catch((error) => {
          if (error && error.name === "AbortError") return;
          if (state.behaviorKey !== requestKey) return;
          state.behaviorError = error.message || "The behavior graph could not be read.";
          state.behaviorLoading = false;
          drawBehavior();
        });
    }
  }
  drawBehavior();
}

function jumpBehavior(id, outside) {
  const graph = state.behavior;
  const node = graph && (graph.nodes || []).find((item) => item.id === id);
  const current = { node: state.behaviorNode, entry: state.behaviorEntry, file: state.behaviorFile };
  const file = node && node.source ? node.source : state.behaviorFile;
  const next = {
    node: id,
    entry: outside ? id : state.behaviorEntry,
    file: file || state.behaviorFile,
  };
  const history = graphHistory({ trail: state.behaviorTrail, ahead: state.behaviorAhead }, current, next);
  state.behaviorTrail = history.trail;
  state.behaviorAhead = history.ahead;
  state.behaviorNode = id;
  state.behaviorFile = next.file;
  if (outside) {
    state.behaviorEntry = id;
    state.behaviorQuery = "";
  }
  state.behaviorFrame = id;
  drawBehavior();
}

function stepBehavior(dir) {
  const current = { node: state.behaviorNode, entry: state.behaviorEntry, file: state.behaviorFile };
  const stepped = historyStep({ trail: state.behaviorTrail, ahead: state.behaviorAhead }, current, dir);
  if (!stepped.current || stepped.current === current) return;
  state.behaviorTrail = stepped.trail;
  state.behaviorAhead = stepped.ahead;
  state.behaviorNode = stepped.current.node || "";
  state.behaviorEntry = stepped.current.entry || "";
  state.behaviorFile = stepped.current.file || "";
  state.behaviorQuery = "";
  state.behaviorFrame = stepped.current.node || "";
  drawBehavior();
}

function drawBehavior() {
  const host = $("behavior-panel");
  if (!host) return;
  const frameId = state.behaviorFrame;
  state.behaviorFrame = "";
  renderBehavior(host, {
    actor: selectedActor(),
    graph: state.behavior,
    layer: state.behaviorLayer,
    selectedId: state.behaviorNode,
    file: state.behaviorFile,
    entry: state.behaviorEntry,
    query: state.behaviorQuery,
    frameId,
    loading: state.behaviorLoading,
    error: state.behaviorError,
    onLayer: (layer) => {
      state.behaviorLayer = layer;
      drawBehavior();
    },
    onSelect: (id) => {
      state.behaviorNode = id;
      drawBehavior();
    },
    onSpotlight: (target) => {
      const stage = host.querySelector(".graph-stage");
      if (stage && stage.__spotlight) stage.__spotlight(target);
    },
    onJump: (id, outside) => jumpBehavior(id, outside),
    onFile: (id, keepQuery) => {
      state.behaviorFile = id;
      state.behaviorEntry = "";
      state.behaviorNode = "";
      if (!keepQuery) state.behaviorQuery = "";
      drawBehavior();
    },
    onEntry: (id) => {
      state.behaviorEntry = id;
      state.behaviorQuery = "";
      state.behaviorNode = id;
      drawBehavior();
    },
    onQuery: (value) => {
      state.behaviorQuery = value;
      drawBehavior();
      const input = host.querySelector(".behavior-search");
      if (input) {
        input.focus();
        const end = input.value.length;
        input.setSelectionRange(end, end);
      }
    },
    onField: (node, field, value) => saveBehaviorField(node, field, value),
    onRevert: (op) => revertBehaviorField(op),
    onExport: () => exportBehavior(),
    onFlags: (input) => fillFlagList(input),
  });
}

async function saveBehaviorField(node, field, value) {
  const gyaml = state.behavior && state.behavior.gyaml;
  if (!gyaml) return;
  if (!projectReady()) {
    toast("Open a project first. Looking around does not write anything.");
    openProjectDialog();
    return;
  }
  const before = (state.behavior.ops || []).map((op) => ({ ...op }));
  try {
    const graph = await api.setBehaviorField({ gyaml, id: node.id, field: field.name, type: field.type, value });
    state.behavior = graph;
    state.history = pushEdit(state.history, {
      label: `Set ${field.name} on ${node.name || node.raw}`,
      behavior: true,
      gyaml,
      before,
      after: graph.ops || [],
    });
    drawBehavior();
    renderChanges();
    updateHistoryButtons();
    const source = (graph.sources || []).find((item) => item.id === node.source);
    if (source && source.warning) toast(source.warning);
  } catch (error) {
    toast(error.message);
  }
}

async function revertBehaviorField(op) {
  const gyaml = state.behavior && state.behavior.gyaml;
  if (!gyaml) return;
  const before = (state.behavior.ops || []).map((item) => ({ ...item }));
  const node = (state.behavior.nodes || []).find((item) => item.id === op.id);
  const field = node && (node.fields || []).find((item) => item.name === op.field);
  if (!field) return;
  await saveBehaviorField(node, { ...field, name: op.field, type: op.type || field.type }, field.base);
  if (state.history.undo.length && state.history.undo[state.history.undo.length - 1].behavior) {
    state.history.undo[state.history.undo.length - 1].label = `Revert ${op.field}`;
    state.history.undo[state.history.undo.length - 1].before = before;
  }
}

async function exportBehavior() {
  const gyaml = state.behavior && state.behavior.gyaml;
  if (!gyaml) return;
  try {
    const result = await api.exportBehavior(gyaml);
    const lines = (result.files || []).map((file) => file.message);
    toast(lines.join(" ") || "Nothing to export.");
  } catch (error) {
    toast(error.message);
  }
}

async function fillFlagList(input) {
  const query = input.value.trim();
  if (query.length < 2) return;
  let list = document.getElementById("flag-names");
  if (!list) {
    list = document.createElement("datalist");
    list.id = "flag-names";
    document.body.append(list);
  }
  try {
    const result = await api.flags(query);
    list.replaceChildren();
    for (const name of result.names || []) {
      const option = document.createElement("option");
      option.value = name;
      list.append(option);
    }
  } catch {
    list.replaceChildren();
  }
}

function openShortcuts() {
  const dialog = $("shortcuts-dialog");
  const host = $("shortcuts-list");
  if (!dialog || !host) return;
  if (!host.childElementCount) fillShortcuts(host);
  if (!dialog.open) dialog.showModal();
}

function fillShortcuts(host) {
  host.replaceChildren();
  for (const group of SHORTCUT_GROUPS) {
    const section = document.createElement("section");
    section.className = "shortcut-group";
    const title = document.createElement("h3");
    title.textContent = group.label;
    section.append(title);
    for (const item of group.items) {
      const row = document.createElement("div");
      row.className = "shortcut-row";
      const keys = document.createElement("span");
      keys.className = "shortcut-keys";
      keys.textContent = item.keys;
      const action = document.createElement("span");
      action.textContent = item.action;
      row.append(keys, action);
      section.append(row);
    }
    host.append(section);
  }
}

function storedFlag(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === "0") return false;
    if (raw === "1") return true;
  } catch {
    /* private mode */
  }
  return fallback;
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

function showRendererNotice() {
  const reason = viewport && viewport.backendReason;
  if (!reason) return;
  let storage;
  try {
    storage = localStorage;
  } catch {
    storage = null;
  }
  if (storage && !webgpuNoticeVisible(storage, reason)) return;
  const node = $("renderer-notice");
  const label = $("renderer-notice-text");
  if (!node || !label) return;
  label.textContent = viewport.backendNote || `WebGPU is unavailable (${reason}). Using WebGL.`;
  node.hidden = false;
  const button = $("renderer-notice-dismiss");
  if (!button) return;
  button.addEventListener("click", () => {
    if (storage) dismissWebgpuNotice(storage, reason);
    node.hidden = true;
  }, { once: true });
}

function watchBuildSkew() {
  const page = frontendBuild();
  api.health().then((body) => {
    const server = body && body.build;
    const skew = describeBuildMismatch(page, server);
    if (!skew) return;
    console.warn(skew.message, { page, server });
    const id = buildNoticeId(page, server);
    let storage;
    try {
      storage = localStorage;
    } catch {
      storage = null;
    }
    if (storage && !buildNoticeVisible(storage, id)) return;
    const node = $("build-notice");
    const label = $("build-notice-text");
    if (!node || !label) return;
    label.textContent = skew.message;
    node.hidden = false;
    const button = $("build-notice-dismiss");
    if (!button) return;
    button.addEventListener("click", () => {
      if (storage) dismissBuildNotice(storage, id);
      node.hidden = true;
    }, { once: true });
  }).catch(() => {
    /* the editor still runs when the health check cannot */
  });
}

function toast(message, { persistent = false } = {}) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  $("toasts").append(node);
  if (!persistent) setTimeout(() => node.remove(), 5200);
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
