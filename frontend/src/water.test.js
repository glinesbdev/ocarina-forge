import assert from "node:assert/strict";
import test from "node:test";
import { BufferGeometry, Float32BufferAttribute } from "three";
import { ensureWaterUv, fishingPondFish, fishingPondPreviewOffset, isPreviewWater } from "./water.js";

function geometryWithPositions(positions) {
  return new BufferGeometry().setAttribute("position", new Float32BufferAttribute(positions, 3));
}

test("the pond preview only lowers matching fish when there is an authored water surface", () => {
  assert.equal(fishingPondFish("Animal_Fish_Bass"), true);
  assert.equal(fishingPondFish("Animal_Fish_Blue"), false);
  assert.equal(fishingPondPreviewOffset(8, -Infinity), 0);
  assert.equal(fishingPondPreviewOffset(3, 5), 0);
  assert.ok(Math.abs(8 + fishingPondPreviewOffset(8, 5, 0.15) - 4.5) < 1e-10);
});

test("startup water recognizes tagged and older lake surfaces but leaves nearby props alone", () => {
  const transparent = { transparent: true };
  assert.equal(isPreviewWater(transparent, "lod0:Surface", "Obj_LakeHrl_WaterPlane_A_07"), true);
  assert.equal(isPreviewWater(transparent, "lod0:WaterfallFront", "Land_ZoraRiver_A_01"), true);
  assert.equal(isPreviewWater({ userData: { banc: "water" } }, "Surface", "Obj_Lake"), true);
  assert.equal(isPreviewWater(transparent, "Glass", "Land_LakeHrl_FishingPondBridge_A_01"), false);
  assert.equal(isPreviewWater({ transparent: false }, "Pillar", "Obj_LakeHrl_WaterPlane_A_07"), false);
});

test("water meshes without UVs use the plane of the surface", () => {
  const lake = geometryWithPositions([0, 5, 0, 8, 5, 0, 0, 5, 4]);
  ensureWaterUv(lake);
  assert.deepEqual([...lake.getAttribute("uv").array], [0, 0, 2, 0, 0, 1]);

  const waterfall = geometryWithPositions([3, 0, 0, 3, 0, 8, 3, 4, 0]);
  ensureWaterUv(waterfall, 1);
  assert.deepEqual([...waterfall.getAttribute("uv1").array], [0, 0, 2, 0, 0, 1]);
});

test("water keeps its authored texture coordinates", () => {
  const mesh = geometryWithPositions([0, 0, 0, 8, 0, 0, 0, 0, 4]);
  const authored = new Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2);
  mesh.setAttribute("uv", authored);
  ensureWaterUv(mesh);
  assert.equal(mesh.getAttribute("uv"), authored);
});

test("a stand-in ripple retile replaces a unit chart and keeps a repeating one", () => {
  const lake = geometryWithPositions([0, 5, 0, 80, 5, 0, 0, 5, 40]);
  lake.setAttribute("uv", new Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2));
  assert.equal(ensureWaterUv(lake, 0, { retile: true }), true);
  assert.deepEqual([...lake.getAttribute("uv").array], [0, 0, 20, 0, 0, 10]);

  const tiled = geometryWithPositions([0, 0, 0, 8, 0, 0, 0, 0, 4]);
  const authored = new Float32BufferAttribute([0, 0, 4, 0, 0, 3], 2);
  tiled.setAttribute("uv", authored);
  assert.equal(ensureWaterUv(tiled, 0, { retile: true }), false);
  assert.equal(tiled.getAttribute("uv"), authored);
});
