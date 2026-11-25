/**
 * Tear down one map before the next load or a reload.
 *
 * The bag is the map. After unload it no longer points at that map's
 * geometries, batches, materials, textures, render targets, wasm pack,
 * workers, or listeners. Objects in `keep` are shared with the viewport
 * and stay. A stale generation token no longer matches, so a fetch that
 * finishes late is dropped.
 */

const TEXTURE_KEYS = [
  "map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap", "alphaMap",
  "bumpMap", "displacementMap", "envMap", "lightMap", "specularMap", "anisotropyMap",
  "clearcoatMap", "clearcoatNormalMap", "clearcoatRoughnessMap", "sheenRoughnessMap",
  "sheenColorMap", "specularIntensityMap", "specularColorMap", "iridescenceMap",
  "iridescenceThicknessMap", "transmissionMap", "thicknessMap",
];

function call(fn) {
  try {
    fn();
  } catch {
    /* already disposed or freed */
  }
}

export function sceneResultCurrent(scene, token) {
  return Boolean(scene) && token === scene.generation && scene.controller && scene.controller.signal.aborted !== true;
}

export function unloadSceneResources(scene) {
  if (!scene) return scene;
  const keep = scene.keep || new Set();
  const disposed = new Set();
  scene.generation = (scene.generation || 0) + 1;
  if (scene.controller) call(() => scene.controller.abort());
  scene.controller = new AbortController();

  for (const worker of scene.workers || []) call(() => worker.terminate?.());
  scene.workers = [];
  for (const off of scene.listeners || []) call(() => off());
  scene.listeners = [];

  function once(object, run) {
    if (!object || keep.has(object) || disposed.has(object)) return false;
    if (object.userData?.forgeViewportShared) return false;
    disposed.add(object);
    run(object);
    return true;
  }

  function disposeTexture(texture) {
    once(texture, (item) => {
      if (item.userData?.forgeSharedLive) return;
      call(() => item.dispose?.());
      const image = item.image;
      if (image && typeof image.close === "function") call(() => image.close());
    });
  }

  function disposeMaterial(material) {
    once(material, (item) => {
      for (const key of TEXTURE_KEYS) disposeTexture(item[key]);
      const extra = item.userData || {};
      for (const key of ["mossMap", "orm1Map", "normal1Map", "blendMap"]) disposeTexture(extra[key]);
      const lit = extra.forgeLit;
      if (lit) {
        for (const key of TEXTURE_KEYS) disposeTexture(lit[key]);
        disposeTexture(lit.blendMap);
      }
      call(() => item.dispose?.());
    });
  }

  function disposeGeometry(geometry) {
    once(geometry, (item) => {
      if (item.userData) {
        item.userData.forgeBatch = null;
        call(() => item.disposeBoundsTree?.());
        item.boundsTree = undefined;
      }
      call(() => item.dispose?.());
    });
  }

  for (const geometry of scene.linkedGeometries || []) {
    if (geometry?.userData) geometry.userData.forgeBatch = null;
  }
  scene.linkedGeometries = [];

  for (const mesh of scene.meshes || []) {
    once(mesh, (item) => {
      call(() => item.removeFromParent?.());
      const linked = item.userData?.forgeLinkedGeometries || [];
      for (const geometry of linked) {
        if (geometry?.userData?.forgeBatch?.mesh === item) geometry.userData.forgeBatch = null;
      }
      if (item.userData) item.userData.forgeLinkedGeometries = [];
      disposeTexture(item.userData?.forgePickTexture);
      if (item.userData) item.userData.forgePickTexture = null;
      const instanced = item.instanceMatrix;
      if (instanced && instanced !== item.geometry) disposeGeometry(instanced);
      call(() => item.dispose?.());
      disposeGeometry(item.geometry);
      const materials = Array.isArray(item.material) ? item.material : [item.material];
      for (const material of materials) disposeMaterial(material);
    });
  }
  scene.meshes = [];

  for (const geometry of scene.geometries || []) disposeGeometry(geometry);
  scene.geometries = [];
  for (const material of scene.materials || []) disposeMaterial(material);
  scene.materials = [];
  for (const texture of scene.textures || []) disposeTexture(texture);
  scene.textures = [];
  for (const target of scene.targets || []) {
    once(target, (item) => {
      disposeTexture(item.texture);
      disposeTexture(item.depthTexture);
      call(() => item.dispose?.());
    });
  }
  scene.targets = [];

  for (const root of scene.objects || []) {
    const nodes = [];
    if (typeof root.traverse === "function") root.traverse((child) => nodes.push(child));
    else nodes.push(root);
    call(() => root.removeFromParent?.());
    for (const node of nodes) {
      disposeGeometry(node.geometry);
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      for (const material of materials) disposeMaterial(material);
      disposeTexture(node.userData?.forgePickTexture);
      if (node.userData) node.userData.forgePickTexture = null;
    }
  }
  scene.objects = [];

  if (scene.surface) {
    call(() => scene.surface.free?.());
    scene.surface = null;
  }
  call(() => scene.picker?.reset?.());
  call(() => scene.gizmo?.detach?.());
  if (scene.selection) scene.selection.current = null;
  for (const map of scene.maps || []) call(() => map.clear?.());
  for (const list of scene.lists || []) {
    if (Array.isArray(list)) list.length = 0;
  }
  if (scene.overlays) {
    for (const node of scene.overlays) call(() => node.remove?.());
    scene.overlays.length = 0;
  }
  return scene;
}
