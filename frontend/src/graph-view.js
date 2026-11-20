/** Blueprint camera and drawing for the behavior graph.

The dump still owns node ids. This layer only pans, zooms, and paints.
*/

export const NODE_W = 248;
export const COL = 360;
export const HEAD = 30;
export const PIN = 22;
export const ROW_GAP = 28;
export const ZOOM_MIN = 0.2;
export const ZOOM_MAX = 1.8;

export const CATEGORIES = {
  entry: { label: "Entry", color: "#c4554a" },
  command: { label: "Command", color: "#d27a3a" },
  action: { label: "Action", color: "#3d7ea6" },
  selector: { label: "Selector", color: "#3d9e6a" },
  sequence: { label: "Sequence", color: "#2f8f8a" },
  subflow: { label: "SubFlow", color: "#7c6adf" },
  event: { label: "Event", color: "#c46b9a" },
  schedule: { label: "Schedule", color: "#c9a15a" },
};

export const PIN_COLORS = {
  bool: "#e23b3b",
  int: "#3ec2c2",
  float: "#8fce4a",
  string: "#d23b8a",
  vec3: "#e0b15a",
  pointer: "#3d7cff",
  flow: "#f4f4f4",
};

const cameras = new Map();
const live = [];
let spaceDown = false;
let keysBound = false;

