/**
 * Decorations have no placement parent. Longshot's `has()` list and
 * `read.placements` do not name one (`actors.attach` is a live bone bind).
 * Grouping is the smallest parent box, expanded by GROUP_MARGIN, plus an
 * override the user can edit. Names and packs are not used. The same rules
 * are in `ocarina_forge/decoration_groups.py` and docs/formats.md.
 */

import * as THREE from "three";

import { actorLayer } from "./layers.js";

export const GROUP_MARGIN = 1.5;
export const FALLBACK_HALF = 3;
export const CANDIDATE_RADIUS = 8;

const PARENT_PREFIXES = ["Land_", "Obj_", "FldObj_"];

function vec(value, fallback) {
  if (!Array.isArray(value) || value.length < 3) return fallback.slice();
  return [Number(value[0]) || 0, Number(value[1]) || 0, Number(value[2]) || 0];
}

function poseObject(pose) {
  const pivot = new THREE.Object3D();
  const translate = vec(pose && pose.translate, [0, 0, 0]);
  const rotate = vec(pose && pose.rotate, [0, 0, 0]);
  const scale = vec(pose && pose.scale, [1, 1, 1]);
  pivot.rotation.order = "ZYX";
  pivot.position.set(translate[0], translate[1], translate[2]);
  pivot.rotation.set(rotate[0], rotate[1], rotate[2]);
  pivot.scale.set(scale[0] || 1, scale[1] || 1, scale[2] || 1);
  pivot.updateMatrix();
  return pivot;
}

export function transformPoint(point, actor) {
  const pivot = poseObject(actor);
  return new THREE.Vector3(point[0], point[1], point[2]).applyMatrix4(pivot.matrix).toArray();
}

export function worldBox(actor, bounds) {
  const authored = Boolean(bounds && bounds.min && bounds.max);
  const min = authored ? vec(bounds.min, [0, 0, 0]) : [-FALLBACK_HALF, -FALLBACK_HALF, -FALLBACK_HALF];
  const max = authored ? vec(bounds.max, [0, 0, 0]) : [FALLBACK_HALF, FALLBACK_HALF, FALLBACK_HALF];
  const corners = [];
  for (const x of [min[0], max[0]]) {
    for (const y of [min[1], max[1]]) {
      for (const z of [min[2], max[2]]) corners.push(new THREE.Vector3(x, y, z));
    }
  }
  const pivot = poseObject(actor);
  const box = new THREE.Box3().setFromPoints(corners.map((corner) => corner.applyMatrix4(pivot.matrix)));
  return { min: box.min.toArray(), max: box.max.toArray(), authored };
}

export function expandBox(box, margin = GROUP_MARGIN) {
  return {
    min: box.min.map((value) => value - margin),
    max: box.max.map((value) => value + margin),
    authored: Boolean(box.authored),
  };
}

function contains(box, point) {
  const value = vec(point, [0, 0, 0]);
  return [0, 1, 2].every((axis) => value[axis] >= box.min[axis] && value[axis] <= box.max[axis]);
}

function volume(box) {
  return [0, 1, 2].reduce((span, axis) => span * Math.max(0, box.max[axis] - box.min[axis]), 1);
}

