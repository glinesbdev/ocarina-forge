/**
 * Background texture fetches, separate from the show/hide switch.
 *
 * Geometry stays ahead: while model loads are in flight, one texture job
 * runs. When they are idle, a few run together. Near visible actors go
 * first. Showing or hiding does not enter this queue.
 */

export const TEXTURE_SLOTS = 4;
export const TEXTURE_SLOTS_WHILE_GEOMETRY = 1;

export function textureSlotLimit({ geometryBusy = false } = {}) {
  return geometryBusy ? TEXTURE_SLOTS_WHILE_GEOMETRY : TEXTURE_SLOTS;
}

export function orderTextureJobs(jobs) {
  return [...(jobs || [])].sort((a, b) => {
    const visibility = Number(Boolean(b && b.visible)) - Number(Boolean(a && a.visible));
    if (visibility) return visibility;
    return (Number(a && a.distance) || 0) - (Number(b && b.distance) || 0);
  });
}

/**
 * How long each actor waits when every job costs `serviceMs` and only `slots`
 * run at once. `prioritize` sorts near visible actors to the front. The old
 * toggle started every actor in insertion order.
 */
export function simulateTextureWave({ jobs, slots, serviceMs = 80, prioritize = false } = {}) {
  const queue = prioritize ? orderTextureJobs(jobs) : [...(jobs || [])];
  const finish = new Map();
  const active = [];
  let index = 0;
  let now = 0;
  const slotCount = Math.max(1, slots | 0);
  while (index < queue.length || active.length) {
    while (active.length < slotCount && index < queue.length) {
      const job = queue[index];
      index += 1;
      active.push({ gyaml: job.gyaml, end: now + serviceMs });
    }
    active.sort((a, b) => a.end - b.end);
    const step = active.shift();
    now = step.end;
    finish.set(step.gyaml, now);
  }
  return finish;
}

export function createTextureQueue() {
  let jobs = [];
  const done = new Set();
  const inflight = new Set();
  let allow = true;

  function pending() {
    return jobs.filter((job) => job && !done.has(job.gyaml) && !inflight.has(job.gyaml));
  }

  return {
    get allow() {
      return allow;
    },
    setAllow(on) {
      allow = Boolean(on);
      if (!allow) jobs = jobs.filter((job) => inflight.has(job.gyaml) || done.has(job.gyaml));
      return allow;
    },
    note(job) {
      if (!allow || !job || !job.gyaml) return false;
      if (done.has(job.gyaml) || inflight.has(job.gyaml) || jobs.some((item) => item.gyaml === job.gyaml)) return false;
      jobs.push({
        gyaml: job.gyaml,
        url: job.url,
        distance: Number(job.distance) || 0,
        visible: Boolean(job.visible),
      });
      return true;
    },
    rank(read) {
      if (typeof read !== "function") return;
      for (const job of pending()) {
        const next = read(job) || {};
        if (next.distance != null) job.distance = Number(next.distance) || 0;
        if (next.visible != null) job.visible = Boolean(next.visible);
      }
    },
    take(limit) {
      if (!allow) return [];
      const count = Math.max(0, limit | 0);
      const next = orderTextureJobs(pending()).slice(0, count);
      for (const job of next) inflight.add(job.gyaml);
      return next;
    },
    finish(gyaml) {
      if (!gyaml || !inflight.delete(gyaml)) return;
      done.add(gyaml);
      jobs = jobs.filter((job) => job.gyaml !== gyaml);
    },
    forget(gyaml) {
      done.delete(gyaml);
      inflight.delete(gyaml);
      jobs = jobs.filter((job) => job.gyaml !== gyaml);
    },
    has(gyaml) {
      return done.has(gyaml);
    },
    progress() {
      if (!allow) return null;
      const waiting = pending().length;
      const total = done.size + inflight.size + waiting;
      if (!total || done.size >= total) return null;
      return { done: done.size, total };
    },
  };
}
