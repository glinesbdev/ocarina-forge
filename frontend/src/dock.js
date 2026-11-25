/** Dockable panels. Pop-out windows are the same page: dockview moves the
 * live DOM into the new window, so selection, edits, and undo stay on this
 * store. A BroadcastChannel is not required. */

import { createDockview } from "dockview-core";

export const LAYOUT_KEY = "ocarinaforge.layout";
/** Version 2 drops the Stats panel. A saved version 1 layout is ignored. */
export const LAYOUT_VERSION = 2;

export const PANELS = [
  { id: "scenes", title: "Scenes", elementId: "scene-pane" },
  { id: "actors", title: "Actors", elementId: "outliner-panel" },
  { id: "viewport", title: "Viewport", elementId: "stage" },
  { id: "inspector", title: "Inspector", elementId: "props-panel" },
  { id: "behavior", title: "Behavior", elementId: "behavior-panel" },
  { id: "details", title: "Details", elementId: "details-panel" },
  { id: "edits", title: "Edits", elementId: "changes-panel" },
  { id: "problems", title: "Problems", elementId: "problems-panel" },
];

const TAB_TO_PANEL = {
  outliner: "actors",
  props: "inspector",
  changes: "edits",
  problems: "problems",
  behavior: "behavior",
  details: "details",
  scenes: "scenes",
  actors: "actors",
  viewport: "viewport",
  inspector: "inspector",
  edits: "edits",
};

const PANEL_TO_TAB = {
  actors: "outliner",
  inspector: "props",
  edits: "changes",
};

const PLACEMENTS = {
  actors: { referencePanel: "scenes", direction: "within" },
  viewport: { referencePanel: "scenes", direction: "right" },
  behavior: { referencePanel: "viewport", direction: "below" },
  details: { referencePanel: "behavior", direction: "within" },
  inspector: { referencePanel: "viewport", direction: "right" },
  edits: { referencePanel: "behavior", direction: "below" },
  problems: { referencePanel: "edits", direction: "within" },
};

export function panelIdForTab(name) {
  return TAB_TO_PANEL[name] || name;
}

export function tabForPanel(id) {
  return PANEL_TO_TAB[id] || id;
}

export function readSavedLayout(raw) {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.version !== LAYOUT_VERSION || !parsed.dock) return null;
    return parsed.dock;
  } catch {
    return null;
  }
}

function storageGet() {
  try {
    return localStorage.getItem(LAYOUT_KEY);
  } catch {
    return null;
  }
}

function storageSet(value) {
  try {
    localStorage.setItem(LAYOUT_KEY, value);
  } catch {
    /* private mode and full storage both leave the layout for this session */
  }
}

function storageClear() {
  try {
    localStorage.removeItem(LAYOUT_KEY);
  } catch {
    /* ignore */
  }
}

