import assert from "node:assert/strict";
import test from "node:test";
import { LengthPrefixedFrameReader } from "./length-prefixed.mjs";

function frame(text) {
  const body = Buffer.from(text);
  const header = Buffer.alloc(4);
  header.writeUInt32LE(body.length);
  return Buffer.concat([header, body]);
}

test("reads fragmented headers, bodies, and consecutive frames", () => {
  const received = [];
  const reader = new LengthPrefixedFrameReader(64, (body) => received.push(body.toString("utf8")));
  const input = Buffer.concat([frame("first"), frame("second")]);

  for (const [start, end] of [[0, 2], [2, 7], [7, 11], [11, input.length]]) {
    reader.push(input.subarray(start, end));
  }

  assert.deepEqual(received, ["first", "second"]);
});

test("rejects empty and oversized frames", () => {
  const emptyReader = new LengthPrefixedFrameReader(8, () => {});
  const emptyHeader = Buffer.alloc(4);
  assert.throws(() => emptyReader.push(emptyHeader), RangeError);

  const oversizedReader = new LengthPrefixedFrameReader(8, () => {});
  const oversizedHeader = Buffer.alloc(4);
  oversizedHeader.writeUInt32LE(9);
  assert.throws(() => oversizedReader.push(oversizedHeader), RangeError);
});
