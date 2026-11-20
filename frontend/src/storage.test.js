import assert from "node:assert/strict";
import test from "node:test";

import { migrateStorage } from "./storage.js";

function memory(initial) {
  const data = new Map(Object.entries(initial));
  return {
    get length() {
      return data.size;
    },
    key(index) {
      return [...data.keys()][index] ?? null;
    },
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(String(key), String(value));
    },
    removeItem(key) {
      data.delete(key);
    },
  };
}

test("bancwright settings are copied to ocarinaforge and a newer value wins", () => {
  const storage = memory({
    "bancwright.stats": "0",
    "bancwright.helperBoxes": "{\"areas\":false}",
    "ocarinaforge.stats": "1",
  });
  migrateStorage(storage);
  assert.equal(storage.getItem("ocarinaforge.stats"), "1");
  assert.equal(storage.getItem("ocarinaforge.helperBoxes"), "{\"areas\":false}");
  assert.equal(storage.getItem("bancwright.stats"), "0");
});
