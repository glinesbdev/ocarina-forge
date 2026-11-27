import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";

import { actorLocked, actorShown, applyActorMatrix, batchKey, batchMissesUpload, batchSubDrawCount, canGizmo, canPick, cloneMaterialPreservingTextures, dropMapWithoutUv, imageIdentity, materialForBatch, materialForGeometry, materialToDraw, presentByDefault, refreshBatchKeys, scheduleMergedDraws } from "./instance.js";

test("the default bridge state shows the lowered model and Show hidden reveals the raised one", () => {
  const params = (flag, negate) => ["dict", { Presence: ["dict", {
    FlagName: ["str", flag], IsNegation: ["bool", negate],
  }] }];
  const gate = (negate) => ({ present: presentByDefault(params("IsHyruleFieldBridgeClose", negate)) });
  assert.equal(actorShown(gate(true)), true);
  assert.equal(actorShown(gate(false)), false);
  assert.equal(actorShown({ ...gate(false), showHidden: true }), true);
  assert.equal(presentByDefault(params("UnknownGameplayFlag", false)), true);
  assert.equal(presentByDefault(undefined), true);
});

const open = { area: "show", system: "show", object: "show" };

test("a repeated model stays pickable until its category is hidden or locked", () => {
  const shown = actorShown({
    status: "same",
    category: "object",
    volume: false,
    showHidden: false,
    categories: open,
    hideVolumes: false,
  });
  assert.equal(shown, true);
  assert.equal(canPick({ shown, locked: false }), true);
  assert.equal(canGizmo({ id: "house", shown, locked: false }), true);

  const hidden = actorShown({
    status: "same",
    category: "object",
    volume: false,
    showHidden: false,
    categories: { ...open, object: "hide" },
    hideVolumes: false,
  });
  assert.equal(hidden, false);
  assert.equal(canPick({ shown: hidden, locked: false }), false);
  assert.equal(canGizmo({ id: "house", shown: hidden, locked: false }), false);

  const locked = actorLocked({ category: "object", categories: { ...open, object: "lock" }, companion: false, pickable: true });
  assert.equal(actorShown({
    status: "same",
    category: "object",
    volume: false,
    showHidden: false,
    categories: { ...open, object: "lock" },
    hideVolumes: false,
  }), true);
  assert.equal(canPick({ shown: true, locked }), false);
  assert.equal(canGizmo({ id: "house", shown: true, locked }), false);
});

test("hidden actors and volumes follow the stage-bar switches", () => {
  assert.equal(actorShown({
    status: "hidden",
    category: "npc",
    volume: false,
    showHidden: false,
    categories: open,
    hideVolumes: false,
  }), false);
  assert.equal(actorShown({
    status: "hidden",
    category: "npc",
    volume: false,
    showHidden: true,
    categories: open,
    hideVolumes: false,
  }), true);
  assert.equal(actorShown({
    status: "same",
    category: "area",
    volume: true,
    showHidden: false,
    categories: open,
    hideVolumes: true,
  }), false);
  assert.equal(actorShown({
    status: "stock",
    category: "object",
    volume: false,
    showHidden: false,
    categories: open,
    hideVolumes: false,
    layer: "effects",
    layersVisible: { effects: false },
  }), false);
});

test("a companion that cannot be picked is locked, and a locked actor has no gizmo", () => {
  assert.equal(actorLocked({ category: "object", categories: open, companion: true, pickable: false }), true);
  assert.equal(actorLocked({ category: "object", categories: open, companion: true, pickable: true }), false);
  assert.equal(canGizmo({ id: "", shown: true, locked: false }), false);
});

