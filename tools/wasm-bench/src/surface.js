/** JS port of `ocarina_forge.world_surface.MeshIndex` and `triangle_height`. */

const EPS = 1e-12;
const BARY = 1e-7;

export function triangleHeight(x, z, tri, offset = 0) {
  const ax = tri[offset], ay = tri[offset + 1], az = tri[offset + 2];
  const bx = tri[offset + 3], by = tri[offset + 4], bz = tri[offset + 5];
  const cx = tri[offset + 6], cy = tri[offset + 7], cz = tri[offset + 8];
  const det = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz);
  if (Math.abs(det) < EPS) return NaN;
  const u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / det;
  const v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / det;
  const w = 1.0 - u - v;
  if (u < -BARY || v < -BARY || w < -BARY) return NaN;
  return u * ay + v * by + w * cy;
}

export class MeshIndex {
  constructor(step, triangles) {
    this.step = step || 32;
    this.triangles = triangles;
    this.cells = new Map();
    const count = Math.floor(triangles.length / 9);
    for (let id = 0; id < count; id++) {
      const o = id * 9;
      const x0 = Math.min(triangles[o], triangles[o + 3], triangles[o + 6]);
      const x1 = Math.max(triangles[o], triangles[o + 3], triangles[o + 6]);
      const z0 = Math.min(triangles[o + 2], triangles[o + 5], triangles[o + 8]);
      const z1 = Math.max(triangles[o + 2], triangles[o + 5], triangles[o + 8]);
      const ix0 = Math.floor(x0 / this.step);
      const ix1 = Math.floor(x1 / this.step);
      const iz0 = Math.floor(z0 / this.step);
      const iz1 = Math.floor(z1 / this.step);
      for (let ix = ix0; ix <= ix1; ix++) {
        for (let iz = iz0; iz <= iz1; iz++) {
          const key = `${ix},${iz}`;
          let cell = this.cells.get(key);
          if (!cell) {
            cell = [];
            this.cells.set(key, cell);
          }
          cell.push(id);
        }
      }
    }
  }

  get triangleCount() {
    return Math.floor(this.triangles.length / 9);
  }

  sample(x, z) {
    const key = `${Math.floor(x / this.step)},${Math.floor(z / this.step)}`;
    const ids = this.cells.get(key);
    if (!ids) return NaN;
    let best = NaN;
    const tri = this.triangles;
    for (const id of ids) {
      const y = triangleHeight(x, z, tri, id * 9);
      if (!Number.isNaN(y) && (Number.isNaN(best) || y > best)) best = y;
    }
    return best;
  }

  sampleMany(points, out) {
    const n = Math.min(Math.floor(points.length / 2), out.length);
    for (let i = 0; i < n; i++) out[i] = this.sample(points[i * 2], points[i * 2 + 1]);
    return out;
  }
}
