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
  capped = 0,
  texturesDone = null,
  texturesTotal = null,
  texturesHidden = false,
  layersOff = null,
  memory = null,
} = {}) {
  const parts = [];
  if (backend) parts.push(backend);
  if (build) parts.push(build);
  parts.push(`${Number(draws) || 0} draws`);
  if (queued) parts.push(`${queued} queued`);
  if (loading) parts.push(`${loading} loading`);
  if (background) parts.push(`${background} background`);
  if (capped) parts.push(`${capped} capped`);
  const texTotal = Number(texturesTotal) || 0;
  const texDone = Number(texturesDone) || 0;
  if (texTotal > 0 && texDone < texTotal) parts.push(`textures ${texDone}/${texTotal}`);
  if (texturesHidden) parts.push("textures hidden");
  if (layersOff && layersOff.length) parts.push(`off: ${layersOff.join(", ")}`);
  if (memory) parts.push(formatMemoryNote(memory));
  return parts.join(" · ");
}

/** renderer.info.memory plus the JS heap, for the status line and the unload log. */
export function formatMemoryNote({ geometries = null, textures = null, heapBytes = null } = {}) {
  const geom = geometries == null ? "?" : String(Number(geometries) || 0);
  const tex = textures == null ? "?" : String(Number(textures) || 0);
  const heap = heapBytes == null ? "n/a" : `${Math.round(Number(heapBytes) / 1048576)}MB`;
  return `gpu ${geom} geom / ${tex} tex · heap ${heap}`;
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
  capped = 0,
  convertMs = null,
  textureMs = null,
  downloadMs = null,
  uploadMs = null,
  warmDone = null,
  warmTotal = null,
  sceneStream = null,
  server = "",
  batchKeys = null,
  pickUploads = null,
  keyReasons = null,
  gpuFrame = null,
  bytes = null,
} = {}) {
  const lines = [];
  lines.push(rendering ? (fps ? `${fps} fps` : "…") : "idle");
  const counts = [];
  if (instances != null) counts.push(`${instances} inst`);
  if (culled != null) counts.push(`${culled} culled`);
  if (separate != null) counts.push(`${separate} separate`);
  if (counts.length) lines.push(counts.join(" · "));
  const stream = [];
  if (queued != null && !(Number(queued) === 0 && Number(capped) > 0)) stream.push(`${queued} queued`);
  if (loading != null) stream.push(`${loading} loading`);
  if (loaded != null) stream.push(`${loaded} loaded`);
  if (capped) stream.push(`${capped} capped`);
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
  if (batchKeys != null || pickUploads != null) {
    const extra = [];
    if (batchKeys != null) extra.push(`keys ${batchKeys}`);
    if (pickUploads != null) extra.push(`pick ${pickUploads}`);
    lines.push(extra.join(" · "));
  }
  if (keyReasons && typeof keyReasons === "object") {
    const reasons = [];
    for (const name of ["array", "image", "blend", "helper", "water", "terrain", "ground", "tint"]) {
      if (keyReasons[name]) reasons.push(`${name} ${keyReasons[name]}`);
    }
    if (reasons.length) lines.push(reasons.join(" · "));
  }
  if (warmTotal != null) lines.push(`warm ${warmDone || 0}/${warmTotal}`);
  if (sceneStream) {
    const note = [];
    if (sceneStream.firstDetail != null) note.push(`detail ${formatMs(sceneStream.firstDetail)} ms`);
    note.push(`${sceneStream.slowFrames} streaming frames >25 ms`);
    if (sceneStream.maxCompileMs > 0) note.push(`compile ${formatMs(sceneStream.maxCompileMs)} ms`);
    if (sceneStream.editBytes != null) note.push(`first edit ${formatMs(sceneStream.editBytes / 1048576)} MiB`);
    lines.push(note.join(" · "));
  }
  if (gpuFrame) {
    lines.push(
      `gpu draws ${gpuFrame.drawCalls} · writeTex ${gpuFrame.writeTexture} · pipelines ${gpuFrame.pipelines} · versions ${gpuFrame.versions} · binds ${gpuFrame.bindGroups}`,
    );
  }
  if (bytes) {
    lines.push(
      `geom ${bytes.geometryMB} MB · bc7 ${bytes.bc7MB} MB · cpu array ${bytes.cpuArrayMB} MB · cpu 2d ${bytes.cpu2dMB} MB · cpu packed ${bytes.cpuPackedMB} MB · layers ${bytes.usedLayers}/${bytes.allocLayers}`,
    );
  }
  if (server) lines.push(server);
  return lines;
}
