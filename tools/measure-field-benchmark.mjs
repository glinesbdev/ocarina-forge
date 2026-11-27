/**
 * Headless check of the Field camera-path benchmark.
 *
 * Walks the fixed 160 m loop and prints the JSON shape the editor writes
 * after a fly. This does not open a scene, a renderer, or the game.
 *
 *   node tools/measure-field-benchmark.mjs
 */
import { FIELD_FLIGHT, createFlight, summarizeBenchmark } from "../frontend/src/field-benchmark.js";

const points = FIELD_FLIGHT.points;
const sides = [];
for (let index = 0; index < points.length - 1; index += 1) {
  const a = points[index];
  const b = points[index + 1];
  sides.push(Math.hypot(b[0] - a[0], b[1] - a[1]));
}
const flight = createFlight();
const samples = [];
let steps = 0;
let pose = flight.sample(0);
while (!flight.done && steps < 5000) {
  pose = flight.sample(1 / 60);
  steps += 1;
  if (steps % 30 === 0) {
    samples.push({
      submitMs: 1,
      updateMs: 1,
      priorityMs: 0,
      labelsMs: 0,
      cullMs: 0,
      writeTex: 0,
      indirectFills: 0,
      lodFlips: 0,
      heapBytes: 1000 + steps,
      draws: 10,
      batches: 4,
      gpuMs: steps % 60 === 0 ? null : 0.5,
    });
  }
}
const summary = summarizeBenchmark(samples);
const payload = {
  ok: true,
  synthetic: true,
  note: "Path geometry only. Not a Hyrule Field load and not a renderer timing.",
  path: "field-loop-160m",
  sides,
  steps,
  end: { x: pose.x, y: pose.y, z: pose.z, done: pose.done },
  summary,
  heap: { avg: summary.heapBytes.avg, final: samples.length ? samples[samples.length - 1].heapBytes : null },
  capped: 0,
};
const axisSides = sides.slice(0, -1);
const closed = pose.done === true && pose.x === 0 && pose.z === 0;
const sidesOk = axisSides.every((length) => length === 160);
payload.ok = closed && sidesOk && summary.submitMs.n > 0 && summary.gpuMs.n < summary.frames;
console.log(JSON.stringify(payload, null, 2));
if (!payload.ok) process.exitCode = 1;
