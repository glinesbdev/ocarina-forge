import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  axisAngleQuat,
  basisFromQuaternion,
  gridSnapEngaged,
  nudge,
  readGridSettings,
  rotationAngleAbout,
  savedRadians,
  snapGridLayout,
  snapRotation,
  snapScalar,
  snapScale,
  snapTranslation,
  writeGridSettings,
} from "./grid-snap.js";

const round4 = (value) => Math.round(value * 10000) / 10000;

function memory() {
  const data = new Map();
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(String(key), String(value));
    },
  };
}

test("snapping from the drag start does not drift the way frame deltas do", () => {
  const origin = 1.25;
  const increment = 0.25;
  const frames = [0.04, 0.08, 0.13, 0.2, 0.24, 0.3, 0.37, 0.49, 0.52, 0.8, 1.01];
  let incremental = origin;
  let previous = 0;
  for (const offset of frames) {
    const delta = offset - previous;
    previous = offset;
    incremental += Math.round(delta / increment) * increment;
  }
  let fromStart = origin;
  for (const offset of frames) fromStart = snapScalar(origin + offset, increment, origin);
  const expected = origin + Math.round(1.01 / increment) * increment;
  assert.equal(fromStart, expected);
  assert.equal(fromStart, 2.25);
  assert.equal(snapScalar(fromStart, increment, origin), fromStart);
  assert.notEqual(incremental, fromStart);
});

test("a saved step stays exact across later drags", () => {
  let origin = 0;
  for (let i = 0; i < 40; i += 1) {
    const snapped = snapScalar(origin + 0.3, 0.25, origin);
    const saved = round4(snapped);
    assert.equal(saved, origin + 0.25);
    origin = saved;
  }
  assert.equal(origin, 10);
});

test("world axis snap moves only the active world axes", () => {
  const snapped = snapTranslation({
    position: [10.4, 8.2, -3.4],
    origin: [10, 8, -3],
    axis: "X",
    space: "world",
    increment: 0.25,
  });
  assert.deepEqual(snapped, [10.5, 8, -3]);
});

test("local axis snap follows the actor basis and stays on that step", () => {
  // Yaw +90°: local +X is world -Z.
  const basis = [
    [0, 0, -1],
    [0, 1, 0],
    [1, 0, 0],
  ];
  const origin = [10, 2, -4];
  const snapped = snapTranslation({
    position: [10, 2, -4.4],
    origin,
    axis: "X",
    space: "local",
    basis,
    increment: 0.25,
  });
  assert.deepEqual(snapped, [10, 2, -4.5]);
  const sameNumbersOnWorldX = snapTranslation({
    position: [10.4, 2, -4],
    origin,
    axis: "X",
    space: "world",
    increment: 0.25,
  });
  assert.deepEqual(sameNumbersOnWorldX, [10.5, 2, -4]);
  assert.notDeepEqual(snapped, sameNumbersOnWorldX);

  let again = origin.slice();
  for (let step = 1; step <= 12; step += 1) {
    again = snapTranslation({
      position: [10, 2, -4 - step * 0.25 - 1e-12],
      origin,
      axis: "X",
      space: "local",
      basis,
      increment: 0.25,
    });
    assert.deepEqual(again, [10, 2, -4 - step * 0.25]);
  }
  assert.deepEqual(snapTranslation({
    position: again,
    origin,
    axis: "X",
    space: "local",
    basis,
    increment: 0.25,
  }), again);
});

test("a yawed basis from the quaternion is the local snap frame", () => {
  const quarter = Math.PI / 4;
  const basis = basisFromQuaternion([0, Math.sin(quarter), 0, Math.cos(quarter)]);
  assert.ok(Math.abs(basis[0][0]) < 1e-12);
  assert.ok(Math.abs(basis[0][1]) < 1e-12);
  assert.ok(Math.abs(basis[0][2] + 1) < 1e-12);
});

test("grid snap keeps X and Z and leaves height for ground snap", () => {
  const snapped = snapTranslation({
    position: [1.2, 9, 2.8],
    origin: [1, 4, 2],
    axis: "XZ",
    space: "world",
    increment: 1,
    lockY: true,
  });
  assert.deepEqual(snapped, [1, 9, 3]);

  const basis = [
    [0.6, 0.8, 0],
    [-0.8, 0.6, 0],
    [0, 0, 1],
  ];
  const tilted = snapTranslation({
    position: [0.24, 0.32, 0],
    origin: [0, 0, 0],
    axis: "X",
    space: "local",
    basis,
    increment: 0.25,
    lockY: true,
  });
  assert.equal(tilted[0], 0.3);
  assert.equal(tilted[1], 0.32);
  assert.equal(tilted[2], 0);
});