function distance(left, right) {
  const a = vec(left, [0, 0, 0]);
  const b = vec(right, [0, 0, 0]);
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

function eligible(actor) {
  return Boolean(actor && actor.id) && !["removed", "locked", "recipe"].includes(actor.status);
}

export function isDecoration(actor) {
  if (!eligible(actor)) return false;
  const layer = actor.layer || actorLayer(actor.gyaml || "");
  return layer === "decorations";
}

export function isParentPiece(actor, hasBounds = false) {
  if (!eligible(actor) || actor.region) return false;
  if (actor.volume && !hasBounds) return false;
  const name = actor.gyaml || "";
  if (actorLayer(name) !== "models") return false;
  return PARENT_PREFIXES.some((prefix) => name.startsWith(prefix));
}

function hasAuthoredBounds(bounds) {
  return Boolean(bounds && bounds.min && bounds.max);
}

/**
 * @returns {{ childOf: Map<string, string>, byParent: Map<string, string[]>, reason: Map<string, string> }}
 */
export function resolveDecorationGroups(actors, options = {}) {
  const boundsFor = options.boundsFor || (() => null);
  const include = options.include || {};
  const exclude = options.exclude instanceof Set ? options.exclude : new Set(options.exclude || []);
  const margin = options.margin === undefined ? GROUP_MARGIN : options.margin;
  const rows = (Array.isArray(actors) ? actors : []).filter(eligible);
  const parents = rows
    .map((actor) => ({ actor, bounds: boundsFor(actor) }))
    .filter(({ actor, bounds }) => isParentPiece(actor, hasAuthoredBounds(bounds)));
  const parentIds = new Set(parents.map(({ actor }) => actor.id));
  const childOf = new Map();
  const reason = new Map();
  for (const child of rows) {
    if (!isDecoration(child)) continue;
    if (exclude.has(child.id)) {
      reason.set(child.id, "excluded");
      continue;
    }
    const forced = include[child.id];
    if (forced && parentIds.has(forced) && forced !== child.id) {
      childOf.set(child.id, forced);
      reason.set(child.id, "override");
      continue;
    }
    let best = null;
    for (const { actor: parent, bounds } of parents) {
      const world = expandBox(worldBox(parent, bounds), margin);
      if (!contains(world, child.translate)) continue;
      const size = volume(world);
      const dist = distance(child.translate, parent.translate);
      if (
        !best
        || size < best.size - 1e-6
        || (Math.abs(size - best.size) <= 1e-6 && dist < best.distance)
      ) {
        best = { id: parent.id, size, distance: dist, authored: world.authored };
      }
    }
    if (best) {
      childOf.set(child.id, best.id);
      reason.set(child.id, best.authored ? "bounds" : "fallback");
    } else {
      reason.set(child.id, "standalone");
    }
  }
  const byParent = new Map(parents.map(({ actor }) => [actor.id, []]));
  for (const [childId, parentId] of childOf) {
    if (!byParent.has(parentId)) byParent.set(parentId, []);
    byParent.get(parentId).push(childId);
  }
  return { childOf, byParent, reason };
}

export function followersFor(selectedId, resolved) {
  if (!selectedId || !resolved) return [];
  return (resolved.byParent.get(selectedId) || []).slice();
}

export function followDecoration(beforeParent, afterParent, beforeChild) {
  const from = poseObject(beforeParent);
  const to = poseObject(afterParent);
  const delta = to.quaternion.clone().multiply(from.quaternion.clone().invert());
  const offset = new THREE.Vector3().fromArray(vec(beforeChild.translate, [0, 0, 0])).sub(from.position);
  offset.applyQuaternion(delta);
  const translate = to.position.clone().add(offset).toArray();
  const child = poseObject(beforeChild);
  const spun = new THREE.Object3D();
  spun.rotation.order = "ZYX";
  spun.quaternion.copy(delta.clone().multiply(child.quaternion));
  return {
    translate,
    rotate: [spun.rotation.x, spun.rotation.y, spun.rotation.z],
    scale: vec(beforeChild.scale, [1, 1, 1]),
  };
}

export function nearbyActors(origin, actors, predicate, radius = CANDIDATE_RADIUS) {
  const point = origin && origin.translate;
  return (actors || []).filter((actor) => {
    if (!predicate(actor) || actor.id === origin.id) return false;
    return distance(point, actor.translate) <= radius;
  });
}

export function groupPanel(actor, actors, resolved) {
  if (!actor || !resolved) return null;
  if (isDecoration(actor)) {
    const parentId = resolved.childOf.get(actor.id) || null;
    const parent = parentId ? (actors || []).find((item) => item.id === parentId) : null;
    return {
      role: "decoration",
      parentId,
      parentName: parent ? (parent.name || parent.gyaml) : "",
      alternatives: nearbyActors(actor, actors, (item) => isParentPiece(item, true) && item.id !== parentId),
    };
  }
  if (!isParentPiece(actor, true)) return null;
  const memberIds = new Set(resolved.byParent.get(actor.id) || []);
  const members = (actors || []).filter((item) => memberIds.has(item.id));
  const candidates = nearbyActors(actor, actors, (item) => isDecoration(item) && !memberIds.has(item.id));
  return { role: "parent", members, candidates };
}
