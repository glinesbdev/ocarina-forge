/**
 * Multi-select and decoration membership. Shift toggles who is selected.
 * Ctrl (or Command) toggles whether a decoration follows the selected parent.
 * A marquee keeps every actor whose screen box meets the rectangle.
 */

import { isDecoration, isParentPiece } from "./decoration-groups.js";

export function toggleId(ids, id) {
  const next = [];
  let seen = false;
  for (const item of ids || []) {
    if (item === id) {
      seen = true;
      continue;
    }
    if (!next.includes(item)) next.push(item);
  }
  if (!seen && id) next.push(id);
  return next;
}

export function normalizeRect(x0, y0, x1, y1) {
  return {
    minX: Math.min(x0, x1),
    minY: Math.min(y0, y1),
    maxX: Math.max(x0, x1),
    maxY: Math.max(y0, y1),
  };
}

export function rectHitsBox(rect, box) {
  if (!rect || !box) return false;
  return box.maxX >= rect.minX && box.minX <= rect.maxX && box.maxY >= rect.minY && box.minY <= rect.maxY;
}

/**
 * @param {{ id: string, box: { minX: number, minY: number, maxX: number, maxY: number } | null }[]} items
 */
export function marqueeIds(items, rect, { additive = false, existing = [] } = {}) {
  const hits = [];
  for (const item of items || []) {
    if (item && item.id && rectHitsBox(rect, item.box)) hits.push(item.id);
  }
  if (!additive) return hits;
  const next = [];
  for (const id of existing || []) if (!next.includes(id)) next.push(id);
  for (const id of hits) if (!next.includes(id)) next.push(id);
  return next;
}

/**
 * Ctrl-click a decoration while a parent piece is the primary selection.
 * Returns the parent to store, or null to detach. Returns undefined when
 * this click is not a membership toggle.
 */
export function groupToggle(primary, clicked, resolved) {
  if (!primary || !clicked || primary.id === clicked.id) return undefined;
  if (!isDecoration(clicked) || !isParentPiece(primary, true)) return undefined;
  const current = resolved && resolved.childOf && resolved.childOf.get(clicked.id);
  return { childId: clicked.id, parentId: current === primary.id ? null : primary.id };
}
