/**
 * Pick order for the viewport.
 *
 * Solids (real meshes and ordinary boxes) win over volumes. A volume joins
 * the list when the click is on its edge, and it is the selection only when
 * the ray hits nothing else. Alt-click, or a second click on the same stack,
 * walks to the next hit.
 */
export function chooseHit({ solids = [], volumes = [], edges = [], selected = null, alt = false, repeat = false } = {}) {
  const list = [];
  for (const id of solids) push(list, id);
  const extra = edges.length ? edges : solids.length ? [] : volumes;
  for (const id of extra) push(list, id);
  if (!list.length) return null;
  if ((alt || repeat) && selected && list.includes(selected) && list.length > 1) {
    return list[(list.indexOf(selected) + 1) % list.length];
  }
  return list[0];
}

function push(list, id) {
  if (id && !list.includes(id)) list.push(id);
}
