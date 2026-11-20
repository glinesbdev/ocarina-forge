/** Layout and drawing for the read-only behavior graph.

Node ids come from the dump. Laying the graph out again does not change them.
The raw layer shows every node, including ones that did not decode. The
curated layer uses the friendly name.
*/

const NODE_W = 196;
const COL = 220;
const HEAD = 34;
const PIN = 16;

export function displayName(node, layer) {
  if (!node) return "";
  if (layer === "raw") return node.raw || "undecoded";
  return node.name || node.raw || "Undecoded";
}

export function layoutGraph(graph) {
  const nodes = (graph && graph.nodes) || [];
  const edges = (graph && graph.edges) || [];
  const sources = (graph && graph.sources) || [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const incoming = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of edges) {
    const from = pinOwner(edge.from, byId);
    const to = pinOwner(edge.to, byId);
    if (from && to && from !== to) incoming.get(to).push(from);
  }
  const positions = {};
  const titles = [];
  let y = 8;
  let width = NODE_W + 16;
  const groups = sources.length ? sources : [{ id: "", title: "Behavior" }];
  const seen = new Set();
  for (const source of groups) {
    const members = nodes.filter((node) => node.source === source.id);
    for (const node of members) seen.add(node.id);
    if (!members.length) continue;
    titles.push({ text: `${source.title || "Behavior"} · ${source.path || ""}`.trim(), x: 8, y });
    y += 22;
    const columns = columnsOf(members, incoming);
    let tallest = 0;
    columns.forEach((column, index) => {
      let rowY = y;
      for (const node of column) {
        const height = nodeHeight(node);
        positions[node.id] = { x: 8 + index * COL, y: rowY, w: NODE_W, h: height };
        rowY += height + 12;
      }
      tallest = Math.max(tallest, rowY - y);
      width = Math.max(width, 8 + index * COL + NODE_W + 16);
    });
    y += tallest + 18;
  }
  const rest = nodes.filter((node) => !seen.has(node.id));
  if (rest.length) {
    titles.push({ text: "Behavior", x: 8, y });
    y += 22;
    rest.forEach((node, index) => {
      const height = nodeHeight(node);
      positions[node.id] = { x: 8, y: y + index * (height + 12), w: NODE_W, h: height };
    });
    y += rest.reduce((sum, node) => sum + nodeHeight(node) + 12, 0);
  }
  return { positions, titles, width, height: Math.max(y, 80) };
}

export function pinAnchor(node, pin, pos) {
  const pins = (node && node.pins) || [];
  const index = Math.max(0, pins.findIndex((item) => item.id === pin.id));
  return {
    x: pin.dir === "out" ? pos.x + pos.w : pos.x,
    y: pos.y + HEAD + index * PIN + PIN / 2,
  };
}

export function renderBehavior(host, model) {
  host.replaceChildren();
  if (!model || !model.actor) {
    host.append(note("Select an actor. The Behavior tab draws its AI graph, schedule, and event-flow hooks."));
    return;
  }
  if (model.loading) {
    host.append(note("Reading the AI graph…"));
    return;
  }
  if (model.error) {
    host.append(note(model.error));
    return;
  }
  const graph = model.graph;
  if (!graph) {
    host.append(note("Reading the AI graph…"));
    return;
  }
  const bar = document.createElement("div");
  bar.className = "behavior-bar";
  bar.append(
    layerButton("Friendly", "curated", model),
    layerButton("Raw", "raw", model),
  );
  const hint = document.createElement("p");
  hint.className = "muted";
  hint.textContent = graph.note
    ? graph.note
    : "Read only. A later edit stores add node, connect, and set field against these ids.";
  host.append(bar, hint);
  if (!graph.nodes || !graph.nodes.length) {
    if (graph.trace && graph.trace.length) host.append(traceBlock(graph.trace));
    return;
  }

  const laid = layoutGraph(graph);
  const canvas = document.createElement("div");
  canvas.className = "graph-canvas";
  canvas.style.width = `${laid.width}px`;
  canvas.style.height = `${laid.height}px`;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "graph-svg");
  svg.setAttribute("width", String(laid.width));
  svg.setAttribute("height", String(laid.height));
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  for (const edge of graph.edges || []) {
    const fromNode = byId.get(pinOwner(edge.from, byId));
    const toNode = byId.get(pinOwner(edge.to, byId));
    const fromPos = fromNode && laid.positions[fromNode.id];
    const toPos = toNode && laid.positions[toNode.id];
    if (!fromPos || !toPos) continue;
    const fromPin = (fromNode.pins || []).find((pin) => pin.id === edge.from) || { id: edge.from, dir: "out" };
    const toPin = (toNode.pins || []).find((pin) => pin.id === edge.to) || { id: edge.to, dir: "in" };
    const start = pinAnchor(fromNode, fromPin, fromPos);
    const end = pinAnchor(toNode, toPin, toPos);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const bend = Math.max(24, (end.x - start.x) / 2);
    path.setAttribute("d", `M ${start.x} ${start.y} C ${start.x + bend} ${start.y}, ${end.x - bend} ${end.y}, ${end.x} ${end.y}`);
    path.setAttribute("class", "graph-edge");
    svg.append(path);
  }
  canvas.append(svg);
  for (const title of laid.titles) {
    const label = document.createElement("div");
    label.className = "graph-title";
    label.style.left = `${title.x}px`;
    label.style.top = `${title.y}px`;
    label.textContent = title.text;
    canvas.append(label);
  }
  for (const node of graph.nodes) {
    const pos = laid.positions[node.id];
    if (!pos) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "graph-node";
    if (!node.decoded) button.classList.add("is-undecoded");
    if (node.id === model.selectedId) button.classList.add("is-on");
    button.style.left = `${pos.x}px`;
    button.style.top = `${pos.y}px`;
    button.style.width = `${pos.w}px`;
    button.style.height = `${pos.h}px`;
    const title = document.createElement("strong");
    title.textContent = displayName(node, model.layer);
    button.append(title);
    for (const pin of node.pins || []) {
      const row = document.createElement("span");
      row.className = pin.dir === "out" ? "pin pin-out" : "pin pin-in";
      row.textContent = pin.name;
      button.append(row);
    }
    button.addEventListener("click", () => model.onSelect(node.id));
    canvas.append(button);
  }
  host.append(canvas);
  const selected = graph.nodes.find((node) => node.id === model.selectedId);
  if (selected) host.append(detail(selected, graph));
}

