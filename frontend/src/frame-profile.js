/**
 * Rolling frame profile: CPU split, GPU time, draw breakdown, p95.
 * The viewport feeds one sample per drawn frame while the HUD is open.
 * GPU samples stay null until a timestamp readback lands, and averages skip those.
 */

import { formatProfileDetail, formatTris } from "./frame.js";
import { arrayTexturesOf } from "./texture-array.js";

export const PROFILE_WINDOW = 180;
export const PROFILE_READY_FRAMES = 12;
export const CULL_HEAVY_MS = 5;
export const CULL_HEAVY_SHARE = 0.35;
export const CPU_OVER_GPU = 1.5;
export const OBJECTS_HEAVY = 100;

const FIELDS = [
  "cpuMs", "updateMs", "cullMs", "submitMs", "gpuMs", "frameMs",
  "objects", "batches", "subDraws", "draws", "triangles",
  "meshes", "textures", "heapBytes",
  "batchKeys", "pickUploads",
  "gpuDraws", "writeTex", "pipelines", "versions", "binds", "bundleRecords",
  "geomBytes", "bc7Bytes",
];

export function average(values) {
  if (!values.length) return null;
  let sum = 0;
  for (const value of values) sum += value;
  return sum / values.length;
}

/** Nearest rank. The 95th percentile of 10 sorted values is the last one. */
export function percentile(values, p) {
  if (!values.length) return null;
  const sorted = values.slice().sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length) - 1;
  const index = Math.max(0, Math.min(sorted.length - 1, rank));
  return sorted[index];
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function summarize(samples) {
  const summary = { frames: samples.length };
  for (const field of FIELDS) {
    const values = [];
    for (const sample of samples) {
      const value = finite(sample && sample[field]);
      if (value != null) values.push(value);
    }
    summary[field] = {
      avg: average(values),
      p95: percentile(values, 95),
      n: values.length,
    };
  }
  return summary;
}

export function createProfileWindow(limit = PROFILE_WINDOW) {
  const samples = [];
  return {
    push(sample) {
      samples.push(sample);
      while (samples.length > limit) samples.shift();
    },
    clear() {
      samples.length = 0;
    },
    samples() {
      return samples;
    },
    latest() {
      return samples.length ? samples[samples.length - 1] : null;
    },
    summary() {
      return summarize(samples);
    },
  };
}

function statAvg(stat) {
  if (!stat || !stat.n || stat.avg == null) return null;
  return stat.avg;
}

/**
 * Pick the next performance step from rolling averages.
 * `gpuState` is "ready", "pending", "unsupported", or "disjoint".
 */
export function recommendNext(summary, { gpuState = "ready" } = {}) {
  const frames = summary.frames || 0;
  const cpu = statAvg(summary.cpuMs);
  const gpu = statAvg(summary.gpuMs);
  const cull = statAvg(summary.cullMs);
  const objects = statAvg(summary.objects) || 0;
  const batches = statAvg(summary.batches) || 0;
  let body;
  if (gpuState === "unsupported" || gpuState === "disjoint") {
    body = "GPU timestamps are unavailable on this adapter. A large cull share of the CPU time points at spatial chunks. Hundreds of objects plus batches point at fewer render objects (texture arrays).";
  } else if (gpu == null) {
    body = "Waiting for the first GPU timestamp. The CPU lines are already live.";
  } else if (cpu != null && gpu > cpu && gpu >= cpu * CPU_OVER_GPU) {
    body = "GPU time is above CPU time. Next, look at overdraw, alpha-tested foliage, far meshes, and far proxies.";
  } else if (cpu != null && cull != null && cull >= CULL_HEAVY_MS && cull >= cpu * CULL_HEAVY_SHARE) {
    body = "Cull time is a large share of the CPU frame. Next is spatial chunk batching.";
  } else if (cpu != null && cpu > gpu && cpu >= gpu * CPU_OVER_GPU && objects + batches >= OBJECTS_HEAVY) {
    body = "CPU time is above GPU time and render objects are in the hundreds. Next is fewer render objects (texture arrays).";
  } else {
    body = "CPU and GPU are close. Orbit a little longer, then compare cull with the object count.";
  }
  if (frames < PROFILE_READY_FRAMES) {
    return `Early read (${frames} frames). Orbit or fly so the window fills. ${body}`;
  }
  return body;
}

function fmtMs(value) {
  if (value == null || !Number.isFinite(Number(value))) return "n/a";
  return (Math.round(Number(value) * 10) / 10).toFixed(1);
}

function fmtInt(value) {
  if (value == null || !Number.isFinite(Number(value))) return "n/a";
  return String(Math.round(Number(value)));
}

function fmtHeap(bytes) {
  if (bytes == null || !Number.isFinite(Number(bytes))) return "n/a";
  return `${(Math.round((Number(bytes) / 1048576) * 10) / 10).toFixed(1)} MB`;
}

function lineMs(label, latest, stat) {
  const now = latest == null || !Number.isFinite(Number(latest)) ? "n/a" : `${fmtMs(latest)} ms`;
  if (!stat || !stat.n || stat.avg == null) return `${label} ${now}`;
  return `${label} ${now} · avg ${fmtMs(stat.avg)} · p95 ${fmtMs(stat.p95)}`;
}

function gpuLine(gpuState, latest, stat) {
  if (gpuState === "unsupported" || gpuState === "disjoint") return "gpu n/a";
  if (latest == null || !Number.isFinite(Number(latest))) return "gpu …";
  return lineMs("gpu", latest, stat);
}

const TIMING_LABEL = {
  webgpu: "timestamp-query",
  webgl: "disjoint-timer-query",
  none: "timestamps unsupported",
};

