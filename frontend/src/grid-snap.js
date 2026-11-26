/** Grid-increment snapping for the transform gizmo.
 *
 * Each drag records the pose where the pointer went down. The gizmo then
 * reports an unsnapped pose computed from that start. Snapping rounds the
 * offset from that start onto whole steps of the increment and writes
 * `start + steps * increment`. Rounding the previous result plus this
 * frame's delta drifts, and the saved value walks off the step.
 *
 * Move steps are metres along the active axes, in world space or in the
 * actor's local basis captured at drag start. When ground Snap also applies,
 * pass `lockY`: the grid keeps X and Z, and the caller writes the height.
 * Rotation steps are radians measured from the drag-start quaternion.
 * Scale steps are optional and use the same start-relative rule. */

export const MOVE_INCREMENTS = [0.25, 0.5, 1, 5, 10];
export const ROTATE_INCREMENTS = [5, 15, 45, 90];
export const SCALE_INCREMENTS = [0.25, 0.5, 1];

export const GRID_KEYS = {
  enabled: "ocarinaforge.gridSnap",
  move: "ocarinaforge.gridMove",
  rotate: "ocarinaforge.gridRotate",
  scaleEnabled: "ocarinaforge.scaleSnap",
  scale: "ocarinaforge.gridScale",
  visible: "ocarinaforge.showSnapGrid",
};

const AXIS = {
  X: [1, 0, 0],
  Y: [0, 1, 0],
  Z: [0, 0, 1],
};

export function defaultGridSettings() {
  return {
    enabled: false,
    move: 1,
    rotate: 15,
    scaleEnabled: false,
    scale: 0.25,
    visible: false,
  };
}

export function coerceIncrement(value, choices, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  for (const choice of choices) {
    if (choice === number) return choice;
  }
  return fallback;
}

export function normalizeGridSettings(settings) {
  const base = defaultGridSettings();
  const source = settings || {};
  return {
    enabled: Boolean(source.enabled),
    move: coerceIncrement(source.move, MOVE_INCREMENTS, base.move),
    rotate: coerceIncrement(source.rotate, ROTATE_INCREMENTS, base.rotate),
    scaleEnabled: Boolean(source.scaleEnabled),
    scale: coerceIncrement(source.scale, SCALE_INCREMENTS, base.scale),
    visible: Boolean(source.visible),
  };
}

export function readGridSettings(storage) {
  const base = defaultGridSettings();
  if (!storage) return base;
  try {
    return normalizeGridSettings({
      enabled: storage.getItem(GRID_KEYS.enabled) === "1",
      move: storage.getItem(GRID_KEYS.move),
      rotate: storage.getItem(GRID_KEYS.rotate),
      scaleEnabled: storage.getItem(GRID_KEYS.scaleEnabled) === "1",
      scale: storage.getItem(GRID_KEYS.scale),
      visible: storage.getItem(GRID_KEYS.visible) === "1",
    });
  } catch {
    return base;
  }
}

export function writeGridSettings(storage, settings) {
  if (!storage) return;
  const value = normalizeGridSettings(settings);
  try {
    storage.setItem(GRID_KEYS.enabled, value.enabled ? "1" : "0");
    storage.setItem(GRID_KEYS.move, String(value.move));
    storage.setItem(GRID_KEYS.rotate, String(value.rotate));
    storage.setItem(GRID_KEYS.scaleEnabled, value.scaleEnabled ? "1" : "0");
    storage.setItem(GRID_KEYS.scale, String(value.scale));
    storage.setItem(GRID_KEYS.visible, value.visible ? "1" : "0");
  } catch {
    /* private mode */
  }
}

/** Ctrl while dragging inverts the toolbar toggle. It does not change the toggle. */
export function gridSnapEngaged(enabled, ctrlHeld) {
  return Boolean(enabled) !== Boolean(ctrlHeld);
}

export function axesOf(axis) {
  const name = String(axis || "").toUpperCase();
  return {
    x: name.includes("X"),
    y: name.includes("Y"),
    z: name.includes("Z"),
  };
}

/** `origin + round((value - origin) / increment) * increment`. */
export function snapScalar(value, increment, origin = 0) {
  if (!Number.isFinite(value) || !(increment > 0)) return value;
  const base = Number.isFinite(origin) ? origin : 0;
  const steps = Math.round((value - base) / increment);
  return base + steps * increment;
}

function component(value, index) {
  const number = Number(value && value[index]);
  return Number.isFinite(number) ? number : 0;
}

function vec3(value) {
  return [component(value, 0), component(value, 1), component(value, 2)];
}

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

/**
 * Snap `position` along the active axes. `origin` is the drag-start position.
 * In local space, `basis` is the actor's unit axes at drag start, in world
 * coordinates. `lockY` copies the incoming world Y through so ground Snap can
 * replace it. Inactive axes stay on the drag start.
 */
export function snapTranslation({ position, origin, axis, space, basis, increment, lockY = false }) {
  const pos = vec3(position);
  const start = vec3(origin);
  if (!(increment > 0) || !axis) return pos;
  const active = axesOf(axis);
  let world;
  if (space === "local" && basis && basis.length === 3) {
    const delta = [pos[0] - start[0], pos[1] - start[1], pos[2] - start[2]];
    const local = [0, 1, 2].map((index) => dot(delta, basis[index]));
    const snapped = [0, 1, 2].map((index) => {
      const key = ["x", "y", "z"][index];
      if (!active[key]) return 0;
      return snapScalar(local[index], increment, 0);
    });
    world = [0, 1, 2].map((row) => start[row]
      + snapped[0] * basis[0][row]
      + snapped[1] * basis[1][row]
      + snapped[2] * basis[2][row]);
  } else {
    world = [0, 1, 2].map((index) => {
      const key = ["x", "y", "z"][index];
      if (!active[key]) return start[index];
      return snapScalar(pos[index], increment, start[index]);
    });
  }
  if (lockY) world[1] = pos[1];
  return world;
}

