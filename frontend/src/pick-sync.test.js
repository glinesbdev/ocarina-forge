import assert from "node:assert/strict";
import test from "node:test";

import { sameIdList, syncDirtyPickIds } from "./pick-sync.js";

test("identical pick-id lists compare equal, including holes", () => {
  assert.equal(sameIdList([1, 0, 4], [1, 0, 4]), true);
  assert.equal(sameIdList([1, null], [1, 0]), true);
  assert.equal(sameIdList([1, 2], [1, 3]), false);
  assert.equal(sameIdList([1], [1, 0]), false);
  assert.equal(sameIdList(null, [1]), false);
});

test("a dirty batch whose ids did not change is not uploaded", () => {
  const batch = { pickIdsDirty: true, mesh: { userData: { pick: { ids: [1, 2] } } } };
  let writes = 0;
  const result = syncDirtyPickIds([batch], {
    idsFor: () => [1, 2],
    write() {
      writes += 1;
      return true;
    },
    previous: () => [1, 2],
    remember() {},
  });
  assert.equal(writes, 0);
  assert.equal(result.uploads, 0);
  assert.equal(batch.pickIdsDirty, false);
});

test("a dirty batch with a new id list is written once", () => {
  const batch = { pickIdsDirty: true, mesh: { userData: { pick: {} } } };
  let written = null;
  const result = syncDirtyPickIds([batch], {
    idsFor: () => [4, 0],
    write(_mesh, ids) {
      written = ids;
      return true;
    },
    previous: () => [4, 9],
    remember() {},
  });
  assert.deepEqual(written, [4, 0]);
  assert.equal(result.uploads, 1);
  assert.equal(batch.pickIdsDirty, false);
});

test("a write that leaves the texture untouched does not count as an upload", () => {
  const batch = { pickIdsDirty: true, mesh: { userData: { pick: {} } } };
  const result = syncDirtyPickIds([batch], {
    idsFor: () => [1],
    write: () => false,
    previous: () => null,
    remember() {},
  });
  assert.equal(result.uploads, 0);
});

test("a clean batch is not visited", () => {
  const batch = { pickIdsDirty: false, mesh: { userData: { pick: {} } } };
  let seen = 0;
  syncDirtyPickIds([batch, null], {
    idsFor() {
      seen += 1;
      return [];
    },
    write() {
      seen += 1;
      return true;
    },
    previous() {
      seen += 1;
      return [];
    },
    remember() {
      seen += 1;
    },
  });
  assert.equal(seen, 0);
  assert.equal(batch.pickIdsDirty, false);
});