test("dragging one copy writes that actor's matrix only", () => {
  const writes = [];
  const mesh = {
    setMatrixAt(index, matrix) {
      writes.push([index, matrix.slice()]);
    },
  };
  const other = {
    setMatrixAt(index, matrix) {
      writes.push(["other", index, matrix.slice()]);
    },
  };
  const moved = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 4, 0, 2, 1];
  const kept = [9];
  const slots = [
    { actor: "grass-a", index: 0, mesh, matrix: { copy(next) { this.value = next.slice(); } } },
    { actor: "grass-b", index: 1, mesh, matrix: { copy() { throw new Error("the other copy was written"); }, value: kept } },
    { actor: "grass-a", index: 3, mesh: other },
  ];
  assert.equal(applyActorMatrix(slots, "grass-a", moved), 2);
  assert.deepEqual(writes, [[0, moved], ["other", 3, moved]]);
  assert.deepEqual(slots[0].matrix.value, moved);
  assert.deepEqual(slots[1].matrix.value, kept);
  assert.equal(applyActorMatrix(slots, "grass-a", moved.concat(0).slice(0, 16).map((value, index) => index === 12 ? 8 : value)), 2);
  assert.equal(writes[2][0], 0);
  assert.equal(writes[2][1][12], 8);
  assert.equal(writes.some((write) => write[0] === 1), false);
});

test("a batched mesh has no instanceMatrix and the drag must not touch it", () => {
  const mesh = {
    setMatrixAt() {},
  };
  assert.equal(mesh.instanceMatrix, undefined);
  assert.doesNotThrow(() => applyActorMatrix([{ actor: "grass-a", index: 2, mesh }], "grass-a", [1]));
  mesh.instanceMatrix = { needsUpdate: false };
  applyActorMatrix([{ actor: "grass-a", index: 2, mesh }], "grass-a", [1]);
  assert.equal(mesh.instanceMatrix.needsUpdate, true);
});

test("r187 merged draws fold one geometry and keep a hidden instance's indirect id", () => {
  const geom = new THREE.BoxGeometry(1, 1, 1);
  const other = new THREE.BoxGeometry(0.5, 2, 0.5);
  const material = new THREE.MeshBasicMaterial();
  const mesh = new THREE.BatchedMesh(4, 5000, 8000, material);
  mesh.perObjectFrustumCulled = false;
  mesh.sortObjects = false;
  assert.equal(mesh.geometry.hasAttribute("uv"), false);
  const first = mesh.addGeometry(geom);
  const second = mesh.addGeometry(other);
  assert.equal(mesh.geometry.hasAttribute("uv"), true);
  mesh.addInstance(first);
  mesh.addInstance(first);
  const later = mesh.addInstance(second);
  const camera = new THREE.PerspectiveCamera();
  assert.equal(scheduleMergedDraws(mesh, camera, mesh.geometry, material), 2);
  assert.equal(mesh._mergedDrawInstanceCounts[0], 2);
  assert.equal(mesh._mergedDrawInstanceCounts[1], 1);
  assert.equal(batchSubDrawCount(mesh), 2);
  mesh.setVisibleAt(0, false);
  assert.equal(scheduleMergedDraws(mesh, camera, mesh.geometry, material), 2);
  assert.equal(mesh._mergedDrawInstanceCounts[0], 1);
  assert.equal(mesh._mergedDrawInstanceCounts[1], 1);
  assert.equal(mesh._indirectTexture.image.data[0], 1);
  assert.equal(mesh._indirectTexture.image.data[1], later);
  geom.dispose();
  other.dispose();
  mesh.dispose();
});

test("same-size compressed images do not share a batch identity, and a clone does", () => {
  const make = () => new THREE.CompressedTexture(
    [{ data: new Uint8Array(16), width: 8, height: 8 }],
    8,
    8,
    36492,
  );
  const left = make();
  const right = make();
  assert.notEqual(imageIdentity(left), imageIdentity(right));
  assert.equal(imageIdentity(left), imageIdentity(left.clone()));
  const part = { material: {}, geometry: {}, materialKey: "Body:0", batchKey: "flat|Body:0" };
  assert.equal(refreshBatchKeys([part], () => imageIdentity(left)), true);
  assert.equal(part.batchKey, `${imageIdentity(left)}|Body:0`);
  assert.equal(refreshBatchKeys([part], () => imageIdentity(left)), false);
});

test("a batch rebuilds when the upload lands on a material the mesh was not compiled with", () => {
  const map = { isTexture: true, uuid: "bc7" };
  const uploaded = { map };
  const plain = { map: null };
  assert.equal(materialForBatch([{ part: { material: plain } }, { part: { material: uploaded } }], plain), uploaded);
  const part = { material: uploaded, geometry: {}, materialKey: "Body:0", batchKey: "flat|Body:0" };
  const compiledFlat = { material: uploaded, userData: { forgeAlbedo: null } };
  assert.equal(batchMissesUpload([part], [{ mesh: compiledFlat }]), true);
  compiledFlat.userData.forgeAlbedo = map;
  assert.equal(batchMissesUpload([part], [{ mesh: compiledFlat }]), false);
  const other = { material: plain, userData: { forgeAlbedo: null } };
  assert.equal(batchMissesUpload([part], [{ mesh: other }]), true);
});

