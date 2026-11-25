/**
 * Actors panel reveal. A viewport pick scrolls the matching row into view.
 * The row is centered when it sits outside the panel, and a collapsed category
 * opens first. Search that hides the row leaves the query alone and asks for
 * a hint. A click inside the panel does not scroll.
 *
 * Lists past OUTLINER_VIRTUAL_AT rows are windowed. Those scroll by index
 * (row height × position) so a long scene never measures every row.
 */

export const OUTLINER_ACTOR_HEIGHT = 64;
export const OUTLINER_GROUP_HEIGHT = 32;
export const OUTLINER_VIRTUAL_AT = 400;

export function outlinerGroupId(actor) {
  return (actor && actor.category) || "other";
}

export function actorMatchesQuery(actor, query) {
  const needle = String(query || "").trim().toLowerCase();
  if (!needle) return true;
  if (!actor) return false;
  const hay = `${actor.gyaml || ""} ${actor.name || ""} ${actor.id || ""} ${actor.category || ""}`.toLowerCase();
  return hay.includes(needle);
}

export function revealHint(actor, query) {
  if (!actor || actorMatchesQuery(actor, query)) return "";
  const name = actor.name || actor.gyaml || "This actor";
  return `Search is hiding ${name}.`;
}

function groupLabel(groups, id) {
  for (const entry of groups || []) {
    if (Array.isArray(entry) && entry[0] === id) return entry[1] || id;
    if (entry && entry.id === id) return entry.label || id;
  }
  return id;
}

/**
 * Category sections in filter order, then any category the filter list does
 * not name. Actors keep scene order inside a category. A collapsed section
 * keeps its header and omits its rows. An empty search match omits the section.
 */
export function buildOutlinerRows({ actors = [], query = "", groups = [], collapsed = null } = {}) {
  const list = Array.isArray(actors) ? actors : [];
  const buckets = new Map();
  for (const actor of list) {
    if (!actor) continue;
    const id = outlinerGroupId(actor);
    if (!buckets.has(id)) buckets.set(id, []);
    buckets.get(id).push(actor);
  }
  const seen = new Set();
  const order = [];
  for (const entry of groups || []) {
    const id = Array.isArray(entry) ? entry[0] : entry && entry.id;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    order.push(id);
  }
  for (const id of buckets.keys()) {
    if (seen.has(id)) continue;
    seen.add(id);
    order.push(id);
  }
  const rows = [];
  for (const id of order) {
    const members = buckets.get(id);
    if (!members || !members.length) continue;
    const matching = members.filter((actor) => actorMatchesQuery(actor, query));
    if (!matching.length) continue;
    const shut = Boolean(collapsed && collapsed[id]);
    rows.push({
      kind: "group",
      id,
      label: groupLabel(groups, id),
      count: matching.length,
      collapsed: shut,
    });
    if (shut) continue;
    for (const actor of matching) {
      rows.push({ kind: "actor", id: actor.id, groupId: id, actor });
    }
  }
  return rows;
}

function heightOf(row, actorHeight, groupHeight) {
  return row && row.kind === "group" ? groupHeight : actorHeight;
}

export function rowOffset(rows, index, { actorHeight = OUTLINER_ACTOR_HEIGHT, groupHeight = OUTLINER_GROUP_HEIGHT } = {}) {
  const stop = Math.max(0, Math.min(index, Array.isArray(rows) ? rows.length : 0));
  let y = 0;
  for (let i = 0; i < stop; i += 1) y += heightOf(rows[i], actorHeight, groupHeight);
  return y;
}

export function contentHeight(rows, sizes) {
  return rowOffset(rows, Array.isArray(rows) ? rows.length : 0, sizes);
}

/** Scroll offset that centers `rowTop`, or null when the row is already fully visible. */
export function centeredScrollTop({ scrollTop = 0, clientHeight = 0, rowTop = 0, rowHeight = 0 } = {}) {
  if (!(clientHeight > 0) || !(rowHeight >= 0)) return null;
  const rowBottom = rowTop + rowHeight;
  const viewBottom = scrollTop + clientHeight;
  if (rowTop >= scrollTop - 0.5 && rowBottom <= viewBottom + 0.5) return null;
  if (rowHeight >= clientHeight) return Math.max(0, rowTop);
  return Math.max(0, rowTop - (clientHeight - rowHeight) / 2);
}

export function measuredScrollTop(list, row) {
  if (!list || !row) return null;
  if (typeof list.getBoundingClientRect !== "function" || typeof row.getBoundingClientRect !== "function") return null;
  const listRect = list.getBoundingClientRect();
  const rowRect = row.getBoundingClientRect();
  const rowTop = (list.scrollTop || 0) + (rowRect.top - listRect.top);
  return centeredScrollTop({
    scrollTop: list.scrollTop || 0,
    clientHeight: list.clientHeight || listRect.height || 0,
    rowTop,
    rowHeight: rowRect.height || 0,
  });
}

