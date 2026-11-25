import assert from "node:assert/strict";
import test from "node:test";

import { fuzzyScore, paletteRows } from "./palette.js";

const actors = [
  { id: "0x1000000000000001", name: "Fado", gyaml: "Npc_Kokiri_F_01", category: "npc" },
  { id: "0x1000000000000002", name: "Mido", gyaml: "Npc_Kokiri_M_01", category: "npc" },
  { id: "0x1000000000000009", name: "Twin", gyaml: "Obj_Tree", category: "object" },
];

const scenes = [
  { path: "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs", name: "KokiriForest_Child_Static", area: "KokiriForest" },
  { path: "Banc/Spot04/Spot04_Static.bcett.byml.zs", name: "Spot04_Static", area: "Spot04" },
];

test("a hash, a class, and a name each find the actor", () => {
  assert.equal(paletteRows({ actors, scenes, query: "0x1000000000000001" })[0].label, "Fado");
  assert.equal(paletteRows({ actors, scenes, query: "kokiri_f" })[0].label, "Fado");
  assert.equal(paletteRows({ actors, scenes, query: "mido" })[0].id, "0x1000000000000002");
});

test("a scene path and a command share the same list", () => {
  const scene = paletteRows({ actors, scenes, query: "spot04" })[0];
  assert.equal(scene.kind, "scene");
  assert.match(scene.id, /Spot04_Static/);
  const command = paletteRows({ actors, scenes, query: "snap ground" })[0];
  assert.equal(command.kind, "command");
  assert.equal(command.id, "snap");
});

test("an empty query lists commands before actors", () => {
  const rows = paletteRows({ actors, scenes, query: "" });
  assert.equal(rows[0].kind, "command");
  assert.ok(rows.some((row) => row.kind === "actor"));
});

test("a query that matches nothing is an empty list", () => {
  assert.equal(fuzzyScore("zzz", "Fado"), 0);
  assert.deepEqual(paletteRows({ actors, scenes, query: "zzzz-nope" }), []);
});