export function createDock(host, hooks = {}) {
  const sources = hooks.sources || document.getElementById("dock-sources");
  const nodes = new Map();
  const titles = new Map(PANELS.map((panel) => [panel.id, panel.title]));
  const seenWindows = new WeakSet();

  globalThis.__ocarinaFind = (id) => {
    for (const node of nodes.values()) {
      if (!node) continue;
      if (node.id === id) return node;
      const inner = node.querySelector(`[id="${id}"]`);
      if (inner) return inner;
    }
    return null;
  };

  function nodeFor(name) {
    const spec = PANELS.find((panel) => panel.id === name);
    if (!spec) return null;
    let node = nodes.get(spec.elementId);
    if (!node) node = document.getElementById(spec.elementId);
    if (!node && sources) node = sources.querySelector(`#${spec.elementId}`);
    if (node) nodes.set(spec.elementId, node);
    return node;
  }

  function createComponent(options) {
    const element = document.createElement("div");
    element.className = "dock-fill";
    return {
      element,
      init() {
        const node = nodeFor(options.name);
        if (!node) return;
        node.hidden = false;
        node.classList.add("dock-panel");
        element.append(node);
      },
      dispose() {
        const node = element.firstElementChild;
        if (node && sources) sources.append(node);
      },
    };
  }

  function createHeaderAction() {
    const element = document.createElement("div");
    element.className = "dock-actions";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dock-max";
    button.textContent = "Max";
    button.title = "Maximize this group";
    element.append(button);
    let api = null;
    button.addEventListener("mousedown", (event) => event.stopPropagation());
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!api) return;
      if (api.isMaximized()) api.exitMaximized();
      else api.maximize();
    });
    return {
      element,
      init(params) {
        api = params.api;
        const sync = () => {
          const on = api.isMaximized();
          button.textContent = on ? "Restore" : "Max";
          button.title = on ? "Restore this group" : "Maximize this group";
        };
        sync();
        const disposable = params.containerApi.onDidLayoutChange(sync);
        this.dispose = () => disposable.dispose();
      },
    };
  }

  const api = createDockview(host, {
    theme: {
      name: "ocarina",
      className: "dockview-theme-dark ocarina-dock",
      gap: 6,
    },
    popoutUrl: "/popout.html",
    defaultRenderer: "always",
    createComponent,
    createRightHeaderActionComponent: createHeaderAction,
  });
  globalThis.__ocarinaDock = api;

  function watchWindows() {
    for (const group of api.groups) {
      const popup = group.api.getWindow();
      if (!popup || popup === window || seenWindows.has(popup)) continue;
      seenWindows.add(popup);
      if (hooks.onWindow) hooks.onWindow(popup);
    }
  }

  function titleFor(id) {
    return titles.get(id) || id;
  }

  function placement(id) {
    const pref = PLACEMENTS[id];
    if (!pref) return undefined;
    if (pref.referencePanel && api.getPanel(pref.referencePanel)) return pref;
    if (api.getPanel("viewport")) return { referencePanel: "viewport", direction: "within" };
    return undefined;
  }

  function reveal(id) {
    const existing = api.getPanel(id);
    if (existing) {
      existing.api.setActive();
      return existing;
    }
    const position = placement(id);
    const panel = api.addPanel({
      id,
      component: id,
      title: titleFor(id),
      ...(position ? { position } : {}),
    });
    panel.api.setActive();
    return panel;
  }

  function close(id) {
    const panel = api.getPanel(id);
    if (panel) panel.api.close();
  }

  function toggle(id) {
    if (api.getPanel(id)) close(id);
    else reveal(id);
  }

  function isOpen(id) {
    return Boolean(api.getPanel(id));
  }

  function setTitle(id, title) {
    titles.set(id, title);
    const panel = api.getPanel(id);
    if (panel) panel.api.setTitle(title);
  }

  function add(id, extra) {
    api.addPanel({
      id,
      component: id,
      title: titleFor(id),
      ...extra,
    });
  }

  function applyDefault() {
    api.clear();
    add("scenes", { initialWidth: 280 });
    add("actors", { position: { referencePanel: "scenes", direction: "within" } });
    const scenes = api.getPanel("scenes");
    if (scenes) scenes.api.setActive();
    add("viewport", { position: { referencePanel: "scenes", direction: "right" } });
    add("behavior", { position: { referencePanel: "viewport", direction: "below" }, initialHeight: 320 });
    add("inspector", { position: { referencePanel: "viewport", direction: "right" }, initialWidth: 340 });
    add("edits", { position: { referencePanel: "behavior", direction: "below" }, initialHeight: 168 });
    add("problems", { position: { referencePanel: "edits", direction: "within" } });
    const edits = api.getPanel("edits");
    if (edits) edits.api.setActive();
    const viewport = api.getPanel("viewport");
    if (viewport) viewport.api.setActive();
  }

  function sizeDefault() {
    const height = host.clientHeight;
    const width = host.clientWidth;
    if (height < 240 || width < 640) return;
    const scenes = api.getPanel("scenes");
    const inspector = api.getPanel("inspector");
    const behavior = api.getPanel("behavior");
    const edits = api.getPanel("edits");
    if (scenes) scenes.api.setSize({ width: Math.max(240, Math.round(width * 0.18)) });
    if (inspector) inspector.api.setSize({ width: Math.max(280, Math.round(width * 0.22)) });
    const viewport = api.getPanel("viewport");
    if (behavior) behavior.api.setSize({ height: Math.max(220, Math.round(height * 0.32)) });
    if (edits) edits.api.setSize({ height: Math.max(130, Math.round(height * 0.16)) });
    if (viewport) viewport.api.setSize({ height: Math.max(260, Math.round(height * 0.48)) });
  }

  function saveNow() {
    if (host.clientHeight < 240 || host.clientWidth < 640) return;
    storageSet(JSON.stringify({ version: LAYOUT_VERSION, dock: api.toJSON() }));
  }

  function fit() {
    const width = host.clientWidth;
    const height = host.clientHeight;
    if (width < 64 || height < 64) return;
    api.layout(width, height, true);
    maybeSize();
  }

  let restored = false;
  function loadLayout() {
    const saved = readSavedLayout(storageGet());
    if (saved) {
      try {
        api.fromJSON(saved);
        if (api.panels.length) {
          restored = true;
          return;
        }
      } catch (error) {
        console.warn("Ocarina Forge dropped a saved layout", error);
      }
    }
    applyDefault();
  }

  let saveTimer = 0;
  let menuTimer = 0;
  api.onDidLayoutChange(() => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 200);
    clearTimeout(menuTimer);
    menuTimer = setTimeout(() => {
      watchWindows();
      if (hooks.onChange) hooks.onChange();
    }, 40);
  });

  host.addEventListener("dblclick", (event) => {
    const target = event.target instanceof Element ? event.target : event.target && event.target.parentElement;
    if (!target) return;
    const tab = target.closest(".dv-default-tab");
    if (!tab || target.closest(".dv-default-tab-action")) return;
    const label = tab.querySelector(".dv-default-tab-content");
    const title = label ? label.textContent : "";
    const panel = api.panels.find((item) => item.api.title === title);
    if (!panel) return;
    if (panel.api.isMaximized()) panel.api.exitMaximized();
    else panel.api.maximize();
  });

  let fitted = false;
  function maybeSize() {
    if (restored || fitted) return;
    if (host.clientHeight < 240 || host.clientWidth < 640) return;
    fitted = true;
    const apply = () => {
      if (!restored) sizeDefault();
    };
    requestAnimationFrame(() => requestAnimationFrame(apply));
    setTimeout(apply, 60);
  }

  loadLayout();
  const sizing = new ResizeObserver(maybeSize);
  sizing.observe(host);
  requestAnimationFrame(() => {
    maybeSize();
    watchWindows();
    if (hooks.onChange) hooks.onChange();
  });

  async function popOut(id) {
    const panel = reveal(id);
    if (!panel) return false;
    if (panel.api.isMaximized()) panel.api.exitMaximized();
    const opened = await api.addPopoutGroup(panel, {
      popoutUrl: "/popout.html",
      onDidOpen(event) {
        watchWindows();
        if (hooks.onWindow && event.window && !seenWindows.has(event.window)) {
          seenWindows.add(event.window);
          hooks.onWindow(event.window);
        }
      },
    });
    watchWindows();
    return Boolean(opened);
  }

  function reset() {
    storageClear();
    restored = false;
    fitted = false;
    applyDefault();
    requestAnimationFrame(() => {
      maybeSize();
      saveNow();
      if (hooks.onChange) hooks.onChange();
    });
  }

  return {
    api,
    reveal,
    close,
    toggle,
    reset,
    popOut,
    setTitle,
    isOpen,
    fit,
    panels: PANELS,
  };
}