export function nodeCategory(node) {
  if (!node) return "action";
  const type = String(node.type || "");
  const raw = String(node.raw || "");
  const name = String(node.name || "");
  const kind = String(node.kind || "");
  const blob = `${type} ${raw} ${name}`;
  if (type === "Command" || /#command:/.test(String(node.id || "")) || /^Command\b/.test(name)) return "command";
  if (node.entry || type === "Entry" || /\broot\b/i.test(raw)) return "entry";
  if (/selector/i.test(blob)) return "selector";
  if (/simultaneous|sequential|parallel|element_fork|element_join/i.test(blob)) return "sequence";
  if (/subflow|moduleif|module_/i.test(blob)) return "subflow";
  if (kind === "event") return "event";
  if (kind === "schedule" || type === "builtin") return "schedule";
  return "action";
}

export function categoryColor(node) {
  return (CATEGORIES[nodeCategory(node)] || CATEGORIES.action).color;
}

export function pinKind(pin) {
  const type = String((pin && pin.type) || "").toLowerCase();
  if (type === "ptr" || type === "pointer" || type === "actor") return "pointer";
  if (type === "vec3f" || type === "vector" || type === "vec3") return "vec3";
  if (type === "exec" || type === "flow") return "flow";
  if (PIN_COLORS[type]) return type;
  return "flow";
}

export function pinColor(pin) {
  return PIN_COLORS[pinKind(pin)] || PIN_COLORS.flow;
}

export function pinRows(node) {
  const pins = (node && node.pins) || [];
  return {
    inputs: pins.filter((pin) => pin.dir !== "out"),
    outputs: pins.filter((pin) => pin.dir === "out"),
  };
}

export function nodeHeight(node) {
  const rows = pinRows(node);
  return HEAD + Math.max(rows.inputs.length, rows.outputs.length, 1) * PIN + 8;
}

export function pinAnchor(node, pin, pos) {
  const rows = pinRows(node);
  const list = pin && pin.dir === "out" ? rows.outputs : rows.inputs;
  let index = list.findIndex((item) => item.id === (pin && pin.id));
  if (index < 0) index = 0;
  return {
    x: pin && pin.dir === "out" ? pos.x + pos.w : pos.x,
    y: pos.y + HEAD + index * PIN + PIN / 2,
  };
}

export function wirePath(start, end) {
  const bend = Math.max(48, Math.abs(end.x - start.x) * 0.45);
  const dir = end.x >= start.x ? 1 : -1;
  return `M ${start.x} ${start.y} C ${start.x + bend * dir} ${start.y}, ${end.x - bend * dir} ${end.y}, ${end.x} ${end.y}`;
}

export function zoomAt(camera, sx, sy, factor) {
  const zoom = clamp(camera.zoom * factor, ZOOM_MIN, ZOOM_MAX);
  const wx = (sx - camera.x) / camera.zoom;
  const wy = (sy - camera.y) / camera.zoom;
  return { zoom, x: sx - wx * zoom, y: sy - wy * zoom };
}

export function fitCamera(bounds, view, pad = 64) {
  const width = Math.max(bounds.width, 1);
  const height = Math.max(bounds.height, 1);
  const roomW = Math.max(view.width - pad * 2, 1);
  const roomH = Math.max(view.height - pad * 2, 1);
  const zoom = clamp(Math.min(roomW / width, roomH / height), ZOOM_MIN, ZOOM_MAX);
  return {
    zoom,
    x: (view.width - width * zoom) / 2 - bounds.x * zoom,
    y: (view.height - height * zoom) / 2 - bounds.y * zoom,
  };
}

export function boundsOf(positions) {
  const boxes = Object.values(positions || {});
  if (!boxes.length) return { x: 0, y: 0, width: NODE_W, height: 80 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const box of boxes) {
    minX = Math.min(minX, box.x);
    minY = Math.min(minY, box.y);
    maxX = Math.max(maxX, box.x + box.w);
    maxY = Math.max(maxY, box.y + box.h);
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

export function minimapLayout(bounds, camera, view, map) {
  const pad = 8;
  const width = Math.max(bounds.width, 1);
  const height = Math.max(bounds.height, 1);
  const scale = Math.min((map.width - pad * 2) / width, (map.height - pad * 2) / height);
  const originX = pad + (map.width - pad * 2 - width * scale) / 2 - bounds.x * scale;
  const originY = pad + (map.height - pad * 2 - height * scale) / 2 - bounds.y * scale;
  const worldLeft = (0 - camera.x) / camera.zoom;
  const worldTop = (0 - camera.y) / camera.zoom;
  return {
    scale,
    originX,
    originY,
    frame: {
      x: originX + worldLeft * scale,
      y: originY + worldTop * scale,
      w: (view.width / camera.zoom) * scale,
      h: (view.height / camera.zoom) * scale,
    },
  };
}

export function mountGraph(stage, spec) {
  for (let index = live.length - 1; index >= 0; index -= 1) {
    if (!live[index].stage.isConnected) {
      live[index].stop();
      live.splice(index, 1);
    }
  }
  bindSpaceKeys();
  const key = spec.key || "graph";
  let camera = cameras.get(key) ? { ...cameras.get(key) } : null;
  const svg = svgEl("svg");
  svg.setAttribute("class", "graph-world");
  const world = svgEl("g");
  const grid = svgEl("rect");
  const patternId = `graph-dots-${Math.random().toString(36).slice(2, 8)}`;
  const defs = svgEl("defs");
  const pattern = svgEl("pattern");
  pattern.setAttribute("id", patternId);
  pattern.setAttribute("width", "28");
  pattern.setAttribute("height", "28");
  pattern.setAttribute("patternUnits", "userSpaceOnUse");
  const dot = svgEl("circle");
  dot.setAttribute("cx", "1");
  dot.setAttribute("cy", "1");
  dot.setAttribute("r", "1.1");
  dot.setAttribute("fill", "#314038");
  pattern.append(dot);
  defs.append(pattern);
  grid.setAttribute("x", "-8000");
  grid.setAttribute("y", "-8000");
  grid.setAttribute("width", "16000");
  grid.setAttribute("height", "16000");
  grid.setAttribute("fill", `url(#${patternId})`);
  const wires = svgEl("g");
  const nodes = svgEl("g");
  world.append(grid, wires, nodes);
  svg.append(defs, world);
  const mini = svgEl("svg");
  mini.setAttribute("class", "graph-minimap");
  mini.setAttribute("viewBox", "0 0 168 112");
  stage.replaceChildren(svg, mini);
  stage.tabIndex = 0;

  const byId = new Map(spec.nodes.map((item) => [item.node.id, item]));
  const byPin = new Map();
  for (const item of spec.nodes) {
    for (const pin of item.node.pins || []) byPin.set(pin.id, { pin, item });
  }

  for (const edge of spec.edges || []) {
    const from = byPin.get(edge.from);
    const to = byPin.get(edge.to);
    if (!from || !to) continue;
    const start = pinAnchor(from.item.node, from.pin, from.item.pos);
    const end = pinAnchor(to.item.node, to.pin, to.item.pos);
    const color = pinColor(from.pin);
    const hit = svgEl("path");
    hit.setAttribute("d", wirePath(start, end));
    hit.setAttribute("class", "graph-wire-hit");
    hit.dataset.from = edge.from;
    hit.dataset.to = edge.to;
    hit.dataset.edge = edge.id || `${edge.from}>${edge.to}`;
    const path = svgEl("path");
    path.setAttribute("d", wirePath(start, end));
    path.setAttribute("class", "graph-wire");
    path.setAttribute("stroke", color);
    wires.append(hit, path);
  }

  for (const item of spec.nodes) {
    nodes.append(drawNode(item, spec.selectedId));
  }

  function viewSize() {
    const rect = stage.getBoundingClientRect();
    return { width: rect.width || stage.clientWidth, height: rect.height || stage.clientHeight };
  }

  function apply() {
    world.setAttribute("transform", `translate(${camera.x} ${camera.y}) scale(${camera.zoom})`);
    drawMinimap();
  }

  function remember() {
    cameras.set(key, { ...camera });
  }

  function fit() {
    const view = viewSize();
    if (view.width < 40 || view.height < 40) return;
    camera = fitCamera(boundsOf(spec.positions), view);
    remember();
    apply();
  }

  stage.__fit = fit;

  function drawMinimap() {
    const view = viewSize();
    const map = { width: 168, height: 112 };
    const bounds = boundsOf(spec.positions);
    const laid = minimapLayout(bounds, camera || { x: 0, y: 0, zoom: 1 }, view, map);
    mini.replaceChildren();
    const back = svgEl("rect");
    back.setAttribute("width", "168");
    back.setAttribute("height", "112");
    back.setAttribute("rx", "8");
    back.setAttribute("fill", "#101512");
    mini.append(back);
    for (const item of spec.nodes) {
      const box = svgEl("rect");
      box.setAttribute("x", String(laid.originX + item.pos.x * laid.scale));
      box.setAttribute("y", String(laid.originY + item.pos.y * laid.scale));
      box.setAttribute("width", String(Math.max(item.pos.w * laid.scale, 2)));
      box.setAttribute("height", String(Math.max(item.pos.h * laid.scale, 2)));
      box.setAttribute("fill", categoryColor(item.node));
      mini.append(box);
    }
    const frame = svgEl("rect");
    frame.setAttribute("x", String(laid.frame.x));
    frame.setAttribute("y", String(laid.frame.y));
    frame.setAttribute("width", String(Math.max(laid.frame.w, 4)));
    frame.setAttribute("height", String(Math.max(laid.frame.h, 4)));
    frame.setAttribute("class", "graph-minimap-frame");
    mini.append(frame);
    mini.__layout = laid;
  }

  if (!camera) {
    camera = { x: 0, y: 0, zoom: 1 };
    requestAnimationFrame(fit);
  } else {
    apply();
  }

  let drag = null;
  svg.addEventListener("pointerdown", (event) => {
    stage.focus({ preventScroll: true });
    const node = event.target.closest && event.target.closest("[data-node]");
    if (event.button === 0 && node && !spaceDown) {
      spec.onSelect(node.dataset.node);
      return;
    }
    if (event.button === 1 || event.button === 2 || (event.button === 0 && spaceDown)) {
      event.preventDefault();
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, ox: camera.x, oy: camera.y, moved: false, button: event.button };
      svg.setPointerCapture(event.pointerId);
      stage.classList.add("is-panning");
    }
  });
  svg.addEventListener("pointermove", (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (Math.hypot(dx, dy) > 4) drag.moved = true;
    camera = { ...camera, x: drag.ox + dx, y: drag.oy + dy };
    remember();
    apply();
  });
  svg.addEventListener("pointerup", (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    const moved = drag.moved;
    const button = drag.button;
    drag = null;
    stage.classList.remove("is-panning");
    if (button === 2 && !moved && spec.onContext) {
      const rect = svg.getBoundingClientRect();
      const localX = event.clientX - rect.left;
      const localY = event.clientY - rect.top;
      spec.onContext({
        clientX: event.clientX,
        clientY: event.clientY,
        worldX: (localX - camera.x) / camera.zoom,
        worldY: (localY - camera.y) / camera.zoom,
      });
    }
  });
  svg.addEventListener("contextmenu", (event) => event.preventDefault());
  svg.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      camera = zoomAt(camera, event.clientX - rect.left, event.clientY - rect.top, event.deltaY < 0 ? 1.12 : 1 / 1.12);
      remember();
      apply();
    },
    { passive: false },
  );

  mini.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    event.stopPropagation();
    panMinimap(event);
    mini.setPointerCapture(event.pointerId);
    mini.dataset.drag = String(event.pointerId);
  });
  mini.addEventListener("pointermove", (event) => {
    if (mini.dataset.drag !== String(event.pointerId)) return;
    panMinimap(event);
  });
  mini.addEventListener("pointerup", () => {
    delete mini.dataset.drag;
  });

  function panMinimap(event) {
    const laid = mini.__layout;
    const view = viewSize();
    if (!laid) return;
    const rect = mini.getBoundingClientRect();
    const mx = ((event.clientX - rect.left) / rect.width) * 168;
    const my = ((event.clientY - rect.top) / rect.height) * 112;
    const worldX = (mx - laid.originX) / laid.scale;
    const worldY = (my - laid.originY) / laid.scale;
    camera = {
      ...camera,
      x: view.width / 2 - worldX * camera.zoom,
      y: view.height / 2 - worldY * camera.zoom,
    };
    remember();
    apply();
  }

  const doc = stage.ownerDocument;
  const onKey = (event) => {
    if (!stage.isConnected) {
      doc.removeEventListener("keydown", onKey);
      return;
    }
    const active = doc.activeElement;
    if (active && active !== stage && (active.closest && active.closest("input, textarea, select"))) return;
    if ((event.key === "f" || event.key === "F") && (stage === active || stage.matches(":hover"))) {
      event.preventDefault();
      fit();
    }
  };
  doc.addEventListener("keydown", onKey);

  const observer = new ResizeObserver(() => {
    if (!stage.isConnected) return;
    if (!cameras.get(key)) fit();
    else drawMinimap();
  });
  observer.observe(stage);
  const stop = () => {
    observer.disconnect();
    doc.removeEventListener("keydown", onKey);
  };
  live.push({ stage, stop });
}

