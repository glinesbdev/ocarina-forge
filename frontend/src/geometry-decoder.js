import { MeshoptDecoder } from "meshoptimizer/decoder";

let worker;
let workerUnavailable = false;
let nextId = 0;
const pending = new Map();

function disableWorker(error) {
  workerUnavailable = true;
  worker?.terminate();
  worker = undefined;
  for (const request of pending.values()) request.reject(error);
  pending.clear();
}

function decodeInWorker(count, stride, source, mode, filter) {
  if (!worker) {
    worker = new Worker(new URL("./geometry-decode-worker.js", import.meta.url), { type: "module" });
    worker.onmessage = ({ data }) => {
      const request = pending.get(data.id);
      if (!request) return;
      pending.delete(data.id);
      if (data.error) request.reject(new Error(data.error));
      else request.resolve(data.output);
    };
    const failed = () => disableWorker(new Error("The geometry decoder worker stopped."));
    worker.onerror = failed;
    worker.onmessageerror = failed;
  }
  const input = source.slice();
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    try {
      worker.postMessage({ id, count, stride, input, mode, filter }, [input.buffer]);
    } catch (error) {
      pending.delete(id);
      reject(error);
    }
  });
}

export const geometryDecoder = {
  supported: MeshoptDecoder.supported,
  async decodeGltfBufferAsync(count, stride, source, mode, filter) {
    // Small buffer views cost less to decode than to dispatch to a worker.
    if (count * stride >= 64 * 1024 && !workerUnavailable && typeof Worker !== "undefined") {
      try {
        return await decodeInWorker(count, stride, source, mode, filter);
      } catch {
        if (!worker) workerUnavailable = true;
      }
    }
    await MeshoptDecoder.ready;
    const output = new Uint8Array(count * stride);
    MeshoptDecoder.decodeGltfBuffer(output, count, stride, source, mode, filter);
    return output;
  },
};
