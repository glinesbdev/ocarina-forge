import { MeshoptDecoder } from "meshoptimizer/decoder";

self.onmessage = async ({ data: { id, count, stride, input, mode, filter } }) => {
  try {
    await MeshoptDecoder.ready;
    const output = new Uint8Array(count * stride);
    MeshoptDecoder.decodeGltfBuffer(output, count, stride, input, mode, filter);
    self.postMessage({ id, output }, [output.buffer]);
  } catch (error) {
    self.postMessage({ id, error: String(error) });
  }
};