function drawNode(item, selectedId) {
  const { node, pos, title } = item;
  const group = svgEl("g");
  group.setAttribute("class", "graph-node-shape");
  group.setAttribute("transform", `translate(${pos.x} ${pos.y})`);
  group.dataset.node = node.id;
  const body = svgEl("rect");
  body.setAttribute("width", String(pos.w));
  body.setAttribute("height", String(pos.h));
  body.setAttribute("rx", "8");
  body.setAttribute("class", "graph-node-body");
  if (!node.decoded) body.classList.add("is-undecoded");
  if (node.id === selectedId) body.classList.add("is-on");
  const header = svgEl("rect");
  header.setAttribute("width", String(pos.w));
  header.setAttribute("height", String(HEAD));
  header.setAttribute("rx", "8");
  header.setAttribute("fill", categoryColor(node));
  const headerSquare = svgEl("rect");
  headerSquare.setAttribute("y", String(HEAD / 2));
  headerSquare.setAttribute("width", String(pos.w));
  headerSquare.setAttribute("height", String(HEAD / 2));
  headerSquare.setAttribute("fill", categoryColor(node));
  const label = svgEl("text");
  label.setAttribute("class", "graph-node-title");
  label.setAttribute("x", "10");
  label.setAttribute("y", "20");
  label.textContent = trim(title || node.name || node.raw || "Node", 28);
  group.append(body, header, headerSquare, label);
  if (node.edited || node.added) {
    const mark = svgEl("rect");
    mark.setAttribute("width", "3");
    mark.setAttribute("height", String(pos.h));
    mark.setAttribute("fill", "#e0b15a");
    group.append(mark);
  }
  const rows = pinRows(node);
  rows.inputs.forEach((pin, index) => group.append(drawPin(pin, index, pos.w, "in")));
  rows.outputs.forEach((pin, index) => group.append(drawPin(pin, index, pos.w, "out")));
  return group;
}

