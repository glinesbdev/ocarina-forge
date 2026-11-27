/**
 * Fixed flythrough used by the Field benchmark. The points are world XZ
 * metres on Hyrule Field Child. Eye height sits above the field. The path
 * is closed and does not depend on what has streamed in.
 *
 * Eight sides are 160 m. The last point returns to the origin on the diagonal.
 */

export const FIELD_FLIGHT = {
  name: "hyrule-field-child",
  eyeY: 190,
  lookY: 150,
  lookAhead: 48,
  metersPerSecond: 90,
  points: [
    [0, 0],
    [160, 0],
    [160, 160],
    [0, 160],
    [-160, 160],
    [-160, 0],
    [-160, -160],
    [0, -160],
    [160, -160],
    [0, 0],
  ],
};

export function streamSettled({ queued = 0, loading = 0, background = 0, pending = 0 } = {}) {
  return Number(queued) === 0 && Number(loading) === 0 && Number(background) === 0 && Number(pending) === 0;
}

export function createFlight(spec = FIELD_FLIGHT) {
  const points = spec.points;
  let seg = 0;
  let along = 0;
  let done = false;
  function pose(x, z, dx, dz, len) {
    const scale = len > 0 ? spec.lookAhead / len : 0;
    return {
      x,
      y: spec.eyeY,
      z,
      lookX: x + dx * scale,
      lookY: spec.lookY,
      lookZ: z + dz * scale,
      done,
    };
  }
  return {
    get done() {
      return done;
    },
    sample(dt) {
      const last = points[points.length - 1];
      if (done || points.length < 2) {
        done = true;
        const prev = points[Math.max(0, points.length - 2)] || last;
        return pose(last[0], last[1], last[0] - prev[0], last[1] - prev[1], Math.hypot(last[0] - prev[0], last[1] - prev[1]));
      }
      let remain = Math.max(0, Number(spec.metersPerSecond) * Math.max(0, Number(dt) || 0));
      while (remain > 0 && seg < points.length - 1) {
        const a = points[seg];
        const b = points[seg + 1];
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
        const left = len - along;
        if (remain < left) {
          along += remain;
          remain = 0;
        } else {
          remain -= left;
          seg += 1;
          along = 0;
        }
      }
      if (seg >= points.length - 1) {
        done = true;
        seg = points.length - 1;
        along = 0;
      }
      const a = points[Math.min(seg, points.length - 2)];
      const b = points[Math.min(seg + 1, points.length - 1)];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      const t = done ? 1 : along / len;
      return pose(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, b[0] - a[0], b[1] - a[1], len);
    },
  };
}

const SUMMARY_FIELDS = [
  "submitMs", "updateMs", "priorityMs", "labelsMs", "cullMs",
  "writeTex", "indirectFills", "lodFlips", "heapBytes", "draws", "batches",
  "cpuMs", "gpuMs", "flyMs", "textureMs", "pipelines", "versions", "binds",
  "geomBytes", "bc7Bytes", "objects",
];

function finite(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function average(values) {
  if (!values.length) return null;
  let sum = 0;
  for (const value of values) sum += value;
  return sum / values.length;
}

function percentile(values, p) {
  if (!values.length) return null;
  const sorted = values.slice().sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length) - 1;
  const index = Math.max(0, Math.min(sorted.length - 1, rank));
  return sorted[index];
}

/** avg/p95 for a benchmark capture. Null samples are left out of that field. */
export function summarizeBenchmark(samples) {
  const summary = { frames: samples.length };
  for (const field of SUMMARY_FIELDS) {
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
