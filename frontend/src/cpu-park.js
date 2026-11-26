/**
 * Park uploaded vertex attributes off the page's JS heap. The browser keeps
 * them in a worker. Tests and a worker that fails to start keep a map on this
 * thread instead, so a rebuild can still restore the attributes.
 *
 * The map stores the typed arrays it was given. It does not slice them again.
 * A second slice, on top of a shared glTF buffer that position still held,
 * is what multiplied the Field heap.
 */
export function createCpuPark({ worker: allowWorker = true } = {}) {
  const local = new Map();
  let worker = null;
  let failed = false;
  let seq = 0;
  const pending = new Map();

  function retainedBytes() {
    let total = 0;
    for (const arrays of local.values()) {
      for (const array of arrays || []) total += array.byteLength || 0;
    }
    return total;
  }

  function uniqueOwned(arrays) {
    const held = [];
    const seen = new Set();
    for (const array of arrays || []) {
      if (!array || !array.byteLength) continue;
      const owned = array.byteOffset === 0 && array.buffer.byteLength === array.byteLength;
      const view = owned && !seen.has(array.buffer) ? array : array.slice();
      if (seen.has(view.buffer)) continue;
      seen.add(view.buffer);
      held.push(view);
    }
    return held;
  }

  function thread() {
    if (!allowWorker || failed || typeof Worker !== "function") return null;
    if (worker) return worker;
    try {
      worker = new Worker(new URL("./cpu-park-worker.js", import.meta.url), { type: "module" });
      worker.onmessage = (event) => {
        const data = event.data || {};
        const wait = pending.get(data.token);
        if (!wait) return;
        pending.delete(data.token);
        wait.resolve(data.buffers || null);
      };
      worker.onerror = () => {
        failed = true;
        for (const wait of pending.values()) wait.resolve(null);
        pending.clear();
        worker = null;
      };
      return worker;
    } catch {
      failed = true;
      return null;
    }
  }

  return {
    park(id, arrays) {
      const held = uniqueOwned(arrays);
      const bytes = held.reduce((sum, array) => sum + array.byteLength, 0);
      const active = thread();
      if (!active) {
        local.set(id, held);
        return Promise.resolve(bytes);
      }
      const token = ++seq;
      return new Promise((resolve) => {
        pending.set(token, { resolve: () => resolve(bytes) });
        try {
          active.postMessage({ op: "park", id, token, buffers: held }, held.map((array) => array.buffer));
          if (held.some((array) => array.byteLength !== 0)) {
            pending.delete(token);
            local.set(id, held);
            resolve(bytes);
          }
        } catch {
          pending.delete(token);
          local.set(id, held);
          resolve(bytes);
        }
      });
    },
    drop(id) {
      local.delete(id);
      const active = thread();
      if (!active) return Promise.resolve(false);
      const token = ++seq;
      return new Promise((resolve) => {
        pending.set(token, { resolve: () => resolve(true) });
        try {
          active.postMessage({ op: "drop", id, token });
        } catch {
          pending.delete(token);
          resolve(false);
        }
      });
    },
    retainedBytes,
    take(id) {
      if (local.has(id)) {
        const arrays = local.get(id);
        local.delete(id);
        return Promise.resolve(arrays);
      }
      const active = thread();
      if (!active) return Promise.resolve(null);
      const token = ++seq;
      return new Promise((resolve) => {
        pending.set(token, { resolve });
        try {
          active.postMessage({ op: "take", id, token });
        } catch {
          pending.delete(token);
          resolve(null);
        }
      });
    },
  };
}
