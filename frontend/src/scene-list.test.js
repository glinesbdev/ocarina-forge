import assert from "node:assert/strict";
import test from "node:test";
import { paletteRows } from "./palette.js";
import { sceneEntries } from "./scene-list.js";

function bank(name, area = "Field", extension = ".bcett.byml.zs") {
  return { name, area, path: `Banc/${area}/${name}${extension}` };
}

test("pairs exact sibling banks once in the list and palette", () => {
  const staticBank = bank("Field_Child_Static");
  const dynamicBank = bank("Field_Child_Dynamic");
  const entries = sceneEntries([staticBank, dynamicBank]);
  assert.deepEqual(entries, [{ ...dynamicBank, name: "Field_Child", banks: [dynamicBank.path, staticBank.path] }]);
  const [row, ...others] = paletteRows({ scenes: entries, commands: [], query: staticBank.path });
  assert.equal(row.id, dynamicBank.path);
  assert.equal(others.length, 0);
});

test("keeps variants, folders and extensions distinct and preserves unpaired banks", () => {
  const sources = [
    bank("Field_Child_Dynamic"), bank("Field_Child_Static"),
    bank("Field_Adult_Static"), bank("Field_Adult_Dynamic"),
    bank("Field_Child_Alternative_Static"),
    bank("Field_Child_Alternative_Dynamic", "Field East"),
    bank("Field_Child_Alternative_Dynamic", "Field", ".bcett.byml"),
  ];
  const entries = sceneEntries(sources);
  assert.deepEqual(entries.map(({ name }) => name), ["Field_Child", "Field_Adult", ...sources.slice(4).map(({ name }) => name)]);
  assert.deepEqual(entries.slice(2).map(({ banks }) => banks), sources.slice(4).map(({ path }) => [path]));
});
