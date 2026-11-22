import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import {
  createGpuTextureLoader,
  gpuTexturesEnabled,
  repairCompressedTexture,
  textureSupport,
} from "./gpu-texture.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { acceleratedRaycast, computeBoundsTree, disposeBoundsTree } from "three-mesh-bvh";

import { boundsGap, instanceLod, isTerrainProxy, resolveLod, transformSphere } from "./cull.js";
import { canHoverFocus } from "./focus.js";
import { shouldReloadTerrain } from "./terrain.js";
import { createBackendRenderer, drawTotal } from "./backend.js";
import { formatStats, keepRendering } from "./frame.js";
import { actorLocked, actorShown, applyActorMatrix, batchKey, canGizmo, canPick, dropMapWithoutUv, groupGeometryDraws, patchBatchedDraw } from "./instance.js";
import { createGpuProfile, sampleGpuFrame } from "./gpu-profile.js";
import { chooseHit } from "./pick.js";
import { chooseSnap } from "./snap.js";
import { resolveTextureIndex } from "./problems.js";

THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;

export const CATEGORY_COLOR = {
  npc: 0xe0b15a,
  enemy: 0xe15b45,
  object: 0x3dbeb6,
  land: 0x7dae4a,
  effect: 0xc58be0,
  animal: 0xe08a3c,
  system: 0x8aa4c0,
  area: 0x6f8f78,
  other: 0xd7dccf,
};

/**
 * Actors are boxes until a converted glTF arrives. The gizmo stays on `pivot`.
 * Volumes (areas, system actors, large land) are edge outlines. The fill is
 * drawn only while that volume is selected, so overlapping areas cannot wash
 * the scene. Picks prefer a solid mesh over a volume.
 */
