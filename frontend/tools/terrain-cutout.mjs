import { Box3, BufferAttribute, BufferGeometry, DoubleSide, Euler, FrontSide, Matrix4, Quaternion, Ray, Vector3 } from "three";
import { MeshBVH } from "three-mesh-bvh";

export async function buildCorrectedTerrain(io, terrain, sources, actors, heightTiles = [], heightScale = 1, entrances = [], starts = [], collision = {}, pond = null) {
  const paths = new Map(Object.entries(sources));
  const document = await io.read(terrain);
  let groundHeight;
  const knownFloor = [];
  const clips = [];
  const approachStep = 0.25;
  const approaches = starts.map(([, y], i) => {
    const bounds = entrances[i];
    const size = Math.ceil((bounds[2] - bounds[0]) / approachStep);
    return { bounds, y, step: approachStep, size, floor: new Float32Array(size ** 2).fill(Infinity) };
  });
  const points = [new Vector3(), new Vector3(), new Vector3()];
  const cross = new Vector3(), edge = new Vector3();
  let modelName, model;
  for (const actor of [...actors].sort((a, b) => a.gyaml.localeCompare(b.gyaml))) {
    const path = paths.get(actor.gyaml);
    if (!path) continue;
    if (modelName !== actor.gyaml) {
      model = await io.read(path);
      modelName = actor.gyaml;
    }
    const verified = actor.verifiedTerrain;
    const entranceIndices = actor.entranceIndices || [];
    const triangles = [];
    const placement = new Matrix4().compose(
      new Vector3(...actor.translate),
      new Quaternion().setFromEuler(new Euler(...actor.rotate, "ZYX")),
      new Vector3(...actor.scale.map((value) => value || 1)),
    );
    for (const node of model.getRoot().listNodes()) {
      if (!node.getName().startsWith("lod0:")) continue;
      const modelTransform = new Matrix4().fromArray(node.getWorldMatrix());
      const transform = placement.clone().multiply(modelTransform);
      const ladder = verified && /^lod0:Obj_.*Ladder/i.test(node.getName()) ? new Box3() : null;
      for (const primitive of node.getMesh()?.listPrimitives() || []) {
        if (primitive.getMode() !== 4 || (primitive.getMaterial()?.getAlphaMode() || "OPAQUE") !== "OPAQUE") continue;
        const vertices = primitive.getAttribute("POSITION")?.getArray();
        const indices = primitive.getIndices()?.getArray();
        if (!vertices || !indices) continue;
        for (let i = 0; i + 2 < indices.length; i += 3) {
          for (let j = 0; j < 3; j += 1) points[j].fromArray(vertices, indices[i + j] * 3).applyMatrix4(transform);
          if (ladder) for (const point of points) ladder.expandByPoint(point);
          const [p, q, r] = points;
          cross.subVectors(q, p).cross(edge.subVectors(r, p));
          if (cross.y > 1e-5 && (verified || cross.y >= 0.7 * cross.length())) triangles.push([...p, ...q, ...r]);
        }
      }
      if (ladder && !ladder.isEmpty()) {
        // A ladder is mostly vertical, so its projected triangles miss portions
        // of the approach. Keep the ground beneath the foot across its width.
        const margin = 0.35;
        const { min, max } = ladder;
        const left = min.x - margin, right = max.x + margin;
        const top = min.z - margin, bottom = max.z + margin;
        const y = min.y;
        triangles.push([left, y, top, left, y, bottom, right, y, top],
          [right, y, top, left, y, bottom, right, y, bottom]);
      }
    }
    if (entranceIndices.length && triangles.length) {
      const collisionTriangles = collision[actor.gyaml].flatMap((triangle) => {
        for (let j = 0; j < 3; j += 1) points[j].fromArray(triangle, 3 * j).applyMatrix4(placement);
        const [p, q, r] = points;
        cross.subVectors(q, p).cross(edge.subVectors(r, p));
        return Math.abs(cross.y) > 1e-5 && Math.abs(cross.y) >= 0.7 * cross.length() ? [[...p, ...q, ...r]] : [];
      });
      for (const i of entranceIndices) matchApproachFloor(approaches[i], triangles, collisionTriangles);
    }
    if (verified) {
      for (const triangle of triangles) knownFloor.push(triangle);
    } else if (triangles.length && (actor.terrainSurface ?? !entranceIndices.length)) {
      groundHeight ||= terrainSampler(document);
      for (const overlap of confirmedTerrainOverlap(triangles, groundHeight)) {
        const fit = rasterizeCutout(overlap, 3);
        clips.push({ ...fit, step: fit.step * 2 });
      }
    }
  }
  const authoredDelta = sampleAuthoredDetail(heightTiles, heightScale);
  for (const approach of approaches) {
    const triangles = connectedApproach(approach);
    if (!triangles.length) continue;
    groundHeight ||= terrainSampler(document);
    const buried = triangles.some(([x, y, z]) =>
      groundHeight(x, z) + Math.max(0, authoredDelta(x, z)) > y + 0.02);
    if (buried) clips.push(rasterizeCutout(triangles, 3));
  }
  if (knownFloor.length) clips.push({ ...rasterizeCutout(knownFloor), authoredDelta });
  for (const bounds of entrances) {
    const [left, top, right, bottom] = bounds;
    let needsDetail = false;
    for (let z = top + 1; z < bottom && !needsDetail; z += 2) {
      for (let x = left + 1; x < right; x += 2) {
        if (Math.abs(authoredDelta(x, z)) < 0.4) continue;
        needsDetail = true;
        break;
      }
    }
    if (needsDetail) clips.push({ bounds, detailBounds: bounds, step: 0.25, authoredDelta });
  }
  if (pond && paths.has(pond.gyaml)) {
    const footprint = pondClearance(await io.read(paths.get(pond.gyaml)), pond);
    if (footprint) clips.push(footprint);
  }
  if (!clips.length) return null;
  let changed = false;
  for (const node of document.getRoot().listNodes()) {
    const matrix = new Matrix4().fromArray(node.getWorldMatrix());
    for (const primitive of node.getMesh()?.listPrimitives() || []) {
      if (deformTerrainPrimitive(primitive, matrix, clips)) changed = true;
    }
  }
  return changed ? document : null;
}