function drawPin(pin, index, width, dir) {
  const y = HEAD + index * PIN + PIN / 2;
  const x = dir === "out" ? width : 0;
  const wrap = svgEl("g");
  wrap.dataset.pin = pin.id;
  wrap.dataset.dir = dir;
  wrap.dataset.type = pinKind(pin);
  const hit = svgEl("circle");
  hit.setAttribute("cx", String(x));
  hit.setAttribute("cy", String(y));
  hit.setAttribute("r", "9");
  hit.setAttribute("class", "graph-pin-hit");
  const dot = svgEl("circle");
  dot.setAttribute("cx", String(x));
  dot.setAttribute("cy", String(y));
  dot.setAttribute("r", "5");
  dot.setAttribute("fill", pinColor(pin));
  dot.setAttribute("stroke", "#121614");
  dot.setAttribute("stroke-width", "1");
  const text = svgEl("text");
  text.setAttribute("class", "graph-pin-label");
  text.setAttribute("y", String(y + 4));
  text.setAttribute("x", dir === "out" ? String(width - 14) : "14");
  text.setAttribute("text-anchor", dir === "out" ? "end" : "start");
  text.textContent = trim(pin.name || pinKind(pin), 18);
  wrap.append(hit, dot, text);
  return wrap;
}

function bindSpaceKeys() {
  if (keysBound || typeof window === "undefined") return;
  keysBound = true;
  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space") return;
    const active = document.activeElement;
    if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.tagName === "SELECT")) return;
    spaceDown = true;
  });
  window.addEventListener("keyup", (event) => {
    if (event.code === "Space") spaceDown = false;
  });
}

function trim(text, max) {
  const value = String(text || "");
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function svgEl(name) {
  return document.createElementNS("http://www.w3.org/2000/svg", name);
}
