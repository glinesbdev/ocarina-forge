import assert from "node:assert/strict";
import test from "node:test";

import { helperKind } from "./scene-helper.js";

test("black boards, dark planes, shadow boards, and black fades are helpers", () => {
  assert.equal(helperKind("lod0BlackBoard__Mt_BlackBorad_Room000"), "board");
  assert.equal(helperKind("lod0pPlane1__Mt_DarkPlane_A0"), "dark");
  assert.equal(helperKind("lod0ShadowBoard1__Mt_ShadowBoard_A43"), "shadow");
  assert.equal(helperKind("lod0VFX_BlackFade_01__Mt_VFX_BlackFade44"), "fade");
  assert.equal(helperKind("lod0BlackFadeA__Mt_Etc_LakeHrl_VFXBlackFade_A1"), "fade");
  assert.equal(helperKind("Land_LakeHrl_Tree_A_01", "Mt_Bark"), "");
  assert.equal(helperKind("pPlane1"), "");
});