test("rotation snap rebuilds the drag-start angle and does not walk", () => {
  const step = 15 * Math.PI / 180;
  const origin = [0, 0, 0, 1];
  const current = axisAngleQuat([0, 1, 0], 20 * Math.PI / 180);
  const snapped = snapRotation({
    current,
    origin,
    axis: "Y",
    space: "world",
    incrementRadians: step,
  });
  const angle = rotationAngleAbout(snapped, [0, 1, 0]);
  assert.ok(Math.abs(angle - step) < 1e-12);
  const again = snapRotation({ current, origin, axis: "Y", space: "world", incrementRadians: step });
  for (let i = 0; i < 4; i += 1) assert.ok(Math.abs(again[i] - snapped[i]) < 1e-12);
  const stable = snapRotation({ current: snapped, origin, axis: "Y", space: "world", incrementRadians: step });
  const stableAngle = rotationAngleAbout(stable, [0, 1, 0]);
  assert.ok(Math.abs(stableAngle - step) < 1e-12);

  const yaw = axisAngleQuat([0, 1, 0], Math.PI / 2);
  const localSpin = axisAngleQuat([1, 0, 0], 20 * Math.PI / 180);
  const localCurrent = [
    yaw[3] * localSpin[0] + yaw[0] * localSpin[3] + yaw[1] * localSpin[2] - yaw[2] * localSpin[1],
    yaw[3] * localSpin[1] - yaw[0] * localSpin[2] + yaw[1] * localSpin[3] + yaw[2] * localSpin[0],
    yaw[3] * localSpin[2] + yaw[0] * localSpin[1] - yaw[1] * localSpin[0] + yaw[2] * localSpin[3],
    yaw[3] * localSpin[3] - yaw[0] * localSpin[0] - yaw[1] * localSpin[1] - yaw[2] * localSpin[2],
  ];
  const local = snapRotation({
    current: localCurrent,
    origin: yaw,
    axis: "X",
    space: "local",
    incrementRadians: step,
  });
  const inv = [-yaw[0], -yaw[1], -yaw[2], yaw[3]];
  const delta = [
    inv[3] * local[0] + inv[0] * local[3] + inv[1] * local[2] - inv[2] * local[1],
    inv[3] * local[1] - inv[0] * local[2] + inv[1] * local[3] + inv[2] * local[0],
    inv[3] * local[2] + inv[0] * local[1] - inv[1] * local[0] + inv[2] * local[3],
    inv[3] * local[3] - inv[0] * local[0] - inv[1] * local[1] - inv[2] * local[2],
  ];
  assert.ok(Math.abs(rotationAngleAbout(delta, [1, 0, 0]) - step) < 1e-10);
});

test("a snapped degree still saves as that degree", () => {
  let radians = 0;
  const step = 15 * Math.PI / 180;
  for (let i = 0; i < 24; i += 1) {
    const snapped = snapScalar(radians + 20 * Math.PI / 180, step, radians);
    radians = savedRadians(snapped);
  }
  const degrees = radians * 180 / Math.PI;
  assert.ok(Math.abs(degrees - 360) < 1e-6);
  assert.equal(Math.round(degrees * 10000) / 10000, 360);
  const free = savedRadians(0.4);
  assert.equal(free, round4(0.4));
});

test("scale snap stays on the drag-start step and refuses zero", () => {
  assert.deepEqual(snapScale({
    current: [1.2, 1.6, 0.2],
    origin: [1, 1, 1],
    axis: "X",
    increment: 0.25,
  }), [1.25, 1, 1]);
  assert.deepEqual(snapScale({
    current: [0.01, 1, 1],
    origin: [0.25, 1, 1],
    axis: "XYZ",
    increment: 0.25,
  }), [0.25, 1, 1]);
});

test("arrow nudges land on exact steps", () => {
  let value = 0;
  for (let i = 0; i < 40; i += 1) value = nudge(value, 0.25, 1);
  assert.equal(value, 10);
  assert.equal(nudge(0.1, 0.25, 1), 0.25);
  assert.equal(nudge(0.25, 0.25, -1), 0);
  assert.equal(nudge(-0.1, 0.25, -1), -0.25);
  assert.equal(nudge(15, 15, 1), 30);
  assert.equal(nudge(15, 15, -1), 0);
});

test("ctrl flips the grid toggle only for the caller that passes it", () => {
  assert.equal(gridSnapEngaged(false, false), false);
  assert.equal(gridSnapEngaged(true, false), true);
  assert.equal(gridSnapEngaged(false, true), true);
  assert.equal(gridSnapEngaged(true, true), false);
});

test("grid settings round-trip and reject an increment that is not offered", () => {
  const storage = memory();
  writeGridSettings(storage, {
    enabled: true,
    move: 0.5,
    rotate: 90,
    scaleEnabled: true,
    scale: 1,
    visible: true,
  });
  assert.deepEqual(readGridSettings(storage), {
    enabled: true,
    move: 0.5,
    rotate: 90,
    scaleEnabled: true,
    scale: 1,
    visible: true,
  });
  writeGridSettings(storage, { enabled: true, move: 0.1, rotate: 7, scale: 2, visible: false });
  const fallback = readGridSettings(storage);
  assert.equal(fallback.move, 1);
  assert.equal(fallback.rotate, 15);
  assert.equal(fallback.scale, 0.25);
  assert.equal(fallback.enabled, true);
});

test("the visible grid is centred on a line at the move increment", () => {
  for (const cell of [0.25, 0.5, 1, 5, 10]) {
    const layout = snapGridLayout(cell);
    assert.equal(layout.cell, cell);
    assert.equal(layout.divisions % 2, 0);
    assert.equal(layout.size, cell * layout.divisions);
  }
});

test("the viewport snaps the raw drag before ground snap writes the height", () => {
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const move = viewport.slice(
    viewport.indexOf('transform.addEventListener("objectChange"'),
    viewport.indexOf('transform.addEventListener("dragging-changed"'),
  );
  const planeAt = move.indexOf("dragPlaneY = entry.pivot.position.y");
  const gridAt = move.indexOf("applyGridSnap(entry)");
  const groundAt = move.indexOf('maybeSnap(entry, "move")');
  const syncAt = move.indexOf("applySelectedActorTransform");
  assert.ok(planeAt !== -1 && gridAt !== -1 && groundAt !== -1 && syncAt !== -1);
  assert.ok(planeAt < gridAt && gridAt < groundAt && groundAt < syncAt);
  const restoreAt = viewport.indexOf("restoreDragRaw(entry)");
  const planeWrite = viewport.indexOf("entry.pivot.position.y = dragPlaneY");
  assert.ok(restoreAt !== -1 && planeWrite !== -1 && restoreAt < planeWrite);
});
