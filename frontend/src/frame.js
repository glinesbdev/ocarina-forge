/** Whether the view should schedule another frame. An idle editor does not redraw. */
export function keepRendering({ flying, damped, looking, dragging }) {
  return Boolean(flying || damped || looking || dragging);
}

export function formatTris(tris) {
  const count = Number(tris) || 0;
  if (count >= 1000000) return `${Math.round(count / 100000) / 10}M`;
  if (count >= 1000) return `${Math.round(count / 100) / 10}k`;
  return String(count);
}

function formatMs(value) {
  const rounded = Math.round(Number(value) * 10) / 10;
  return String(rounded);
}

/**
 * Stats line. `instances` and `culled` are omitted until that pass records them.
 * A frame that will not be followed by another one is `idle`, even if fps was just measured.
 * `backend` and `frameMs` are included when the caller has them.
 * `build` is the page's short git revision, when the caller has one.
 */
export function formatStats({
  rendering,
  fps,
  frameMs,
  backend,
  draws,
  instances,
  culled,
  separate,
  tris,
  queued,
  loading,
  loaded,
  deferred,
  backgroundLoading,
  convertMs,
  textureMs,
  downloadMs,
  uploadMs,
  warmDone,
  warmTotal,
  sceneStream,
  build,
}) {
  const rate = rendering ? (fps ? `${fps} fps` : "…") : "idle";
  const parts = [];
  if (backend) parts.push(backend);
  parts.push(rate);
  if (frameMs != null) parts.push(`${formatMs(frameMs)} ms`);
  parts.push(`${draws} draws`);
  if (instances != null) parts.push(`${instances} inst`);
  if (culled != null) parts.push(`${culled} culled`);
  parts.push(`${separate} separate`, `${formatTris(tris)} tris`);
  if (queued != null) parts.push(`${queued} queued`);
  if (loading != null) parts.push(`${loading} loading`);
  if (loaded != null) parts.push(`${loaded} loaded`);
  if (deferred || backgroundLoading) {
    const remaining = (deferred || 0) + (backgroundLoading || 0);
    parts.push(`${remaining} background remaining`);
    if (backgroundLoading) parts.push(`${backgroundLoading} background loading`);
  }
  if (convertMs != null) parts.push(`conv ${formatMs(convertMs)} ms`);
  if (textureMs != null) parts.push(`tex ${formatMs(textureMs)} ms`);
  if (downloadMs != null) parts.push(`dl ${formatMs(downloadMs)} ms`);
  if (uploadMs != null) parts.push(`up ${formatMs(uploadMs)} ms`);
  if (warmTotal != null) parts.push(`warm ${warmDone || 0}/${warmTotal}`);
  if (sceneStream) {
    if (sceneStream.firstDetail != null) parts.push(`detail ${formatMs(sceneStream.firstDetail)} ms`);
    parts.push(`${sceneStream.slowFrames} streaming frames >25 ms`);
    if (sceneStream.editBytes != null) parts.push(`first edit ${formatMs(sceneStream.editBytes / 1048576)} MiB`);
  }
  if (build) parts.push(build);
  return parts.join(" · ");
}
