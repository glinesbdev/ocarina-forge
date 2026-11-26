import assert from "node:assert/strict";
import test from "node:test";

import { releaseGltfDocument } from "./gltf-release.js";

test("releasing a parsed glTF drops the document and the binary cache", () => {
  const cache = {
    removed: false,
    removeAll() {
      this.removed = true;
    },
  };
  const gltf = {
    scene: { name: "root" },
    parser: {
      json: { buffers: [{ byteLength: 100 }], images: [{ uri: "a.ktx2" }] },
      associations: new Map([[{}, { meshes: 0 }]]),
      cache,
    },
  };
  assert.equal(releaseGltfDocument(gltf), true);
  assert.equal(gltf.parser, null);
  assert.equal(gltf.scene.name, "root");
  assert.equal(cache.removed, true);
});