/** Keep the coarse ground below the pond sheet within its authored footprint. */
export function pondClearance(document, actor) {
  const placement = new Matrix4().compose(new Vector3(...actor.translate),
    new Quaternion().setFromEuler(new Euler(...actor.rotate, "ZYX")), new Vector3(...actor.scale));
  const bounds = new Box3();
  const meshes = [];
  for (const node of document.getRoot().listNodes()) {
    if (!node.getName().startsWith("lod0:")) continue;
    const matrix = placement.clone().multiply(new Matrix4().fromArray(node.getWorldMatrix()));
    for (const primitive of node.getMesh()?.listPrimitives() || []) {
      const positions = primitive.getAttribute("POSITION")?.getArray();
      if (!positions || primitive.getMode() !== 4 || primitive.getMaterial()?.getAlphaMode() !== "BLEND") continue;
      const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(positions.slice(), 3));
      const indices = primitive.getIndices()?.getArray();
      if (indices) geometry.setIndex(new BufferAttribute(indices.slice(), 1));
      geometry.applyMatrix4(matrix).computeBoundingBox();
      bounds.union(geometry.boundingBox);
      meshes.push(new MeshBVH(geometry));
    }
  }
  if (!meshes.length) return null;
  const ray = new Ray(new Vector3(), new Vector3(0, -1, 0));
  const { min, max } = bounds;
  const footprint = [min.x, min.z, max.x, max.z];
  return {
    bounds: footprint, detailBounds: footprint, step: 0.125,
    sample(x, y, z) {
      ray.origin.set(x, max.y + 1, z);
      let waterHeight = -Infinity;
      for (const mesh of meshes) {
        const hit = mesh.raycastFirst(ray, DoubleSide);
        if (hit) waterHeight = Math.max(waterHeight, hit.point.y);
      }
      return Number.isFinite(waterHeight) ? Math.min(y, waterHeight - 0.12) : y;
    },
  };
}

