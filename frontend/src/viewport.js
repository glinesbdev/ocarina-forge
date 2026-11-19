import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import { chooseHit } from "./pick.js";

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
 * Volumes (areas, system actors, large land) are translucent and lose picks
 * to anything solid along the same ray.
 */
export function createViewport(host, hooks) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x141b16);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 4000);
  camera.position.set(12, 9, 14);
  camera.rotation.order = "YXZ";

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);

  const labels = new CSS2DRenderer();
  labels.domElement.className = "label-layer";
  host.appendChild(labels.domElement);

  scene.add(new THREE.HemisphereLight(0xf3f6ea, 0x2c3a2e, 0.9));
  const sun = new THREE.DirectionalLight(0xfff6df, 1.2);
  sun.position.set(18, 36, 14);
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
  const loader = new GLTFLoader();
  const templateCache = new Map();
  const metaCache = new Map();
  const categories = { area: "show", system: "show" };
  let generation = 0;
  let terrainRoot = null;
  let lastClick = null;
  let grid = null;
  let selected = null;
  let hovered = null;
  let showLabels = false;
  let showHidden = false;
  let nav = "orbit";
  let looking = false;
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
    if (entry.model) {
      entry.model.traverse((child) => {
        const materials = child.material ? (Array.isArray(child.material) ? child.material : [child.material]) : [];
        materials.forEach((material) => material.dispose());
      });
    }
    if (entry.label) entry.label.element.remove();
  }

  function clearTerrain() {
    if (!terrainRoot) return;
    terrainRoot.traverse((child) => {
      const materials = child.material ? (Array.isArray(child.material) ? child.material : [child.material]) : [];
      materials.forEach((material) => material.dispose());
    });
    terrainRoot.removeFromParent();
    terrainRoot = null;
  }

  function clearActors() {
    transform.detach();
    for (const entry of entries.values()) disposeEntry(entry);
    entries.clear();
  }

  function paint(entry) {
    const on = entry.key === selected;
    entry.material.emissive.set(on ? entry.color : 0x000000);
    entry.material.emissiveIntensity = on ? 0.45 : 0;
    const marked = entry.status === "edited" || entry.status === "added" || entry.status === "recipe";
    entry.edgeMaterial.color.set(on || marked ? 0xf2d7a2 : entry.volume ? entry.color : 0x101612);
    entry.mesh.visible = !entry.model;
    entry.edges.visible = !entry.model && (entry.volume || on || marked || entry.status === "hidden");
    if (!entry.model) return;
    entry.model.traverse((child) => {
      if (!child.isMesh || !child.material || !child.material.emissive) return;
      child.material.emissive.set(on ? 0x3a2a10 : 0x000000);
      child.material.emissiveIntensity = on ? 0.35 : 0;
    });
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
    for (const entry of entries.values()) {
      const show = entry.pivot.visible && (showLabels || entry.key === selected || entry.key === hovered);
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
    camera.far = Math.max(2000, radius * 40);
    camera.updateProjectionMatrix();
    orbit.update();
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
    const token = ++generation;
    actors.forEach((actor, index) => {
      const key = actor.id || `locked-${index}`;
      const recipe = actor.status === "recipe";
      const color = recipe ? 0xc9b6f2 : CATEGORY_COLOR[actor.category] || CATEGORY_COLOR.other;
      const volume = Boolean(actor.volume);
      const material = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.7,
        metalness: 0.05,
      });
      if (actor.status === "hidden" || volume || recipe) {
        material.transparent = true;
        material.opacity = actor.status === "hidden" ? 0.18 : recipe ? 0.72 : 0.07;
        material.depthWrite = recipe;
      }
      const mesh = new THREE.Mesh(boxGeometry, material);
      const edgeMaterial = new THREE.LineBasicMaterial({ color: volume ? color : 0x101612 });
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
      pivot.visible = !hiddenByStatus && !hiddenByCategory;
      scene.add(pivot);
      entries.set(key, {
        key,
        id: actor.id,
        gyaml: actor.gyaml,
        category: actor.category,
        status: actor.status,
        volume,
        locked: categories[actor.category] === "lock",
        color,
        pivot,
        mesh,
        material,
        edges,
        edgeMaterial,
        model: null,
        modelMeta: metaCache.get(actor.gyaml) || null,
        label: null,
        labelText: recipe
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

  function report(info) {
    if (hooks.onProgress) hooks.onProgress(info);
    if (info.finished && hooks.onModels) hooks.onModels();
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
        if (meta.status === "mesh" && meta.url) {
          ensureTemplate(meta.url)
            .then((template) => {
              if (token !== generation) return;
              const current = entries.get(entry.key);
              if (current) attachModel(current, template);
            })
            .catch(() => {});
        }
      }
    };
    applyCached();
    report({
      done,
      total: names.length,
      boxes,
      finished: pending.length === 0,
      label: names.length ? `Models ${done} of ${names.length}` : "No actors",
    });
    const queue = pending.slice();
    async function worker() {
      while (queue.length) {
        if (token !== generation) return;
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
          if (meta.status !== "mesh" || !meta.url) continue;
          try {
            const template = await ensureTemplate(meta.url);
            if (token !== generation) return;
            attachModel(entry, template);
          } catch (error) {
            entry.modelMeta = { status: "box", reason: error.message || "The glTF did not load.", source: meta.source || "" };
            entry.mesh.visible = true;
          }
        }
        done += 1;
        report({
          done,
          total: names.length,
          boxes,
          finished: done >= names.length,
          label: `Models ${done} of ${names.length}`,
        });
      }
    }
    const workers = Math.min(3, queue.length);
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

  function cloneModel(template) {
    const clone = template.clone(true);
    clone.traverse((child) => {
      if (!child.isMesh || !child.material) return;
      const source = Array.isArray(child.material) ? child.material[0] : child.material;
      const material = source.clone();
      material.side = THREE.DoubleSide;
      material.metalness = 0;
      material.roughness = 0.85;
      child.material = material;
    });
    return clone;
  }

  function attachModel(entry, template) {
    if (entry.model) return;
    const model = cloneModel(template);
    model.traverse((child) => {
      child.userData.actorId = entry.id;
    });
    if (entry.status === "recipe") {
      model.traverse((child) => {
        if (!child.isMesh || !child.material) return;
        child.material.color.set(0xc9b6f2);
        child.material.transparent = true;
        child.material.opacity = 0.92;
      });
    }
    entry.pivot.add(model);
    entry.model = model;
    entry.volume = false;
    paint(entry);
  }

  async function loadTerrain(token, banc) {
    clearTerrain();
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
      terrainRoot = cloneModel(template);
      terrainRoot.name = "terrain";
      terrainRoot.raycast = () => {};
      terrainRoot.traverse((child) => {
        child.raycast = () => {};
      });
      scene.add(terrainRoot);
    } catch (error) {
      if (token === generation && hooks.onTerrain) {
        hooks.onTerrain({ status: "box", reason: error.message || "The map glTF did not load.", source: meta.source || "" });
      }
    }
  }

  function attach() {
    const entry = selected ? entries.get(selected) : null;
    if (!entry || !entry.id || !entry.pivot.visible || entry.locked || entry.status === "recipe") {
      transform.detach();
      return;
    }
    transform.attach(entry.pivot);
  }

  function select(id) {
    selected = id;
    for (const entry of entries.values()) paint(entry);
    attach();
    syncLabels();
  }

  function setNav(mode) {
    nav = mode;
    orbit.enabled = mode === "orbit";
    looking = false;
  }

  function setGizmo(mode) {
    transform.setMode(mode);
    transform.setSpace(mode === "rotate" ? "local" : "world");
  }

  function setFlags({ hidden, labels, categories: next }) {
    if (hidden !== undefined) showHidden = hidden;
    if (labels !== undefined) showLabels = labels;
    if (next) Object.assign(categories, next);
    for (const entry of entries.values()) {
      const hiddenByStatus = entry.status === "hidden" && !showHidden;
      const hiddenByCategory = categories[entry.category] === "hide";
      entry.locked = categories[entry.category] === "lock";
      entry.pivot.visible = !hiddenByStatus && !hiddenByCategory;
    }
    attach();
    syncLabels();
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
    for (const entry of entries.values()) {
      if (entry.pivot.visible && !entry.locked) roots.push(entry.pivot);
    }
    return classify(roots.length ? raycaster.intersectObjects(roots, true) : []);
  }

  function classify(hits) {
    const solids = [];
    const volumes = [];
    const edges = [];
    for (const hit of hits) {
      if (!hit.object.visible) continue;
      const id = idOf(hit.object);
      const entry = id ? entries.get(id) : null;
      if (!entry || entry.locked || !entry.pivot.visible) continue;
      if (entry.volume && !entry.model) {
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
      event.preventDefault();
      return;
    }
    if (event.button !== 0) return;
    lastPointer = { x: event.clientX, y: event.clientY, moved: false };
    gizmoUsed = Boolean(transform.axis);
    if (gizmoUsed) orbit.enabled = false;
  });

  renderer.domElement.addEventListener("pointermove", (event) => {
    if (looking && lastPointer) {
      const dx = event.clientX - lastPointer.x;
      const dy = event.clientY - lastPointer.y;
      lastPointer = { x: event.clientX, y: event.clientY };
      camera.rotation.order = "YXZ";
      camera.rotation.y -= dx * 0.003;
      camera.rotation.x = Math.max(-1.4, Math.min(1.4, camera.rotation.x - dy * 0.003));
      const forward = new THREE.Vector3();
      camera.getWorldDirection(forward);
      orbit.target.copy(camera.position).add(forward.multiplyScalar(8));
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
      hovered = key;
      syncLabels();
    }
  });

  function endPointer(event) {
    if (event.button === 2) looking = false;
    if (event.button === 0 && lastPointer && !lastPointer.moved && !gizmoUsed && !transform.dragging) pick(event);
    if (nav === "orbit") orbit.enabled = true;
    gizmoUsed = false;
    lastPointer = null;
  }

  renderer.domElement.addEventListener("pointerup", endPointer);
  renderer.domElement.addEventListener("pointerleave", () => {
    looking = false;
  });
  renderer.domElement.addEventListener("contextmenu", (event) => event.preventDefault());

  transform.addEventListener("objectChange", () => {
    const entry = selected ? entries.get(selected) : null;
    if (!entry) return;
    const p = entry.pivot.position;
    const r = entry.pivot.rotation;
    hooks.onPreview(entry.id, [p.x, p.y, p.z], [r.x, r.y, r.z]);
  });

  transform.addEventListener("dragging-changed", (event) => {
    orbit.enabled = !event.value && nav === "orbit";
    if (event.value) {
      gizmoUsed = true;
      return;
    }
    const entry = selected ? entries.get(selected) : null;
    if (!entry || !entry.id || entry.status === "recipe") return;
    const p = entry.pivot.position;
    const r = entry.pivot.rotation;
    hooks.onTransform(entry.id, [p.x, p.y, p.z], [r.x, r.y, r.z]);
  });

  window.addEventListener("keydown", (event) => {
    if (isTyping(event)) return;
    keys.add(event.key.toLowerCase());
  });
  window.addEventListener("keyup", (event) => keys.delete(event.key.toLowerCase()));
  window.addEventListener("blur", () => keys.clear());

  function updateFly(dt) {
    if (nav !== "fly") return;
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
    if (move.lengthSq() === 0) return;
    move.normalize().multiplyScalar(speed);
    camera.position.add(move);
    orbit.target.add(move);
  }

  function resize() {
    const width = host.clientWidth || 1;
    const height = host.clientHeight || 1;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    labels.setSize(width, height);
  }

  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  let running = true;
  function tick() {
    if (!running) return;
    const dt = Math.min(clock.getDelta(), 0.05);
    updateFly(dt);
    if (nav === "orbit") orbit.update();
    const entry = selected ? entries.get(selected) : null;
    if (entry) {
      const distance = camera.position.distanceTo(entry.pivot.position);
      transform.size = THREE.MathUtils.clamp(distance / 14, 0.55, 2.4);
    }
    renderer.render(scene, camera);
    labels.render(scene, camera);
    requestAnimationFrame(tick);
  }
  tick();

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
    destroy() {
      running = false;
      generation += 1;
      observer.disconnect();
      clearActors();
      clearTerrain();
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
