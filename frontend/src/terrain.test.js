import assert from "node:assert/strict";
import test from "node:test";

import { shouldReloadTerrain } from "./terrain.js";

test("an actor edit on the open scene does not refetch terrain", () => {
  const kokiri = "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs";
  const field = "Banc/HyruleField/HyruleField_Adult_Static.bcett.byml.zs";
  assert.equal(shouldReloadTerrain(null, kokiri), true);
  assert.equal(shouldReloadTerrain(kokiri, kokiri), false);
  assert.equal(shouldReloadTerrain(kokiri, field), true);
  assert.equal(shouldReloadTerrain(kokiri, undefined), false);
  assert.equal(shouldReloadTerrain(kokiri, ""), false);
  assert.equal(shouldReloadTerrain(undefined, kokiri), true);
});