export function snapScale({ current, origin, axis, increment }) {
  const value = vec3(current);
  const start = vec3(origin);
  if (!(increment > 0) || !axis) return value;
  const active = axesOf(axis);
  return [0, 1, 2].map((index) => {
    const key = ["x", "y", "z"][index];
    if (!active[key]) return start[index];
    const snapped = snapScalar(value[index], increment, start[index]);
    if (snapped === 0) {
      const sign = Math.sign(value[index]) || Math.sign(start[index]) || 1;
      return sign * increment;
    }
    return snapped;
  });
}

/** Next grid line in `direction` (+1 or -1), as an exact multiple of the step. */
export function nudge(value, increment, direction) {
  const current = Number(value);
  const step = Number(increment);
  const way = direction < 0 ? -1 : 1;
  if (!Number.isFinite(current)) return current;
  if (!(step > 0)) return current + way;
  const steps = current / step;
  const epsilon = 1e-8;
  const next = way > 0 ? Math.floor(steps + epsilon) + 1 : Math.ceil(steps - epsilon) - 1;
  return next * step;
}

/** Columns of the rotation matrix. Each entry is a unit axis in world space. */
export function basisFromQuaternion(quaternion) {
  const x = component(quaternion, 0);
  const y = component(quaternion, 1);
  const z = component(quaternion, 2);
  const w = component(quaternion, 3);
  const xx = x * x;
  const yy = y * y;
  const zz = z * z;
  const xy = x * y;
  const xz = x * z;
  const yz = y * z;
  const wx = w * x;
  const wy = w * y;
  const wz = w * z;
  return [
    [1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy)],
    [2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx)],
    [2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy)],
  ];
}

function quatMultiply(a, b) {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

function quatConjugate(q) {
  return [-q[0], -q[1], -q[2], q[3]];
}

function quatNormalize(q) {
  const length = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / length, q[1] / length, q[2] / length, q[3] / length];
}

export function axisAngleQuat(axis, angle) {
  const length = Math.hypot(axis[0], axis[1], axis[2]) || 1;
  const half = angle / 2;
  const scale = Math.sin(half) / length;
  return [axis[0] * scale, axis[1] * scale, axis[2] * scale, Math.cos(half)];
}

export function rotationAngleAbout(delta, axis) {
  const sinHalf = delta[0] * axis[0] + delta[1] * axis[1] + delta[2] * axis[2];
  return 2 * Math.atan2(sinHalf, delta[3]);
}

/** Screen-space and free rotates are world spins. A single local axis stays local. */
export function effectiveRotationSpace(space, axis) {
  if (axis === "E" || axis === "XYZE" || axis === "XYZ") return "world";
  return space === "local" ? "local" : "world";
}

/**
 * Snap the drag angle about the gizmo axis onto `incrementRadians`, then
 * rebuild from the drag-start quaternion. The same unsnapped quaternion
 * always produces the same result.
 */
export function snapRotation({ current, origin, axis, space, incrementRadians }) {
  if (!(incrementRadians > 0) || !current || !origin) return current ? current.slice() : current;
  const start = [component(origin, 0), component(origin, 1), component(origin, 2), Number.isFinite(Number(origin[3])) ? Number(origin[3]) : 1];
  const live = [component(current, 0), component(current, 1), component(current, 2), Number.isFinite(Number(current[3])) ? Number(current[3]) : 1];
  const useLocal = space === "local" && axis !== "E" && axis !== "XYZE" && axis !== "XYZ";
  const delta = useLocal ? quatMultiply(quatConjugate(start), live) : quatMultiply(live, quatConjugate(start));
  let rotAxis = AXIS[axis];
  if (!rotAxis) {
    const length = Math.hypot(delta[0], delta[1], delta[2]);
    if (length < 1e-12) return start.slice();
    rotAxis = [delta[0] / length, delta[1] / length, delta[2] / length];
  }
  const angle = rotationAngleAbout(delta, rotAxis);
  const snapped = snapScalar(angle, incrementRadians, 0);
  const spin = axisAngleQuat(rotAxis, snapped);
  const next = useLocal ? quatMultiply(start, spin) : quatMultiply(spin, start);
  return quatNormalize(next);
}

/**
 * Radians that still display as the degree they came from. A snapped 15°
 * stays `15 * π / 180` instead of a 4-decimal truncation that reads back as
 * 15.0004. Anything that is not already that degree keeps 4 decimal places.
 */
export function savedRadians(radians) {
  if (!Number.isFinite(radians)) return radians;
  const degrees = radians * 180 / Math.PI;
  const shown = Math.round(degrees * 10000) / 10000;
  const canonical = shown * Math.PI / 180;
  if (Math.abs(canonical - radians) < 1e-7) return canonical;
  return Math.round(radians * 10000) / 10000;
}

/** Even division count so a grid line crosses the centre. `size` is `cell * divisions`. */
export function snapGridLayout(increment, divisions = 60) {
  const cell = coerceIncrement(increment, MOVE_INCREMENTS, 1);
  let count = Math.max(2, Math.round(Number(divisions) || 60));
  if (count % 2) count += 1;
  return { size: cell * count, divisions: count, cell };
}
