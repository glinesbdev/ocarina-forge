import assert from "node:assert/strict";
import test from "node:test";

import {
  createTextureQueue,
  orderTextureJobs,
  simulateTextureWave,
  textureSlotLimit,
} from "./texture-stream.js";

test("near visible actors are fetched before the rest, and geometry keeps the wider slots", () => {
  const jobs = [
    { gyaml: "far", distance: 800, visible: false },
    { gyaml: "mid", distance: 40, visible: true },
    { gyaml: "near", distance: 4, visible: true },
    { gyaml: "hidden", distance: 1, visible: false },
  ];
  assert.deepEqual(orderTextureJobs(jobs).map((job) => job.gyaml), ["near", "mid", "hidden", "far"]);
  assert.equal(textureSlotLimit({ geometryBusy: true }), 1);
  assert.equal(textureSlotLimit({ geometryBusy: false }), 4);
});

test("a finished stream does not fetch again when the show switch flips", () => {
  const queue = createTextureQueue();
  queue.setAllow(true);
  assert.equal(queue.note({ gyaml: "Rock", url: "/api/models/Rock/textures", distance: 1, visible: true }), true);
  const [job] = queue.take(4);
  queue.finish(job.gyaml);
  assert.equal(queue.progress(), null);
  assert.equal(queue.note({ gyaml: "Rock", url: "/api/models/Rock/textures", distance: 1, visible: true }), false);
  queue.setAllow(false);
  queue.setAllow(true);
  assert.equal(queue.note({ gyaml: "Rock", url: "/api/models/Rock/model.glb?flat=1", distance: 1, visible: true }), false);
  assert.equal(queue.take(4).length, 0);
});

test("turning the download off keeps finished actors and drops the ones still waiting", () => {
  const queue = createTextureQueue();
  queue.note({ gyaml: "Near", url: "/textures/near", distance: 1, visible: true });
  queue.note({ gyaml: "Far", url: "/textures/far", distance: 50, visible: true });
  queue.take(1);
  queue.finish("Near");
  queue.setAllow(false);
  assert.equal(queue.progress(), null);
  queue.setAllow(true);
  assert.equal(queue.note({ gyaml: "Near", url: "/textures/near", distance: 1, visible: true }), false);
  assert.equal(queue.note({ gyaml: "Far", url: "/textures/far", distance: 50, visible: true }), true);
  assert.equal(queue.progress().total, 2);
});

test("the old all-at-once order holds a near actor behind the field", () => {
  const jobs = [];
  for (let i = 0; i < 450; i += 1) jobs.push({ gyaml: `far-${i}`, distance: 1000 + i, visible: false });
  jobs.push({ gyaml: "near", distance: 1, visible: true });
  const flooded = simulateTextureWave({ jobs, slots: 6, serviceMs: 80, prioritize: false });
  const ordered = simulateTextureWave({ jobs, slots: 4, serviceMs: 80, prioritize: true });
  assert.equal(ordered.get("near"), 80);
  assert.equal(flooded.get("near"), 6080);
  assert.ok(flooded.get("near") / ordered.get("near") >= 70);
});