// Use rendered heights only where the same actor's collision surface agrees.
function matchApproachFloor({ bounds: [left, top], y, step, size, floor }, visual, collision) {
  const maxHeightDifference = 1.5;
  const collisionHeights = new Map();
  projectTriangles(collision, left, top, size, size, step, (cell, height) => {
    if (Math.abs(height - y) > maxHeightDifference) return;
    const heights = collisionHeights.get(cell);
    if (heights) heights.push(height);
    else collisionHeights.set(cell, [height]);
  });
  projectTriangles(visual, left, top, size, size, step, (cell, height) => {
    const distance = Math.abs(height - y);
    if (distance > maxHeightDifference || distance >= Math.abs(floor[cell] - y)) return;
    if (collisionHeights.get(cell)?.some((physical) => Math.abs(physical - height) <= 0.12)) floor[cell] = height;
  });
}

function connectedApproach({ bounds: [left, top], y, step, size, floor }) {
  const center = (size - 1) / 2;
  const connected = new Uint8Array(floor.length);
  const stack = [];
  // Separate pieces of the same approach can meet the spawn area without
  // sharing mesh edges, as at the Water Temple landing and doorway ramp.
  for (let i = 0; i < floor.length; i += 1) {
    if (Math.abs(floor[i] - y) > 0.75) continue;
    const distance = Math.hypot((i % size - center) * step, (Math.floor(i / size) - center) * step);
    if (distance > 3) continue;
    connected[i] = 1;
    stack.push(i);
  }
  if (!stack.length) return [];

  while (stack.length) {
    const cell = stack.pop();
    const column = cell % size;
    const neighbors = [column > 0 ? cell - 1 : -1, column < size - 1 ? cell + 1 : -1, cell - size, cell + size];
    for (const next of neighbors) {
      if (next < 0 || next >= floor.length || connected[next] || Math.abs(floor[cell] - floor[next]) > 0.35) continue;
      connected[next] = 1;
      stack.push(next);
    }
  }

  const triangles = [];
  for (let i = 0; i < connected.length - size; i += 1) {
    const column = i % size;
    if (column === size - 1 || !connected[i] || !connected[i + 1] ||
        !connected[i + size] || !connected[i + size + 1]) continue;
    const x = left + (column + 0.5) * step, z = top + (Math.floor(i / size) + 0.5) * step;
    const a = floor[i], b = floor[i + 1], c = floor[i + size], d = floor[i + size + 1];
    if (Math.max(a, b, c, d) - Math.min(a, b, c, d) > 0.35) continue;
    triangles.push([x, a, z, x, c, z + step, x + step, b, z],
      [x + step, b, z, x, c, z + step, x + step, d, z + step]);
  }
  return triangles;
}

function footprintBounds(triangles) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const triangle of triangles) {
    for (let i = 0; i < triangle.length; i += 3) {
      for (let axis = 0; axis < 3; axis += 1) {
        min[axis] = Math.min(min[axis], triangle[i + axis]);
        max[axis] = Math.max(max[axis], triangle[i + axis]);
      }
    }
  }
  return [min, max];
}

function projectTriangles(triangles, x0, z0, width, height, step, visit) {
  for (const [px, py, pz, qx, qy, qz, rx, ry, rz] of triangles) {
    const ux = qx - px, uz = qz - pz, vx = rx - px, vz = rz - pz;
    const det = ux * vz - uz * vx;
    const left = Math.max(0, Math.ceil((Math.min(px, qx, rx) - x0) / step - 0.5));
    const right = Math.min(width - 1, Math.floor((Math.max(px, qx, rx) - x0) / step - 0.5));
    const top = Math.max(0, Math.ceil((Math.min(pz, qz, rz) - z0) / step - 0.5));
    const bottom = Math.min(height - 1, Math.floor((Math.max(pz, qz, rz) - z0) / step - 0.5));
    for (let row = top; row <= bottom; row += 1) {
      for (let column = left; column <= right; column += 1) {
        const x = x0 + (column + 0.5) * step - px;
        const z = z0 + (row + 0.5) * step - pz;
        const a = (x * vz - z * vx) / det;
        const b = (z * ux - x * uz) / det;
        if (a < -1e-6 || b < -1e-6 || a + b > 1 + 1e-6) continue;
        const y = py * (1 - a - b) + qy * a + ry * b;
        visit(row * width + column, y);
      }
    }
  }
}

