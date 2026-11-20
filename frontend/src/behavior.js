/** Layout and drawing for the behavior graph.

Node ids come from the dump. Laying the graph out again does not change them.
The raw layer shows every node, including ones that did not decode. The
curated layer uses the friendly name. The picture is a blueprint: it fills
the panel, and the camera pans and zooms.
*/

import { COL, NODE_W, ROW_GAP, mountGraph, nodeHeight } from "./graph-view.js";

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
        rowY += height + ROW_GAP;
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
      positions[node.id] = { x: 8, y: y + index * (height + ROW_GAP), w: NODE_W, h: height };
    });
    y += rest.reduce((sum, node) => sum + nodeHeight(node) + ROW_GAP, 0);
  }
  return { positions, titles, width, height: Math.max(y, 80) };
}

const LAYOUT_CAP = 160;

export function visibleNodes(graph, options = {}) {
  const sources = (graph && graph.sources) || [];
  const nodes = (graph && graph.nodes) || [];
  const edges = (graph && graph.edges) || [];
  const requested = options.file || (graph && graph.open) || (sources[0] && sources[0].id) || "";
  const source = sources.find((item) => item.id === requested) || sources[0] || null;
  const sourceId = source ? source.id : "";
  const members = nodes.filter((node) => !sourceId || node.source === sourceId);
  const query = String(options.query || "").trim().toLowerCase();
  const entry = options.entry || (source && source.openEntry) || "";
  if (query) {
    const matches = members.filter((node) => nodeMatches(node, query));
    let elsewhere = null;
    if (!matches.length) {
      const counts = new Map();
      for (const node of nodes) {
        if (node.source === sourceId || !nodeMatches(node, query)) continue;
        counts.set(node.source, (counts.get(node.source) || 0) + 1);
      }
      let best = "";
      let bestCount = 0;
      for (const [id, count] of counts) {
        if (count > bestCount) {
          best = id;
          bestCount = count;
        }
      }
      if (best) {
        const other = sources.find((item) => item.id === best);
        elsewhere = { id: best, count: bestCount, file: (other && (other.file || other.title)) || "another file" };
      }
    }
    return {
      source,
      entry: "",
      nodes: matches.slice(0, LAYOUT_CAP),
      total: matches.length,
      capped: matches.length > LAYOUT_CAP,
      elsewhere,
    };
  }
  const order = entry ? neighborhood(entry, members, edges) : members.map((node) => node.id);
  const keep = new Set(order.slice(0, LAYOUT_CAP));
  return {
    source,
    entry,
    nodes: members.filter((node) => keep.has(node.id)),
    total: order.length,
    capped: order.length > LAYOUT_CAP,
    elsewhere: null,
  };
}

function nodeMatches(node, query) {
  const fields = (node.fields || []).map((field) => `${field.name} ${field.value}`);
  return [node.name, node.raw, node.type, node.id, ...fields].join(" ").toLowerCase().includes(query);
}

