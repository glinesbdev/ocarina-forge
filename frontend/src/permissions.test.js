import assert from "node:assert/strict";
import test from "node:test";

import {
  addPermission,
  catalogRows,
  declarationFromSelection,
  isNetOrigin,
  nextMode,
  parseNetLines,
  selectionFromDeclaration,
  validateClient,
} from "./permissions.js";

const catalog = {
  groups: [
    {
      id: "game",
      label: "Game",
      permissions: [
        { id: "state.read", player: "Can see what happens in your game", risk: "low", systemOnly: false },
        { id: "state.write", player: "Can change your game and what gets saved", risk: "medium", systemOnly: false },
      ],
    },
    {
      id: "system",
      label: "System mods",
      permissions: [
        { id: "ipc", player: "Can talk to other programs", risk: "unsafe", systemOnly: true },
      ],
    },
  ],
};

test("rows stay grouped and a declaration becomes a required or optional choice", () => {
  const rows = catalogRows(catalog);
  assert.deepEqual(rows.map((row) => row.groupLabel), ["Game", "Game", "System mods"]);
  const selection = selectionFromDeclaration({
    permissions: ["state.read"],
    optionalPermissions: ["storage"],
  });
  assert.equal(selection.get("state.read"), "required");
  assert.equal(selection.get("storage"), "optional");
  assert.equal(nextMode("off"), "required");
  assert.equal(nextMode("required"), "optional");
  assert.equal(nextMode("optional"), "off");
  const back = declarationFromSelection(selection, { net: [" https://tracker.example.org "], multiplayer: "all" });
  assert.deepEqual(back.permissions, ["state.read"]);
  assert.deepEqual(back.optionalPermissions, ["storage"]);
  assert.deepEqual(back.net, ["https://tracker.example.org"]);
});

test("adding a net permission also fills the origin field", () => {
  const added = addPermission({ permissions: [], optionalPermissions: [], net: [] }, "net:wss://ap.example.org:38281");
  assert.deepEqual(added.permissions, ["net:wss://ap.example.org:38281"]);
  assert.deepEqual(added.net, ["wss://ap.example.org:38281"]);
  const star = addPermission(added, "net:*");
  assert.deepEqual(star.net, ["wss://ap.example.org:38281", "*"]);
  assert.equal(isNetOrigin("https://tracker.example.org"), true);
  assert.equal(isNetOrigin("http://bad.example/path"), false);
  assert.deepEqual(parseNetLines("https://a.example.org, *\nwss://b.example.org"), [
    "https://a.example.org",
    "*",
    "wss://b.example.org",
  ]);
});

test("the panel repeats the manifest rules", () => {
  const rows = catalogRows(catalog);
  const local = validateClient(
    { permissions: ["state.write"], optionalPermissions: [], net: [], multiplayer: "local" },
    { rows },
  );
  assert.match(local.errors.join(" "), /cannot be one player's own choice/);
  const overlap = validateClient(
    { permissions: ["state.read"], optionalPermissions: ["state.read"], net: ["http://example.org/x"], multiplayer: "local" },
    { rows },
  );
  assert.match(overlap.errors.join(" "), /already in permissions/);
  assert.match(overlap.errors.join(" "), /not "\*"/);
  const system = validateClient(
    { permissions: ["ipc"], optionalPermissions: [], net: [], multiplayer: "local" },
    { rows },
  );
  assert.match(system.errors.join(" "), /system mod/);
  const leaked = validateClient(
    { permissions: ["romfs.read", "net:*"], optionalPermissions: [], net: ["*"], multiplayer: "all" },
    { rows },
  );
  assert.match(leaked.errors.join(" "), /way off this PC/);
});
