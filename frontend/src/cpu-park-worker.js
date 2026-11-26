/**
 * Holds typed arrays the main thread has finished uploading. They stay in this
 * worker's heap, so performance.memory.usedJSHeapSize on the page no longer
 * counts them. A rebuild asks for them back.
 */
const parked = new Map();

self.onmessage = (event) => {
  const { op, id, token, buffers } = event.data || {};
  if (op === "park") {
    parked.set(id, buffers || []);
    self.postMessage({ token });
    return;
  }
  if (op === "take") {
    const arrays = parked.get(id) || null;
    parked.delete(id);
    const transfer = arrays ? arrays.map((array) => array.buffer) : [];
    self.postMessage({ token, buffers: arrays }, transfer);
    return;
  }
  if (op === "drop") {
    parked.delete(id);
    self.postMessage({ token });
  }
};
