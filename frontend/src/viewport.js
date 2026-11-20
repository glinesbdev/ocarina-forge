import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { acceleratedRaycast, computeBoundsTree, disposeBoundsTree } from "three-mesh-bvh";

import { chooseLod, isTerrainProxy } from "./cull.js";
import { chooseHit } from "./pick.js";

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
export function createViewport(host, hooks) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x141b16);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 8000);
  camera.position.set(12, 9, 14);
  camera.rotation.order = "YXZ";

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = false;
  renderer.shadowMap.autoUpdate = false;
  host.appendChild(renderer.domElement);

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
  orbit.mouseButtons = {
    LEFT: THREE.MOUSE.ROTATE,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.PAN,
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
  statsEl.hidden = true;
  host.appendChild(statsEl);
  let showStats = false;
  let naiveDraws = 0;
  let running = true;
  let frameHandle = 0;
  const loader = new GLTFLoader();
  const templateCache = new Map();
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
  let showSub = true;
  let showTerrainDebug = false;
  let generation = 0;
  let loadQueue = null;
  const loadDelta = new THREE.Vector3();
  const loadForward = new THREE.Vector3();
  let terrainRoot = null;
  let groundGrid = null;
  let lastClick = null;
  let grid = null;
  let selected = null;
  let hovered = null;
  let showLabels = false;
  let showHidden = false;
  let nav = "orbit";
  let looking = false;
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

  function clearTerrain() {
    if (!terrainRoot) return;
    terrainRoot.traverse((child) => {
      if (child.geometry) child.geometry.dispose();
    });
    terrainRoot.removeFromParent();
    terrainRoot = null;
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

  function clearActors() {
    transform.detach();
    clearBatches();
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

  function setActors(actors, options = {}) {
    clearActors();
    clearTerrain();
    clearGround();
    const token = ++generation;
    actors.forEach((actor, index) => {
      const key = actor.id || `locked-${index}`;
      const recipe = actor.status === "recipe";
      const color = recipe ? 0xc9b6f2 : CATEGORY_COLOR[actor.category] || CATEGORY_COLOR.other;
      const volume = Boolean(actor.volume);
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
      pivot.rotation.set(rx, ry, rz);
      pivot.scale.set(sx || 1, sy || 1, sz || 1);
      pivot.userData.actorId = actor.id;
      const hiddenByStatus = actor.status === "hidden" && !showHidden;
      const hiddenByCategory = categories[actor.category] === "hide";
      const hiddenByVolume = volume && hideVolumes;
      pivot.visible = !hiddenByStatus && !hiddenByCategory && !hiddenByVolume;
      scene.add(pivot);
      entries.set(key, {
        key,
        id: actor.id,
        gyaml: actor.gyaml,
        category: actor.category,
        status: actor.status,
        volume,
        companion: Boolean(actor.companion),
        parts: null,
        farNames: null,
        slots: [],
        pickable: actor.pickable !== false,
        locked: categories[actor.category] === "lock" || (actor.companion && actor.pickable === false),
        color,
        pivot,
        mesh,
        material,
        edges,
        edgeMaterial,
        model: null,
        modelMeta: metaCache.get(actor.gyaml) || null,
        label: null,
        labelText: actor.companion
          ? `${actor.name || actor.gyaml} · ${actor.companionLabel || "companion"}`
          : recipe
            ? `${actor.name || actor.gyaml} · ${(actor.recipe && actor.recipe.note) || "added by build"}`
            : actor.name || actor.gyaml,
      });
    });
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
    if (options.banc !== undefined) {
      const banc = options.banc;
      // The static map can be hundreds of thousands of triangles. Let the
      // actor requests start first, and never raycast the map, so picking
      // stays on the actors while the terrain converts.
      setTimeout(() => {
        if (token === generation) loadTerrain(token, banc);
      }, 300);
    }
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

  function prioritizeLoad() {
    if (!loadQueue || loadQueue.length < 2) return;
    camera.getWorldDirection(loadForward);
    loadQueue.sort((a, b) => gyamlScore(a) - gyamlScore(b));
  }

  function loadModels(token, actors) {
    const names = [...new Set(actors.map((actor) => actor.gyaml).filter(Boolean))];
    const pending = names.filter((name) => !metaCache.has(name));
    let done = names.length - pending.length;
    let boxes = names.filter((name) => {
      const meta = metaCache.get(name);
      return meta && meta.status !== "mesh";
    }).length;
    const applyCached = () => {
      for (const entry of entries.values()) {
        const meta = metaCache.get(entry.gyaml);
        if (!meta) continue;
        entry.modelMeta = meta;
        if (meta.status !== "mesh") fitBounds(entry, meta.bounds);
        if (meta.status === "mesh" && meta.url) {
          ensureTemplate(meta.url)
            .then((template) => {
              if (token !== generation) return;
              const current = entries.get(entry.key);
              if (current) attachModel(current, template, meta.url);
            })
            .catch((error) => {
              if (token !== generation) return;
              const current = entries.get(entry.key);
              const message = (error && error.message) || "The glTF did not load.";
              console.error(`${entry.gyaml}: ${message}`);
              if (current) {
                current.modelMeta = { ...(current.modelMeta || {}), status: "box", reason: message };
                current.mesh.visible = true;
                fitBounds(current, current.modelMeta.bounds);
              }
              if (hooks.onModelError) hooks.onModelError(entry.gyaml, message);
            });
        }
      }
    };
    applyCached();
    const queue = pending.slice();
    loadQueue = queue;
    prioritizeLoad();
    const progressLabel = (finished) => {
      if (!names.length) return "No actors";
      return finished ? `Models ${done} of ${names.length}` : `Models ${done} of ${names.length} · nearest first, view stays usable`;
    };
    report({
      done,
      total: names.length,
      boxes,
      finished: pending.length === 0,
      label: progressLabel(pending.length === 0),
    });
    async function worker() {
      while (queue.length) {
        if (token !== generation) return;
        prioritizeLoad();
        const gyaml = queue.shift();
        let meta;
        try {
          meta = hooks.fetchModel ? await hooks.fetchModel(gyaml) : { status: "box", reason: "Model loading is not connected." };
        } catch (error) {
          meta = { status: "box", reason: error.message || "The model request failed.", source: "" };
        }
        if (token !== generation) return;
        metaCache.set(gyaml, meta);
        if (meta.status !== "mesh") boxes += 1;
        for (const entry of entries.values()) {
          if (entry.gyaml !== gyaml) continue;
          entry.modelMeta = meta;
          if (meta.status !== "mesh") fitBounds(entry, meta.bounds);
          if (meta.status !== "mesh" || !meta.url) continue;
          try {
            const template = await ensureTemplate(meta.url);
            if (token !== generation) return;
            attachModel(entry, template, meta.url);
          } catch (error) {
            const message = error.message || "The glTF did not load.";
            console.error(`${gyaml}: ${message}`);
            entry.modelMeta = { status: "box", reason: message, source: meta.source || "", bounds: meta.bounds || null };
            entry.mesh.visible = true;
            fitBounds(entry, meta.bounds);
            if (hooks.onModelError) hooks.onModelError(gyaml, message);
          }
          if (meta.status !== "mesh") fitBounds(entry, meta.bounds);
        }
        done += 1;
        report({
          done,
          total: names.length,
          boxes,
          finished: done >= names.length,
          label: progressLabel(done >= names.length),
        });
      }
    }
    const workers = Math.min(6, queue.length);
    if (workers) Promise.all(Array.from({ length: workers }, () => worker()));
  }

  function ensureTemplate(url) {
    let pending = templateCache.get(url);
    if (!pending) {
      pending = new Promise((resolve, reject) => {
        loader.load(url, (gltf) => resolve(gltf.scene), undefined, reject);
      });
      templateCache.set(url, pending);
    }
    return pending;
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

  function prepareMaterial(material) {
    if (!material || material.userData.bancPrepared) return material;
    material.metalness = 0;
    material.roughness = 0.85;
    if (material.map) {
      material.map.wrapS = THREE.RepeatWrapping;
      material.map.wrapT = THREE.RepeatWrapping;
      material.map.generateMipmaps = true;
      material.map.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
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
    return material;
  }

  function partsFromTemplate(template, url) {
    const parts = [];
    let index = 0;
    template.traverse((child) => {
      if (!child.isMesh || !child.geometry) return;
      const identity = lodOf(child);
      const geometry = child.geometry;
      if (!geometry.boundingSphere) geometry.computeBoundingSphere();
      if (!geometry.boundsTree) geometry.computeBoundsTree();
      const source = Array.isArray(child.material) ? child.material[0] : child.material;
      if (!source) return;
      const material = prepareMaterial(source);
      parts.push({
        lod: identity.lod,
        family: identity.family,
        geometry,
        material,
        radius: geometry.boundingSphere ? geometry.boundingSphere.radius : 1,
        key: `${url}|${identity.lod}|${identity.family}|${index}`,
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
    for (const slot of entry.slots) {
      slot.mesh.setMatrixAt(slot.index, entry.pivot.matrixWorld);
      slot.mesh.instanceMatrix.needsUpdate = true;
    }
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
    const imageKey = material.map && material.map.image
      ? imageSignature(material.map.image)
      : `flat:${material.color ? material.color.getHexString() : "fff"}:${material.opacity}`;
    const alpha = material.transparent ? `blend:${material.opacity}` : material.alphaTest > 0 ? `mask:${material.alphaTest}` : "opaque";
    const attrs = Object.keys(geometry.attributes).sort().join(",");
    const indexed = geometry.getIndex() ? "idx" : "no";
    const tintHex = material.color ? material.color.getHexString() : "fff";
    return `${imageKey}|${alpha}|s${material.side}|c${tintHex}|${indexed}|${attrs}`;
  }

  function actorScale(entry) {
    return Math.max(entry.pivot.scale.x, entry.pivot.scale.y, entry.pivot.scale.z, 0.001);
  }

  function screenPixels(radius, distance) {
    const height = renderer.domElement.height || 0;
    if (height < 2) return 999;
    return projectedPixels(radius, distance);
  }

  function lodFor(entry, part, distance) {
    const scale = actorScale(entry);
    const radius = part.radius * scale;
    return chooseLod({
      distance,
      radius,
      pixels: screenPixels(radius, distance),
      hasFar: Boolean(entry.farNames && entry.farNames.has(part.family)),
      selected: entry.key === selected,
    });
  }

  function syncBatchVisibility() {
    for (const entry of entries.values()) {
      if (!entry.slots || !entry.slots.length) continue;
      const distance = camera.position.distanceTo(entry.pivot.position);
      for (const slot of entry.slots) {
        const lod = entry.pivot.visible ? lodFor(entry, slot, distance) : null;
        slot.mesh.setVisibleAt(slot.index, lod === slot.lod);
      }
    }
  }

  function rebuildBatches() {
    clearBatches();
    const groups = new Map();
    naiveDraws = 0;
    for (const entry of entries.values()) {
      syncGhost(entry);
      if (!entry.parts || !entry.parts.length) continue;
      if (entry.pivot.visible) naiveDraws += entry.parts.filter((part) => part.lod === 0).length;
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
      mesh.perObjectFrustumCulled = true;
      mesh.sortObjects = Boolean(group.material.transparent);
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
      for (const record of group.records) {
        const geometryId = geomIds.get(record.part.geometry.uuid);
        if (geometryId === undefined) continue;
        const instanceId = mesh.addInstance(geometryId);
        mesh.setMatrixAt(instanceId, record.entry.pivot.matrixWorld);
        mesh.setColorAt(instanceId, instanceTint(record.entry, tint));
        ids[instanceId] = record.entry.key;
        geometries[instanceId] = record.part.geometry;
        spheres[instanceId] = record.part.geometry.boundingSphere;
        count = Math.max(count, instanceId + 1);
        record.entry.slots.push({
          mesh,
          index: instanceId,
          lod: record.part.lod,
          family: record.part.family,
          radius: record.part.radius,
        });
      }
      mesh.userData.pick = { ids, geometries, spheres, count };
      mesh.raycast = raycastBatch;
      mesh.computeBoundingSphere();
      scene.add(mesh);
      batches.push({ mesh, ids, water });
    }
    syncBatchVisibility();
  }

  let rebuildTimer = 0;
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
    const ys = [];
    const point = new THREE.Vector3();
    template.updateWorldMatrix(true, true);
    template.traverse((child) => {
      if (!child.isMesh || !child.geometry) return;
      const position = child.geometry.attributes.position;
      if (!position) return;
      const step = Math.max(1, Math.floor(position.count / 2500));
      for (let i = 0; i < position.count; i += step) {
        point.fromBufferAttribute(position, i).applyMatrix4(child.matrixWorld);
        box.expandByPoint(point);
        ys.push(point.y);
      }
    });
    ys.sort((a, b) => a - b);
    const height = ys.length ? ys[Math.floor(ys.length * 0.5)] : 0;
    return { box, height };
  }

  function placeGround(box, height) {
    clearGround();
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const span = Math.max(10, Math.ceil(Math.max(size.x, size.z)));
    const divisions = Math.min(120, Math.max(12, Math.round(span / 8)));
    const gridLines = new THREE.GridHelper(span, divisions, 0x9aa394, 0x3c4740);
    gridLines.position.set(center.x, height, center.z);
    gridLines.visible = showGround;
    gridLines.raycast = () => {};
    gridLines.traverse((child) => {
      child.raycast = () => {};
    });
    scene.add(gridLines);
    groundGrid = gridLines;
  }

  const TERRAIN_MAP = `
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
    if (terrainSub < 0.5 && vTerrainMark.y > 0.5) discard;
    vec4 sampledDiffuseColor = mix(
      terrainTri(vTerrainLayer.x),
      terrainTri(vTerrainLayer.y),
      clamp(vTerrainShade.x, 0.0, 1.0)
    );
    if (terrainDebug > 0.5) {
      float hue = vTerrainMark.x * 6.2831853;
      vec3 ink = clamp(vec3(
        0.55 + 0.45 * cos(hue),
        0.55 + 0.45 * cos(hue + 2.094395),
        0.55 + 0.45 * cos(hue + 4.188790)
      ), 0.0, 1.0);
      if (vTerrainMark.y > 0.5) ink = mix(ink, vec3(1.0), 0.45);
      sampledDiffuseColor = vec4(ink, 1.0);
    }
    diffuseColor *= sampledDiffuseColor;
  `;

  function installTerrainShader(material) {
    if (!material || material.userData.terrainShader) return material;
    const columns = Number(material.userData.atlas) || 1;
    const repeat = Number(material.userData.repeat) || 8;
    material.userData.terrainShader = true;
    if (!material.userData.terrainDebugUniform) {
      material.userData.terrainDebugUniform = { value: showTerrainDebug ? 1 : 0 };
    }
    if (!material.userData.terrainSubUniform) {
      material.userData.terrainSubUniform = { value: showSub ? 1 : 0 };
    }
    if (material.map) {
      material.map.wrapS = THREE.ClampToEdgeWrapping;
      material.map.wrapT = THREE.ClampToEdgeWrapping;
      material.map.needsUpdate = true;
    }
    const previous = material.onBeforeCompile;
    material.onBeforeCompile = (shader) => {
      if (previous) previous(shader);
      shader.uniforms.terrainCols = { value: columns };
      shader.uniforms.terrainRepeat = { value: repeat };
      shader.uniforms.terrainDebug = material.userData.terrainDebugUniform;
      shader.uniforms.terrainSub = material.userData.terrainSubUniform;
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
uniform float terrainSub;`
        )
        .replace("#include <map_fragment>", TERRAIN_MAP);
    };
    const previousKey = material.customProgramCacheKey ? material.customProgramCacheKey.bind(material) : null;
    material.customProgramCacheKey = () => `${previousKey ? previousKey() : ""}|terrain|${columns}|${repeat}`;
    material.needsUpdate = true;
    return material;
  }

  function mergeStatic(template) {
    const byMaterial = new Map();
    template.traverse((child) => {
      if (!child.isMesh || !child.geometry) return;
      const source = Array.isArray(child.material) ? child.material[0] : child.material;
      if (!source) return;
      let material = prepareMaterial(source);
      const geometry = child.geometry.clone();
      const terrain =
        geometry.getAttribute("uv1") &&
        (((material.userData && material.userData.banc) === "terrain") || (child.name || "").includes("terrain"));
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
            root.add(mesh);
          }
          continue;
        }
      }
      if (!geometry) continue;
      if (!geometry.boundsTree) geometry.computeBoundsTree();
      const mesh = new THREE.Mesh(geometry, group.material);
      mesh.raycast = () => {};
      root.add(mesh);
    }
    return root;
  }

  async function loadTerrain(token, banc) {
    clearTerrain();
    clearGround();
    if (!banc || !hooks.fetchTerrain) return;
    let meta;
    try {
      meta = await hooks.fetchTerrain(banc);
    } catch (error) {
      meta = { status: "none", reason: error.message || "The map model request failed." };
    }
    if (token !== generation) return;
    if (hooks.onTerrain) hooks.onTerrain(meta);
    if (meta.status !== "mesh" || !meta.url) return;
    try {
      const template = await ensureTemplate(meta.url);
      if (token !== generation) return;
      await new Promise((resolve) => {
        if (typeof requestIdleCallback === "function") requestIdleCallback(() => resolve(), { timeout: 800 });
        else setTimeout(resolve, 0);
      });
      if (token !== generation) return;
      if (isTerrainProxy(meta)) {
        const measured = measureTemplate(template);
        if (!measured.box.isEmpty()) placeGround(measured.box, measured.height);
        requestRender();
        return;
      }
      terrainRoot = mergeStatic(template);
      terrainRoot.name = "terrain";
      terrainRoot.visible = showGround;
      terrainRoot.renderOrder = 0;
      terrainRoot.raycast = () => {};
      terrainRoot.traverse((child) => {
        child.raycast = () => {};
        child.castShadow = false;
        child.receiveShadow = false;
        child.renderOrder = 0;
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        for (const material of materials) {
          if (!material) continue;
          material.transparent = false;
          material.depthWrite = true;
          material.side = THREE.DoubleSide;
          material.needsUpdate = true;
        }
      });
      scene.add(terrainRoot);
      requestRender();
    } catch (error) {
      if (token === generation && hooks.onTerrain) {
        hooks.onTerrain({ status: "box", reason: error.message || "The map glTF did not load.", source: meta.source || "" });
      }
    }
  }

  function attach() {
    const entry = selected ? entries.get(selected) : null;
    if (!entry || !entry.id || !entry.pivot.visible || entry.locked) {
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

  function setNav(mode) {
    nav = mode;
    orbit.enabled = mode === "orbit";
    releaseLook();
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
    transform.setSpace(mode === "rotate" ? "local" : "world");
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

  function setFlags({ hidden, labels, categories: next, volumes, ground, water, boxes, terrainDebug, subTerrain }) {
    if (hidden !== undefined) showHidden = hidden;
    if (labels !== undefined) showLabels = labels;
    if (volumes !== undefined) hideVolumes = volumes;
    if (ground !== undefined) showGround = Boolean(ground);
    if (water !== undefined) showWater = Boolean(water);
    if (terrainDebug !== undefined) showTerrainDebug = Boolean(terrainDebug);
    if (subTerrain !== undefined) showSub = Boolean(subTerrain);
    if (next) Object.assign(categories, next);
    if (boxes) Object.assign(boxCategories, boxes);
    for (const entry of entries.values()) {
      const hiddenByStatus = entry.status === "hidden" && !showHidden;
      const hiddenByCategory = categories[entry.category] === "hide";
      const hiddenByVolume = entry.volume && hideVolumes;
      entry.locked = categories[entry.category] === "lock" || (entry.companion && entry.pickable === false);
      entry.pivot.visible = !hiddenByStatus && !hiddenByCategory && !hiddenByVolume;
      syncGhost(entry);
    }
    syncBatchVisibility();
    applyGround();
    applyWater();
    applyTerrainUniforms();
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

  function spawnPoint() {
    return [round(orbit.target.x), round(orbit.target.y), round(orbit.target.z)];
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
  const pointer = new THREE.Vector2();

  function aimed(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const roots = [];
    for (const batch of batches) roots.push(batch.mesh);
    for (const entry of entries.values()) {
      if (entry.pivot.visible && !entry.locked && (!entry.parts || !entry.parts.length)) roots.push(entry.pivot);
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
      if (!entry.volume || entry.locked || !entry.pivot.visible) continue;
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
      if (!entry || entry.locked || !entry.pivot.visible) continue;
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

  renderer.domElement.addEventListener("pointerdown", (event) => {
    if (event.button === 2 && nav === "fly") {
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
    if (lastPointer && event.buttons === 1) {
      const dx = event.clientX - lastPointer.x;
      const dy = event.clientY - lastPointer.y;
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
    if (event.button === 0 && lastPointer && !lastPointer.moved && !gizmoUsed && !transform.dragging) pick(event);
    if (nav === "orbit") orbit.enabled = true;
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
    const p = entry.pivot.position;
    const r = entry.pivot.rotation;
    hooks.onPreview(entry.id, [p.x, p.y, p.z], [r.x, r.y, r.z]);
    syncInstanceMatrix(entry);
    requestRender();
  });

  transform.addEventListener("dragging-changed", (event) => {
    orbit.enabled = !event.value && nav === "orbit";
    if (event.value) {
      gizmoUsed = true;
      return;
    }
    const entry = selected ? entries.get(selected) : null;
    if (!entry || !entry.id) return;
    const p = entry.pivot.position;
    const r = entry.pivot.rotation;
    hooks.onTransform(entry.id, [p.x, p.y, p.z], [r.x, r.y, r.z]);
  });

  window.addEventListener("keydown", (event) => {
    if (isTyping(event)) return;
    keys.add(event.key.toLowerCase());
    requestRender();
  });
  window.addEventListener("keyup", (event) => keys.delete(event.key.toLowerCase()));
  window.addEventListener("blur", () => keys.clear());

  function updateFly(dt) {
    if (nav !== "fly") return false;
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

  function resize() {
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

  function requestRender() {
    if (!running || frameHandle) return;
    frameHandle = requestAnimationFrame(tick);
  }

  function writeStats() {
    if (!showStats) return;
    const now = performance.now();
    frameTimes.push(now);
    while (frameTimes.length && now - frameTimes[0] > 500) frameTimes.shift();
    if (frameTimes.length >= 4) {
      fps = Math.round(((frameTimes.length - 1) * 1000) / (now - frameTimes[0]));
    }
    const info = renderer.info.render;
    const tris = info.triangles >= 1000 ? `${Math.round(info.triangles / 100) / 10}k` : String(info.triangles);
    const rate = fps ? `${fps} fps` : "idle";
    statsEl.textContent = `${rate} · ${info.calls} draws · ${naiveDraws} model draws if each copy was separate · ${tris} tris`;
  }

  function tick() {
    frameHandle = 0;
    if (!running) return;
    const dt = Math.min(clock.getDelta(), 0.05);
    const flying = updateFly(dt);
    const damped = nav === "orbit" ? orbit.update() : false;
    const now = performance.now();
    if (now - lastCullAt > 120 && camera.position.distanceTo(lastCull) > 2) {
      lastCullAt = now;
      lastCull.copy(camera.position);
      prioritizeLoad();
      syncBatchVisibility();
      syncLabels();
    }
    const entry = selected ? entries.get(selected) : null;
    if (entry) {
      const distance = camera.position.distanceTo(entry.pivot.position);
      transform.size = THREE.MathUtils.clamp(distance / 14, 0.55, 2.4);
    }
    renderer.render(scene, camera);
    labels.render(scene, camera);
    writeStats();
    if (flying || damped || looking || transform.dragging) requestRender();
  }
  orbit.addEventListener("start", () => requestRender());
  orbit.addEventListener("change", () => requestRender());
  requestRender();

  return {
    setActors,
    select,
    setNav,
    setGizmo,
    setFlags,
    modelInfo,
    frame,
    spawnPoint,
    resize,
    setShowStats(on) {
      showStats = Boolean(on);
      statsEl.hidden = !showStats;
      requestRender();
    },
    destroy() {
      running = false;
      if (rebuildTimer) clearTimeout(rebuildTimer);
      if (frameHandle) cancelAnimationFrame(frameHandle);
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
  return tag === "INPUT" || tag === "TEXTAREA" || (event.target && event.target.isContentEditable);
}