function layerButton(label, layer, model) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = model.layer === layer ? "is-on" : "";
  button.textContent = label;
  button.setAttribute("aria-pressed", model.layer === layer ? "true" : "false");
  button.addEventListener("click", () => model.onLayer(layer));
  return button;
}

function detail(node, graph) {
  const wrap = document.createElement("div");
  wrap.className = "model-note";
  const title = document.createElement("div");
  title.className = "axis-label";
  title.textContent = node.decoded ? node.name : "Undecoded";
  wrap.append(title);
  const raw = document.createElement("p");
  raw.textContent = node.raw;
  wrap.append(raw);
  const source = (graph.sources || []).find((item) => item.id === node.source);
  if (source) {
    const path = document.createElement("p");
    path.className = "muted";
    path.textContent = source.path;
    wrap.append(path);
  }
  for (const field of node.fields || []) {
    const line = document.createElement("p");
    line.textContent = `${field.name}: ${field.value}`;
    wrap.append(line);
  }
  if (node.note) {
    const line = document.createElement("p");
    line.className = "muted";
    line.textContent = node.note;
    wrap.append(line);
  }
  const ident = document.createElement("p");
  ident.className = "muted hash";
  ident.textContent = node.id;
  wrap.append(ident);
  return wrap;
}

function traceBlock(lines) {
  const block = document.createElement("pre");
  block.className = "trace";
  block.textContent = lines.join("\n");
  return block;
}

function note(text) {
  const paragraph = document.createElement("p");
  paragraph.className = "muted";
  paragraph.textContent = text;
  return paragraph;
}

function nodeHeight(node) {
  return HEAD + Math.max((node.pins || []).length, 1) * PIN;
}

function columnsOf(members, incoming) {
  const ids = new Set(members.map((node) => node.id));
  const layer = new Map();
  function depth(id, trail) {
    if (layer.has(id)) return layer.get(id);
    if (trail.has(id)) return 0;
    trail.add(id);
    let best = 0;
    for (const pred of incoming.get(id) || []) {
      if (ids.has(pred)) best = Math.max(best, depth(pred, trail) + 1);
    }
    trail.delete(id);
    layer.set(id, best);
    return best;
  }
  for (const node of members) depth(node.id, new Set());
  const columns = [];
  const sorted = [...members].sort((a, b) => (a.index || 0) - (b.index || 0) || String(a.id).localeCompare(String(b.id)));
  for (const node of sorted) {
    const column = layer.get(node.id) || 0;
    while (columns.length <= column) columns.push([]);
    columns[column].push(node);
  }
  return columns;
}

function pinOwner(pinId, byId) {
  if (!pinId) return "";
  if (byId.has(pinId)) return pinId;
  let found = "";
  for (const id of byId.keys()) {
    if (pinId.startsWith(`${id}:`) && id.length > found.length) found = id;
  }
  return found;
}