test("tint stays in the batch key", () => {
  const house = { image: "roof", alpha: "opaque", side: 0, tint: "ffffff", indexed: "idx", attributes: "position,uv" };
  assert.notEqual(batchKey(house), batchKey({ ...house, tint: "c8b090" }));
});

test("a mapped geometry without uv loses only its own map", () => {
  const shared = {
    map: { id: 1 },
    clone() {
      return { map: this.map, needsUpdate: false, userData: {} };
    },
  };
  const bare = { name: "grass-card", geometry: { hasAttribute: () => false }, material: shared, children: [] };
  const textured = { name: "trunk", geometry: { hasAttribute: () => true }, material: shared, children: [] };
  const names = dropMapWithoutUv({ children: [bare, textured] });
  assert.deepEqual(names, ["grass-card"]);
  assert.equal(bare.material.map, null);
  assert.equal(textured.material.map.id, 1);
  assert.equal(shared.map.id, 1);
});

test("AO and light maps without their uv set are dropped and a mapped neighbour keeps them", () => {
  const shared = {
    map: { id: 1, channel: 0 },
    aoMap: { id: 2, channel: 0 },
    lightMap: { id: 3, channel: 1 },
    normalMap: { id: 4, channel: 0 },
    aoMapIntensity: 1,
    lightMapIntensity: 1,
    clone() {
      return {
        map: this.map,
        aoMap: this.aoMap,
        lightMap: this.lightMap,
        normalMap: this.normalMap,
        aoMapIntensity: this.aoMapIntensity,
        lightMapIntensity: this.lightMapIntensity,
        needsUpdate: false,
        userData: {},
      };
    },
  };
  const bare = { name: "card", geometry: { hasAttribute: () => false }, material: shared, children: [] };
  const textured = {
    name: "trunk",
    geometry: { hasAttribute: (name) => name === "uv" || name === "uv1" },
    material: shared,
    children: [],
  };
  dropMapWithoutUv({ children: [bare, textured] });
  assert.equal(bare.material.map, null);
  assert.equal(bare.material.aoMap, null);
  assert.equal(bare.material.lightMap, null);
  assert.equal(bare.material.normalMap, null);
  assert.equal(bare.material.aoMapIntensity, 0);
  assert.equal(textured.material, shared);
  assert.equal(shared.aoMap.id, 2);
});

test("an AO map on uv1 stays when that set exists and a channel-0 map still needs uv", () => {
  const ao = { channel: 1 };
  const albedo = { channel: 0 };
  const material = { aoMap: ao, map: albedo, aoMapIntensity: 1 };
  const mesh = {
    geometry: { hasAttribute: (name) => name === "uv1" },
    material,
    children: [],
  };
  const names = dropMapWithoutUv(mesh);
  assert.deepEqual(names, ["mesh"]);
  assert.equal(mesh.material.aoMap, ao);
  assert.equal(mesh.material.map, null);
});

test("an albedo colour node is not sampled when the batch geometry has no uv", () => {
  const map = { isTexture: true, channel: 0 };
  const colorNode = { sampled: "uv" };
  const shared = {
    map,
    colorNode,
    userData: { albedoNode: true },
    clone() {
      return { map: this.map, colorNode: this.colorNode, userData: { ...this.userData }, needsUpdate: false };
    },
  };
  const bare = { name: "card", geometry: { hasAttribute: () => false }, material: shared, children: [] };
  const textured = { name: "wall", geometry: { hasAttribute: (name) => name === "uv" }, material: shared, children: [] };
  dropMapWithoutUv({ children: [bare, textured] });
  assert.equal(bare.material.map, null);
  assert.equal(bare.material.colorNode, null);
  assert.equal(bare.material.userData.albedoNode, false);
  assert.equal(textured.material, shared);
  assert.equal(shared.colorNode, colorNode);
  const drawn = materialForGeometry(shared, { hasAttribute: () => false });
  assert.equal(drawn.map, null);
  assert.equal(drawn.colorNode, null);
  assert.equal(materialForGeometry(shared, { hasAttribute: (name) => name === "uv" }), shared);
  const uv1 = { isTexture: true, channel: 1 };
  const channeled = {
    map: uv1,
    userData: {},
    clone() { return { map: this.map, userData: {}, needsUpdate: false }; },
  };
  assert.equal(materialForGeometry(channeled, { hasAttribute: (name) => name === "uv1" }), channeled);
  assert.equal(materialForGeometry(channeled, { hasAttribute: (name) => name === "uv" }).map, null);
});