function terrainSampler(document) {
  const meshes = [];
  for (const node of document.getRoot().listNodes()) {
    for (const primitive of node.getMesh()?.listPrimitives() || []) {
      const marks = primitive.getAttribute("TEXCOORD_3")?.getArray();
      const points = primitive.getAttribute("POSITION")?.getArray();
      const input = primitive.getIndices()?.getArray();
      if (!marks || !points || !input || primitive.getMode() !== 4) continue;
      const indices = [];
      for (let i = 0; i < input.length; i += 3) {
        const a = input[i], b = input[i + 1], c = input[i + 2];
        if (marks[2 * a + 1] < 0.5 && marks[2 * b + 1] < 0.5 && marks[2 * c + 1] < 0.5) indices.push(a, b, c);
      }
      if (!indices.length) continue;
      const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(points.slice(), 3));
      geometry.setIndex(indices).applyMatrix4(new Matrix4().fromArray(node.getWorldMatrix()));
      geometry.computeBoundingBox();
      meshes.push({ bounds: geometry.boundingBox, tree: new MeshBVH(geometry) });
    }
  }
  const ray = new Ray(new Vector3(), new Vector3(0, -1, 0));
  return (x, z) => {
    let y = -Infinity;
    for (const { bounds, tree } of meshes) {
      if (x < bounds.min.x || x > bounds.max.x || z < bounds.min.z || z > bounds.max.z) continue;
      ray.origin.set(x, bounds.max.y + 1, z);
      const hit = tree.raycastFirst(ray, FrontSide);
      if (hit) y = Math.max(y, hit.point.y);
    }
    return y;
  };
}

export function confirmedTerrainOverlap(triangles, groundHeight) {
  const [min, max] = footprintBounds(triangles);
  const left = Math.floor(min[0]), top = Math.floor(min[2]);
  const width = Math.ceil(max[0]) - left, height = Math.ceil(max[2]) - top;
  if (width > 128 || height > 128 || width < 2 || height < 2) return [];
  const floor = new Float32Array(width * height).fill(-Infinity);
  projectTriangles(triangles, left, top, width, height, 1, (cell, y) => { floor[cell] = Math.max(floor[cell], y); });
  const buried = new Set();
  const contacts = new Set();
  for (let i = 0; i < floor.length; i += 1) {
    if (!Number.isFinite(floor[i])) continue;
    const gap = groundHeight(left + i % width + 0.5, top + Math.floor(i / width) + 0.5) - floor[i];
    if (gap >= 0.35 && gap <= 3) buried.add(i);
    else if (gap >= -0.75 && gap < 0.35) contacts.add(i);
  }
  // Require ground contact as well as a cluster of buried surface samples.
  // That excludes floating platforms and floors in a separate layer below ground.
  if (contacts.size < 2 || buried.size < 4) return [];
  const squares = new Map();
  for (const i of buried) {
    const col = i % width;
    if (col + 1 === width || !buried.has(i + 1) || !buried.has(i + width) || !buried.has(i + width + 1)) continue;
    const row = Math.floor(i / width);
    let nearby = 0;
    for (let j = Math.max(0, row - 3); j <= Math.min(height - 1, row + 4); j += 1) {
      for (let k = Math.max(0, col - 3); k <= Math.min(width - 1, col + 4); k += 1) {
        if (contacts.has(j * width + k)) nearby += 1;
      }
    }
    if (nearby < 2) continue;
    const x = left + col + 0.5, z = top + row + 0.5;
    const a = floor[i], b = floor[i + 1], c = floor[i + width], d = floor[i + width + 1];
    squares.set(i, [[x, a, z, x, c, z + 1, x + 1, b, z], [x + 1, b, z, x, c, z + 1, x + 1, d, z + 1]]);
  }
  const patches = [];
  for (const first of squares.keys()) {
    const patch = [];
    const stack = [first];
    while (stack.length) {
      const cell = stack.pop();
      const surface = squares.get(cell);
      if (!surface) continue;
      squares.delete(cell);
      patch.push(...surface);
      stack.push(cell - 1, cell + 1, cell - width, cell + width);
    }
    patches.push(patch);
  }
  return patches;
}

