/** Binary fixture layout written by tools/export_wasm_fixture.py
 *
 *  magic "OFWS" (4) | version u32 | step f64 | tri_count u32 | point_count u32
 *  | reserved u32 | triangles f64[tri_count*9] | points f64[point_count*2]
 *  | expected f64[point_count]
 *
 *  A sibling .json holds metadata and the Python baseline timings.
 */

const MAGIC = 0x5357464f; // "OFWS" little-endian

export function parseSurfaceFixture(buffer) {
  const view = new DataView(buffer);
  const magic = view.getUint32(0, true);
  if (magic !== MAGIC) throw new Error("Not an Ocarina Forge wasm surface fixture.");
  const version = view.getUint32(4, true);
  if (version !== 1) throw new Error(`Unsupported fixture version ${version}.`);
  const step = view.getFloat64(8, true);
  const triCount = view.getUint32(16, true);
  const pointCount = view.getUint32(20, true);
  const header = 32;
  const triBytes = triCount * 9 * 8;
  const pointBytes = pointCount * 2 * 8;
  const expectedBytes = pointCount * 8;
  const need = header + triBytes + pointBytes + expectedBytes;
  if (buffer.byteLength < need) throw new Error(`Fixture is truncated (${buffer.byteLength} < ${need}).`);
  const triangles = new Float64Array(buffer, header, triCount * 9);
  const points = new Float64Array(buffer, header + triBytes, pointCount * 2);
  const expected = new Float64Array(buffer, header + triBytes + pointBytes, pointCount);
  return { step, triCount, pointCount, triangles, points, expected, bytes: need };
}

export function parseTextureFixture(buffer) {
  const view = new DataView(buffer);
  const magic = view.getUint32(0, true);
  if (magic !== 0x53574654) throw new Error("Not an Ocarina Forge wasm texture fixture."); // "TFWS"
  const version = view.getUint32(4, true);
  if (version !== 1) throw new Error(`Unsupported texture fixture version ${version}.`);
  const width = view.getInt32(8, true);
  const height = view.getInt32(12, true);
  const blockW = view.getInt32(16, true);
  const blockH = view.getInt32(20, true);
  const bytesPer = view.getInt32(24, true);
  const tile = view.getInt32(28, true);
  const log2 = view.getInt32(32, true);
  const codecLen = view.getUint32(36, true);
  const rawLen = view.getUint32(40, true);
  const rgbaLen = view.getUint32(44, true);
  let offset = 48;
  const codec = new TextDecoder().decode(new Uint8Array(buffer, offset, codecLen));
  offset += codecLen;
  const raw = new Uint8Array(buffer, offset, rawLen);
  offset += rawLen;
  const rgba = new Uint8Array(buffer, offset, rgbaLen);
  return { width, height, blockW, blockH, bytesPer, tile, log2, codec, raw, rgba, bytes: offset + rgbaLen };
}
