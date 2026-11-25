import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { applyTextureManifest, clearTextureMaps, materialKey } from "./texture-apply.js";

function color(rgb) {
  return {
    rgb: rgb.slice(),
    clone() {
      return color(this.rgb);
    },
    copy(other) {
      this.rgb = other.rgb.slice();
    },
    setRGB(r, g, b) {
      this.rgb = [r, g, b];
    },
  };
}

function material() {
  return {
    map: null,
    normalMap: null,
    roughnessMap: null,
    metalnessMap: null,
    aoMap: null,
    emissiveMap: null,
    color: color([0.6, 0.5, 0.4]),
    alphaTest: 0,
    transparent: false,
    roughness: 0.9,
    metalness: 0,
    needsUpdate: false,
    userData: {},
  };
}

test("a manifest keys the glTF node without its lod prefix", () => {
  assert.equal(materialKey("lod0:Body:0"), "Body:0");
  assert.equal(materialKey("lod1:Body:0"), "Body:0");
});

test("applying textures does not rebuild geometry and clearing drops them", async () => {
  const mesh = material();
  const parts = [{ name: "lod0:Body:0", material: mesh, geometry: { id: "same" } }];
  const geometry = parts[0].geometry;
  const loads = [];
  await applyTextureManifest(parts, {
    materials: {
      "Body:0": {
        map: "/api/model-textures/albedo.ktx2",
        orm: "/api/model-textures/orm.ktx2",
        alpha: "mask",
        cutoff: 0.4,
        color: [1, 1, 1, 1],
      },
    },
  }, async (url) => {
    loads.push(url);
    return { url, disposed: false, dispose() { this.disposed = true; } };
  });
  assert.deepEqual(loads, ["/api/model-textures/albedo.ktx2", "/api/model-textures/orm.ktx2"]);
  assert.equal(parts[0].geometry, geometry);
  assert.equal(mesh.map.url, "/api/model-textures/albedo.ktx2");
  assert.equal(mesh.roughnessMap, mesh.metalnessMap);
  assert.equal(mesh.alphaTest, 0.4);
  assert.deepEqual(mesh.color.rgb, [1, 1, 1]);
  loads.length = 0;
  await applyTextureManifest(parts, {
    materials: { "Body:0": { map: "/api/model-textures/albedo.ktx2" } },
  }, async (url) => {
    loads.push(url);
    return { url };
  });
  assert.deepEqual(loads, []);
  const released = [];
  clearTextureMaps(parts, (texture) => released.push(texture.url));
  assert.deepEqual(released.sort(), ["/api/model-textures/albedo.ktx2", "/api/model-textures/orm.ktx2"]);
  assert.equal(mesh.map, null);
  assert.equal(mesh.roughnessMap, null);
  assert.equal(mesh.alphaTest, 0);
  assert.equal(mesh.roughness, 0.9);
  assert.deepEqual(mesh.color.rgb, [0.6, 0.5, 0.4]);
  assert.equal(parts[0].geometry, geometry);
});

test("the texture toggle does not ask the model loader to run", () => {
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const viewport = readFileSync(new URL("./viewport.js", import.meta.url), "utf8");
  const geometry = main.slice(main.indexOf("function geometryRequest"), main.indexOf("viewport = await createViewport"));
  const change = main.slice(main.indexOf("async function applyLoadChange"), main.indexOf("async function openScene"));
  assert.match(geometry, /flat: "1"/);
  assert.doesNotMatch(geometry, /textures/);
  assert.match(main, /fetchTextures:/);
  assert.doesNotMatch(change, /invalidateModels/);
  assert.doesNotMatch(change, /forceSceneLoad/);
  assert.match(viewport, /planAssetFetches/);
  assert.match(viewport, /function setTextures/);
  assert.doesNotMatch(viewport, /function invalidateModels/);
  assert.doesNotMatch(viewport, /formatStats/);
});
