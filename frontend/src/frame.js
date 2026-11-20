/** Whether the view should schedule another frame. An idle editor does not redraw. */
export function keepRendering({ flying, damped, looking, dragging, loading }) {
  return Boolean(flying || damped || looking || dragging || loading);
}

export function formatTris(tris) {
  const count = Number(tris) || 0;
  if (count >= 1000000) return `${Math.round(count / 100000) / 10}M`;
  if (count >= 1000) return `${Math.round(count / 100) / 10}k`;
  return String(count);
}

/**
 * Stats line. `instances` and `culled` are omitted until that pass records them.
 * A frame that will not be followed by another one is `idle`, even if fps was just measured.
 */
export function formatStats({ rendering, fps, draws, instances, culled, separate, tris, queued, loading, loaded }) {
  const rate = rendering ? (fps ? `${fps} fps` : "…") : "idle";
  const parts = [rate, `${draws} draws`];
  if (instances != null) parts.push(`${instances} inst`);
  if (culled != null) parts.push(`${culled} culled`);
  parts.push(`${separate} separate`, `${formatTris(tris)} tris`);
  if (queued != null) parts.push(`${queued} queued`);
  if (loading != null) parts.push(`${loading} loading`);
  if (loaded != null) parts.push(`${loaded} loaded`);
  return parts.join(" · ");
}
