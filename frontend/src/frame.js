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
 * Always-visible viewport label. The backend includes a WebGPU fallback reason
 * when one was recorded. `build` is the page's short git revision.
 * Queued, loading, and background counts appear only while they are non-zero.
 */
export function formatStatusLabel({
  backend = "",
  build = "",
  draws = 0,
  queued = 0,
  loading = 0,
  background = 0,
  layersOff = null,
} = {}) {
  const parts = [];
  if (backend) parts.push(backend);
  if (build) parts.push(build);
  parts.push(`${Number(draws) || 0} draws`);
  if (queued) parts.push(`${queued} queued`);
  if (loading) parts.push(`${loading} loading`);
  if (background) parts.push(`${background} background`);
  if (layersOff && layersOff.length) parts.push(`off: ${layersOff.join(", ")}`);
  return parts.join(" · ");
}

/**
 * The rest of the old stats line, shown inside the Profile HUD.
 * A frame that will not be followed by another one is `idle`.
 */
export function formatProfileDetail({
  rendering = false,
  fps = 0,
  instances = null,
  culled = null,
  separate = null,
  queued = null,
  loading = null,
  loaded = null,
  deferred = 0,
  backgroundLoading = 0,
  convertMs = null,
  textureMs = null,
  downloadMs = null,
  uploadMs = null,
  warmDone = null,
  warmTotal = null,
  sceneStream = null,
  server = "",
} = {}) {
  const lines = [];
  lines.push(rendering ? (fps ? `${fps} fps` : "…") : "idle");
  const counts = [];
  if (instances != null) counts.push(`${instances} inst`);
  if (culled != null) counts.push(`${culled} culled`);
  if (separate != null) counts.push(`${separate} separate`);
  if (counts.length) lines.push(counts.join(" · "));
  const stream = [];
  if (queued != null) stream.push(`${queued} queued`);
  if (loading != null) stream.push(`${loading} loading`);
  if (loaded != null) stream.push(`${loaded} loaded`);
  const background = (Number(deferred) || 0) + (Number(backgroundLoading) || 0);
  if (background) {
    stream.push(`${background} background`);
    if (backgroundLoading) stream.push(`${backgroundLoading} background loading`);
  }
  if (stream.length) lines.push(stream.join(" · "));
  const times = [];
  if (convertMs != null) times.push(`conv ${formatMs(convertMs)} ms`);
  if (textureMs != null) times.push(`tex ${formatMs(textureMs)} ms`);
  if (downloadMs != null) times.push(`dl ${formatMs(downloadMs)} ms`);
  if (uploadMs != null) times.push(`up ${formatMs(uploadMs)} ms`);
  if (times.length) lines.push(times.join(" · "));
  if (warmTotal != null) lines.push(`warm ${warmDone || 0}/${warmTotal}`);
  if (sceneStream) {
    const note = [];
    if (sceneStream.firstDetail != null) note.push(`detail ${formatMs(sceneStream.firstDetail)} ms`);
    note.push(`${sceneStream.slowFrames} streaming frames >25 ms`);
    if (sceneStream.editBytes != null) note.push(`first edit ${formatMs(sceneStream.editBytes / 1048576)} MiB`);
    lines.push(note.join(" · "));
  }
  if (server) lines.push(server);
  return lines;
}