export function formatProfileHud({
  backend = "",
  revision = "",
  mode = "none",
  gpuState = "unsupported",
  latest = {},
  summary,
  recommendation = "",
  detail = null,
} = {}) {
  const stats = summary || summarize([]);
  const timing = TIMING_LABEL[mode] || TIMING_LABEL.none;
  const name = backend || "renderer";
  const lines = [
    `profile · ${name} · ${timing} · ${stats.frames} frames`,
  ];
  if (revision) lines.push(revision);
  lines.push(
    lineMs("cpu", latest.cpuMs, stats.cpuMs),
    `update ${fmtMs(latest.updateMs)} · cull ${fmtMs(latest.cullMs)} · submit ${fmtMs(latest.submitMs)}`,
    gpuLine(gpuState, latest.gpuMs, stats.gpuMs),
    lineMs("frame", latest.frameMs, stats.frameMs),
    drawLine(latest),
    `tris ${formatTris(latest.triangles)} · meshes ${fmtInt(latest.meshes)} · tex ${fmtInt(latest.textures)} · heap ${fmtHeap(latest.heapBytes)}`,
    gpuCounterLine(latest),
    memorySplitLine(latest),
  );
  if (detail) lines.push(...formatProfileDetail(detail));
  if (recommendation) lines.push(recommendation);
  return lines.filter((line) => line).join("\n");
}

function gpuCounterLine(latest) {
  if (latest.gpuDraws == null && latest.writeTex == null && latest.bundleRecords == null) return "";
  const parts = [
    `gpu draws ${fmtInt(latest.gpuDraws)}`,
    `writeTex ${fmtInt(latest.writeTex)}`,
    `pipelines ${fmtInt(latest.pipelines)}`,
    `versions ${fmtInt(latest.versions)}`,
    `binds ${fmtInt(latest.binds)}`,
    `bundles ${fmtInt(latest.bundleRecords)}`,
  ];
  return parts.join(" · ");
}

function memorySplitLine(latest) {
  if (latest.geomBytes == null && latest.bc7Bytes == null) return "";
  return `geom ${fmtHeap(latest.geomBytes)} · bc7 ${fmtHeap(latest.bc7Bytes)}`;
}

function drawLine(latest) {
  let line = `obj ${fmtInt(latest.objects)} · batches ${fmtInt(latest.batches)} · sub ${fmtInt(latest.subDraws)} · draws ${fmtInt(latest.draws)}`;
  if (latest.batchKeys == null && latest.pickUploads == null) return line;
  const extra = [];
  if (latest.batchKeys != null) extra.push(`keys ${fmtInt(latest.batchKeys)}`);
  if (latest.pickUploads != null) extra.push(`pick ${fmtInt(latest.pickUploads)}`);
  if (extra.length) line += ` · ${extra.join(" · ")}`;
  return line;
}

function textureId(value) {
  if (!value || value.isTexture !== true) return null;
  return (value.source && value.source.uuid) || value.uuid || value.id || null;
}

/**
 * Live draw textures. Packed source images listed on the material are not
 * counted: the array that replaced them is the texture the batch samples.
 * Non-enumerable array and layer fields are included.
 */
export function countTextures(materials) {
  const seen = new Set();
  const add = (value) => {
    const id = textureId(value);
    if (id != null) seen.add(id);
  };
  for (const material of materials || []) {
    if (!material) continue;
    const bags = [material, material.userData];
    for (const bag of bags) {
      if (!bag) continue;
      for (const value of Object.values(bag)) add(value);
    }
    for (const value of arrayTexturesOf(material)) add(value);
  }
  return seen.size;
}

const KEY_REASON_ORDER = ["array", "image", "blend", "helper", "water", "terrain", "ground", "tint"];

/** Why a batch key did not join another mesh. Overflow suffixes are ignored. */
export function classifyBatchKey(key) {
  const raw = String(key || "").split("\u0000")[0];
  if (raw.includes("|h:")) return "helper";
  if (raw.includes("|water")) return "water";
  if (raw.includes("|terrain")) return "terrain";
  if (raw.includes("|ground")) return "ground";
  if (raw.includes("|moss:")) return "blend";
  if (!raw.includes("arr:")) return "image";
  const tint = raw.match(/\|c([0-9a-f]+)\|/i);
  if (tint && tint[1].toLowerCase() !== "ffffff") return "tint";
  return "array";
}

export function summarizeBatchKeys(keys) {
  const counts = { array: 0, image: 0, blend: 0, helper: 0, water: 0, terrain: 0, ground: 0, tint: 0 };
  const seen = new Set();
  for (const key of keys || []) {
    if (key == null || key === false) continue;
    const raw = String(key).split("\u0000")[0];
    if (!raw || seen.has(raw)) continue;
    seen.add(raw);
    const reason = classifyBatchKey(raw);
    if (counts[reason] == null) counts[reason] = 0;
    counts[reason] += 1;
  }
  return counts;
}

export function formatKeyReasons(counts) {
  if (!counts || typeof counts !== "object") return "";
  const parts = [];
  for (const name of KEY_REASON_ORDER) {
    if (counts[name]) parts.push(`${name} ${counts[name]}`);
  }
  return parts.join(" · ");
}

export function buildCapture({
  backend = "",
  revision = "",
  gpuMode = "none",
  gpuState = "unsupported",
  samples = [],
  server = null,
  keyReasons = null,
  imagePack = null,
  at = new Date().toISOString(),
} = {}) {
  const frames = samples.map((sample) => ({ ...sample }));
  const summary = summarize(frames);
  return {
    version: 1,
    capturedAt: at,
    backend,
    revision,
    gpuTiming: gpuMode,
    gpuState,
    frames,
    summary,
    recommendation: recommendNext(summary, { gpuState }),
    server,
    keyReasons,
    imagePack,
  };
}
