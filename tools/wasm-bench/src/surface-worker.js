/// <reference lib="webworker" />

import init, { WasmMeshIndex } from "../pkg/ocarina_forge_native.js";

let index = null;
let ready = false;

function attachSab(buffer, offsets) {
  return {
    points: new Float64Array(buffer, offsets.points, offsets.pointFloats),
    results: new Float64Array(buffer, offsets.results, offsets.resultFloats),
    control: new Int32Array(buffer, offsets.control, 4),
  };
}

self.onmessage = async (event) => {
  const msg = event.data;
  try {
    if (msg.type === "init") {
      await init({ module_or_path: msg.wasmUrl });
      index = new WasmMeshIndex(msg.step, new Float64Array(msg.triangles));
      ready = true;
      self.postMessage({ type: "ready", triangleCount: index.triangleCount });
      return;
    }
    if (msg.type === "sample-sab") {
      if (!ready) throw new Error("Worker wasm is not ready.");
      const { points, results, control } = attachSab(msg.sab, msg.offsets);
      const chunk = msg.chunk || 4096;
      const total = msg.count;
      while (true) {
        const start = Atomics.add(control, 0, chunk);
        if (start >= total) break;
        const end = Math.min(total, start + chunk);
        const slice = index.sampleRange(points, start, end);
        results.set(slice, start);
      }
      Atomics.add(control, 1, 1);
      Atomics.notify(control, 1);
      self.postMessage({ type: "done" });
      return;
    }
    if (msg.type === "sample-copy") {
      if (!ready) throw new Error("Worker wasm is not ready.");
      const out = index.sampleRange(new Float64Array(msg.points), msg.start, msg.end);
      self.postMessage({ type: "result", start: msg.start, results: out }, [out.buffer]);
      return;
    }
  } catch (error) {
    self.postMessage({ type: "error", message: String(error && error.message ? error.message : error) });
  }
};
