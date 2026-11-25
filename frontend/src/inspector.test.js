import assert from "node:assert/strict";
import test from "node:test";

import { modelLine } from "./inspector.js";

test("a mesh is the file name and the triangle count", () => {
  assert.equal(
    modelLine({ status: "mesh", source: "Model/Land_HrlField.FarMountain_A_01.bfres.zs", triangles: 12400 }),
    "Land_HrlField.FarMountain_A_01.bfres.zs · 12,400 triangles",
  );
  assert.equal(modelLine({ status: "mesh", source: "Pot.bfres", triangles: 1 }), "Pot.bfres · 1 triangle");
});

test("a model that is not ready yet says it is waiting", () => {
  assert.equal(modelLine(null), "Waiting to convert");
  assert.equal(modelLine({ status: "loading" }), "Waiting to convert");
  assert.equal(modelLine({ status: "queued", source: "Model/Stone.bfres.zs" }), "Waiting to convert");
});

test("a box uses its status", () => {
  assert.equal(modelLine({ status: "box", reason: "geometry format not decoded (FSHP flag 0x10)" }), "geometry format not decoded (FSHP flag 0x10)");
  assert.equal(modelLine({ status: "box" }), "No model file");
});