function neighborhood(start, members, edges) {
  const ids = new Set(members.map((node) => node.id));
  if (!ids.has(start)) return [...ids];
  const byId = new Map(members.map((node) => [node.id, node]));
  const nextTo = new Map();
  for (const id of ids) nextTo.set(id, []);
  for (const edge of edges) {
    const from = pinOwner(edge.from, byId);
    const to = pinOwner(edge.to, byId);
    if (!from || !to || from === to || !ids.has(from) || !ids.has(to)) continue;
    nextTo.get(from).push(to);
    nextTo.get(to).push(from);
  }
  const order = [];
  const seen = new Set([start]);
  const queue = [start];
  while (queue.length) {
    const id = queue.shift();
    order.push(id);
    for (const next of nextTo.get(id) || []) {
      if (!seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return order;
}

export function renderBehavior(host, model) {
  host.replaceChildren();
  host.__fitGraph = null;
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
  const shell = document.createElement("div");
  shell.className = "behavior-shell";
  const chrome = document.createElement("div");
  chrome.className = "behavior-chrome";
  const bar = document.createElement("div");
  bar.className = "behavior-bar";
  bar.append(
    layerButton("Friendly", "curated", model),
    layerButton("Raw", "raw", model),
  );
  if (model.onExport && graph && graph.ops && graph.ops.length) {
    const exp = document.createElement("button");
    exp.type = "button";
    exp.textContent = "Export";
    exp.addEventListener("click", () => model.onExport());
    bar.append(exp);
  }
  chrome.append(bar);
  shell.append(chrome);
  host.append(shell);
  if (!graph.nodes || !graph.nodes.length) {
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent = graph.note || "This actor's pack has no AI graph, schedule, or event flow.";
    host.append(hint);
    if (graph.trace && graph.trace.length) host.append(traceBlock(graph.trace));
    return;
  }

  const view = visibleNodes(graph, { file: model.file, entry: model.entry, query: model.query });
  chrome.append(fileList(graph, view, model));
  const search = document.createElement("input");
  search.className = "behavior-search";
  search.type = "search";
  search.placeholder = "Search this actor";
  search.value = model.query || "";
  search.addEventListener("input", (event) => model.onQuery && model.onQuery(event.target.value));
  chrome.append(search);
  if (!model.query) chrome.append(entryList(view, model));
  const hint = document.createElement("p");
  hint.className = "muted";
  hint.textContent = viewHint(graph, view);
  chrome.append(hint);
  if (view.elsewhere && model.onFile) {
    const jump = document.createElement("button");
    jump.type = "button";
    jump.className = "behavior-jump";
    jump.textContent = `${view.elsewhere.count} in ${view.elsewhere.file}`;
    jump.addEventListener("click", () => model.onFile(view.elsewhere.id, true));
    chrome.append(jump);
  }
  if (!view.nodes.length) return;

  const fit = document.createElement("button");
  fit.type = "button";
  fit.textContent = "Fit";
  fit.addEventListener("click", () => host.__fitGraph && host.__fitGraph());
  bar.append(fit);

  const laid = layoutGraph({ nodes: view.nodes, edges: graph.edges, sources: view.source ? [view.source] : [] });
  const body = document.createElement("div");
  body.className = "behavior-body";
  const stage = document.createElement("div");
  stage.className = "graph-stage";
  stage.setAttribute("aria-label", "Behavior graph");
  body.append(stage);
  const selected = (graph.nodes || []).find((node) => node.id === model.selectedId);
  if (selected) {
    const inspector = detail(selected, graph, model);
    inspector.classList.add("behavior-inspector");
    body.append(inspector);
  }
  shell.append(body);
  mountGraph(stage, {
    key: graphKey(graph, view, model),
    nodes: view.nodes.map((node) => ({
      node,
      pos: laid.positions[node.id],
      title: displayName(node, model.layer),
    })),
    edges: graph.edges || [],
    positions: laid.positions,
    selectedId: model.selectedId,
    onSelect: (id) => model.onSelect && model.onSelect(id),
    onContext: (point) => model.onContext && model.onContext(point, view),
  });
  host.__fitGraph = () => stage.__fit && stage.__fit();
  if ((graph.diff || []).length) chrome.append(diffList(graph, model));
}

function graphKey(graph, view, model) {
  const source = (view.source && view.source.id) || "";
  return [graph.gyaml || "", source, view.entry || "", model.query || "", model.layer || ""].join("|");
}

export function sharedFileLabel(source, count) {
  const file = source.file || source.title || source.kind;
  const label = `${file} · ${count}`;
  return source.sharedFrom ? `${label} · shared from ${source.sharedFrom}` : label;
}

function fileList(graph, view, model) {
  const files = document.createElement("div");
  files.className = "behavior-files";
  for (const source of graph.sources || []) {
    const button = document.createElement("button");
    button.type = "button";
    const count = source.count != null ? source.count : (graph.nodes || []).filter((node) => node.source === source.id).length;
    button.textContent = sharedFileLabel(source, count);
    button.title = source.warning || source.path || "";
    if (view.source && source.id === view.source.id) button.classList.add("is-on");
    button.addEventListener("click", () => model.onFile && model.onFile(source.id));
    files.append(button);
  }
  return files;
}

function entryList(view, model) {
  const row = document.createElement("div");
  row.className = "behavior-entries";
  const entries = (view.source && view.source.entries) || [];
  for (const entry of entries.slice(0, 24)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = entry.name || entry.raw;
    button.title = entry.raw || "";
    if (entry.id === view.entry) button.classList.add("is-on");
    button.addEventListener("click", () => model.onEntry && model.onEntry(entry.id));
    row.append(button);
  }
  if (entries.length > 24) {
    const more = document.createElement("span");
    more.className = "muted";
    more.textContent = `+${entries.length - 24}`;
    row.append(more);
  }
  return row;
}

function viewHint(graph, view) {
  const file = (view.source && (view.source.file || view.source.title)) || "Behavior";
  const shown = view.capped ? `showing ${view.nodes.length} of ${view.total}` : `${view.nodes.length} in view`;
  const whole = `${(graph.nodes || []).length} nodes`;
  const missed = graph.undecoded ? `${graph.undecoded} undecoded` : "";
  return [file, shown, whole, missed].filter(Boolean).join(" · ");
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

function detail(node, graph, model) {
  const wrap = document.createElement("div");
  wrap.className = "model-note";
  const title = document.createElement("div");
  title.className = "axis-label";
  title.textContent = node.edited ? `${node.decoded ? node.name : "Undecoded"} · edited` : node.decoded ? node.name : "Undecoded";
  wrap.append(title);
  const raw = document.createElement("p");
  raw.textContent = node.raw;
  wrap.append(raw);
  const source = (graph.sources || []).find((item) => item.id === node.source);
  if (source) {
    const path = document.createElement("p");
    path.className = "muted";
    path.textContent = source.sharedFrom ? `shared from ${source.sharedFrom}` : source.path;
    wrap.append(path);
    if (source.warning) {
      const warning = document.createElement("p");
      warning.textContent = source.warning;
      wrap.append(warning);
    }
  }
  const editable = (node.fields || []).filter((field) => field.editable);
  if (!editable.length) {
    const line = document.createElement("p");
    line.className = "muted";
    line.textContent = "This node has no editable params.";
    wrap.append(line);
  }
  for (const field of node.fields || []) {
    wrap.append(fieldRow(node, field, model));
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

function fieldRow(node, field, model) {
  const row = document.createElement("label");
  row.className = "field-row";
  const name = document.createElement("span");
  name.textContent = field.edited ? `${field.name} · edited` : field.name;
  row.append(name);
  if (!field.editable) {
    const value = document.createElement("span");
    value.className = "muted";
    value.textContent = String(field.value ?? "");
    row.append(value);
    return row;
  }
  const commit = (value) => model.onField && model.onField(node, field, value);
  if (field.type === "bool") {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(field.value);
    input.addEventListener("change", () => commit(input.checked));
    row.append(input);
    return row;
  }
  if (field.type === "enum" && (field.options || []).length) {
    const select = document.createElement("select");
    for (const option of field.options) {
      const item = document.createElement("option");
      item.value = String(option);
      item.textContent = String(option);
      if (String(option) === String(field.value)) item.selected = true;
      select.append(item);
    }
    select.addEventListener("change", () => commit(select.value));
    row.append(select);
    return row;
  }
  const input = document.createElement("input");
  input.type = field.type === "int" || field.type === "float" ? "number" : "text";
  if (field.type === "float") input.step = "any";
  input.value = field.value == null ? "" : String(field.value);
  if (field.type === "flag") {
    input.setAttribute("list", "flag-names");
    input.placeholder = "Flag name";
    input.addEventListener("focus", () => model.onFlags && model.onFlags(input));
  }
  input.addEventListener("change", () => commit(input.value));
  row.append(input);
  return row;
}

function diffList(graph, model) {
  const wrap = document.createElement("div");
  wrap.className = "behavior-diff";
  const title = document.createElement("div");
  title.className = "axis-label";
  title.textContent = "Edits";
  wrap.append(title);
  for (const op of graph.diff || []) {
    const row = document.createElement("p");
    const node = (graph.nodes || []).find((item) => item.id === op.id);
    row.textContent = `${node ? node.name : "Node"} · ${op.field} = ${op.value}`;
    if (op.missing) row.textContent += " (node is gone)";
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Revert";
    button.addEventListener("click", () => model.onRevert && model.onRevert(op));
    row.append(button);
    wrap.append(row);
  }
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