export function rasterizeCutout(triangles, margin = 8) {
  if (!triangles.length) return null;
  const [min, max] = footprintBounds(triangles);
  const step = Math.max(0.125, (max[0] - min[0]) / 510, (max[2] - min[2]) / 510);
  const padding = Math.ceil(margin / step) * step;
  const x0 = min[0] - padding, z0 = min[2] - padding;
  const width = Math.ceil((max[0] + padding - x0) / step);
  const height = Math.ceil((max[2] + padding - z0) / step);
  const floor = new Float32Array(width * height).fill(Infinity);
  projectTriangles(triangles, x0, z0, width, height, step, (cell, y) => { floor[cell] = Math.min(floor[cell], y); });
  const heights = floor.slice();
  const radius = 1 / step;
  const rowReaches = [];
  for (let dy = -Math.floor(radius); dy <= radius; dy += 1) {
    rowReaches.push([dy, Math.floor(Math.sqrt(radius * radius - dy * dy))]);
  }
  for (let row = 0; row < height; row += 1) {
    for (let column = 0; column < width; column += 1) {
      const cell = row * width + column;
      if (Number.isFinite(floor[cell])) continue;
      // The base heightmap bridges openings between the authored rock meshes.
      for (const [dy, reach] of rowReaches) {
        const y = row + dy;
        if (y < 0 || y >= height) continue;
        for (let x = Math.max(0, column - reach); x <= Math.min(width - 1, column + reach); x += 1) {
          heights[cell] = Math.min(heights[cell], floor[y * width + x]);
        }
      }
    }
  }
  // Propagate a rising clearance limit from the rock footprint. An abrupt
  // cutoff at the footprint perimeter creates near-vertical terrain fins.
  const rise = 1.3 * step;
  const diagonalRise = Math.SQRT2 * rise;
  for (const direction of [1, -1]) {
    const firstRow = direction > 0 ? 0 : height - 1;
    const firstColumn = direction > 0 ? 0 : width - 1;
    for (let row = firstRow; row >= 0 && row < height; row += direction) {
      for (let column = firstColumn; column >= 0 && column < width; column += direction) {
        const cell = row * width + column;
        const previousColumn = column - direction;
        if (previousColumn >= 0 && previousColumn < width) {
          heights[cell] = Math.min(heights[cell], heights[cell - direction] + rise);
        }
        const previousRow = row - direction;
        if (previousRow < 0 || previousRow >= height) continue;
        const previous = previousRow * width + column;
        heights[cell] = Math.min(heights[cell], heights[previous] + rise);
        if (column > 0) heights[cell] = Math.min(heights[cell], heights[previous - 1] + diagonalRise);
        if (column + 1 < width) heights[cell] = Math.min(heights[cell], heights[previous + 1] + diagonalRise);
      }
    }
  }
  const sample = (x, y, z) => {
    const u = (x - x0) / step - 0.5;
    const v = (z - z0) / step - 0.5;
    const column = Math.floor(u), row = Math.floor(v);
    const dx = u - column, dz = v - row;
    const at = (i, j) => {
      if (i < 0 || i >= width || j < 0 || j >= height) return y;
      const edge = Math.min(i, width - 1 - i, j, height - 1 - j) * step;
      const t = Math.min(1, edge / 3);
      return y + t * t * (3 - 2 * t) * Math.min(0, heights[j * width + i] - 0.02 - y);
    };
    const upper = (1 - dx) * at(column, row) + dx * at(column + 1, row);
    const lower = (1 - dx) * at(column, row + 1) + dx * at(column + 1, row + 1);
    return (1 - dz) * upper + dz * lower;
  };
  return { sample, step, bounds: [x0, z0, x0 + width * step, z0 + height * step],
    detailBounds: [min[0] - 1, min[2] - 1, max[0] + 1, max[2] + 1] };
}

