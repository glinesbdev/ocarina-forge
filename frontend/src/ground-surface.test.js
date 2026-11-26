import assert from "node:assert/strict";
import test from "node:test";

import { parseActorHash, sampleGroundSurface, groundSurfaceReady, disposeGroundSurface } from "./ground-surface.js";

test("parseActorHash splits a 64-bit 0x hash into lo and hi words", () => {
  const parsed = parseActorHash("0x0000000100000002");
  assert.equal(parsed.lo, 2);
  assert.equal(parsed.hi, 1);
  assert.equal(parsed.value, 0x0000000100000002n);
});

test("an empty exclude hash is zero", () => {
  const parsed = parseActorHash("");
  assert.equal(parsed.lo, 0);
  assert.equal(parsed.hi, 0);
});

test("sampleGroundSurface is null when no pack is loaded", () => {
  disposeGroundSurface();
  assert.equal(groundSurfaceReady(), false);
  assert.equal(sampleGroundSurface(0, 0, ""), null);
});

test("an aborted ground load does not replace the current pack", async () => {
  const { loadGroundSurface } = await import("./ground-surface.js");
  disposeGroundSurface();
  const loaded = await loadGroundSurface("/api/ground/surface?banc=old", { signal: AbortSignal.abort() });
  assert.equal(loaded, null);
  assert.equal(groundSurfaceReady(), false);
});
