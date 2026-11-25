import assert from "node:assert/strict";
import test from "node:test";

import { groupModelErrors, isMarker, resolveTextureIndex } from "./problems.js";

test("model failures group by message and keep every actor", () => {
  const actors = [
    { id: "a", gyaml: "Land_HrlField_Stone_A_02", name: "Stone A" },
    { id: "b", gyaml: "Land_HrlField_Stone_A_02", name: "Stone A copy" },
    { id: "c", gyaml: "Land_HrlCastle_Cliff_A_02", name: "Cliff" },
  ];
  const groups = groupModelErrors(
    [
      { gyaml: "Land_HrlField_Stone_A_02", message: "Cannot read properties of undefined (reading 'extensions')" },
      { gyaml: "Land_HrlField_Stone_A_02", message: "Cannot read properties of undefined (reading 'extensions')" },
      { gyaml: "Land_HrlCastle_Cliff_A_02", message: "Cannot read properties of undefined (reading 'extensions')" },
    ],
    actors,
  );
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 3);
  assert.deepEqual(groups[0].actors.map((actor) => actor.id), ["a", "b", "c"]);
});

test("air walls and effect locators are markers", () => {
  assert.equal(isMarker("EftLoc_Smoke"), true);
  assert.equal(isMarker("FldObj_AirWall_CharacterSight_GenerateNavMesh_Body"), true);
  assert.equal(isMarker("Npc_Kokiri_F_01"), false);
});

test("a missing overlay texture is not requested", () => {
  const json = { images: [{ name: "img0" }, { name: "img1" }], textures: [{ source: 0 }] };
  assert.equal(resolveTextureIndex(json, 4, "img1"), -1);
  json.textures.push({ source: 1 });
  assert.equal(resolveTextureIndex(json, 4, "img1"), 1);
  assert.equal(resolveTextureIndex({ textures: [{ source: 0 }] }, 0, ""), 0);
});