export function shouldVirtualize(rows, virtualizeAt = OUTLINER_VIRTUAL_AT) {
  return Array.isArray(rows) && rows.length > virtualizeAt;
}

export function planViewportReveal({
  actors = [],
  selectedId = null,
  source = "",
  query = "",
  groups = [],
  collapsed = null,
  scrollTop = 0,
  clientHeight = 0,
  actorHeight = OUTLINER_ACTOR_HEIGHT,
  groupHeight = OUTLINER_GROUP_HEIGHT,
  virtualizeAt = OUTLINER_VIRTUAL_AT,
  behavior = "smooth",
} = {}) {
  if (source !== "viewport" || selectedId == null || selectedId === "") return { action: "none" };
  const actor = (Array.isArray(actors) ? actors : []).find((item) => item && item.id === selectedId);
  if (!actor) return { action: "none" };
  const hint = revealHint(actor, query);
  if (hint) return { action: "hint", hint, expand: [], actorId: selectedId };
  const groupId = outlinerGroupId(actor);
  const expand = collapsed && collapsed[groupId] ? [groupId] : [];
  const nextCollapsed = { ...(collapsed || {}) };
  for (const id of expand) delete nextCollapsed[id];
  const rows = buildOutlinerRows({ actors, query, groups, collapsed: nextCollapsed });
  const index = rows.findIndex((row) => row.kind === "actor" && row.id === selectedId);
  if (index < 0) {
    return {
      action: "hint",
      hint: hint || `${actor.name || actor.gyaml || "This actor"} is not in the list.`,
      expand: [],
      actorId: selectedId,
    };
  }
  const virtual = shouldVirtualize(rows, virtualizeAt);
  const top = virtual
    ? virtualRevealTop(rows, index, { scrollTop, clientHeight, actorHeight, groupHeight })
    : null;
  return {
    action: "reveal",
    index,
    actorId: selectedId,
    expand,
    rows,
    virtual,
    scrollTop: top,
    measure: !virtual,
    behavior,
    block: "center",
  };
}

/** Index-based scroll target for a windowed list. Null when that row is already fully visible. */
export function virtualRevealTop(rows, index, {
  scrollTop = 0,
  clientHeight = 0,
  actorHeight = OUTLINER_ACTOR_HEIGHT,
  groupHeight = OUTLINER_GROUP_HEIGHT,
} = {}) {
  if (!Array.isArray(rows) || index == null || index < 0 || index >= rows.length) return null;
  return centeredScrollTop({
    scrollTop,
    clientHeight,
    rowTop: rowOffset(rows, index, { actorHeight, groupHeight }),
    rowHeight: heightOf(rows[index], actorHeight, groupHeight),
  });
}

/** Window of rows to paint. `top` is the content offset used as the scroll index. */
export function virtualSlice(rows, {
  scrollTop = 0,
  clientHeight = 0,
  actorHeight = OUTLINER_ACTOR_HEIGHT,
  groupHeight = OUTLINER_GROUP_HEIGHT,
  overscan = 8,
} = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const sizes = { actorHeight, groupHeight };
  const total = contentHeight(list, sizes);
  const view = clientHeight > 0 ? clientHeight : actorHeight * 12;
  const slack = Math.max(0, overscan) * actorHeight;
  const startPx = Math.max(0, scrollTop - slack);
  const endPx = scrollTop + view + slack;
  const items = [];
  let y = 0;
  for (let index = 0; index < list.length; index += 1) {
    const height = heightOf(list[index], actorHeight, groupHeight);
    const next = y + height;
    if (next > startPx && y < endPx) items.push({ index, top: y, height, row: list[index] });
    y = next;
    if (y > endPx) break;
  }
  return { total, items };
}

export function applyScrollTop(list, top, behavior = "smooth") {
  if (!list || top == null || !Number.isFinite(top)) return false;
  const current = list.scrollTop || 0;
  if (Math.abs(current - top) < 1) return false;
  if (typeof list.scrollTo === "function") list.scrollTo({ top, behavior });
  else list.scrollTop = top;
  return true;
}

/**
 * Virtual lists scroll by the planned index. Flat lists measure the one row.
 * Anything other than a viewport reveal leaves the scroll position alone.
 */
export function followSelection(list, plan, row) {
  if (!list || !plan || plan.action !== "reveal") return "none";
  const behavior = plan.behavior || "smooth";
  if (plan.virtual) return applyScrollTop(list, plan.scrollTop, behavior) ? "index" : "stay";
  if (!plan.measure) return "stay";
  return applyScrollTop(list, measuredScrollTop(list, row), behavior) ? "measured" : "stay";
}
