import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  actorLayer,
  enabledLayers,
  filterActors,
  fullLoad,
  planAssetFetches,
  fullVisual,
  layersOff,
  linkPairs,
  mergeStoredDiff,
  parseLoadParam,
  presetLoad,
  textureFilterNotice,
  wantsMesh,
} from "./layers.js";

test("gyaml prefixes land on the same layers as the banc", () => {
  assert.equal(actorLayer("Npc_Kokiri_F_01"), "models");
  assert.equal(actorLayer("Npc_River_A"), "models");
  assert.equal(actorLayer("Enemy_DekuBaba"), "models");
  assert.equal(actorLayer("Animal_Cucco"), "models");
  assert.equal(actorLayer("Shop_Kokiri"), "models");
  assert.equal(actorLayer("Obj_Tree"), "models");
  assert.equal(actorLayer("Land_Kokiri_Ground"), "models");
  assert.equal(actorLayer("Area_Kokiri_Town"), "models");
  assert.equal(actorLayer("StartPos"), "models");
  assert.equal(actorLayer("Eft_Firefly"), "effects");
  assert.equal(actorLayer("EftLoc_Smoke"), "effects");
  assert.equal(actorLayer("Obj_Lake_Water"), "water");
  assert.equal(actorLayer("Obj_LakeHrl_WaterPlane_A_07"), "water");
  assert.equal(actorLayer("FldObj_AirWall_A"), "collision");
  assert.equal(actorLayer("Obj_Collision_A"), "collision");
  assert.equal(actorLayer("Obj_Grass_A"), "decorations");
  assert.equal(actorLayer("Obj_Lamp_A"), "lights");
});

test("presets and the load query", () => {
  assert.deepEqual(enabledLayers(fullLoad()), [
    "models", "terrain", "water", "collision", "behavior", "effects", "decorations", "lights",
  ]);
  assert.equal(presetLoad("layout").textures, false);
  assert.equal(presetLoad("layout").layers.effects, false);
  assert.equal(presetLoad("layout").layers.models, true);
  assert.equal(presetLoad("visual").layers.behavior, false);
  assert.equal(presetLoad("visual").layers.collision, false);
  assert.equal(presetLoad("logic").layers.behavior, true);
  assert.equal(presetLoad("logic").layers.models, false);
  assert.deepEqual(enabledLayers(presetLoad("terrain-water")), ["terrain", "water"]);

  const query = parseLoadParam("terrain,models");
  assert.equal(query.layers.terrain, true);
  assert.equal(query.layers.models, true);
  assert.equal(query.layers.effects, false);
  assert.equal(query.textures, true);
  assert.equal(parseLoadParam("layout").preset, "layout");
  assert.equal(parseLoadParam("models", "0").textures, false);
  assert.equal(parseLoadParam(""), null);
});

test("visibility is not a load, and stats name the layers that were not fetched", () => {
  const layout = presetLoad("layout");
  assert.deepEqual(layersOff(layout), ["behavior", "effects", "textures"]);
  assert.equal(fullVisual(fullLoad()), true);
  assert.equal(fullVisual(layout), false);
  const hidden = { ...fullLoad(), visible: { ...fullLoad().visible, water: false } };
  assert.equal(layersOff(hidden).includes("water"), false);
});

test("picking and meshes follow what was loaded", () => {
  const load = presetLoad("logic");
  const actors = [
    { id: "0x1", gyaml: "Npc_Kokiri_F_01", layer: "models" },
    { id: "0x2", gyaml: "FldObj_AirWall_A", layer: "collision", fetchModel: false },
    { id: "0x3", gyaml: "Eft_Firefly", layer: "behavior", logicOnly: true, fetchModel: false },
  ];
  const shown = filterActors(actors, load);
  assert.deepEqual(shown.map((actor) => actor.id), ["0x2", "0x3"]);
  assert.equal(wantsMesh(shown[0]), false);
  assert.equal(wantsMesh(shown[1]), false);
  assert.equal(wantsMesh({ gyaml: "Obj_Tree", layer: "models" }), true);
});

test("a save with layers off keeps unloaded edits byte for byte", () => {
  const stored = {
    "0x1000000000000008": {
      state: "edited",
      gyaml: "Eft_Firefly",
      translate: [1, 2, 3],
      set: { "Dynamic.Marker": ["str", "keep-me"] },
    },
    "0x10000000000000f4": {
      state: "edited",
      gyaml: "Obj_Lamp_A",
      translate: [4, 0, 1],
    },
  };
  const incoming = {
    "0x1000000000000001": {
      state: "edited",
      gyaml: "Npc_Kokiri_F_01",
      translate: [9, 0, 0],
    },
    "0x10000000000000f4": {
      state: "edited",
      gyaml: "Obj_Lamp_A",
      translate: [99, 99, 99],
    },
  };
  const before = JSON.stringify(stored);
  const merged = mergeStoredDiff(stored, incoming, ["models", "terrain"]);
  assert.equal(JSON.stringify(stored), before);
  assert.equal(JSON.stringify(merged["0x1000000000000008"]), JSON.stringify(stored["0x1000000000000008"]));
  assert.equal(merged["0x1000000000000008"], stored["0x1000000000000008"]);
  assert.equal(merged["0x10000000000000f4"], stored["0x10000000000000f4"]);
  assert.deepEqual(merged["0x1000000000000001"], incoming["0x1000000000000001"]);
  assert.equal(linkPairs([
    { id: "0x1", links: ["0x2"] },
    { id: "0x2", links: [] },
  ]).length, 1);
});

test("turning textures on does not request a model", () => {
  assert.deepEqual(
    planAssetFetches({
      wantGeometry: true,
      geometryResident: true,
      wantTextures: true,
      texturesResident: false,
    }),
    { models: 0, textures: 1 },
  );
  assert.deepEqual(
    planAssetFetches({
      wantGeometry: true,
      geometryResident: true,
      wantTextures: false,
      texturesResident: true,
    }),
    { models: 0, textures: 0 },
  );
});

test("turning models on does not request textures unless they are wanted and missing", () => {
  assert.deepEqual(
    planAssetFetches({
      wantGeometry: true,
      geometryResident: false,
      wantTextures: false,
      texturesResident: false,
    }),
    { models: 1, textures: 0 },
  );
  assert.deepEqual(
    planAssetFetches({
      wantGeometry: false,
      geometryResident: true,
      wantTextures: true,
      texturesResident: true,
    }),
    { models: 0, textures: 0 },
  );
});

test("models and textures load once each", () => {
  assert.deepEqual(
    planAssetFetches({
      wantGeometry: true,
      geometryResident: false,
      wantTextures: true,
      texturesResident: false,
    }),
    { models: 1, textures: 1 },
  );
  assert.deepEqual(
    planAssetFetches({
      wantGeometry: true,
      geometryResident: true,
      wantTextures: true,
      texturesResident: true,
    }),
    { models: 0, textures: 0 },
  );
});

test("textures off is a notice, and the stage bar is not the filter", () => {
  assert.equal(textureFilterNotice(fullLoad()), "");
  assert.equal(textureFilterNotice(presetLoad("logic")), "");
  const text = textureFilterNotice(presetLoad("layout"));
  assert.match(text, /Load textures is off/);
  assert.match(text, /look like a hang/);
  assert.match(text, /Actors panel/);
  assert.match(text, /stage bar/);
  assert.match(text, /already loaded/);
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  assert.match(html, /id="texture-filter-notice"/);
  assert.match(html, /Turn on load textures/);
  assert.match(main, /textureFilterNotice/);
  assert.match(main, /texture-filter-notice-enable/);
});
