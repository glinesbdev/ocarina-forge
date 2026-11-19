import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";

export const CATEGORY_COLOR = {
  npc: 0xe0b15a,
  enemy: 0xe15b45,
  object: 0x3dbeb6,
  land: 0x7dae4a,
  effect: 0xc58be0,
  animal: 0xe08a3c,
  system: 0x8aa4c0,
  other: 0xd7dccf,
};

/**
 * Boxes stand in for actors. A later stage can swap `entry.visual` for a
 * cached glTF keyed by gyaml. TransformControls stays attached to `entry.pivot`.
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
    if (entry.label) entry.label.element.remove();
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
    const marked = entry.status === "edited" || entry.status === "added";
    entry.edgeMaterial.color.set(on || marked ? 0xf2d7a2 : 0x101612);
    entry.edges.visible = on || marked || entry.status === "hidden";
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
    actors.forEach((actor, index) => {
      const key = actor.id || `locked-${index}`;
      const color = CATEGORY_COLOR[actor.category] || CATEGORY_COLOR.other;
      const material = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.7,
        metalness: 0.05,
      });
      if (actor.status === "hidden") {
        material.transparent = true;
        material.opacity = 0.3;
        material.depthWrite = false;
      }
      const mesh = new THREE.Mesh(boxGeometry, material);
      const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x101612 });
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
      pivot.visible = actor.status !== "hidden" || showHidden;
      scene.add(pivot);
      entries.set(key, {
        key,
        id: actor.id,
        status: actor.status,
        color,
        pivot,
        material,
        edges,
        edgeMaterial,
        label: null,
        labelText: actor.name || actor.gyaml,
      });
    });
    if (selected && !entries.has(selected)) selected = null;
    for (const entry of entries.values()) paint(entry);
    attach();
    if (options.frame) {
      const visible = [...entries.values()].filter((entry) => entry.pivot.visible).map((entry) => entry.pivot);
      frameObjects(visible.length ? visible : [...entries.values()].map((entry) => entry.pivot));
      rebuildGrid();
    }
    syncLabels();
  }

  function attach() {
    const entry = selected ? entries.get(selected) : null;
    if (!entry || !entry.id || !entry.pivot.visible) {
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

  function setFlags({ hidden, labels }) {
    if (hidden !== undefined) showHidden = hidden;
    if (labels !== undefined) showLabels = labels;
    for (const entry of entries.values()) {
      if (entry.status === "hidden") entry.pivot.visible = showHidden;
    }
    attach();
    syncLabels();
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

  function pick(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects([...entries.values()].map((entry) => entry.pivot), true);
    const hit = hits.find((item) => item.object.visible);
    hooks.onSelect(hit ? idOf(hit.object) : null);
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
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects([...entries.values()].map((entry) => entry.pivot), true);
    const next = hits.length ? idOf(hits[0].object) : null;
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
    if (!entry || !entry.id) return;
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
    frame,
    spawnPoint,
    resize,
    destroy() {
      running = false;
      observer.disconnect();
      clearActors();
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
