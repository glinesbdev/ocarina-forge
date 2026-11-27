/**
 * Spatial cells for WebGPU render bundles.
 *
 * A cell is a 64 m column. Static batched scenery in that column is one
 * BundleGroup. The group is shown or hidden as a whole. three re-records the
 * GPU bundle only when the group's version changes, so a camera move that
 * only toggles visibility does not encode those draws again.
 *
 * Water, terrain, ground, and helpers stay on the normal path. A selected
 * actor is keyed loose so it is drawn outside the group. WebGL has no
 * beginBundle, and the caller keeps the ungrouped batch key.
 */

export const CELL_METERS = 64;

const CELL_MARK = "|cell:";
const LOOSE_MARK = "|loose:";

export function cellCoords(x, z, size = CELL_METERS) {
  const ix = Math.floor(Number(x) / size);
  const iz = Math.floor(Number(z) / size);
  return { ix, iz };
}

export function cellId(ix, iz) {
  return `${ix},${iz}`;
}

export function cellOfPosition(x, z, size = CELL_METERS) {
  const { ix, iz } = cellCoords(x, z, size);
  if (!Number.isFinite(ix) || !Number.isFinite(iz)) return null;
  return cellId(ix, iz);
}

/** Roles that must not be frozen into a bundle. */
export function bundleEligible(key) {
  const raw = String(key || "").split("\u0000")[0];
  if (!raw || raw.includes(LOOSE_MARK)) return false;
  if (raw.includes("|h:")) return false;
  if (raw.includes("|water")) return false;
  if (raw.includes("|terrain")) return false;
  if (raw.includes("|ground")) return false;
  return true;
}

export function materialBatchKey(key) {
  const raw = String(key || "").split("\u0000")[0];
  const cell = raw.indexOf(CELL_MARK);
  const loose = raw.indexOf(LOOSE_MARK);
  let cut = raw.length;
  if (cell >= 0) cut = Math.min(cut, cell);
  if (loose >= 0) cut = Math.min(cut, loose);
  return raw.slice(0, cut);
}

export function cellFromBatchKey(key) {
  const raw = String(key || "").split("\u0000")[0];
  const at = raw.lastIndexOf(CELL_MARK);
  if (at < 0) return null;
  const id = raw.slice(at + CELL_MARK.length);
  if (!/^-?\d+,-?\d+$/.test(id)) return null;
  return id;
}

/**
 * Group key for one instance. `loose` is the selected actor, drawn outside
 * every bundle. Ineligible keys are returned unchanged.
 */
export function groupedBatchKey(baseKey, { enabled = false, x = 0, z = 0, loose = false, actor = "" } = {}) {
  const raw = String(baseKey || "").split("\u0000")[0];
  if (!enabled || !bundleEligible(raw)) return raw;
  if (loose) return `${raw}${LOOSE_MARK}${actor || "selected"}`;
  const id = cellOfPosition(x, z);
  if (!id) return raw;
  return `${raw}${CELL_MARK}${id}`;
}

export function nominalCellBox(id, size = CELL_METERS) {
  const [ix, iz] = String(id).split(",").map(Number);
  const minX = ix * size;
  const minZ = iz * size;
  return {
    minX,
    maxX: minX + size,
    minY: -256,
    maxY: 1024,
    minZ,
    maxZ: minZ + size,
  };
}

export function emptyBox() {
  return {
    minX: Infinity,
    maxX: -Infinity,
    minY: Infinity,
    maxY: -Infinity,
    minZ: Infinity,
    maxZ: -Infinity,
  };
}

export function boxReady(box) {
  return Boolean(box) && box.minX <= box.maxX && box.minY <= box.maxY && box.minZ <= box.maxZ;
}

export function expandBox(box, x, y, z, radius = 0) {
  const r = Number.isFinite(radius) ? Math.max(radius, 0) : 0;
  const px = Number(x);
  const py = Number(y);
  const pz = Number(z);
  if (!Number.isFinite(px) || !Number.isFinite(py) || !Number.isFinite(pz)) return box;
  box.minX = Math.min(box.minX, px - r);
  box.maxX = Math.max(box.maxX, px + r);
  box.minY = Math.min(box.minY, py - r);
  box.maxY = Math.max(box.maxY, py + r);
  box.minZ = Math.min(box.minZ, pz - r);
  box.maxZ = Math.max(box.maxZ, pz + r);
  return box;
}

/**
 * three.js frustum test: a box is outside a plane when its positive vertex
 * is still behind that plane. `planes` are `{ nx, ny, nz, constant }`.
 */
export function aabbIntersectsFrustum(box, planes) {
  if (!boxReady(box) || !planes || !planes.length) return true;
  for (const plane of planes) {
    const nx = plane.nx;
    const ny = plane.ny;
    const nz = plane.nz;
    const px = nx > 0 ? box.maxX : box.minX;
    const py = ny > 0 ? box.maxY : box.minY;
    const pz = nz > 0 ? box.maxZ : box.minZ;
    if (nx * px + ny * py + nz * pz + plane.constant < 0) return false;
  }
  return true;
}

export function streamSettled({ queued = 0, loading = 0, background = 0, pending = 0 } = {}) {
  return Number(queued) === 0 && Number(loading) === 0 && Number(background) === 0 && Number(pending) === 0;
}

/**
 * Fixed flythrough of Hyrule Field Child. The points are world XZ metres.
 * Eye height sits above the field sample at y=147. The path is closed and
 * does not depend on what has streamed in.
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
  "cpuMs", "updateMs", "cullMs", "submitMs", "gpuMs",
  "gpuDraws", "draws", "bundleRecords", "writeTex", "pipelines", "versions", "binds",
  "heapBytes", "geomBytes", "bc7Bytes",
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
    const sum = values.reduce((total, value) => total + value, 0);
    summary[field] = {
      avg: average(values),
      p95: percentile(values, 95),
      n: values.length,
      sum: values.length ? sum : null,
    };
  }
  return summary;
}
