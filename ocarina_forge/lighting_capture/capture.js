// Shared lighting capture. This file does not import longshot/gfx.
// Spec revision 4, section 7.2: a mod's modules are its static imports.
// import() is not a way to load one. Section 9: a longshot module this API
// version lacks is refused at link time. A function whose feature this
// build lacks is still exported and throws NotAvailableError when called,
// so this file never calls a gfx function. It only records names and types.

import { has } from "longshot";
import log from "longshot/log";
import { state, update } from "longshot/game";
import player from "longshot/player";
import camera from "longshot/camera";
import { list } from "longshot/actors";
import { global } from "longshot/storage";

const PROBES = ["world.ray", "world.rayAll", "game.pause", "gfx.world", "gfx.shader"];
const UNAVAILABLE = ["sun", "ambient", "hemisphere", "fog", "sky", "exposure", "tonemap", "env"];
const KNOWN_GAME = ["frame", "mode", "map", "age", "timeOfDay"];
const KNOWN_PLAYER = ["position", "velocity", "life", "maxLife", "rupees", "maxRupees"];
const MAX_SAMPLES = 48;
const MOVE_METRES = 8;

const maps = {};
let last = null;
let framesSince = 120;
let actorNote = null;
let gfxNote = null;

function num(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isEffectType(type) {
  return type.startsWith("Eft") || type.includes("Fog") || type.startsWith("Effect");
}

function shrink(value, depth) {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") return value.length > 80 ? value.slice(0, 80) : value;
  if (depth <= 0) return null;
  if (Array.isArray(value)) {
    const out = [];
    for (const item of value.slice(0, 4)) out.push(shrink(item, depth - 1));
    return out;
  }
  if (typeof value !== "object") return null;
  const out = {};
  const keys = Object.keys(value).slice(0, 12);
  for (const key of keys) out[key] = shrink(value[key], depth - 1);
  return out;
}

function extras(record, known) {
  if (!record || typeof record !== "object") return {};
  const skip = new Set(known);
  const out = {};
  for (const key of Object.keys(record)) {
    if (skip.has(key)) continue;
    out[key] = shrink(record[key], 2);
    if (Object.keys(out).length >= 12) break;
  }
  return out;
}

function vec(value) {
  if (!value || typeof value !== "object") return null;
  const x = num(value.x);
  const y = num(value.y);
  const z = num(value.z);
  if (x === null || y === null || z === null) return null;
  return { x, y, z };
}

function safeMap(name) {
  return typeof name === "string" && /^[A-Za-z0-9_.-]+$/.test(name) ? name : null;
}

function activeEffects(origin) {
  let actors = [];
  try {
    actors = list({}) || [];
  } catch (error) {
    actorNote = error && error.name ? error.name : "actors";
    return [];
  }
  const rows = [];
  for (const actor of actors) {
    const type = actor && typeof actor.type === "string" ? actor.type : "";
    if (!isEffectType(type)) continue;
    const at = vec(actor.position);
    rows.push({
      type,
      key: typeof actor.key === "string" ? actor.key.slice(0, 80) : null,
      x: at && at.x,
      y: at && at.y,
      z: at && at.z,
    });
  }
  const ox = origin.x;
  const oz = origin.z;
  const distance = (row) => (
    row.x == null || row.z == null ? Infinity : (row.x - ox) ** 2 + (row.z - oz) ** 2
  );
  rows.sort((a, b) => distance(a) - distance(b));
  return rows.slice(0, 24);
}

function describeValue(value, depth) {
  if (typeof value === "function") return "function";
  if (value == null || typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return shrink(value, depth);
  }
  if (depth <= 0 || typeof value !== "object") return typeof value;
  if (Array.isArray(value)) return shrink(value, depth);
  const out = {};
  for (const key of Object.keys(value).slice(0, 16)) {
    const item = value[key];
    out[key] = typeof item === "function" ? "function" : shrink(item, depth - 1);
  }
  return out;
}

export function errorDetail(error) {
  const name = error && error.name ? String(error.name) : "error";
  const message = error && typeof error.message === "string" ? error.message.slice(0, 240) : "";
  let stack = "";
  if (error && typeof error.stack === "string") {
    const flat = error.stack.split(/\r?\n/).slice(0, 4).join(" | ");
    if (flat.length <= 360) stack = flat;
  }
  return { name, message, stack };
}

export function describeModule(mod) {
  const keys = mod && (typeof mod === "object" || typeof mod === "function")
    ? Object.keys(mod).slice(0, 24)
    : [];
  const types = {};
  if (mod && (typeof mod === "object" || typeof mod === "function")) {
    for (const key of keys) types[key] = typeof mod[key];
  }
  const world = mod && typeof mod === "object" && Object.prototype.hasOwnProperty.call(mod, "world")
    ? mod.world
    : null;
  return {
    moduleKeys: keys,
    types,
    worldType: world === null ? "null" : typeof world,
    worldKeys: world && typeof world === "object" ? Object.keys(world).slice(0, 24) : [],
    world: describeValue(world, 2),
  };
}

function persist(mapName) {
  const probes = {};
  for (const name of PROBES) probes[name] = has(name);
  const summary = {
    schema: 1,
    status: "sampling",
    unavailable: UNAVAILABLE,
    probes,
    mapsSeen: Object.keys(maps),
    actorNote,
    gfx: gfxNote,
    notes: [
      "sun, ambient, hemisphere, fog, sky, exposure, tonemap, and env stay null. The published longshot/gfx surface is drawing. gfx is a size-capped dump of export names, not a light reading.",
    ],
  };
  const bucket = maps[mapName];
  try {
    global.set("lighting", summary);
    global.set("lighting:" + mapName, { map: mapName, samples: bucket.samples });
  } catch (error) {
    log.warn("FORGE_LIGHTING store " + (error && error.name ? error.name : "error"));
    return;
  }
  log.info("FORGE_LIGHTING " + JSON.stringify({
    map: mapName,
    samples: bucket.samples.length,
    effects: bucket.samples[bucket.samples.length - 1].effects.length,
    probes,
  }));
}

function sample() {
  const game = state();
  if (!game || game.mode !== "play") return;
  const mapName = safeMap(game.map);
  if (!mapName) return;
  const who = player.state();
  const at = who && vec(who.position);
  if (!at) return;

  const bucket = maps[mapName] || (maps[mapName] = { samples: [] });
  const moved = !last || last.map !== mapName
    || Math.hypot(at.x - last.x, at.y - last.y, at.z - last.z) >= MOVE_METRES;
  framesSince += 1;
  if (bucket.samples.length >= MAX_SAMPLES) return;
  if (!moved && framesSince < 120) return;
  framesSince = 0;
  last = { map: mapName, x: at.x, y: at.y, z: at.z };

  let view = null;
  try {
    view = shrink(camera.state(), 2);
  } catch (_error) {
    view = null;
  }

  bucket.samples.push({
    x: at.x,
    y: at.y,
    z: at.z,
    timeOfDay: num(game.timeOfDay),
    frame: num(game.frame),
    age: typeof game.age === "string" ? game.age : null,
    mode: game.mode,
    sun: null,
    ambient: null,
    hemisphere: null,
    fog: null,
    sky: null,
    exposure: null,
    tonemap: null,
    env: null,
    camera: view,
    effects: activeEffects(at),
    observed: {
      game: extras(game, KNOWN_GAME),
      player: extras(who, KNOWN_PLAYER),
    },
  });
  persist(mapName);
}

export function start(loadGfx) {
  const probes = {};
  for (const name of PROBES) probes[name] = has(name);
  log.info("FORGE_LIGHTING ready " + JSON.stringify({ probes, unavailable: UNAVAILABLE }));
  Promise.resolve()
    .then(loadGfx)
    .then((note) => {
      gfxNote = note;
    })
    .catch((error) => {
      gfxNote = { present: true, import: errorDetail(error) };
      log.warn("FORGE_LIGHTING gfx " + JSON.stringify(gfxNote));
    });
  update.on(sample);
}