export function sampleAuthoredDetail(tiles, heightScale) {
  const size = 257;
  const sources = tiles.map(({ bounds, step, grid }) => {
    const bytes = Buffer.from(grid, "base64");
    const data = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return { bounds, step, data };
  });
  function sample(data, x, z, step) {
    const col = Math.min(size - 1 - step, Math.floor(x / step) * step);
    const row = Math.min(size - 1 - step, Math.floor(z / step) * step);
    const u = (x - col) / step, v = (z - row) / step;
    const at = (i, j) => data.getFloat32((j * size + i) * 4, true);
    const b = at(col + step, row), c = at(col, row + step);
    // Use the same diagonal as the two faces emitted by build_scene().
    if (u + v <= 1) {
      const a = at(col, row);
      return a + (b - a) * u + (c - a) * v;
    }
    const d = at(col + step, row + step);
    return d + (c - d) * (1 - u) + (b - d) * (1 - v);
  }
  return (x, z) => {
    for (const { bounds: [left, top, right, bottom], step, data } of sources) {
      if (x < left || z < top || x > right || z > bottom || step < 1) continue;
      const col = (x - left) * (size - 1) / (right - left);
      const row = (z - top) * (size - 1) / (bottom - top);
      const fine = sample(data, col, row, 1);
      const coarse = sample(data, col, row, step);
      if (Number.isFinite(fine) && Number.isFinite(coarse)) return (fine - coarse) * heightScale;
    }
    return 0;
  };
}

