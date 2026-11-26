/** Permission picker helpers. The table and the manifest rules live in
 * ocarina_forge/permissions.py, which is the Longshot table at 563ca4e5.
 * This module only shapes that table for the dialog and repeats the rules
 * the panel shows before a save. The server checks them again. */

const ROOM_SHARED = new Set(["state.write", "items.grant", "content.add", "actors.spawn", "actors.remove"]);
const RAW_DATA = new Set(["romfs.read", "memory.read"]);
const OFF_PC = new Set(["net:*", "net.lan", "session", "ipc", "discord.status"]);
const SYSTEM_ONLY = new Set(["mods.inspect", "mods.control", "ipc"]);
const ORIGIN = /^(wss|https):\/\/((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*|(?:\d{1,3}\.){3}\d{1,3})(?::(\d{1,5}))?$/;

export function catalogRows(catalog) {
  const rows = [];
  for (const group of (catalog && catalog.groups) || []) {
    for (const row of group.permissions || []) {
      rows.push({ ...row, group: group.id, groupLabel: group.label });
    }
  }
  return rows;
}

export function selectionFromDeclaration(declaration) {
  const selection = new Map();
  for (const id of (declaration && declaration.permissions) || []) {
    if (typeof id === "string" && id.trim()) selection.set(id.trim(), "required");
  }
  for (const id of (declaration && declaration.optionalPermissions) || []) {
    if (typeof id === "string" && id.trim() && !selection.has(id.trim())) selection.set(id.trim(), "optional");
  }
  return selection;
}

export function nextMode(mode) {
  if (mode === "required") return "optional";
  if (mode === "optional") return "off";
  return "required";
}

export function declarationFromSelection(selection, { net = [], multiplayer = "local" } = {}) {
  const permissions = [];
  const optionalPermissions = [];
  for (const [id, mode] of selection) {
    if (mode === "required") permissions.push(id);
    else if (mode === "optional") optionalPermissions.push(id);
  }
  return {
    permissions,
    optionalPermissions,
    net: net.map((item) => String(item).trim()).filter(Boolean),
    multiplayer: multiplayer || "local",
  };
}

export function isNetOrigin(value) {
  return value === "*" || ORIGIN.test(value);
}

export function parseNetLines(text) {
  return String(text || "")
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function originOf(permission) {
  if (permission === "net:*") return "*";
  if (typeof permission === "string" && permission.startsWith("net:") && permission !== "net:") return permission.slice(4);
  return "";
}

export function addPermission(declaration, id) {
  const current = declaration || {};
  const permissions = [...(current.permissions || [])];
  const optional = [...(current.optionalPermissions || [])];
  const net = [...(current.net || [])];
  if (!permissions.includes(id) && !optional.includes(id)) permissions.push(id);
  const origin = originOf(id);
  if (origin && !net.includes(origin)) net.push(origin);
  return { ...current, permissions, optionalPermissions: optional, net };
}

function known(id, rows) {
  if (rows.some((row) => row.id === id)) return true;
  if (id.startsWith("net:") && id !== "net:" && id !== "net:*") return ORIGIN.test(id.slice(4));
  return false;
}

export function validateClient(declaration, { content = "", runtime = "", system = false, rows = [] } = {}) {
  const errors = [];
  const warnings = [];
  const required = [...(declaration.permissions || [])];
  const optional = [...(declaration.optionalPermissions || [])];
  const net = [...(declaration.net || [])];
  const multiplayer = declaration.multiplayer || "local";
  if (!["local", "all", "host"].includes(multiplayer)) errors.push('multiplayer has to be "local", "all", or "host".');

  function check(names, field) {
    const seen = new Set();
    for (const name of names) {
      if (seen.has(name)) errors.push(`${field} lists "${name}" twice.`);
      seen.add(name);
      if (rows.length && !known(name, rows)) errors.push(`${field}: "${name}" is not a permission.`);
      if (SYSTEM_ONLY.has(name) && !system) errors.push(`${name} is only for a Longshot system mod. A player mod that names it is refused.`);
    }
  }
  check(required, "permissions");
  check(optional, "optionalPermissions");
  for (const name of optional) {
    if (required.includes(name)) errors.push(`optionalPermissions also lists "${name}", which is already in permissions.`);
  }
  const origins = [];
  for (const entry of net) {
    if (!isNetOrigin(entry)) {
      errors.push(`net: "${entry}" is not "*" or a lower-case wss:// or https:// host with an optional port and no path.`);
      continue;
    }
    if (origins.includes(entry)) errors.push(`net lists "${entry}" twice.`);
    else origins.push(entry);
    const implied = entry === "*" ? "net:*" : `net:${entry}`;
    if (!required.includes(implied) && !optional.includes(implied)) {
      errors.push(`net "${entry}" needs ${implied} in permissions, or in optionalPermissions when the mod can start without it.`);
    }
  }
  const asked = new Set([...required, ...optional]);
  const blocked = [...asked].filter((name) => ROOM_SHARED.has(name));
  if (multiplayer === "local" && blocked.length) {
    errors.push(
      `multiplayer is local, and the mod asks for ${blocked.join(", ")}. A mod that changes the shared game cannot be one player's own choice. Say "all" or "host".`,
    );
  }
  const raw = [...asked].filter((name) => RAW_DATA.has(name));
  const off = [...asked].filter((name) => OFF_PC.has(name) || name.startsWith("net:"));
  if (raw.length && (off.length || net.length)) {
    errors.push(`${raw.join(", ")} cannot be held together with a way off this PC (${off.join(", ") || "net"}).`);
  }
  if (String(content || "").trim()) {
    if (!String(runtime || "").trim()) errors.push("content is only read when the manifest also sets runtime.");
    if (!required.includes("content.add") && !required.includes("content.patch")) {
      errors.push("content needs content.add in permissions, or content.patch when that module is typed patches only.");
    }
  }
  const unsafe = rows.filter((row) => asked.has(row.id) && row.risk === "unsafe").map((row) => row.id);
  if (unsafe.length) warnings.push(`Unsafe permissions: ${unsafe.join(", ")}. The game asks for these itself.`);
  return { errors, warnings };
}
