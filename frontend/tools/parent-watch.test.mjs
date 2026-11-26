import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { parentAlive, watchParent } from "./parent-watch.mjs";

function missing() {
  const error = new Error("gone");
  error.code = "ESRCH";
  throw error;
}

test("a missing or dead pid is not alive", () => {
  assert.equal(parentAlive(0), false);
  assert.equal(parentAlive(-1), false);
  assert.equal(parentAlive(Number.NaN), false);
  assert.equal(parentAlive(424242, missing), false);
});

test("permission errors still count as alive", () => {
  assert.equal(parentAlive(7, () => {
    const error = new Error("denied");
    error.code = "EPERM";
    throw error;
  }), true);
  assert.equal(parentAlive(7, () => {}), true);
});

test("watchParent exits immediately when the parent is already gone", () => {
  let code = null;
  const timer = watchParent(123, { exit: (status) => { code = status; }, kill: missing });
  assert.equal(code, 0);
  assert.equal(timer, null);
});

test("watchParent exits after the parent disappears", async () => {
  let alive = true;
  let code = null;
  const timer = watchParent(42, {
    exit: (status) => { code = status; },
    intervalMs: 20,
    kill: () => {
      if (!alive) missing();
    },
  });
  assert.equal(code, null);
  assert.ok(timer);
  alive = false;
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(code, 0);
  clearInterval(timer);
});

test("the meshopt helper watches its parent and still exits on stdin EOF", () => {
  const source = readFileSync(new URL("./meshopt-worker.mjs", import.meta.url), "utf8");
  assert.match(source, /watchParent/);
  assert.match(source, /OCARINA_FORGE_MESHOPT_PARENT/);
  assert.match(source, /process\.stdin\.on\("end"/);
});
