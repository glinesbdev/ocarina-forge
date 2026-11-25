import assert from "node:assert/strict";
import test from "node:test";
import { Document } from "@gltf-transform/core";
import { pondClearance } from "./terrain-cutout.mjs";

test("the pond fit only lowers high ground inside the transformed water triangle", () => {
  const document = new Document();
  const buffer = document.createBuffer();
  const surface = document.createPrimitive()
    .setAttribute("POSITION", document.createAccessor().setType("VEC3").setBuffer(buffer)
      .setArray(new Float32Array([0, 0, 0, 0, 0, 4, 4, 0, 0])))
    .setMaterial(document.createMaterial().setAlphaMode("BLEND"));
  document.createScene().addChild(document.createNode("lod0:Water")
    .setMesh(document.createMesh().addPrimitive(surface)));
  const fit = pondClearance(document, { translate: [10, 3, 20], rotate: [0, 0, 0], scale: [1, 1, 1] });

  assert.equal(fit.sample(11, 10, 21), 2.88);
  assert.equal(fit.sample(11, 1, 21), 1);
  assert.equal(fit.sample(13, 10, 23), 10);
});
