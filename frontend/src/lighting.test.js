import assert from "node:assert/strict";
import test from "node:test";

import { EDITOR_LIGHTING, isEffectGizmo, nearestSample, resolveLighting, tonemapName } from "./lighting.js";

test("an empty scene keeps the editor lighting and does not invent fog", () => {
  const resolved = resolveLighting(null, { x: 0, z: 0 });
  assert.equal(resolved.source, "editor-default");
  assert.equal(resolved.lighting.fog, null);
  assert.match(resolved.note, /No capture/);
  assert.equal(resolved.lighting.tonemap, "aces");
  const told = resolveLighting({
    notes: ["No imported capture for this map."],
    unavailable: ["sun"],
  }, null);
  assert.equal(told.note, "No imported capture for this map.");
  assert.equal(told.source, "editor-default");
  assert.equal(resolved.lighting.exposure, EDITOR_LIGHTING.exposure);
  assert.deepEqual(resolved.lighting.sun.position, [18, 36, 14]);
});

test("a capture without sun or fog stays on the editor lights", () => {
  const resolved = resolveLighting({
    samples: [{ x: 1, y: 2, z: 3, timeOfDay: 9, sun: null, fog: null, effects: [] }],
    unavailable: ["sun", "fog"],
  }, { x: 1, z: 3 });
  assert.equal(resolved.source, "capture-partial");
  assert.equal(resolved.timeOfDay, 9);
  assert.equal(resolved.lighting.sun.intensity, EDITOR_LIGHTING.sun.intensity);
  assert.match(resolved.note, /not in the game API/);
});

test("the nearest sample supplies fog and the others do not", () => {
  const far = { x: 100, y: 0, z: 100, fog: { kind: "exp2", color: "#112233", density: 0.02 } };
  const near = {
    x: 1, y: 0, z: 1,
    sun: { color: "#fff2d6", intensity: 3, position: [1, 2, 3] },
    exposure: 0.9,
    tonemap: "reinhard",
    fog: { kind: "linear", color: "#abcdef", near: 5, far: 40 },
  };
  assert.equal(nearestSample([far, near], { x: 0, z: 0 }), near);
  const resolved = resolveLighting({ samples: [far, near] }, { x: 0, z: 0 });
  assert.equal(resolved.source, "capture");
  assert.equal(resolved.lighting.fog.color, "#abcdef");
  assert.equal(resolved.lighting.tonemap, "reinhard");
  assert.equal(resolved.lighting.exposure, 0.9);
  assert.deepEqual(resolved.lighting.sun.position, [1, 2, 3]);
  assert.equal(tonemapName("nope"), "aces");
});

test("effect gizmos are emitters, locators, and fog volumes", () => {
  assert.equal(isEffectGizmo({ gyaml: "Eft_Firefly", category: "effect" }), true);
  assert.equal(isEffectGizmo({ gyaml: "EftLoc_Marker", category: "effect" }), true);
  assert.equal(isEffectGizmo({ gyaml: "VolumeFogArea", category: "area" }), true);
  assert.equal(isEffectGizmo({ gyaml: "Npc_Kokiri_F_01", category: "npc" }), false);
  assert.equal(isEffectGizmo({ gyaml: "", category: "effect" }), false);
});