export function deformTerrainPrimitive(primitive, matrix, fit) {
  const source = primitive.getAttribute("POSITION");
  const marks = primitive.getAttribute("TEXCOORD_3");
  const indices = primitive.getIndices();
  if (primitive.getMode() !== 4 || !source || !indices || !marks) return;
  const attributes = primitive.listSemantics().map((semantic) => {
    const accessor = primitive.getAttribute(semantic);
    return { semantic, accessor, size: accessor.getElementSize(), data: Array.from(accessor.getArray()) };
  });
  const positions = attributes.find(({ semantic }) => semantic === "POSITION").data;
  const points = Array.from({ length: source.getCount() }, (_, i) => new Vector3().fromArray(positions, i * 3).applyMatrix4(matrix));
  const clips = Array.isArray(fit) ? fit : [fit];
  const intersects = ([left, top, right, bottom], x0, z0, x1, z1, margin = 0) =>
    x1 >= left - margin && x0 <= right + margin && z1 >= top - margin && z0 <= bottom + margin;
  // Split by the endpoints alone so both faces sharing an edge keep the same boundary.
  const splits = new Map();
  function midpoint(a, b) {
    const p = points[a], q = points[b];
    const x0 = Math.min(p.x, q.x), z0 = Math.min(p.z, q.z);
    const x1 = Math.max(p.x, q.x), z1 = Math.max(p.z, q.z);
    const span = Math.max(x1 - x0, z1 - z0);
    const split = clips.some((clip) => {
      if (!intersects(clip.bounds, x0, z0, x1, z1)) return false;
      // The taper outside the modeled terrain changes more gradually.
      const nearModels = intersects(clip.detailBounds, x0, z0, x1, z1, 1);
      return span > clip.step * (nearModels ? 4 : 8);
    });
    if (!split) return -1;
    const key = a < b ? `${a}:${b}` : `${b}:${a}`;
    if (splits.has(key)) return splits.get(key);
    const index = points.length;
    splits.set(key, index);
    points.push(p.clone().lerp(q, 0.5));
    for (const { size, data } of attributes) {
      for (let k = 0; k < size; k += 1) data.push((data[a * size + k] + data[b * size + k]) / 2);
    }
    return index;
  }

  const output = [];
  const ground = new Set();
  function subdivide(a, b, c) {
    const ab = midpoint(a, b), bc = midpoint(b, c), ca = midpoint(c, a);
    if (ab < 0 && bc < 0 && ca < 0) {
      output.push(a, b, c);
      ground.add(a).add(b).add(c);
    } else if (ab >= 0 && bc >= 0 && ca >= 0) {
      subdivide(a, ab, ca);
      subdivide(ab, b, bc);
      subdivide(ca, bc, c);
      subdivide(ab, bc, ca);
    } else if (ab >= 0) {
      subdivide(ab, b, bc >= 0 ? bc : c);
      subdivide(a, ab, ca >= 0 ? ca : c);
      if (bc >= 0) subdivide(ab, bc, c);
      if (ca >= 0) subdivide(ab, c, ca);
    } else {
      subdivide(b, c, a);
    }
  }

  const input = indices.getArray();
  const layers = marks.getArray();
  for (let i = 0; i < input.length; i += 3) {
    const a = input[i], b = input[i + 1], c = input[i + 2];
    const p = points[a], q = points[b], r = points[c];
    const x0 = Math.min(p.x, q.x, r.x), z0 = Math.min(p.z, q.z, r.z);
    const x1 = Math.max(p.x, q.x, r.x), z1 = Math.max(p.z, q.z, r.z);
    if (layers[a * 2 + 1] > 0.5 || layers[b * 2 + 1] > 0.5 || layers[c * 2 + 1] > 0.5 ||
        !clips.some((clip) => intersects(clip.bounds, x0, z0, x1, z1))) {
      output.push(a, b, c);
    } else {
      subdivide(a, b, c);
    }
  }
  const inverse = matrix.clone().invert();
  const changed = [];
  for (const index of ground) {
    const point = points[index];
    const { x, z } = point;
    let base = point.y;
    // Fade to the existing mesh at the border. The delta preserves offsets
    // such as the tile skirts while restoring detail lost to coarse spacing.
    let detailWeight = 0;
    let delta = 0;
    for (const { detailBounds: [left, top, right, bottom], authoredDelta } of clips) {
      if (!authoredDelta) continue;
      const distance = Math.min(x - left, right - x, z - top, bottom - z);
      const weight = Math.min(1, distance / 2);
      if (weight <= detailWeight) continue;
      detailWeight = weight;
      delta = authoredDelta(x, z);
    }
    base += detailWeight * delta;
    let y = base;
    for (const clip of clips) if (clip.sample && intersects(clip.bounds, x, z, x, z)) y = Math.min(y, clip.sample(x, base, z));
    if (Math.abs(y - point.y) < 1e-6) continue;
    point.y = y;
    point.applyMatrix4(inverse).toArray(positions, index * 3);
    changed.push(index);
  }
  if (!changed.length) return;
  const positionArray = Float32Array.from(positions);
  const indexArray = points.length <= 65536 ? Uint16Array.from(output) : Uint32Array.from(output);
  const normals = attributes.find(({ semantic }) => semantic === "NORMAL");
  if (normals) {
    const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(positionArray, 3));
    geometry.setIndex(new BufferAttribute(indexArray, 1));
    geometry.computeVertexNormals();
    const normalArray = geometry.getAttribute("normal").array;
    for (const index of changed) {
      const i = index * 3;
      for (let k = 0; k < 3; k += 1) normals.data[i + k] = normalArray[i + k];
    }
  }
  primitive.setIndices(indices.clone().setArray(indexArray));
  if (indices.listParents().length === 1) indices.dispose();
  for (const { semantic, accessor, data } of attributes) {
    const array = semantic === "POSITION" ? positionArray : accessor.getArray().constructor.from(data);
    primitive.setAttribute(semantic, accessor.clone().setArray(array));
    if (accessor.listParents().length === 1) accessor.dispose();
  }
  return true;
}