test("a map can use an authored UV channel other than uv", () => {
  const map = { channel: 1 };
  const material = { map };
  const mesh = { geometry: { hasAttribute: (name) => name === "uv1" }, material };
  assert.deepEqual(dropMapWithoutUv(mesh), []);
  assert.equal(mesh.material.map, map);
});

test("cloneMaterialPreservingTextures keeps userData maps and does not serialize them", () => {
  const moss = {
    isTexture: true,
    uuid: "moss",
    toJSON() {
      throw new Error("texture was serialized");
    },
  };
  const material = {
    userData: { mossMap: moss, orm1Uniform: { value: moss }, note: "keep" },
    clone() {
      return { userData: JSON.parse(JSON.stringify(this.userData)), cloned: true };
    },
  };
  const copy = cloneMaterialPreservingTextures(material);
  assert.equal(material.userData.mossMap, moss);
  assert.equal(material.userData.orm1Uniform.value, moss);
  assert.equal(material.userData.note, "keep");
  assert.equal(copy.cloned, true);
  assert.equal(copy.userData.mossMap, moss);
  assert.equal(copy.userData.orm1Uniform.value, moss);
  assert.equal(copy.userData.note, "keep");
});

test("materialToDraw returns the part material when the mesh is still flat", () => {
  const map = { isTexture: true, channel: 0 };
  const mapped = { map };
  const mesh = { material: { map: null }, geometry: { hasAttribute: (name) => name === "uv" } };
  const decision = materialToDraw(mesh, [{ material: mapped }]);
  assert.equal(decision.material, mapped);
  assert.equal(decision.map, map);
  assert.equal(decision.missingUv, false);
  assert.equal(materialToDraw({ material: mapped, geometry: mesh.geometry }, [{ material: mapped }]), null);
  const missing = materialToDraw(
    { material: { map: null }, geometry: { hasAttribute: () => false } },
    [{ material: mapped }],
  );
  assert.equal(missing.missingUv, true);
  assert.equal(missing.material, null);
});

test("dropMapWithoutUv logs once per material at debug level", () => {
  const warns = [];
  const debugs = [];
  const originalWarn = console.warn;
  const originalDebug = console.debug;
  console.warn = (message) => warns.push(String(message));
  console.debug = (message) => debugs.push(String(message));
  try {
    const shared = {
      map: { id: 1 },
      userData: {},
      clone() {
        return { map: this.map, needsUpdate: false, userData: {} };
      },
    };
    const first = { name: "lod0Eye_Plane__Mt_Black0", geometry: { hasAttribute: () => false }, material: shared, children: [] };
    const second = { name: "lod0Eye_Plane__Mt_Black1", geometry: { hasAttribute: () => false }, material: shared, children: [] };
    const names = dropMapWithoutUv({ children: [first, second] });
    assert.deepEqual(names, ["lod0Eye_Plane__Mt_Black0"]);
    assert.equal(first.material.map, null);
    assert.equal(second.material.map, null);
    assert.equal(warns.length, 0);
    assert.equal(debugs.length, 1);
    assert.match(debugs[0], /has a map and no uv/);
  } finally {
    console.warn = originalWarn;
    console.debug = originalDebug;
  }
});

test("copies that share an albedo share a batch key", () => {
  const house = { image: "roof", alpha: "opaque", side: 0, tint: "ffffff", indexed: "idx", attributes: "position,uv" };
  assert.equal(batchKey(house), batchKey({ ...house }));
  assert.notEqual(batchKey(house), batchKey({ ...house, image: "wall" }));
});