export async function createViewport(host, hooks) {
  const created = await createBackendRenderer(THREE, globalThis.location ? globalThis.location.search : "");
  const renderer = created.renderer;
  const backendName = created.backend;
  const backendNote = created.note;
  let terrainNodes = null;
  let mapNodes = null;
  let blendNodes = null;
  if (created.nodes) {
    terrainNodes = await import("./terrain-node.js");
    mapNodes = await import("./cutout-node.js");
    blendNodes = await import("./blend-node.js");
  }
  if (renderer.backend && renderer.backend.isWebGPUBackend === true) patchBatchedDraw(renderer.backend);
  const gpuProfile = renderer.backend && renderer.backend.isWebGPUBackend === true ? createGpuProfile(renderer) : null;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x141b16);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 8000);
  camera.position.set(12, 9, 14);
  camera.rotation.order = "YXZ";

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = false;
  if (renderer.shadowMap && "autoUpdate" in renderer.shadowMap) renderer.shadowMap.autoUpdate = false;
  if (renderer.isWebGPURenderer && typeof renderer.onDeviceLost === "function") {
    const previousLost = renderer.onDeviceLost.bind(renderer);
    renderer.onDeviceLost = (info) => {
      previousLost(info);
      if (hooks.onDeviceLost) hooks.onDeviceLost("The WebGPU device was lost. Reload without ?webgpu=1 to use WebGL.");
    };
  }
  host.appendChild(renderer.domElement);
  const canvas = renderer.domElement;
  canvas.tabIndex = 0;
  canvas.setAttribute("aria-label", "Scene view");
  let hoverTookFocus = false;
  host.addEventListener("pointerenter", () => {
    const doc = host.ownerDocument;
    if (!canHoverFocus(doc.activeElement, Boolean(doc.querySelector("dialog[open]")))) return;
    if (doc.activeElement === canvas) return;
    canvas.focus({ preventScroll: true });
    hoverTookFocus = doc.activeElement === canvas;
  });
  host.addEventListener("pointerleave", () => {
    if (!hoverTookFocus) return;
    hoverTookFocus = false;
    const doc = host.ownerDocument;
    if (doc.activeElement === canvas) canvas.blur();
  });
  canvas.addEventListener("pointerdown", () => {
    hoverTookFocus = false;
  });

  const labels = new CSS2DRenderer();
  labels.domElement.className = "label-layer";
  host.appendChild(labels.domElement);

  scene.add(new THREE.HemisphereLight(0xf3f6ea, 0x2c3a2e, 0.9));
  const sun = new THREE.DirectionalLight(0xfff6df, 1.2);
  sun.position.set(18, 36, 14);
  sun.castShadow = false;
  scene.add(sun);
  scene.add(new THREE.AmbientLight(0x8ea090, 0.25));

  const orbit = new OrbitControls(camera, renderer.domElement);
  orbit.enableDamping = true;
  orbit.dampingFactor = 0.08;
  orbit.target.set(0, 0, 0);
  orbit.maxPolarAngle = Math.PI * 0.49;
  // Fly is the camera. Left-drag stays a click. Alt+left orbits, middle pans, the wheel zooms.
  orbit.mouseButtons = {
    LEFT: -1,
    MIDDLE: THREE.MOUSE.PAN,
    RIGHT: -1,
  };

  const transform = new TransformControls(camera, renderer.domElement);
  transform.setMode("translate");
  transform.setSpace("world");
  scene.add(transform.getHelper());

  const boxGeometry = new THREE.BoxGeometry(1, 1, 1);
  const edgesGeometry = new THREE.EdgesGeometry(boxGeometry);
  const entries = new Map();
  const batches = [];
  const parsedTemplates = new Map();
  const templateOrder = [];
  const statsEl = document.createElement("div");
  statsEl.className = "view-stats";
  host.appendChild(statsEl);
  let showStats = true;
  let naiveDraws = 0;
  let running = true;
  let frameHandle = 0;
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  const ktx2Loader = new KTX2Loader().setTranscoderPath("/basis/");
  ktx2Loader.detectSupport(renderer);
  const gpuCaps = textureSupport(renderer.getContext());
  const gl = renderer.getContext();
  let loadingGyaml = "";
  function reportGpuFallback(reason) {
    const name = loadingGyaml || "texture";
    if (hooks.onModelError) hooks.onModelError(name, `GPU texture fell back to the bitmap (${reason}).`);
  }
  loader.setKTX2Loader(createGpuTextureLoader({
    THREE,
    gl,
    caps: gpuCaps,
    enabled: () => gpuTexturesEnabled(window.location.search, window.localStorage.getItem("ocarinaforge.gpuTextures")),
    onFallback: reportGpuFallback,
    // vkFormat 0 is Basis. three's transcoder is the retry for that file.
    basisParse: (buffer, onLoad, onError) => ktx2Loader.parse(buffer, onLoad, onError),
  }));
  const templateCache = new Map();
  const modelRequests = new Map();
  const GLB_SLOTS = 8;
  const metaCache = new Map();
  const categories = { area: "show", system: "show" };
  const boxCategories = {
    npc: true,
    enemy: true,
    object: true,
    land: true,
    effect: true,
    animal: true,
    area: false,
    system: false,
    other: true,
  };
  let hideVolumes = false;
  let showGround = false;
  let showWater = true;
  let showSub = false;
  let drawDistance = 0;
  let showTerrainDebug = false;
  let showBlendMask = false;
  let generation = 0;
  let rebuildTimer = 0;
  let loadQueue = null;
  let pendingLoads = 0;
  const loadDelta = new THREE.Vector3();
  const loadForward = new THREE.Vector3();
  let terrainRoot = null;
  let snapOn = false;
  let terrainBanc = null;
  let terrainToken = 0;
  let groundGrid = null;
  let lastClick = null;
  let grid = null;
  let selected = null;
  let hovered = null;
  let showLabels = false;
  let showHidden = false;
  let looking = false;
  let orbitGesture = false;
  let orbitArmed = false;
  let orbitMoved = false;
  let orbitSettle = 0;
  const savedOrbitTarget = new THREE.Vector3();
  let lookLocked = false;
  let lastPointer = null;
  let gizmoUsed = false;
  const keys = new Set();
  const clock = new THREE.Clock();

  function clearGrid() {
    if (!grid) return;
    scene.remove(grid);
    grid.geometry.dispose();
    grid.material.dispose();
    grid = null;
  }

  function disposeEntry(entry) {
    entry.pivot.removeFromParent();
    entry.material.dispose();
    entry.edgeMaterial.dispose();
    if (entry.label) entry.label.element.remove();
  }

  function disposeTerrainObject(root) {
    if (!root) return;
    root.traverse((child) => {
      if (child.geometry) child.geometry.dispose();
    });
    root.removeFromParent();
  }

  function clearTerrain() {
    terrainToken += 1;
    disposeTerrainObject(terrainRoot);
    terrainRoot = null;
    terrainBanc = null;
  }

  function clearGround() {
    if (!groundGrid) return;
    if (groundGrid.geometry) groundGrid.geometry.dispose();
    const materials = Array.isArray(groundGrid.material) ? groundGrid.material : [groundGrid.material];
    for (const material of materials) if (material) material.dispose();
    groundGrid.removeFromParent();
    groundGrid = null;
  }

  function applyGround() {
    if (groundGrid) groundGrid.visible = showGround;
    if (terrainRoot) terrainRoot.visible = showGround;
  }

  function applyWater() {
    for (const batch of batches) {
      if (batch.water) batch.mesh.visible = showWater;
    }
  }

  function clearActors(options = {}) {
    transform.detach();
    if (rebuildTimer) {
      clearTimeout(rebuildTimer);
      rebuildTimer = 0;
    }
    // Dropping the batches here is the blink after a save: every model leaves
    // until the replacement BatchedMesh objects finish building. A scene change
    // keeps them until rebuildBatches has the next set, unless the viewport is
    // going away (keepBatches stays false).
    if (!options.keepBatches) clearBatches();
    for (const entry of entries.values()) disposeEntry(entry);
    entries.clear();
  }

  function helperVisible(entry) {
    if (!entry.pivot.visible) return false;
    if (entry.key === selected || entry.key === hovered) return true;
    if (boxCategories[entry.category] === false) return false;
    return !entry.parts || !entry.parts.length;
  }

  function syncGhost(entry) {
    const show = helperVisible(entry);
    const volume = Boolean(entry.volume);
    const on = entry.key === selected;
    if (volume) {
      entry.material.opacity = on ? 0.08 : 0;
      entry.material.depthWrite = false;
      entry.edgeMaterial.color.set(on ? 0xf2d7a2 : 0xb7c0b4);
      entry.edgeMaterial.opacity = on ? 1 : 0.66;
    }
    entry.mesh.visible = show && (!volume || on);
    entry.edges.visible = show;
  }

  function paint(entry) {
    const on = entry.key === selected;
    const marked = entry.status === "edited" || entry.status === "added" || entry.status === "recipe";
    const ghost = !entry.parts || !entry.parts.length;
    entry.material.depthWrite = false;
    if (!(ghost && entry.volume)) {
      entry.material.opacity = on ? 0.1 : entry.status === "hidden" ? 0.02 : 0.04;
      entry.edgeMaterial.color.set(on || marked ? 0xf2d7a2 : entry.color);
      entry.edgeMaterial.opacity = on ? 1 : 0.82;
    }
    syncGhost(entry);
    tintInstance(entry);
  }

  function ensureLabel(entry) {
    if (entry.label) return;
    const el = document.createElement("div");
    el.className = "actor-label";
    el.textContent = entry.labelText;
    const label = new CSS2DObject(el);
    label.position.set(0, 0.62, 0);
    label.center.set(0.5, 1);
    entry.pivot.add(label);
    entry.label = label;
  }

  function syncLabels() {
    const wanted = new Set();
    if (selected) wanted.add(selected);
    if (hovered) wanted.add(hovered);
    if (showLabels) {
      const ranked = [...entries.values()].filter(
        (entry) =>
          entry.pivot.visible &&
          entry.key !== selected &&
          entry.key !== hovered &&
          boxCategories[entry.category] !== false
      );
      ranked.sort((a, b) => camera.position.distanceToSquared(a.pivot.position) - camera.position.distanceToSquared(b.pivot.position));
      for (const entry of ranked.slice(0, 24)) wanted.add(entry.key);
    }
    for (const entry of entries.values()) {
      const show = entry.pivot.visible && wanted.has(entry.key);
      if (!show) {
        if (entry.label) entry.label.visible = false;
        continue;
      }
      ensureLabel(entry);
      entry.label.visible = true;
      entry.label.element.classList.toggle("is-selected", entry.key === selected);
    }
  }

  function frameObjects(objects) {
    const box = new THREE.Box3();
    for (const object of objects) box.expandByObject(object);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() * 0.55, 3);
    camera.position.copy(center).add(new THREE.Vector3(radius * 0.7, radius * 0.5, radius * 0.7));
    orbit.target.copy(center);
    camera.near = 0.05;
    camera.far = Math.max(4000, radius * 80);
    camera.updateProjectionMatrix();
    orbit.update();
    requestRender();
  }

  function rebuildGrid() {
    clearGrid();
    const box = new THREE.Box3();
    for (const entry of entries.values()) {
      if (entry.pivot.visible) box.expandByObject(entry.pivot);
    }
    if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(), new THREE.Vector3(10, 1, 10));
    const size = box.getSize(new THREE.Vector3());
    const span = Math.max(10, Math.ceil(Math.max(size.x, size.z) * 1.3));
    const divisions = Math.min(60, Math.max(8, Math.round(span / 2)));
    grid = new THREE.GridHelper(span, divisions, 0x7d8b74, 0x2c3830);
    grid.position.y = Math.min(0, box.min.y);
    scene.add(grid);
  }

  function mountActor(actor, index) {
    const key = actor.id || `locked-${index}`;
    const recipe = actor.status === "recipe";
    const color = recipe ? 0xc9b6f2 : CATEGORY_COLOR[actor.category] || CATEGORY_COLOR.other;
    const volume = Boolean(actor.volume);
    const region = Boolean(actor.region);
    const material = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.04,
      depthWrite: false,
      depthTest: true,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(boxGeometry, material);
    const edgeMaterial = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.82,
      depthWrite: false,
    });
    const edges = new THREE.LineSegments(edgesGeometry, edgeMaterial);
    const pivot = new THREE.Group();
    pivot.add(mesh);
    pivot.add(edges);
    const [x, y, z] = actor.translate;
    const [rx, ry, rz] = actor.rotate;
    const [sx, sy, sz] = actor.scale;
    pivot.position.set(x, y, z);
    placeRotate(pivot, [rx, ry, rz]);
    pivot.scale.set(sx || 1, sy || 1, sz || 1);
    pivot.userData.actorId = actor.id;
    const gate = {
      status: actor.status,
      category: actor.category,
      volume,
      showHidden,
      categories,
      hideVolumes,
      companion: Boolean(actor.companion),
      pickable: actor.pickable !== false,
    };
    pivot.visible = actorShown(gate);
    scene.add(pivot);
    const entry = {
      key,
      id: actor.id,
      gyaml: actor.gyaml,
      category: actor.category,
      status: actor.status,
      volume,
      region,
      companion: Boolean(actor.companion),
      parts: null,
      farNames: null,
      slots: [],
      pickable: actor.pickable !== false,
      locked: actorLocked(gate),
      color,
      pivot,
      mesh,
      material,
      edges,
      edgeMaterial,
      model: null,
      modelMeta: metaCache.get(actor.gyaml) || null,
      name: actor.name || "",
      companionLabel: actor.companionLabel || "",
      recipeNote: (actor.recipe && actor.recipe.note) || "",
      label: null,
      labelText: "",
    };
    paintLabel(entry);
    entries.set(key, entry);
    return entry;
  }

  function setActors(actors, options = {}) {
    clearActors({ keepBatches: true });
    // The field is not part of the actor diff. Clearing it here is what turned
    // the ground black for a couple of seconds after POST /api/scene/edit.
    const token = ++generation;
    actors.forEach((actor, index) => mountActor(actor, index));
    if (selected && !entries.has(selected)) selected = null;
    for (const entry of entries.values()) paint(entry);
    attach();
    if (options.frame) {
      const solids = [...entries.values()].filter((entry) => entry.pivot.visible && !entry.volume);
      const visible = (solids.length ? solids : [...entries.values()].filter((entry) => entry.pivot.visible)).map((entry) => entry.pivot);
      frameObjects(visible.length ? visible : [...entries.values()].map((entry) => entry.pivot));
      rebuildGrid();
    }
    syncLabels();
    requestRender();
    loadModels(token, actors);
    if (shouldReloadTerrain(terrainBanc, options.banc)) {
      const banc = options.banc;
      const next = ++terrainToken;
      terrainBanc = banc;
      // The static map can be hundreds of thousands of triangles. Let the
      // actor requests start first, and never raycast the map, so picking
      // stays on the actors while the terrain converts. A later actor edit
      // does not reach this branch: the banc has not changed.
      setTimeout(() => {
        if (next === terrainToken) loadTerrain(next, banc);
      }, 300);
    }
  }

  function placeRotate(pivot, rotate) {
    // Banc Rotate is extrinsic XYZ: R = Rz · Ry · Rx on a column vector.
    // three.js order ZYX is that composition. Picking uses the pivot matrix,
    // and the gizmo writes these same components back.
    const [rx, ry, rz] = rotate || [0, 0, 0];
    pivot.rotation.set(rx || 0, ry || 0, rz || 0, "ZYX");
  }

  function fileRotate(pivot) {
    if (pivot.rotation.order !== "ZYX") pivot.rotation.order = "ZYX";
    const r = pivot.rotation;
    return [r.x, r.y, r.z];
  }

  function actorLabel(actor, recipe, source) {
    let text;
    if (actor.companion) text = `${actor.name || actor.gyaml} · ${actor.companionLabel || "companion"}`;
    else if (recipe) text = `${actor.name || actor.gyaml} · ${(actor.recipe && actor.recipe.note) || "added by build"}`;
    else if (actor.name && actor.name !== actor.gyaml) text = `${actor.name} · ${actor.gyaml}`;
    else text = actor.name || actor.gyaml;
    if (source) text += ` · ${source}`;
    return text;
  }

  function paintLabel(entry) {
    const source = entry.modelMeta && entry.modelMeta.source;
    entry.labelText = actorLabel(
      {
        name: entry.name,
        gyaml: entry.gyaml,
        companion: entry.companion,
        companionLabel: entry.companionLabel,
        recipe: entry.recipeNote ? { note: entry.recipeNote } : null,
      },
      entry.status === "recipe",
      source,
    );
    if (entry.label) entry.label.element.textContent = entry.labelText;
  }

  function applyActorRecord(entry, actor) {
    const recipe = actor.status === "recipe";
    const color = recipe ? 0xc9b6f2 : CATEGORY_COLOR[actor.category] || CATEGORY_COLOR.other;
    const [x, y, z] = actor.translate;
    const [rx, ry, rz] = actor.rotate;
    const [sx, sy, sz] = actor.scale || [1, 1, 1];
    entry.pivot.position.set(x, y, z);
    placeRotate(entry.pivot, [rx, ry, rz]);
    entry.pivot.scale.set(sx || 1, sy || 1, sz || 1);
    entry.status = actor.status;
    entry.category = actor.category;
    entry.gyaml = actor.gyaml;
    entry.name = actor.name || "";
    entry.companionLabel = actor.companionLabel || "";
    entry.recipeNote = (actor.recipe && actor.recipe.note) || "";
    if (!entry.parts || !entry.parts.length) entry.volume = Boolean(actor.volume);
    if (actor.region !== undefined) entry.region = Boolean(actor.region);
    entry.companion = Boolean(actor.companion);
    entry.pickable = actor.pickable !== false;
    entry.color = color;
    entry.material.color.setHex(color);
    entry.locked = actorLocked({
      category: actor.category,
      categories,
      companion: entry.companion,
      pickable: entry.pickable,
    });
    entry.pivot.visible = actorShown({
      status: actor.status,
      category: actor.category,
      volume: entry.volume,
      showHidden,
      categories,
      hideVolumes,
    });
    paintLabel(entry);
    paint(entry);
    syncInstanceMatrix(entry);
  }

  function patchActors(actors) {
    if (!Array.isArray(actors)) return false;
    const rows = [];
    for (const actor of actors) {
      const entry = actor && actor.id ? entries.get(actor.id) : null;
      if (!entry) return false;
      rows.push([entry, actor]);
    }
    for (const [entry, actor] of rows) applyActorRecord(entry, actor);
    syncBatchVisibility();
    syncLabels();
    attach();
    requestRender();
    return true;
  }

  function fitBounds(entry, bounds) {
    if (!entry || !bounds || !bounds.min || !bounds.max) return;
    const size = [0, 1, 2].map((axis) => Math.max(Math.abs(bounds.max[axis] - bounds.min[axis]), 0.05));
    const center = [0, 1, 2].map((axis) => (bounds.min[axis] + bounds.max[axis]) / 2);
    entry.mesh.scale.set(size[0], size[1], size[2]);
    entry.mesh.position.set(center[0], center[1], center[2]);
    if (entry.edges) {
      entry.edges.scale.copy(entry.mesh.scale);
      entry.edges.position.copy(entry.mesh.position);
    }
  }

  function hideSlots(entry) {
    for (const slot of entry.slots || []) {
      slot.shown = false;
      if (slot.mesh && slot.mesh.setVisibleAt) slot.mesh.setVisibleAt(slot.index, false);
      if (slot.mesh) slot.mesh._visibilityChanged = true;
    }
  }

  function adoptModel(entry) {
    if (entry.region) return false;
    const sibling = [...entries.values()].find(
      (other) => other !== entry && other.gyaml === entry.gyaml && other.parts && other.parts.length,
    );
    if (!sibling) return false;
    entry.parts = sibling.parts;
    entry.farNames = sibling.farNames;
    entry.templateUrl = sibling.templateUrl;
    entry.modelMeta = sibling.modelMeta;
    entry.volume = false;
    entry.mesh.visible = false;
    queueRebuild();
    return true;
  }

  function reconcileActors(actors) {
    if (!entries.size || !Array.isArray(actors)) return false;
    const wanted = new Map();
    actors.forEach((actor, index) => wanted.set(actor.id || `locked-${index}`, { actor, index }));
    for (const [key, entry] of [...entries.entries()]) {
      if (wanted.has(key)) continue;
      hideSlots(entry);
      disposeEntry(entry);
      entries.delete(key);
    }
    const fresh = [];
    for (const [key, item] of wanted) {
      const entry = entries.get(key);
      if (entry && entry.gyaml === item.actor.gyaml) {
        applyActorRecord(entry, item.actor);
        continue;
      }
      if (entry) {
        hideSlots(entry);
        disposeEntry(entry);
        entries.delete(key);
      }
      fresh.push({ entry: mountActor(item.actor, item.index), actor: item.actor });
    }
    const loading = [];
    for (const item of fresh) {
      paint(item.entry);
      if (!adoptModel(item.entry)) loading.push(item.actor);
    }
    if (loading.length) loadModels(generation, loading);
    if (selected && !entries.has(selected)) selected = null;
    syncBatchVisibility();
    syncLabels();
    attach();
    requestRender();
    return true;
  }

  function report(info) {
    if (hooks.onProgress) hooks.onProgress(info);
    if (info.finished && hooks.onModels) hooks.onModels();
  }

  function gyamlScore(gyaml) {
    camera.getWorldDirection(loadForward);
    let best = Infinity;
    for (const entry of entries.values()) {
      if (entry.gyaml !== gyaml) continue;
      loadDelta.subVectors(entry.pivot.position, camera.position);
      const dist = loadDelta.lengthSq();
      const len = Math.sqrt(dist) || 1;
      const ahead = loadDelta.dot(loadForward) / len;
      const score = dist * (ahead > -0.15 ? 1 : 8);
      if (score < best) best = score;
    }
    return best;
  }

  function retainLoad() {
    pendingLoads += 1;
    requestRender();
  }

  function releaseLoad() {
    pendingLoads = Math.max(0, pendingLoads - 1);
    requestRender();
  }

  function prioritizeLoad() {
    if (!loadQueue || loadQueue.length < 2) return;
    camera.getWorldDirection(loadForward);
    loadQueue.sort((a, b) => gyamlScore(a) - gyamlScore(b));
  }

  function fetchModelOnce(gyaml) {
    const existing = modelRequests.get(gyaml);
    if (existing) return existing;
    const pending = (
      hooks.fetchModel ? hooks.fetchModel(gyaml) : Promise.reject(new Error("Model loading is not connected."))
    ).finally(() => {
      if (modelRequests.get(gyaml) === pending) modelRequests.delete(gyaml);
    });
    modelRequests.set(gyaml, pending);
    return pending;
  }

  function wantsGlb(meta) {
    return Boolean(meta && (meta.status === "mesh" || meta.status === "queued") && meta.url);
  }

  function loadModels(token, actors) {
    const names = [...new Set(actors.map((actor) => actor.gyaml).filter(Boolean))];
    if (hooks.onWarm) hooks.onWarm(names.slice().sort((a, b) => gyamlScore(a) - gyamlScore(b)));
    statQueued = 0;
    statLoading = 0;
    statLoaded = 0;
    statConvert = 0;
    statTexture = 0;
    statDownload = 0;
    statUpload = 0;
    timedModels.clear();
    let done = 0;
    let boxes = 0;
    const progressLabel = (finished) => {
      if (!names.length) return "No actors";
      return finished ? `Models ${done} of ${names.length}` : `Models ${done} of ${names.length} · nearest first, view stays usable`;
    };
    const publish = (finished) => {
      statQueued = loadQueue ? loadQueue.length : 0;
      report({
        done,
        total: names.length,
        boxes,
        finished,
        label: progressLabel(finished),
      });
      requestRender();
    };
    const failEntry = (entry, meta, message) => {
      entry.modelMeta = { ...(meta || entry.modelMeta || {}), status: "box", reason: message };
      paintLabel(entry);
      entry.mesh.visible = true;
      fitBounds(entry, entry.modelMeta.bounds);
      if (hooks.onModelError) hooks.onModelError(entry.gyaml, message);
    };
    async function deliver(gyaml, meta, template) {
      for (const entry of entries.values()) {
        if (entry.gyaml !== gyaml) continue;
        entry.modelMeta = { ...meta, status: "mesh" };
        paintLabel(entry);
        // Areas, air walls, and other regions stay wireframes. Their packs
        // often contain a solid cube, and one castle cube also carries a
        // miniature of the courtyard. The Volumes and category toggles hide them.
        if (entry.region) {
          fitBounds(entry, meta.bounds);
          continue;
        }
        if (template) attachModel(entry, template, meta.url);
        else fitBounds(entry, meta.bounds);
      }
    }
    async function glbWorker() {
      while (loadQueue && loadQueue.length) {
        if (token !== generation) return;
        prioritizeLoad();
        const gyaml = loadQueue.shift();
        statQueued = loadQueue.length;
        let meta = metaCache.get(gyaml);
        statLoading += 1;
        requestRender();
        try {
          if (!meta || meta.status === "queued") {
            try {
              meta = await fetchModelOnce(gyaml);
            } catch (error) {
              meta = { status: "box", reason: error.message || "The model request failed.", source: "" };
            }
            metaCache.set(gyaml, meta);
          }
          if (token !== generation) return;
          if (meta.status !== "mesh" || !meta.url) {
            boxes += 1;
            for (const entry of entries.values()) {
              if (entry.gyaml !== gyaml) continue;
              entry.modelMeta = meta;
              paintLabel(entry);
              fitBounds(entry, meta.bounds);
            }
          } else {
            try {
              const template = await ensureTemplate(meta.url, meta);
              if (token !== generation) return;
              await deliver(gyaml, meta, template);
            } catch (error) {
              const message = error.message || "The glTF did not load.";
              console.error(`${gyaml}: ${message}`);
              boxes += 1;
              for (const entry of entries.values()) {
                if (entry.gyaml === gyaml) failEntry(entry, meta, message);
              }
            }
          }
        } finally {
          if (token !== generation) return;
          statLoading = Math.max(0, statLoading - 1);
          statLoaded += 1;
          done += 1;
          publish(done >= names.length && (!loadQueue || !loadQueue.length) && statLoading === 0);
        }
      }
    }
    retainLoad();
    const run = async () => {
      const missing = names.filter((name) => !metaCache.has(name));
      if (missing.length && hooks.fetchModels) {
        try {
          const batch = await hooks.fetchModels(missing);
          if (token !== generation) return;
          for (const name of missing) {
            metaCache.set(
              name,
              (batch && batch[name]) || { status: "box", reason: "The model list did not include this actor.", source: "" }
            );
          }
        } catch (error) {
          console.error(error);
        }
      }
      if (token !== generation) return;
      const queue = [];
      for (const name of names) {
        const meta = metaCache.get(name);
        if (!meta) {
          queue.push(name);
          continue;
        }
        if (!wantsGlb(meta)) {
          boxes += meta.status === "box" ? 1 : 0;
          statLoaded += 1;
          done += 1;
          for (const entry of entries.values()) {
            if (entry.gyaml !== name) continue;
            entry.modelMeta = meta;
            fitBounds(entry, meta.bounds);
          }
          continue;
        }
        if (templateCache.has(meta.url)) {
          try {
            noteServerTiming(meta);
            const template = await templateCache.get(meta.url);
            if (token !== generation) return;
            await deliver(name, meta, template);
            statLoaded += 1;
            done += 1;
          } catch (error) {
            queue.push(name);
          }
          continue;
        }
        queue.push(name);
      }
      loadQueue = queue;
      statQueued = queue.length;
      publish(queue.length === 0);
      const workers = Math.min(GLB_SLOTS, queue.length);
      if (workers) await Promise.all(Array.from({ length: workers }, () => glbWorker()));
      if (token !== generation) return;
      // Models that were already on screen stay until this rebuild swaps them.
      // A box-only scene still has to run it, or the previous batches never leave.
      queueRebuild();
    };
    run().finally(releaseLoad);
  }

  function noteServerTiming(meta) {
    const key = (meta && meta.url) || (meta && meta.gyaml) || "";
    if (!key || timedModels.has(key)) return;
    const timing = meta && meta.timing;
    if (!timing) return;
    timedModels.add(key);
    statConvert += Number(timing.convertMs) || 0;
    statTexture += Number(timing.textureMs) || 0;
  }

  function ensureTemplate(url, meta) {
    noteServerTiming(meta);
    let pending = templateCache.get(url);
    if (!pending) {
      pending = (async () => {
        const started = performance.now();
        const response = await fetch(url);
        if (!response.ok) throw new Error(`The model download failed (${response.status}).`);
        const buffer = await response.arrayBuffer();
        const downloaded = performance.now();
        loadingGyaml = (meta && (meta.gyaml || meta.source)) || "";
        const gltf = await new Promise((resolve, reject) => {
          loader.parse(buffer, "", resolve, reject);
        });
        loadingGyaml = "";
        gltf.scene.traverse((child) => {
          const materials = child.material ? (Array.isArray(child.material) ? child.material : [child.material]) : [];
          for (const material of materials) {
            repairCompressedTexture(material.map);
            repairCompressedTexture(material.emissiveMap);
          }
        });
        await bindSurfaceMaps(gltf);
        const uploaded = performance.now();
        statDownload += downloaded - started;
        statUpload += uploaded - downloaded;
        requestRender();
        return gltf.scene;
      })();
      templateCache.set(url, pending);
    }
    return pending;
  }

  function asDataMap(map) {
    if (!map) return;
    map.colorSpace = THREE.LinearSRGBColorSpace;
    map.wrapS = THREE.RepeatWrapping;
    map.wrapT = THREE.RepeatWrapping;
    map.needsUpdate = true;
  }

  function bindNamedTexture(parser, index, imageName) {
    const slot = resolveTextureIndex(parser.json, index, imageName);
    if (slot < 0) return Promise.resolve(null);
    return parser.getDependency("texture", slot).catch(() => null);
  }

  async function bindSurfaceMaps(gltf) {
    const parser = gltf.parser;
    const surface = [];
    if (!parser) return;
    gltf.scene.traverse((child) => {
      if (!child.material) return;
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) {
        const extra = (material.userData && material.userData.surface) || {};
        const blend = (material.userData && material.userData.blend) || {};
        const want = [
          [extra.orm1, extra.orm1Image, (texture) => {
            asDataMap(texture);
            material.userData.orm1Map = texture;
          }],
          [extra.normal1, extra.normal1Image, (texture) => {
            asDataMap(texture);
            material.userData.normal1Map = texture;
          }],
          [blend.moss, blend.mossImage, (texture) => {
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.wrapS = THREE.RepeatWrapping;
            texture.wrapT = THREE.RepeatWrapping;
            material.userData.mossMap = texture;
          }],
        ];
        for (const [index, imageName, apply] of want) {
          if (!Number.isInteger(index) && !imageName) continue;
          surface.push(bindNamedTexture(parser, index, imageName).then((texture) => {
            if (texture) apply(texture);
          }));
        }
      }
    });
    await Promise.all(surface);
  }

  function lodOf(object) {
    let current = object;
    while (current) {
      const match = /^lod(\d+):(.*)$/.exec(current.name || "");
      if (match) return { lod: Number(match[1]) || 0, family: match[2] || current.uuid };
      current = current.parent;
    }
    return { lod: 0, family: object.uuid };
  }

  function prepareMaterial(material, role) {
    if (!material || material.userData.bancPrepared) return material;
    const hasSurface = Boolean(material.roughnessMap || material.metalnessMap || material.aoMap);
    material.metalness = hasSurface ? 1 : 0;
    material.roughness = hasSurface ? 1 : 0.85;
    material.aoMapIntensity = 1;
    asDataMap(material.roughnessMap);
    asDataMap(material.metalnessMap);
    asDataMap(material.aoMap);
    asDataMap(material.normalMap);
    if (material.map) {
      material.map.wrapS = THREE.RepeatWrapping;
      material.map.wrapT = THREE.RepeatWrapping;
      const maxAniso = renderer.capabilities && renderer.capabilities.getMaxAnisotropy ? renderer.capabilities.getMaxAnisotropy() : 4;
      material.map.anisotropy = Math.min(4, maxAniso);
      // A compressed albedo already has its chain. generateMipmaps asks WebGL
      // to build mips, which compressed formats reject, and GLTFLoader has
      // already overwritten minFilter from the sampler.
      if (material.map.isCompressedTexture) repairCompressedTexture(material.map);
      else material.map.generateMipmaps = true;
      material.map.needsUpdate = true;
    }
    const tag = (material.userData && material.userData.banc) || material.name || "";
    if (material.alphaTest > 0) {
      material.alphaToCoverage = true;
      material.transparent = false;
      material.depthWrite = true;
    } else if (material.transparent || tag === "water") {
      material.transparent = true;
      material.depthWrite = false;
      if (tag === "water") material.side = THREE.DoubleSide;
    }
    material.userData.bancPrepared = true;
    if (
      mapNodes
      && role !== "terrain"
      && !(material.userData && (material.userData.banc === "terrain" || material.userData.terrainShader))
    ) {
      mapNodes.attachCutout(material);
    }
    return material;
  }

  function isIdentityMatrix(matrix) {
    const got = matrix.elements;
    for (let i = 0; i < 16; i += 1) {
      const expected = i % 5 === 0 ? 1 : 0;
      if (Math.abs(got[i] - expected) > 1e-5) return false;
    }
    return true;
  }

  function partsFromTemplate(template, url) {
    const parts = [];
    let index = 0;
    template.updateWorldMatrix(true, true);
    const rootInverse = new THREE.Matrix4().copy(template.matrixWorld).invert();
    const local = new THREE.Matrix4();
    template.traverse((child) => {
      if (!child.isMesh || !child.geometry) return;
      const identity = lodOf(child);
      // The glTF node transform belongs to the mesh, not to one placed actor.
      // Bake it into a clone so a later change to the template cannot slide
      // every copy, and so the instance matrix stays the actor matrix alone.
      local.multiplyMatrices(rootInverse, child.matrixWorld);
      const geometry = child.geometry.clone();
      if (!isIdentityMatrix(local)) geometry.applyMatrix4(local);
      geometry.computeBoundingSphere();
      if (!geometry.boundsTree) geometry.computeBoundsTree();
      const source = Array.isArray(child.material) ? child.material[0] : child.material;
      if (!source) return;
      let material = prepareMaterial(source);
      if (material.userData && material.userData.blend && geometry.getAttribute("color")) {
        material = material.clone();
        geometry.setAttribute("blendMask", geometry.getAttribute("color"));
        geometry.deleteAttribute("color");
        const blendUv = material.userData.blend.uv;
        if ((blendUv === "_u1" || blendUv === "u1") && geometry.getAttribute("uv1")) {
          geometry.setAttribute("blendUv", geometry.getAttribute("uv1"));
        }
        material.vertexColors = false;
        installBlendShader(material);
        if (blendNodes) blendNodes.attachBlend(material);
      }
      const sphere = geometry.boundingSphere ? geometry.boundingSphere.clone() : new THREE.Sphere(new THREE.Vector3(), 1);
      parts.push({
        lod: identity.lod,
        family: identity.family,
        name: child.name || "",
        geometry,
        material,
        sphere,
        radius: sphere.radius,
        key: `${url}|${identity.lod}|${identity.family}|${index}`,
        // Same material key as WebGL. WebGPU groups copies of one geometry into
        // one instanced draw inside that mesh instead of a mesh per geometry.
        batchKey: batchKeyFor(material, geometry),
      });
      index += 1;
    });
    return { parts, farNames: new Set(parts.filter((part) => part.lod > 0).map((part) => part.family)) };
  }

  function rememberTemplate(url, parsed) {
    parsedTemplates.set(url, parsed);
    const existing = templateOrder.indexOf(url);
    if (existing >= 0) templateOrder.splice(existing, 1);
    templateOrder.push(url);
    while (templateOrder.length > 48) {
      const old = templateOrder.shift();
      if ([...entries.values()].some((entry) => entry.templateUrl === old)) {
        templateOrder.push(old);
        break;
      }
      parsedTemplates.delete(old);
      templateCache.delete(old);
    }
  }

  function clearBatches() {
    for (const batch of batches) {
      batch.mesh.removeFromParent();
      batch.mesh.dispose();
    }
    batches.length = 0;
    for (const entry of entries.values()) entry.slots = [];
  }

  const tint = new THREE.Color();

  function instanceTint(entry, target) {
    if (entry.status === "recipe") target.setHex(0xc9b6f2);
    else if (entry.companion) target.setRGB(0.78, 0.8, 0.76);
    else target.setRGB(1, 1, 1);
    if (entry.key === selected) target.lerp(new THREE.Color(0xffe0a0), 0.72);
    return target;
  }

  function tintInstance(entry) {
    if (!entry.slots) return;
    for (const slot of entry.slots) {
      slot.mesh.setColorAt(slot.index, instanceTint(entry, tint));
      if (slot.mesh.instanceColor) slot.mesh.instanceColor.needsUpdate = true;
    }
  }

  function syncInstanceMatrix(entry) {
    if (!entry.slots || !entry.slots.length) return;
    entry.pivot.updateWorldMatrix(true, false);
    // Copy first. The live matrixWorld is reused by the next object update, and
    // the write below must be this actor's 16 floats only.
    actorMatrix.copy(entry.pivot.matrixWorld);
    applyActorMatrix(entry.slots, entry.key, actorMatrix);
  }

  function projectedPixels(radius, distance) {
    const fov = (camera.fov * Math.PI) / 180;
    const height = renderer.domElement.height || 1;
    return (radius / Math.max(distance, 0.25)) * (height / (2 * Math.tan(fov / 2)));
  }

  const imageKeys = new WeakMap();

  function imageSignature(image) {
    if (!image) return "none";
    const cached = imageKeys.get(image);
    if (cached) return cached;
    const width = image.width || image.videoWidth || 0;
    const height = image.height || image.videoHeight || 0;
    if (!width || !height) return `unready:${image.src || image.uuid || ""}`;
    let key = `${width}x${height}`;
    try {
      const canvas = document.createElement("canvas");
      canvas.width = 16;
      canvas.height = 16;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      context.drawImage(image, 0, 0, 16, 16);
      const data = context.getImageData(0, 0, 16, 16).data;
      let hash = 2166136261;
      for (let i = 0; i < data.length; i += 1) {
        hash ^= data[i];
        hash = Math.imul(hash, 16777619);
      }
      key = `${key}:${hash >>> 0}`;
    } catch {
      key = `${key}:${image.src || "bitmap"}`;
    }
    imageKeys.set(image, key);
    return key;
  }

  function batchKeyFor(material, geometry) {
    const image = material.map && material.map.image
      ? imageSignature(material.map.image)
      : `flat:${material.color ? material.color.getHexString() : "fff"}:${material.opacity}`;
    const alpha = material.transparent ? `blend:${material.opacity}` : material.alphaTest > 0 ? `mask:${material.alphaTest}` : "opaque";
    const blend = material.userData && material.userData.blend;
    const blendImage = material.userData && material.userData.blendMap && material.userData.blendMap.image;
    const blendKey = blend ? `|moss:${blend.channel || "r"}:${blendImage ? imageSignature(blendImage) : "flat"}` : "";
    const roughImage = material.roughnessMap && material.roughnessMap.image;
    const normalImage = material.normalMap && material.normalMap.image;
    const orm1Image = material.userData && material.userData.orm1Map && material.userData.orm1Map.image;
    const normal1Image = material.userData && material.userData.normal1Map && material.userData.normal1Map.image;
    const surfaceKey = (roughImage || normalImage || orm1Image || normal1Image)
      ? `|orm:${roughImage ? imageSignature(roughImage) : "flat"}|n:${normalImage ? imageSignature(normalImage) : "flat"}|orm1:${orm1Image ? imageSignature(orm1Image) : "flat"}|n1:${normal1Image ? imageSignature(normal1Image) : "flat"}`
      : "";
    return batchKey({
      image: image + blendKey + surfaceKey,
      alpha,
      side: material.side,
      tint: material.color ? material.color.getHexString() : "fff",
      indexed: geometry.getIndex() ? "idx" : "no",
      attributes: Object.keys(geometry.attributes).sort().join(","),
    });
  }

  function actorScale(entry) {
    return Math.max(entry.pivot.scale.x, entry.pivot.scale.y, entry.pivot.scale.z, 0.001);
  }

  function screenPixels(radius, distance) {
    const height = renderer.domElement.height || 0;
    if (height < 2) return 999;
    return projectedPixels(radius, distance);
  }

  const cullFrustum = new THREE.Frustum();
  const cullMatrix = new THREE.Matrix4();
  const cullSphere = new THREE.Sphere();

  const worldSphere = { center: [0, 0, 0], radius: 0 };

  function slotProbe(entry, slot, elements) {
    const scale = actorScale(entry);
    if (!slot.sphere) {
      const pivotDistance = camera.position.distanceTo(entry.pivot.position);
      return { inFrustum: true, distance: pivotDistance, radius: (slot.radius || 1) * scale };
    }
    transformSphere(
      [slot.sphere.center.x, slot.sphere.center.y, slot.sphere.center.z],
      slot.sphere.radius,
      elements,
      worldSphere,
    );
    cullSphere.center.set(worldSphere.center[0], worldSphere.center[1], worldSphere.center[2]);
    cullSphere.radius = worldSphere.radius;
    return {
      inFrustum: cullFrustum.intersectsSphere(cullSphere),
      distance: boundsGap(camera.position.distanceTo(cullSphere.center), cullSphere.radius),
      radius: cullSphere.radius,
    };
  }

  function syncBatchVisibility() {
    camera.updateMatrixWorld();
    // Flying out must not clip the field on a far plane framed for the village.
    const farNeed = Math.max(8000, camera.position.distanceTo(orbit.target) * 6);
    if (camera.far < farNeed) {
      camera.far = farNeed;
      camera.updateProjectionMatrix();
    }
    cullMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    cullFrustum.setFromProjectionMatrix(cullMatrix);
    let instances = 0;
    let visible = 0;
    for (const entry of entries.values()) {
      if (!entry.slots || !entry.slots.length) continue;
      entry.pivot.updateWorldMatrix(true, false);
      const elements = entry.pivot.matrixWorld.elements;
      const probes = [];
      const closest = new Map();
      for (const slot of entry.slots) {
        instances += 1;
        const probe = slotProbe(entry, slot, elements);
        probes.push(probe);
        const previous = closest.get(slot.family);
        if (!previous || probe.distance < previous.distance) closest.set(slot.family, probe);
      }
      const wanted = new Map();
      for (const [family, probe] of closest) {
        const lod = entry.pivot.visible
          ? instanceLod({
              distance: probe.distance,
              radius: probe.radius,
              pixels: screenPixels(probe.radius, probe.distance),
              hasFar: Boolean(entry.farNames && entry.farNames.has(family)),
              selected: entry.key === selected,
              inFrustum: true,
              maxDistance: drawDistance,
            })
          : null;
        wanted.set(family, lod);
      }
      const levels = new Map();
      for (const slot of entry.slots) {
        const list = levels.get(slot.family);
        if (list) list.push(slot.lod);
        else levels.set(slot.family, [slot.lod]);
      }
      for (let i = 0; i < entry.slots.length; i += 1) {
        const slot = entry.slots[i];
        const probe = probes[i];
        const lod = resolveLod(wanted.get(slot.family), levels.get(slot.family));
        const show = probe.inFrustum && lod === slot.lod;
        if (slot.shown !== show) {
          const wasHidden = slot.shown === false;
          slot.shown = show;
          if (show && wasHidden && slot.matrix) slot.mesh.setMatrixAt(slot.index, slot.matrix);
          slot.mesh.setVisibleAt(slot.index, show);
        }
        if (show) visible += 1;
      }
    }
    statInstances = instances;
    statCulled = instances - visible;
  }

  function looseParts() {
    let loose = 0;
    for (const entry of entries.values()) {
      if (!entry.pivot.visible || !entry.parts || !entry.parts.length) continue;
      const covered = new Set();
      for (const slot of entry.slots || []) covered.add(`${slot.family}|${slot.lod}`);
      for (const part of entry.parts) {
        if (part.lod !== 0) continue;
        if (!covered.has(`${part.family}|${part.lod}`)) loose += 1;
      }
    }
    return loose;
  }

  function rebuildBatches() {
    uvDirty = true;
    const previous = batches.slice();
    const nextBatches = [];
    for (const entry of entries.values()) entry.slots = [];
    const groups = new Map();
    naiveDraws = 0;
    for (const entry of entries.values()) {
      syncGhost(entry);
      if (!entry.parts || !entry.parts.length) continue;
      entry.pivot.updateWorldMatrix(true, false);
      for (const part of entry.parts) {
        let group = groups.get(part.batchKey);
        if (!group) {
          group = { material: part.material, records: [], geometries: new Map() };
          groups.set(part.batchKey, group);
        }
        group.records.push({ entry, part });
        if (!group.geometries.has(part.geometry.uuid)) group.geometries.set(part.geometry.uuid, part.geometry);
      }
    }
    for (const group of groups.values()) {
      let verts = 0;
      let indices = 0;
      for (const geometry of group.geometries.values()) {
        const position = geometry.getAttribute("position");
        verts += position ? position.count : 0;
        indices += geometry.getIndex() ? geometry.getIndex().count : position ? position.count : 0;
      }
      if (!verts || !group.records.length) continue;
      let mesh;
      try {
        mesh = new THREE.BatchedMesh(group.records.length, verts, Math.max(indices, 1), group.material);
      } catch (error) {
        console.error(error);
        continue;
      }
      mesh.castShadow = false;
      mesh.receiveShadow = false;
      mesh.frustumCulled = false;
      // Instance matrices are world matrices. The batch object has to stay at
      // the origin, or dragging one pivot would slide every copy parented to it.
      mesh.matrixAutoUpdate = false;
      mesh.matrix.identity();
      mesh.matrixWorld.identity();
      mesh.matrixWorldAutoUpdate = false;
      // The view culls each instance from its own world sphere. The mesh-level
      // sphere is the untransformed geometry and would hide the whole forest
      // once the camera looks away from the origin.
      mesh.perObjectFrustumCulled = false;
      mesh.sortObjects = Boolean(group.material.transparent);
      const label = group.records.map((record) => record.part.name).find(Boolean);
      if (label) mesh.name = label;
      if (renderer.backend && renderer.backend.isWebGPUBackend === true) {
        const previous = mesh.onBeforeRender.bind(mesh);
        mesh.onBeforeRender = function groupDraws(...args) {
          // A quiet frame leaves the grouped ranges in place. Grouping them
          // again would treat each run as one instance and restart the next
          // geometry at slot 1.
          const dirty = mesh._visibilityChanged || mesh.perObjectFrustumCulled || mesh.sortObjects;
          previous(...args);
          if (dirty) groupGeometryDraws(mesh);
        };
      }
      mesh.renderOrder = 1;
      const tag = (group.material.userData && group.material.userData.banc) || group.material.name || "";
      const water = tag === "water";
      if (water) {
        mesh.renderOrder = 2;
        mesh.visible = showWater;
      }
      const geomIds = new Map();
      let built = true;
      for (const [uuid, geometry] of group.geometries) {
        try {
          geomIds.set(uuid, mesh.addGeometry(geometry));
        } catch (error) {
          console.error(error);
          built = false;
          break;
        }
      }
      if (!built) {
        mesh.dispose();
        continue;
      }
      const ids = [];
      const geometries = [];
      const spheres = [];
      let count = 0;
      group.records.sort((a, b) => {
        const left = a.part.geometry.uuid;
        const right = b.part.geometry.uuid;
        if (left < right) return -1;
        if (left > right) return 1;
        return 0;
      });
      for (const record of group.records) {
        const geometryId = geomIds.get(record.part.geometry.uuid);
        if (geometryId === undefined) continue;
        const instanceId = mesh.addInstance(geometryId);
        const placed = new THREE.Matrix4().copy(record.entry.pivot.matrixWorld);
        mesh.setMatrixAt(instanceId, placed);
        mesh.setColorAt(instanceId, instanceTint(record.entry, tint));
        ids[instanceId] = record.entry.key;
        geometries[instanceId] = record.part.geometry;
        spheres[instanceId] = record.part.sphere;
        count = Math.max(count, instanceId + 1);
        record.entry.slots.push({
          actor: record.entry.key,
          mesh,
          index: instanceId,
          lod: record.part.lod,
          family: record.part.family,
          radius: record.part.radius,
          sphere: record.part.sphere,
          matrix: placed,
          shown: true,
        });
      }
      mesh.userData.pick = { ids, geometries, spheres, count };
      mesh.raycast = raycastBatch;
      mesh.computeBoundingSphere();
      scene.add(mesh);
      nextBatches.push({ mesh, ids, water });
    }
    const expectMeshes = [...entries.values()].some((entry) => entry.parts && entry.parts.length);
    if (!nextBatches.length && expectMeshes && previous.length) {
      for (const entry of entries.values()) entry.slots = [];
      console.error("Actor batches failed to build; the meshes already on screen stay up.");
      return;
    }
    batches.length = 0;
    for (const batch of nextBatches) batches.push(batch);
    naiveDraws = looseParts();
    for (const batch of previous) {
      batch.mesh.removeFromParent();
      batch.mesh.dispose();
    }
    syncBatchVisibility();
    guardMissingUv();
  }

  function queueRebuild() {
    if (rebuildTimer) clearTimeout(rebuildTimer);
    rebuildTimer = window.setTimeout(() => {
      rebuildTimer = 0;
      rebuildBatches();
      requestRender();
    }, 100);
  }

  function attachModel(entry, template, url) {
    if (entry.parts) return;
    let parsed = parsedTemplates.get(url);
    if (!parsed) {
      parsed = partsFromTemplate(template, url);
      rememberTemplate(url, parsed);
    }
    entry.parts = parsed.parts;
    entry.farNames = parsed.farNames;
    entry.templateUrl = url;
    entry.volume = false;
    queueRebuild();
  }

  const scratchMatrix = new THREE.Matrix4();
  const actorMatrix = new THREE.Matrix4();
  const scratchSphere = new THREE.Sphere();
  const scratchMesh = new THREE.Mesh();
  const scratchHits = [];
  scratchMesh.matrixAutoUpdate = false;

  function raycastBatch(raycaster, intersects) {
    const pick = this.userData.pick;
    if (!pick) return;
    for (let i = 0; i < pick.count; i += 1) {
      if (!pick.ids[i] || !this.getVisibleAt(i)) continue;
      this.getMatrixAt(i, scratchMatrix);
      const source = pick.spheres[i];
      if (!source) continue;
      scratchSphere.copy(source).applyMatrix4(scratchMatrix);
      if (!raycaster.ray.intersectsSphere(scratchSphere)) continue;
      scratchMesh.geometry = pick.geometries[i];
      scratchMesh.material = this.material;
      scratchMesh.matrix.copy(scratchMatrix);
      scratchMesh.matrixWorld.copy(scratchMatrix);
      scratchHits.length = 0;
      scratchMesh.raycast(raycaster, scratchHits);
      for (let j = 0; j < scratchHits.length; j += 1) {
        const hit = scratchHits[j];
        hit.object = this;
        hit.batchId = i;
        intersects.push(hit);
      }
    }
  }

  function measureTemplate(template) {
    const box = new THREE.Box3();
    template.updateWorldMatrix(true, true);
    template.traverse((child) => {
      if (!child.isMesh || !child.geometry) return;
      if (!child.geometry.boundingBox) child.geometry.computeBoundingBox();
      const childBox = child.geometry.boundingBox;
      if (!childBox) return;
      box.union(childBox.clone().applyMatrix4(child.matrixWorld));
    });
    return box;
  }

  function placeGround(box) {
    clearGround();
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const span = Math.max(10, Math.ceil(Math.max(size.x, size.z)));
    const divisions = Math.min(120, Math.max(12, Math.round(span / 8)));
    const gridLines = new THREE.GridHelper(span, divisions, 0x9aa394, 0x3c4740);
    // y = 0 is the terrain origin. The proxy mesh median sits above the walkable floor.
    gridLines.position.set(center.x, 0, center.z);
    gridLines.visible = showGround;
    gridLines.raycast = () => {};
    gridLines.traverse((child) => {
      child.raycast = () => {};
    });
    scene.add(gridLines);
    groundGrid = gridLines;
  }

  // These three are GLSL functions. They have to live outside main(). #include <map_fragment>
  // is inside main(), so the atlas lookup stays in TERRAIN_MAP and the helpers are
  // injected just before void main(), after the uniform declarations.
  const TERRAIN_FUNCS = `
    vec2 terrainCell(vec2 worldUv, float slot) {
      float columns = max(terrainCols, 1.0);
      float column = mod(floor(slot + 0.5), columns);
      float row = floor(floor(slot + 0.5) / columns);
      vec2 repeated = fract(worldUv);
      vec2 inside = repeated * 0.96 + 0.02;
      return (vec2(column, row) + inside) / columns;
    }
    vec4 terrainSample(vec2 worldUv, float slot) {
      return texture2D(map, terrainCell(worldUv, slot));
    }
    vec4 terrainTri(float slot) {
      vec3 blend = abs(normalize(vTerrainNormal));
      blend = pow(max(blend, vec3(0.0001)), vec3(4.0));
      blend /= (blend.x + blend.y + blend.z);
      vec4 alongX = terrainSample(vTerrainWorld.zy / terrainRepeat, slot);
      vec4 alongY = terrainSample(vTerrainWorld.xz / terrainRepeat, slot);
      vec4 alongZ = terrainSample(vTerrainWorld.xy / terrainRepeat, slot);
      return alongX * blend.x + alongY * blend.y + alongZ * blend.z;
    }
  `;

  const TERRAIN_MAP = `
    if (terrainLayerMissing < 0.5 && terrainSub < 0.5 && vTerrainMark.y > 0.5) discard;
    vec4 sampledDiffuseColor = mix(
      terrainTri(vTerrainLayer.x),
      terrainTri(vTerrainLayer.y),
      clamp(vTerrainShade.x, 0.0, 1.0)
    );
    if (terrainDebug > 0.5) {
      if (terrainLayerMissing > 0.5) {
        float stripe = step(0.5, fract((vTerrainWorld.x + vTerrainWorld.z) * 0.15));
        sampledDiffuseColor = vec4(mix(vec3(0.75, 0.2, 0.55), vec3(0.25, 0.12, 0.18), stripe), 1.0);
      } else {
        float hue = vTerrainMark.x * 6.2831853;
        vec3 ink = clamp(vec3(
          0.55 + 0.45 * cos(hue),
          0.55 + 0.45 * cos(hue + 2.094395),
          0.55 + 0.45 * cos(hue + 4.188790)
        ), 0.0, 1.0);
        if (vTerrainMark.y > 0.5) ink = mix(ink, vec3(1.0), 0.45);
        sampledDiffuseColor = vec4(ink, 1.0);
      }
    }
    diffuseColor *= sampledDiffuseColor;
  `;

  let sharedOrm = null;
  let sharedFlatNormal = null;

  function sharedOrmMap() {
    if (sharedOrm) return sharedOrm;
    const data = new Uint8Array([255, 217, 0, 255]);
    const tex = new THREE.DataTexture(data, 1, 1);
    tex.colorSpace = THREE.LinearSRGBColorSpace;
    tex.wrapS = THREE.RepeatWrapping;
    tex.wrapT = THREE.RepeatWrapping;
    tex.magFilter = THREE.LinearFilter;
    tex.minFilter = THREE.LinearFilter;
    tex.generateMipmaps = false;
    tex.needsUpdate = true;
    sharedOrm = tex;
    return tex;
  }

  function sharedNormalMap() {
    if (sharedFlatNormal) return sharedFlatNormal;
    const data = new Uint8Array([128, 128, 255, 255]);
    const tex = new THREE.DataTexture(data, 1, 1);
    tex.colorSpace = THREE.LinearSRGBColorSpace;
    tex.wrapS = THREE.RepeatWrapping;
    tex.wrapT = THREE.RepeatWrapping;
    tex.magFilter = THREE.LinearFilter;
    tex.minFilter = THREE.LinearFilter;
    tex.generateMipmaps = false;
    tex.needsUpdate = true;
    sharedFlatNormal = tex;
    return tex;
  }

  const BLEND_MIX = `
{
  float mask = blendChannel < 0.5 ? vBlendMask.r : blendChannel < 1.5 ? vBlendMask.g : blendChannel < 2.5 ? vBlendMask.b : vBlendMask.a;
  blendWeight = smoothstep(0.35, 0.65, mask);
  blendMossUv = vMossUv * blendScale + blendTranslate;
  vec4 moss = texture2D(blendMap, blendMossUv);
  diffuseColor.rgb = mix(diffuseColor.rgb, moss.rgb, blendWeight);
  if (blendDebug > 0.5) diffuseColor.rgb = vec3(blendWeight);
}`;

  const BLEND_SURFACE = `
{
  vec4 surface1 = texture2D(orm1Map, blendMossUv);
  roughnessFactor = mix(roughnessFactor, surface1.g, blendWeight);
  metalnessFactor = mix(metalnessFactor, surface1.b, blendWeight);
}`;

  const BLEND_NORMAL = `
{
  vec3 overlayN = texture2D(normal1Map, blendMossUv).xyz * 2.0 - 1.0;
  overlayN.xy *= normalScale;
  normal = normalize(mix(normal, normalize(tbn * overlayN), blendWeight));
}`;

  const BLEND_AO = `
{
  float overlayAo = texture2D(orm1Map, blendMossUv).r;
  float mixedAo = mix(ambientOcclusion, overlayAo, blendWeight);
  float aoScale = mixedAo / max(ambientOcclusion, 0.001);
  reflectedLight.indirectDiffuse *= aoScale;
#if defined( USE_ENVMAP ) && defined( STANDARD )
  reflectedLight.indirectSpecular *= aoScale;
#endif
}`;

  function installBlendShader(material) {
    const blend = material.userData && material.userData.blend;
    if (!blend || material.userData.blendShader) return material;
    const scale = blend.scale || [1, 1];
    const translate = blend.translate || [0, 0];
    material.userData.blendShader = true;
    material.userData.blendDebugUniform = { value: showBlendMask ? 1 : 0 };
    material.userData.blendScaleUniform = { value: new THREE.Vector2(Number(scale[0]) || 1, Number(scale[1]) || 1) };
    material.userData.blendTranslateUniform = { value: new THREE.Vector2(Number(translate[0]) || 0, Number(translate[1]) || 0) };
    material.userData.blendChannelUniform = { value: { r: 0, g: 1, b: 2, a: 3 }[blend.channel] ?? 0 };
    material.userData.blendUv1Uniform = { value: blend.uv === "_u1" || blend.uv === "u1" ? 1 : 0 };
    if (material.userData.mossMap && !material.userData.blendMap) {
      material.userData.blendMap = material.userData.mossMap;
      material.userData.blendMap.wrapS = THREE.RepeatWrapping;
      material.userData.blendMap.wrapT = THREE.RepeatWrapping;
    } else if (material.emissiveMap && !material.userData.blendMap) {
      material.userData.blendMap = material.emissiveMap;
      material.userData.blendMap.wrapS = THREE.RepeatWrapping;
      material.userData.blendMap.wrapT = THREE.RepeatWrapping;
      material.emissive.setRGB(0, 0, 0);
      material.emissiveMap = null;
    }
    if (!material.roughnessMap) {
      const orm = sharedOrmMap();
      material.roughnessMap = orm;
      material.metalnessMap = orm;
      material.aoMap = orm;
    } else {
      if (!material.metalnessMap) material.metalnessMap = material.roughnessMap;
      if (!material.aoMap) material.aoMap = material.roughnessMap;
    }
    material.metalness = 1;
    material.roughness = 1;
    material.aoMapIntensity = 1;
    asDataMap(material.roughnessMap);
    if (!material.normalMap) material.normalMap = sharedNormalMap();
    asDataMap(material.normalMap);
    if (!material.userData.orm1Map) material.userData.orm1Map = material.roughnessMap;
    if (!material.userData.normal1Map) material.userData.normal1Map = material.normalMap;
    material.userData.orm1Uniform = { value: material.userData.orm1Map };
    material.userData.normal1Uniform = { value: material.userData.normal1Map };
    const useUv1 = material.userData.blendUv1Uniform.value > 0.5;
    const vertexPars = useUv1
      ? `attribute vec4 blendMask;\nattribute vec2 blendUv;\nvarying vec4 vBlendMask;\nvarying vec2 vMossUv;`
      : `attribute vec4 blendMask;\nvarying vec4 vBlendMask;\nvarying vec2 vMossUv;`;
    const vertexBody = useUv1 ? "vBlendMask = blendMask;\nvMossUv = blendUv;" : "vBlendMask = blendMask;\nvMossUv = uv;";
    material.onBeforeCompile = (shader) => {
      shader.uniforms.blendDebug = material.userData.blendDebugUniform;
      shader.uniforms.blendScale = material.userData.blendScaleUniform;
      shader.uniforms.blendTranslate = material.userData.blendTranslateUniform;
      shader.uniforms.blendChannel = material.userData.blendChannelUniform;
      shader.uniforms.blendMap = { value: material.userData.blendMap };
      shader.uniforms.orm1Map = material.userData.orm1Uniform;
      shader.uniforms.normal1Map = material.userData.normal1Uniform;
      shader.vertexShader = shader.vertexShader
        .replace("#include <uv_pars_vertex>", `#include <uv_pars_vertex>\n${vertexPars}`)
        .replace("#include <uv_vertex>", `#include <uv_vertex>\n${vertexBody}`);
      const fragPars = `#include <uv_pars_fragment>
varying vec4 vBlendMask;
varying vec2 vMossUv;
uniform sampler2D blendMap;
uniform sampler2D orm1Map;
uniform sampler2D normal1Map;
uniform float blendDebug;
uniform float blendChannel;
uniform vec2 blendScale;
uniform vec2 blendTranslate;
float blendWeight;
vec2 blendMossUv;`;
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <uv_pars_fragment>", fragPars)
        .replace("#include <map_fragment>", `#include <map_fragment>\n${BLEND_MIX}`)
        .replace("#include <metalnessmap_fragment>", `#include <metalnessmap_fragment>\n${BLEND_SURFACE}`)
        .replace("#include <normal_fragment_maps>", `#include <normal_fragment_maps>\n${BLEND_NORMAL}`)
        .replace("#include <aomap_fragment>", `#include <aomap_fragment>\n${BLEND_AO}`);
    };
    material.customProgramCacheKey = () => (useUv1 ? "blend|uv1" : "blend");
    material.needsUpdate = true;
    return material;
  }

  function applyBlendUniforms() {
    const value = showBlendMask ? 1 : 0;
    for (const entry of entries.values()) {
      for (const part of entry.parts || []) {
        const uniform = part.material && part.material.userData && part.material.userData.blendDebugUniform;
        if (uniform) uniform.value = value;
      }
    }
  }

  function installTerrainShader(material) {
    if (!material || material.userData.terrainShader) return material;
    const columns = Number(material.userData.atlas) || 1;
    const repeat = Number(material.userData.repeat) || 8;
    material.userData.terrainShader = true;
    material.userData.terrainColsUniform = { value: columns };
    material.userData.terrainRepeatUniform = { value: repeat };
    if (!material.userData.terrainDebugUniform) {
      material.userData.terrainDebugUniform = { value: showTerrainDebug ? 1 : 0 };
    }
    if (!material.userData.terrainSubUniform) {
      material.userData.terrainSubUniform = { value: showSub ? 1 : 0 };
    }
    if (!material.userData.terrainLayerUniform) {
      material.userData.terrainLayerUniform = { value: 0 };
    }
    if (material.map) {
      material.map.wrapS = THREE.ClampToEdgeWrapping;
      material.map.wrapT = THREE.ClampToEdgeWrapping;
      material.map.needsUpdate = true;
      material.userData.terrainMap = material.map;
    }
    const previous = material.onBeforeCompile;
    material.onBeforeCompile = (shader) => {
      if (previous && previous !== material.onBeforeCompile) previous(shader);
      material.userData.terrainShaderRef = shader;
      shader.uniforms.terrainCols = material.userData.terrainColsUniform;
      shader.uniforms.terrainRepeat = material.userData.terrainRepeatUniform;
      shader.uniforms.terrainDebug = material.userData.terrainDebugUniform;
      shader.uniforms.terrainSub = material.userData.terrainSubUniform;
      shader.uniforms.terrainLayerMissing = material.userData.terrainLayerUniform;
      // A second compile (gizmo interaction can rebuild programs) must not
      // inject the helpers twice, and must not leave the stock map chunk in
      // place. The stock chunk samples uv, which is not the atlas.
      if (shader.fragmentShader.includes("terrainCell(")) return;
      shader.vertexShader = shader.vertexShader
        .replace(
          "#include <uv_pars_vertex>",
          `#include <uv_pars_vertex>
attribute vec2 uv1;
attribute vec2 uv2;
attribute vec2 uv3;
varying vec3 vTerrainWorld;
varying vec3 vTerrainNormal;
varying vec2 vTerrainLayer;
varying vec2 vTerrainShade;
varying vec2 vTerrainMark;`
        )
        .replace(
          "#include <uv_vertex>",
          `#include <uv_vertex>
vTerrainLayer = uv1;
vTerrainShade = uv2;
vTerrainMark = uv3;
vTerrainWorld = (modelMatrix * vec4(position, 1.0)).xyz;
vTerrainNormal = normalize(mat3(modelMatrix) * normal);`
        );
      shader.fragmentShader = shader.fragmentShader
        .replace(
          "#include <uv_pars_fragment>",
          `#include <uv_pars_fragment>
varying vec3 vTerrainWorld;
varying vec3 vTerrainNormal;
varying vec2 vTerrainLayer;
varying vec2 vTerrainShade;
varying vec2 vTerrainMark;
uniform float terrainCols;
uniform float terrainRepeat;
uniform float terrainDebug;
uniform float terrainSub;
uniform float terrainLayerMissing;`
        )
        .replace("void main() {", `${TERRAIN_FUNCS}\nvoid main() {`)
        .replace("#include <map_fragment>", TERRAIN_MAP);
    };
    // Stable key. The default key is the onBeforeCompile source, which changes
    // identity if the callback is wrapped again and would swap in a program
    // that never received the atlas uniforms.
    material.customProgramCacheKey = () => `terrain|${columns}|${repeat}`;
    material.needsUpdate = true;
    // WebGPU does not run onBeforeCompile. The node graph reads the same
    // uniform objects the GLSL path writes.
    if (terrainNodes) terrainNodes.attachTerrainColor(material);
    return material;
  }

  function bindTerrainMesh(mesh) {
    mesh.frustumCulled = false;
    mesh.onBeforeRender = () => {
      const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
      if (!material || !material.userData) return;
      const atlas = material.userData.terrainMap;
      // The node colour samples terrainMap. Putting map back asks the standard
      // node for attribute "uv", which terrain tiles do not have.
      if (atlas && material.map !== atlas && !material.userData.terrainNode) material.map = atlas;
      const shader = material.userData.terrainShaderRef;
      if (!shader || !shader.uniforms) return;
      shader.uniforms.terrainCols = material.userData.terrainColsUniform;
      shader.uniforms.terrainRepeat = material.userData.terrainRepeatUniform;
      shader.uniforms.terrainDebug = material.userData.terrainDebugUniform;
      shader.uniforms.terrainSub = material.userData.terrainSubUniform;
      shader.uniforms.terrainLayerMissing = material.userData.terrainLayerUniform;
    };
  }

  function mergeStatic(template) {
    const byMaterial = new Map();
    template.traverse((child) => {
      if (!child.isMesh || !child.geometry) return;
      const source = Array.isArray(child.material) ? child.material[0] : child.material;
      if (!source) return;
      const geometry = child.geometry.clone();
      const terrain =
        geometry.getAttribute("uv1") &&
        (((source.userData && source.userData.banc) === "terrain") || (child.name || "").includes("terrain"));
      let material = prepareMaterial(source, terrain ? "terrain" : "");
      if (geometry.getAttribute("color") || terrain) {
        material = material.clone();
        if (geometry.getAttribute("color")) material.vertexColors = true;
        material.needsUpdate = true;
      }
      if (terrain) installTerrainShader(material);
      child.updateWorldMatrix(true, false);
      geometry.applyMatrix4(child.matrixWorld);
      const list = byMaterial.get(material) || { material, geometries: [] };
      list.geometries.push(geometry);
      byMaterial.set(material, list);
    });
    const root = new THREE.Group();
    for (const group of byMaterial.values()) {
      let geometry = group.geometries[0];
      if (group.geometries.length > 1) {
        try {
          geometry = mergeGeometries(group.geometries, false);
          group.geometries.forEach((item) => item.dispose());
        } catch {
          for (const item of group.geometries) {
            const mesh = new THREE.Mesh(item, group.material);
            mesh.raycast = () => {};
            mesh.frustumCulled = false;
            if (group.material.userData && group.material.userData.terrainShader) bindTerrainMesh(mesh);
            root.add(mesh);
          }
          continue;
        }
      }
      if (!geometry) continue;
      if (!geometry.boundsTree) geometry.computeBoundsTree();
      const mesh = new THREE.Mesh(geometry, group.material);
      mesh.raycast = () => {};
      mesh.frustumCulled = false;
      if (group.material.userData && group.material.userData.terrainShader) bindTerrainMesh(mesh);
      root.add(mesh);
    }
    return root;
  }

  function terrainCurrent(token) {
    return token === terrainToken;
  }

  async function warmTerrain(root) {
    // Compile against the lit scene before the mesh is visible, then upload the
    // atlas. Swapping earlier draws the field with an empty program (solid black)
    // until the shader and the texture finish.
    if (typeof renderer.compileAsync === "function") {
      await renderer.compileAsync(root, camera, scene);
    } else if (typeof renderer.compile === "function") {
      renderer.compile(root, camera, scene);
    }
    const maps = new Set();
    root.traverse((child) => {
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) {
        const atlas = material && (material.map || (material.userData && material.userData.terrainMap));
        if (atlas) maps.add(atlas);
      }
    });
    for (const map of maps) {
      const image = map.image;
      if (image && image.complete === false && typeof image.decode === "function") {
        try {
          await image.decode();
        } catch {
          /* the loader already decoded it, or the browser will upload on draw */
        }
      }
      if (typeof renderer.initTexture === "function") renderer.initTexture(map);
    }
  }

  function showTerrain(nextRoot) {
    uvDirty = true;
    const previous = terrainRoot;
    terrainRoot = nextRoot;
    if (nextRoot) scene.add(nextRoot);
    clearGround();
    disposeTerrainObject(previous);
    requestRender();
  }

  async function loadTerrain(token, banc) {
    if (!banc || !hooks.fetchTerrain) return;
    retainLoad();
    try {
      await loadTerrainBody(token, banc);
    } finally {
      releaseLoad();
    }
  }

  async function loadTerrainBody(token, banc) {
    let meta;
    try {
      meta = await hooks.fetchTerrain(banc);
    } catch (error) {
      meta = { status: "none", reason: error.message || "The map model request failed." };
    }
    if (!terrainCurrent(token)) return;
    if (hooks.onTerrain) hooks.onTerrain(meta);
    if (meta.status !== "mesh" || !meta.url) {
      showTerrain(null);
      return;
    }
    try {
      const template = await ensureTemplate(meta.url, meta);
      if (!terrainCurrent(token)) return;
      await new Promise((resolve) => {
        if (typeof requestIdleCallback === "function") requestIdleCallback(() => resolve(), { timeout: 800 });
        else setTimeout(resolve, 0);
      });
      if (!terrainCurrent(token)) return;
      if (isTerrainProxy(meta)) {
        const box = measureTemplate(template);
        if (!terrainCurrent(token)) return;
        if (!box.isEmpty()) placeGround(box);
        uvDirty = true;
        const previous = terrainRoot;
        terrainRoot = null;
        disposeTerrainObject(previous);
        requestRender();
        return;
      }
      // A heightmap mesh is the ground. Leave the proxy grid off so it cannot sit above the field.
      const nextRoot = mergeStatic(template);
      nextRoot.name = "terrain";
      nextRoot.visible = showGround;
      // Draw the field before batched actors. A gizmo drag re-uploads the
      // float matrix texture; drawing the field first keeps that upload from
      // being the texture the atlas sampler sees.
      nextRoot.renderOrder = -1;
      nextRoot.frustumCulled = false;
      nextRoot.raycast = () => {};
      let missingLayer = false;
      nextRoot.traverse((child) => {
        child.raycast = () => {};
        child.castShadow = false;
        child.receiveShadow = false;
        child.frustumCulled = false;
        child.renderOrder = -1;
        if (child.isMesh) bindTerrainMesh(child);
        const layered = Boolean(child.geometry && child.geometry.getAttribute("uv3"));
        if (child.isMesh && child.geometry && !layered) missingLayer = true;
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        for (const material of materials) {
          if (!material) continue;
          material.transparent = false;
          material.depthWrite = true;
          material.side = THREE.FrontSide;
          if (material.userData && material.userData.terrainLayerUniform) {
            material.userData.terrainLayerUniform.value = layered ? 0 : 1;
          }
          material.needsUpdate = true;
        }
      });
      if (missingLayer) {
        const message = "Terrain mesh has no per-vertex layer attribute (TEXCOORD_3). Tile colors and Sub terrain need a models-22 rebuild; this glb is from an older cache. Tile colors draws warning stripes until then.";
        console.warn(message);
        if (hooks.onTerrainWarn) hooks.onTerrainWarn(message);
      }
      try {
        await warmTerrain(nextRoot);
      } catch (error) {
        console.warn(error);
      }
      if (!terrainCurrent(token)) {
        disposeTerrainObject(nextRoot);
        return;
      }
      showTerrain(nextRoot);
    } catch (error) {
      if (terrainCurrent(token) && hooks.onTerrain) {
        hooks.onTerrain({ status: "box", reason: error.message || "The map glTF did not load.", source: meta.source || "" });
      }
    }
  }

  function attach() {
    const entry = selected ? entries.get(selected) : null;
    if (!entry || !canGizmo({ id: entry.id, shown: entry.pivot.visible, locked: entry.locked })) {
      transform.detach();
      return;
    }
    transform.attach(entry.pivot);
  }

  function select(id) {
    selected = id;
    for (const entry of entries.values()) paint(entry);
    syncBatchVisibility();
    attach();
    syncLabels();
    requestRender();
  }

  function aimOrbit(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const roots = [];
    if (terrainRoot) roots.push(terrainRoot);
    for (const batch of batches) roots.push(batch.mesh);
    for (const entry of entries.values()) {
      if (entry.pivot.visible) roots.push(entry.pivot);
    }
    const hits = roots.length ? raycaster.intersectObjects(roots, true) : [];
    const hit = hits.find((item) => item.point);
    if (hit) {
      orbit.target.copy(hit.point);
      return;
    }
    const entry = selected ? entries.get(selected) : null;
    if (entry) {
      entry.pivot.getWorldPosition(orbit.target);
      return;
    }
    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);
    orbit.target.copy(camera.position).add(forward.multiplyScalar(8));
  }

  function requestLookLock() {
    const canvas = renderer.domElement;
    if (!canvas.requestPointerLock || document.pointerLockElement === canvas) return;
    const pending = canvas.requestPointerLock();
    if (pending && typeof pending.catch === "function") pending.catch(() => {});
  }

  function releaseLook() {
    looking = false;
    lookLocked = false;
    const canvas = renderer.domElement;
    if (document.pointerLockElement === canvas && document.exitPointerLock) document.exitPointerLock();
  }

  function applyLook(dx, dy) {
    if (!dx && !dy) return;
    camera.rotation.order = "YXZ";
    camera.rotation.y -= dx * 0.003;
    camera.rotation.x = Math.max(-1.4, Math.min(1.4, camera.rotation.x - dy * 0.003));
    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);
    orbit.target.copy(camera.position).add(forward.multiplyScalar(8));
    requestRender();
  }

  function setGizmo(mode) {
    transform.setMode(mode);
    transform.setSpace(mode === "translate" ? "world" : "local");
  }

  function pivotScale(pivot) {
    return [pivot.scale.x, pivot.scale.y, pivot.scale.z];
  }

  function applyTerrainUniforms() {
    if (!terrainRoot) return;
    const debug = showTerrainDebug ? 1 : 0;
    const sub = showSub ? 1 : 0;
    terrainRoot.traverse((child) => {
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) {
        if (!material || !material.userData) continue;
        if (material.userData.terrainDebugUniform) material.userData.terrainDebugUniform.value = debug;
        if (material.userData.terrainSubUniform) material.userData.terrainSubUniform.value = sub;
      }
    });
  }

  function setFlags({ hidden, labels, categories: next, volumes, ground, water, boxes, terrainDebug, subTerrain, blendMask, distance }) {
    if (hidden !== undefined) showHidden = hidden;
    if (labels !== undefined) showLabels = labels;
    if (volumes !== undefined) hideVolumes = volumes;
    if (ground !== undefined) showGround = Boolean(ground);
    if (water !== undefined) showWater = Boolean(water);
    if (terrainDebug !== undefined) showTerrainDebug = Boolean(terrainDebug);
    if (subTerrain !== undefined) showSub = Boolean(subTerrain);
    if (blendMask !== undefined) showBlendMask = Boolean(blendMask);
    if (distance !== undefined) drawDistance = Number(distance) || 0;
    if (next) Object.assign(categories, next);
    if (boxes) Object.assign(boxCategories, boxes);
    for (const entry of entries.values()) {
      const gate = {
        status: entry.status,
        category: entry.category,
        volume: entry.volume,
        showHidden,
        categories,
        hideVolumes,
        companion: entry.companion,
        pickable: entry.pickable,
      };
      entry.locked = actorLocked(gate);
      entry.pivot.visible = actorShown(gate);
      syncGhost(entry);
    }
    syncBatchVisibility();
    applyGround();
    applyWater();
    applyTerrainUniforms();
    applyBlendUniforms();
    attach();
    syncLabels();
    requestRender();
  }

  function modelInfo(id) {
    const entry = entries.get(id);
    if (!entry) return null;
    return entry.modelMeta || { status: "loading", reason: "Looking up the model.", source: "", triangles: 0, textures: 0 };
  }

  function frame(id) {
    if (id && entries.has(id)) frameObjects([entries.get(id).pivot]);
    else frameObjects([...entries.values()].filter((entry) => entry.pivot.visible).map((entry) => entry.pivot));
  }

  function isCollisionEntry(entry) {
    const name = String(entry.gyaml || "").toLowerCase();
    return name.includes("collision") || name.includes("collider");
  }

  function surfaceAt(x, y, z, ignoreId) {
    const hits = [];
    snapRay.ray.origin.set(x, y + 80, z);
    snapRay.ray.direction.set(0, -1, 0);
    if (terrainRoot) {
      terrainRoot.traverse((child) => {
        if (!child.isMesh || !child.visible || !child.geometry) return;
        const found = [];
        try {
          acceleratedRaycast.call(child, snapRay, found);
        } catch {
          return;
        }
        for (const hit of found) {
          if (hit.point) hits.push({ y: hit.point.y });
        }
      });
    }
    for (const entry of entries.values()) {
      if (!isCollisionEntry(entry) || !entry.pivot.visible) continue;
      if (entry.key === ignoreId || entry.id === ignoreId) continue;
      const top = volumeSurfaceY(entry, snapRay);
      if (top != null) hits.push({ y: top });
    }
    return chooseSnap(y, hits);
  }

  function maybeSnap(entry) {
    if (!snapOn || !entry || transform.mode !== "translate") return;
    const p = entry.pivot.position;
    const y = surfaceAt(p.x, p.y, p.z, entry.id);
    if (y == null) return;
    p.y = y;
  }

  function spawnPoint() {
    const point = [round(orbit.target.x), round(orbit.target.y), round(orbit.target.z)];
    if (!snapOn) return point;
    const y = surfaceAt(point[0], point[1], point[2], null);
    if (y == null) return point;
    return [point[0], round(y), point[2]];
  }

  function round(value) {
    return Math.round(value * 1000) / 1000;
  }

  function idOf(object) {
    let current = object;
    while (current) {
      if (current.userData && "actorId" in current.userData) return current.userData.actorId;
      current = current.parent;
    }
    return null;
  }

  const raycaster = new THREE.Raycaster();
  const snapRay = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  function aimed(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const roots = [];
    for (const batch of batches) roots.push(batch.mesh);
    for (const entry of entries.values()) {
      if (canPick({ shown: entry.pivot.visible, locked: entry.locked }) && (!entry.parts || !entry.parts.length)) roots.push(entry.pivot);
    }
    const groups = classify(roots.length ? raycaster.intersectObjects(roots, true) : []);
    for (const found of volumeRayHits(raycaster)) {
      if (!groups.volumes.includes(found.id)) groups.volumes.push(found.id);
      if (found.edge && !groups.edges.includes(found.id)) groups.edges.push(found.id);
    }
    return groups;
  }

  const unitBox = new THREE.Box3(new THREE.Vector3(-0.5, -0.5, -0.5), new THREE.Vector3(0.5, 0.5, 0.5));
  const localRay = new THREE.Ray();
  const localInverse = new THREE.Matrix4();
  const localPoint = new THREE.Vector3();

  function volumeRayHits(raycaster) {
    const found = [];
    for (const entry of entries.values()) {
      if (!entry.volume || !canPick({ shown: entry.pivot.visible, locked: entry.locked })) continue;
      if (entry.parts && entry.parts.length) continue;
      entry.mesh.updateWorldMatrix(true, false);
      localInverse.copy(entry.mesh.matrixWorld).invert();
      localRay.copy(raycaster.ray).applyMatrix4(localInverse);
      if (!localRay.intersectBox(unitBox, localPoint)) continue;
      const edge = ["x", "y", "z"].filter((axis) => Math.abs(Math.abs(localPoint[axis]) - 0.5) < 0.08).length >= 2;
      const worldPoint = localPoint.clone().applyMatrix4(entry.mesh.matrixWorld);
      found.push({ id: entry.key, edge, distance: raycaster.ray.origin.distanceTo(worldPoint) });
    }
    found.sort((a, b) => a.distance - b.distance);
    return found;
  }

  function volumeSurfaceY(entry, ray) {
    if (!entry.mesh) return null;
    entry.mesh.updateWorldMatrix(true, false);
    localInverse.copy(entry.mesh.matrixWorld).invert();
    localRay.copy(ray.ray).applyMatrix4(localInverse);
    if (!localRay.intersectBox(unitBox, localPoint)) return null;
    return localPoint.clone().applyMatrix4(entry.mesh.matrixWorld).y;
  }

  function idOfHit(hit) {
    const index = hit.batchId !== undefined ? hit.batchId : hit.instanceId;
    if (index !== undefined) {
      const batch = batches.find((item) => item.mesh === hit.object);
      if (batch) return batch.ids[index] || null;
    }
    return idOf(hit.object);
  }

  function classify(hits) {
    const solids = [];
    const volumes = [];
    const edges = [];
    for (const hit of hits) {
      if (!hit.object.visible) continue;
      const id = idOfHit(hit);
      const entry = id ? entries.get(id) : null;
      if (!entry || !canPick({ shown: entry.pivot.visible, locked: entry.locked })) continue;
      if (entry.volume && (!entry.parts || !entry.parts.length)) {
        if (!volumes.includes(id)) volumes.push(id);
        if (nearEdge(hit, entry) && !edges.includes(id)) edges.push(id);
      } else if (!solids.includes(id)) {
        solids.push(id);
      }
    }
    return { solids, volumes, edges };
  }

  function nearEdge(hit, entry) {
    const local = entry.mesh.worldToLocal(hit.point.clone());
    const close = ["x", "y", "z"].filter((axis) => Math.abs(Math.abs(local[axis]) - 0.5) < 0.08);
    return close.length >= 2;
  }

  function signature(groups) {
    return [...groups.solids, "|", ...groups.edges, "|", ...groups.volumes].join(",");
  }

  function pick(event) {
    const groups = aimed(event);
    const repeat = Boolean(
      lastClick && Math.hypot(event.clientX - lastClick.x, event.clientY - lastClick.y) < 6 && lastClick.signature === signature(groups),
    );
    const id = chooseHit({ ...groups, selected, alt: event.altKey, repeat });
    lastClick = { x: event.clientX, y: event.clientY, signature: signature(groups) };
    hooks.onSelect(id);
  }

  renderer.domElement.addEventListener(
    "pointerdown",
    (event) => {
      if (event.button !== 0 || !event.altKey || transform.dragging) return;
      savedOrbitTarget.copy(orbit.target);
      aimOrbit(event);
      orbit.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
      orbitArmed = true;
      orbitMoved = false;
      orbitSettle = 0;
      requestRender();
    },
    true,
  );

  renderer.domElement.addEventListener("pointerdown", (event) => {
    if (event.button === 2) {
      looking = true;
      lastPointer = { x: event.clientX, y: event.clientY };
      try {
        renderer.domElement.setPointerCapture(event.pointerId);
      } catch {
        /* capture is only the fallback when pointer lock is refused */
      }
      requestLookLock();
      event.preventDefault();
      return;
    }
    if (event.button !== 0) return;
    lastPointer = { x: event.clientX, y: event.clientY, moved: false };
    gizmoUsed = Boolean(transform.axis);
    if (gizmoUsed) orbit.enabled = false;
  });

  renderer.domElement.addEventListener("pointermove", (event) => {
    if (looking) {
      applyLook(event.movementX || 0, event.movementY || 0);
      return;
    }
    if ((event.buttons & 2) !== 0) orbitMoved = true;
    if (lastPointer && event.buttons === 1) {
      const dx = event.clientX - lastPointer.x;
      const dy = event.clientY - lastPointer.y;
      if (event.altKey && (dx !== 0 || dy !== 0)) orbitMoved = true;
      if (Math.hypot(dx, dy) > 4) lastPointer.moved = true;
    }
    if (transform.dragging) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    const next = chooseHit(aimed(event));
    const key = next || null;
    if (key !== hovered) {
      const previous = hovered ? entries.get(hovered) : null;
      hovered = key;
      if (previous) syncGhost(previous);
      const nextEntry = hovered ? entries.get(hovered) : null;
      if (nextEntry) syncGhost(nextEntry);
      syncLabels();
      requestRender();
    }
  });

  function endPointer(event) {
    if (event.button === 2) {
      releaseLook();
      event.preventDefault();
    }
    if (event.button === 0 && lastPointer && !lastPointer.moved && !orbitMoved && !gizmoUsed && !transform.dragging) pick(event);
    const didOrbit = orbitMoved;
    if (event.button === 0 && orbitArmed && !didOrbit) orbit.target.copy(savedOrbitTarget);
    orbitArmed = false;
    orbitMoved = false;
    orbit.mouseButtons.LEFT = -1;
    orbit.enabled = true;
    if (orbitGesture) {
      orbitGesture = false;
      if (didOrbit || event.button === 1) orbitSettle = 24;
    }
    gizmoUsed = false;
    lastPointer = null;
  }

  renderer.domElement.addEventListener("pointerup", endPointer);
  window.addEventListener("pointerup", (event) => {
    if (event.button === 2 && looking) releaseLook();
  });
  document.addEventListener("pointerlockchange", () => {
    const locked = document.pointerLockElement === renderer.domElement;
    if (locked) {
      lookLocked = true;
      return;
    }
    // Esc, or the browser dropping the lock. A refused request never locked,
    // so a drag that is still held keeps turning through pointer capture.
    if (lookLocked) {
      lookLocked = false;
      looking = false;
    }
  });
  renderer.domElement.addEventListener("contextmenu", (event) => event.preventDefault());

  transform.addEventListener("objectChange", () => {
    const entry = selected ? entries.get(selected) : null;
    if (!entry) return;
    const axis = transform.axis;
    if (snapOn && transform.mode === "translate" && (axis === "X" || axis === "Z" || axis === "XZ")) maybeSnap(entry);
    const p = entry.pivot.position;
    hooks.onPreview(entry.id, [p.x, p.y, p.z], fileRotate(entry.pivot), pivotScale(entry.pivot));
    syncInstanceMatrix(entry);
    requestRender();
  });

  transform.addEventListener("dragging-changed", (event) => {
    orbit.enabled = !event.value;
    if (event.value) {
      gizmoUsed = true;
      return;
    }
    const entry = selected ? entries.get(selected) : null;
    if (!entry || !entry.id) return;
    maybeSnap(entry);
    const p = entry.pivot.position;
    hooks.onTransform(entry.id, [p.x, p.y, p.z], fileRotate(entry.pivot), pivotScale(entry.pivot));
  });

  let keyWindow = null;
  function onFlyKey(event) {
    const docs = [document];
    if (event.view && event.view.document && event.view.document !== document) docs.push(event.view.document);
    if (isTyping(event) || docs.some((doc) => doc.querySelector("dialog[open]"))) return;
    keys.add(event.key.toLowerCase());
    requestRender();
  }
  function onFlyUp(event) {
    keys.delete(event.key.toLowerCase());
  }
  function onFlyBlur() {
    keys.clear();
  }
  function bindFlyKeys() {
    const next = host.ownerDocument.defaultView || window;
    if (next === keyWindow) return;
    if (keyWindow) {
      keyWindow.removeEventListener("keydown", onFlyKey);
      keyWindow.removeEventListener("keyup", onFlyUp);
      keyWindow.removeEventListener("blur", onFlyBlur);
    }
    keyWindow = next;
    keyWindow.addEventListener("keydown", onFlyKey);
    keyWindow.addEventListener("keyup", onFlyUp);
    keyWindow.addEventListener("blur", onFlyBlur);
  }
  bindFlyKeys();

  function updateFly(dt) {
    const speed = (keys.has("shift") ? 32 : 10) * dt;
    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
    const move = new THREE.Vector3();
    if (keys.has("w")) move.add(forward);
    if (keys.has("s")) move.sub(forward);
    if (keys.has("d")) move.add(right);
    if (keys.has("a")) move.sub(right);
    if (keys.has("e")) move.y += 1;
    if (keys.has("q")) move.y -= 1;
    if (move.lengthSq() === 0) return false;
    move.normalize().multiplyScalar(speed);
    camera.position.add(move);
    orbit.target.add(move);
    return true;
  }

  let observedDoc = host.ownerDocument;
  function resize() {
    bindFlyKeys();
    if (host.ownerDocument !== observedDoc) {
      observedDoc = host.ownerDocument;
      observer.disconnect();
      observer.observe(host);
    }
    const width = host.clientWidth || 1;
    const height = host.clientHeight || 1;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    labels.setSize(width, height);
    requestRender();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  const lastCull = new THREE.Vector3(1e9, 1e9, 1e9);
  let lastCullAt = 0;
  const frameTimes = [];
  let fps = 0;
  let frameMs = null;
  let lastTickAt = 0;
  let statInstances = 0;
  let statCulled = 0;
  let statQueued = 0;
  let statLoading = 0;
  let statLoaded = 0;
  let statConvert = 0;
  let statTexture = 0;
  let statDownload = 0;
  let statUpload = 0;
  let statWarmDone = null;
  let statWarmTotal = null;
  const timedModels = new Set();
  let uvDirty = true;

  function guardMissingUv() {
    if (!renderer.isWebGPURenderer || !uvDirty) return;
    uvDirty = false;
    dropMapWithoutUv(scene);
  }

  function requestRender() {
    if (!running || frameHandle) return;
    frameHandle = requestAnimationFrame(tick);
  }

  function writeStats(rendering) {
    const now = performance.now();
    frameTimes.push(now);
    while (frameTimes.length && now - frameTimes[0] > 500) frameTimes.shift();
    if (frameTimes.length >= 4) {
      fps = Math.round(((frameTimes.length - 1) * 1000) / (now - frameTimes[0]));
    }
    const info = renderer.info && renderer.info.render;
    const text = formatStats({
      backend: backendName,
      rendering,
      fps,
      frameMs,
      draws: drawTotal(
        info,
        Boolean(renderer.isWebGPURenderer),
        renderer.backend && renderer.backend.isWebGPUBackend === true
          ? batches.reduce((sum, batch) => sum + (batch.mesh._multiDrawCount || 0), 0)
          : 0,
      ),
      instances: statInstances,
      culled: statCulled,
      separate: naiveDraws,
      tris: info.triangles,
      queued: statQueued,
      loading: statLoading,
      loaded: statLoaded,
      convertMs: statConvert,
      textureMs: statTexture,
      downloadMs: statDownload,
      uploadMs: statUpload,
      warmDone: statWarmDone,
      warmTotal: statWarmTotal,
    });
    if (showStats) statsEl.textContent = text;
    if (hooks.onStats) hooks.onStats(text);
  }

  function tick() {
    frameHandle = 0;
    if (!running) return;
    const tickAt = performance.now();
    const gap = lastTickAt ? tickAt - lastTickAt : 0;
    lastTickAt = tickAt;
    const dt = Math.min(clock.getDelta(), 0.05);
    const flying = updateFly(dt);
    const damped = !looking && ((orbitGesture && orbitMoved) || orbitSettle > 0) ? orbit.update() : false;
    if (!orbitGesture && orbitSettle > 0) orbitSettle -= 1;
    const now = performance.now();
    if (now - lastCullAt > 120 && camera.position.distanceTo(lastCull) > 2) {
      lastCullAt = now;
      lastCull.copy(camera.position);
      prioritizeLoad();
      syncLabels();
    }
    syncBatchVisibility();
    guardMissingUv();
    const entry = selected ? entries.get(selected) : null;
    if (entry) {
      const distance = camera.position.distanceTo(entry.pivot.position);
      transform.size = THREE.MathUtils.clamp(distance / 14, 0.55, 2.4);
    }
    if (renderer.isWebGPURenderer && renderer.info && typeof renderer.info.reset === "function") renderer.info.reset();
    renderer.render(scene, camera);
    if (gpuProfile) {
      const materials = [];
      for (const batch of batches) materials.push(batch.mesh.material);
      if (terrainRoot) {
        terrainRoot.traverse((child) => {
          if (!child.material) return;
          materials.push(Array.isArray(child.material) ? child.material[0] : child.material);
        });
      }
      const info = renderer.info && renderer.info.render;
      sampleGpuFrame(gpuProfile, {
        calls: info ? info.calls : 0,
        materials,
        loading: pendingLoads > 0,
      });
    }
    labels.render(scene, camera);
    const loading = pendingLoads > 0;
    const again = keepRendering({
      flying,
      damped,
      looking,
      dragging: transform.dragging,
      loading,
    });
    if (again && gap > 0 && gap < 250) frameMs = Math.round(gap * 10) / 10;
    writeStats(again);
    if (again) requestRender();
  }
  renderer.domElement.addEventListener(
    "wheel",
    () => {
      orbitSettle = 24;
      requestRender();
    },
    { passive: true },
  );
  orbit.addEventListener("start", () => {
    orbitGesture = true;
    requestRender();
  });
  orbit.addEventListener("end", () => {
    orbitGesture = false;
    orbit.mouseButtons.LEFT = -1;
    orbitSettle = 24;
    requestRender();
  });
  orbit.addEventListener("change", () => requestRender());
  requestRender();

  return {
    backend: backendName,
    backendNote,
    setActors,
    patchActors,
    reconcileActors,
    setSnap(on) {
      snapOn = Boolean(on);
    },
    select,
    setGizmo,
    setFlags,
    modelInfo,
    frame,
    spawnPoint,
    resize,
    setWarm(status) {
      const total = status && Number(status.total);
      statWarmDone = total ? Number(status.done) || 0 : null;
      statWarmTotal = total || null;
      requestRender();
    },
    setShowStats(on) {
      showStats = Boolean(on);
      statsEl.hidden = !showStats;
      requestRender();
    },
    destroy() {
      running = false;
      if (rebuildTimer) clearTimeout(rebuildTimer);
      if (frameHandle) cancelAnimationFrame(frameHandle);
      if (keyWindow) {
        keyWindow.removeEventListener("keydown", onFlyKey);
        keyWindow.removeEventListener("keyup", onFlyUp);
        keyWindow.removeEventListener("blur", onFlyBlur);
      }
      observer.disconnect();
      statsEl.remove();
      generation += 1;
      observer.disconnect();
      clearActors();
      clearTerrain();
      clearGround();
      clearGrid();
      boxGeometry.dispose();
      edgesGeometry.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      labels.domElement.remove();
    },
  };
}

function isTyping(event) {
  const tag = event.target && event.target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (event.target && event.target.isContentEditable);
}
