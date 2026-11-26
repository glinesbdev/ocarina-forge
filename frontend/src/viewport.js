import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import {
  createGpuTextureLoader,
  gpuTexturesEnabled,
  releaseTextureCpu,
  repairCompressedTexture,
  textureSupport,
} from "./gpu-texture.js";
import { loadForgeImageSource } from "./gltf-image-source.js";
import { createSharedImageCache } from "./shared-image-cache.js";
import {
  RESIDENT_BYTE_HARD,
  RESIDENT_BYTE_SOFT,
  RESIDENT_BYTE_TARGET,
  RESIDENT_HEAP_HARD,
  RESIDENT_MODEL_SOFT_CAP,
  RESIDENT_UNLOAD_COOLDOWN_FRAMES,
  cameraNeedsPriorityUpdate,
  effectiveDistance,
  isWorsePriority,
  overResidentHardCap,
  overResidentSoftCap,
  planEvictions,
  uniqueResidentNames,
} from "./resident-budget.js";
import { budgetAssetBytes, collectMemoryReport, residentModelBytes } from "./memory-report.js";
import { cancelGeometryDecodes, geometryDecoder } from "./geometry-decoder.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { acceleratedRaycast, computeBoundsTree, disposeBoundsTree, MeshBVH } from "three-mesh-bvh";

import { boundsGap, instanceLod, isForegroundModel, isTerrainProxy, modelLoadTier, modelLod, resolveLod, transformSphere } from "./cull.js";
import { isMarker } from "./problems.js";
import { canHoverFocus } from "./focus.js";
import { guardPointerCapture } from "./pointer-capture.js";
import { shouldReloadTerrain } from "./terrain.js";
import { ensureWaterUv, FISHING_POND_WATER, fishingPondFish, fishingPondPreviewOffset, isPreviewWater } from "./water.js";
import { compileObjectPipeline, createBackendRenderer, drawTotal, warmWebGLPrograms } from "./backend.js";
import { frontendBuild, shortRevision } from "./build-info.js";
import { formatMemoryNote, formatStatusLabel, keepRendering } from "./frame.js";
import { buildCapture, countTextures, createProfileWindow, formatProfileHud, recommendNext } from "./frame-profile.js";
import { createFrameTimer } from "./gpu-timer.js";
import { actorLayer, planAssetFetches, wantsMesh } from "./layers.js";
import { applyTextureManifest, formatTextureReport, presentTextures, separateSharedMaterials } from "./texture-apply.js";
import { createTextureQueue, textureSlotLimit } from "./texture-stream.js";
import { actorLocked, actorShown, applyActorMatrix, batchKey, canGizmo, canPick, cloneMaterialPreservingTextures, dropMapWithoutUv, groupGeometryDraws, patchBatchedDraw, presentByDefault } from "./instance.js";
import { createGpuProfile, sampleGpuFrame } from "./gpu-profile.js";
import { observeWgsl } from "./wgsl-diagnostics.js";
import { chooseHit } from "./pick.js";
import {
  PICK_LAYER,
  PREVIEW_PICK_LAYER,
  TERRAIN_PICK_LAYER,
  createGpuPicker,
  groupsFromPick,
  ndcFromClient,
} from "./gpu-pick.js";
import { ensureGeometryCpu, geometryAttributeView, noteGeometryBatch, releaseGeometryCpu } from "./geometry-retain.js";
import { chooseSnap, GROUND_SNAP_RADIUS, isExplicitLift, isSameActor, isSnapSelfHit, nearestGroundSample, selectSnapHits, shouldApplyGroundReply, shouldSnapTranslate, snapExcludeId } from "./snap.js";
import { basisFromQuaternion, defaultGridSettings, effectiveRotationSpace, gridSnapEngaged, normalizeGridSettings, snapGridLayout, snapRotation, snapScale, snapTranslation } from "./grid-snap.js";
import { normalizeSpace, spaceForMode } from "./gizmo-space.js";
import { columnHits } from "./snap-surface.js";
import { commitTranslateDrag } from "./drag-commit.js";
import { disposeGroundSurface, groundSurfaceMeta, groundSurfaceReady, loadGroundSurface, sampleGroundSurface } from "./ground-surface.js";
import { unloadSceneResources } from "./scene-unload.js";
import { resolveTextureIndex } from "./problems.js";
import { isEffectGizmo, resolveLighting, tonemapName } from "./lighting.js";

THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;

let preparedRenderer = null;
const buildStamp = shortRevision(frontendBuild().revision);

export async function prepareViewportRenderer(options = "") {
  const settings = typeof options === "string" ? { note: options } : (options || {});
  const created = await createBackendRenderer(THREE, settings);
  preparedRenderer = {
    ...created,
    note: settings.note || created.note || "",
    reason: settings.reason || created.reason || "",
  };
  return preparedRenderer;
}

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
 * the scene. Clicks and hovers read a GPU id buffer. Volume edges stay on the
 * CPU, and Snap still samples the wasm ground.
 */
export async function createViewport(host, hooks) {
  const prepared = preparedRenderer;
  preparedRenderer = null;
  const created = prepared ?? await createBackendRenderer(THREE);
  const renderer = created.renderer;
  const backendName = created.backend;
  const backendNote = created.note;
  const backendReason = created.reason || "";
  let terrainNodes = null;
  let mapNodes = null;
  let blendNodes = null;
  let pickNodes = null;
  if (created.nodes) {
    terrainNodes = await import("./terrain-node.js");
    mapNodes = await import("./cutout-node.js");
    blendNodes = await import("./blend-node.js");
    pickNodes = await import("./gpu-pick-node.js");
  }
  if (renderer.backend && renderer.backend.isWebGPUBackend === true) patchBatchedDraw(renderer.backend);
  const shaderDiagnostics = observeWgsl(renderer.backend);
  const gpuProfile = renderer.backend && renderer.backend.isWebGPUBackend === true ? createGpuProfile(renderer) : null;
  const frameTimer = createFrameTimer(renderer);
  const profileWindow = createProfileWindow();
  let lastCpu = null;

  const scene = new THREE.Scene();
  const linkLines = new THREE.LineSegments(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ color: 0x8ec8ff, transparent: true, opacity: 0.85 }),
  );
  linkLines.frustumCulled = false;
  linkLines.raycast = () => {};
  linkLines.visible = false;
  scene.add(linkLines);
  scene.background = new THREE.Color(0x141b16);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 8000);
  camera.position.set(12, 9, 14);
  camera.rotation.order = "YXZ";
  const gpuPicker = createGpuPicker({
    THREE,
    renderer,
    scene,
    getCamera: () => camera,
    createNodeMaterial: pickNodes ? (args) => pickNodes.createNodePickMaterial(args) : null,
  });
  await gpuPicker.attachNodeMaterial();
  let pickFallbackWarned = false;
  let hoverFrame = 0;
  let hoverToken = 0;
  let hoverPending = null;
  let hoverScheduled = false;
  let pickChain = Promise.resolve();
  let orbitToken = 0;

  function enqueuePick(task) {
    const run = pickChain.then(task, task);
    pickChain = run.then(() => {}, () => {});
    return run;
  }

  function disposePickTexture(mesh) {
    const texture = mesh && mesh.userData && mesh.userData.forgePickTexture;
    if (texture && typeof texture.dispose === "function") texture.dispose();
    if (mesh && mesh.userData) mesh.userData.forgePickTexture = null;
  }

  function bindPick(object) {
    if (!object || object.userData.forgePickBound) return;
    object.userData.forgePickBound = true;
    const previous = object.onBeforeRender;
    object.onBeforeRender = function (rendererArg, sceneArg, cameraArg, geometry, material, ...rest) {
      const picking = Boolean(sceneArg.overrideMaterial && sceneArg.overrideMaterial.userData && sceneArg.overrideMaterial.userData.forgePick);
      if (picking) gpuPicker.prepareObject(object, object.material);
      if (typeof previous === "function") {
        previous.call(this, rendererArg, sceneArg, cameraArg, geometry, picking ? object.material : material, ...rest);
      }
    };
  }

  function markTerrainPick(root) {
    if (!root) return;
    root.traverse((child) => {
      if (!child.isMesh) return;
      child.layers.enable(TERRAIN_PICK_LAYER);
      child.userData.forgePickDepthOnly = 1;
      child.userData.forgePickKind = 0;
      child.userData.forgePickId = 0;
      bindPick(child);
    });
  }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = false;
  if (renderer.shadowMap && "autoUpdate" in renderer.shadowMap) renderer.shadowMap.autoUpdate = false;
  if (renderer.isWebGPURenderer && typeof renderer.onDeviceLost === "function") {
    const previousLost = renderer.onDeviceLost.bind(renderer);
    renderer.onDeviceLost = (info) => {
      previousLost(info);
      if (hooks.onDeviceLost) hooks.onDeviceLost("The WebGPU device was lost. Finish any pending edits, then open Forge with ?renderer=webgl to use WebGL2.");
    };
  }
  host.appendChild(renderer.domElement);
  const canvas = renderer.domElement;
  guardPointerCapture(canvas);
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

  const hemi = new THREE.HemisphereLight(0xd5e0d4, 0x4a4034, 0.45);
  hemi.name = "forge-hemi";
  const sun = new THREE.DirectionalLight(0xfff2d6, 2.6);
  sun.name = "forge-sun";
  sun.position.set(18, 36, 14);
  sun.castShadow = false;
  sun.shadow.mapSize.set(512, 512);
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 180;
  sun.shadow.camera.left = -40;
  sun.shadow.camera.right = 40;
  sun.shadow.camera.top = 40;
  sun.shadow.camera.bottom = -40;
  sun.shadow.bias = -0.0004;
  sun.shadow.autoUpdate = false;
  const ambient = new THREE.AmbientLight(0x8ea090, 0.08);
  ambient.name = "forge-ambient";
  scene.add(hemi);
  scene.add(sun);
  scene.add(ambient);
  const editorBackground = new THREE.Color(0x141b16);
  let showShadows = false;
  let showAo = true;
  let showEffectGizmos = true;
  let lightingState = null;
  let lightingSample = null;
  let showEditorDefaults = false;
  let lastLightingNote = "";
  const effectGizmoGeometry = new THREE.BufferGeometry();
  effectGizmoGeometry.setAttribute("position", new THREE.Float32BufferAttribute([
    -0.5, 0, 0, 0.5, 0, 0,
    0, -0.5, 0, 0, 0.5, 0,
    0, 0, -0.5, 0, 0, 0.5,
  ], 3));
  const effectGizmoMaterial = new THREE.LineBasicMaterial({
    color: CATEGORY_COLOR.effect,
    transparent: true,
    opacity: 0.95,
    depthWrite: false,
  });

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
  // Y the translate controls assigned for this move, before Snap replaces it.
  // The drag plane follows the pivot. Leaving the snapped height there makes
  // the next sample take X and Z from the view ray at that height.
  let dragPlaneY = null;
  // Unsnapped pose from the controls, and the pose where this drag began.
  // Grid snap writes over the pivot. The next sample has to see the raw pose
  // or the drag plane follows the snapped point and X/Z walk off the handle.
  let dragRaw = null;
  let dragOrigin = null;
  let gizmoSpace = "world";
  let ctrlSnap = false;
  let gridSettings = defaultGridSettings();
  let snapGrid = null;
  renderer.domElement.addEventListener("pointermove", () => {
    if (!transform.dragging) return;
    const entry = selected ? entries.get(selected) : null;
    if (!entry) return;
    restoreDragRaw(entry);
    if (dragPlaneY == null || transform.axis === "Y") return;
    entry.pivot.position.y = dragPlaneY;
    entry.pivot.updateMatrixWorld(true);
    transform._root.updateMatrixWorld(true);
  }, true);
  scene.add(transform.getHelper());
  transform.getHelper().traverse((child) => {
    child.layers.disable(PICK_LAYER);
    child.layers.disable(PREVIEW_PICK_LAYER);
    child.layers.disable(TERRAIN_PICK_LAYER);
  });

  const boxGeometry = new THREE.BoxGeometry(1, 1, 1);
  const edgesGeometry = new THREE.EdgesGeometry(boxGeometry);
  const entries = new Map();
  const batches = [];
  const parsedTemplates = new Map();
  const templateOrder = [];
  const recentSceneTemplates = new Map();
  const recentTerrain = new Map();
  const statusEl = document.createElement("div");
  statusEl.className = "view-status";
  host.appendChild(statusEl);
  const profileEl = document.createElement("div");
  profileEl.className = "view-profile";
  profileEl.hidden = true;
  const profileText = document.createElement("pre");
  const profileDump = document.createElement("button");
  profileDump.type = "button";
  profileDump.className = "ghost";
  profileDump.textContent = "Dump JSON";
  profileDump.title = "Copy a JSON capture of the recent frames and print it in the console";
  profileEl.append(profileText, profileDump);
  host.appendChild(profileEl);
  profileDump.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    publishProfile();
  });
  let showProfile = false;
  let lastRendering = false;
  let serverHead = "";
  let serverLines = [];
  let gpuPoll = 0;
  let naiveDraws = 0;
  let running = true;
  let frameHandle = 0;
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(geometryDecoder);
  const sharedImageCache = createSharedImageCache({ limit: 128 });
  const textureDetailLoaders = new Map();
  const textureDetailFailed = new Set();
  const textureDetailReady = new Map();
  const textureDetailPending = new Set();
  const detailParses = [];
  let detailCandidates = new Map();
  let nextDetailScan = 0;

  const sceneImageUris = new Set();
  function sharedImage(uri, load) {
    if (uri) sceneImageUris.add(uri);
    return sharedImageCache.get(uri, load);
  }

  function loadDetailImage(uri, imageLoader, deferred = false) {
    return new Promise((resolve, reject) => {
      const load = deferred && imageLoader.loadDeferred ? imageLoader.loadDeferred : imageLoader.load;
      load.call(imageLoader, uri, (image) => {
        resolve(imageLoader.isImageBitmapLoader ? new THREE.Texture(image) : image);
      }, undefined, reject);
    });
  }

  const ktx2Loader = new KTX2Loader().setTranscoderPath("/basis/");
  ktx2Loader.detectSupport(renderer);
  const gpuCaps = textureSupport(renderer.getContext(), renderer);
  const gl = renderer.getContext();
  let loadingGyaml = "";
  function reportGpuFallback(reason) {
    const name = loadingGyaml || "texture";
    if (hooks.onModelError) hooks.onModelError(name, `GPU texture fell back to the bitmap (${reason}).`);
  }
  const gpuTextureLoader = createGpuTextureLoader({
    THREE,
    gl,
    caps: gpuCaps,
    enabled: () => gpuTexturesEnabled(window.location.search, window.localStorage.getItem("ocarinaforge.gpuTextures")),
    onFallback: reportGpuFallback,
    scheduleDetailParse: (apply) => {
      detailParses.push(apply);
      requestRender();
    },
    // vkFormat 0 is Basis. three's transcoder is the retry for that file.
    basisParse: (buffer, onLoad, onError) => ktx2Loader.parse(buffer, onLoad, onError),
  });
  loader.setKTX2Loader(gpuTextureLoader);
  loader.register((parser) => ({
    name: "ForgeSharedModelImages",
    beforeRoot() {
      const load = parser.loadImageSource.bind(parser);
      parser.loadImageSource = (index, imageLoader) => loadForgeImageSource({
        parser,
        sourceIndex: index,
        imageLoader,
        gpuLoader: gpuTextureLoader,
        original: load,
        THREE,
        loadExternal: (sourceIndex, externalLoader, image) => {
          const uri = image.uri;
          const firstMip = image.extras?.forgeLowTexture;
          if (!firstMip?.startsWith("/api/model-textures/")) {
            return sharedImage(uri, () => load(sourceIndex, externalLoader)).then((texture) => texture.clone());
          }
          textureDetailLoaders.set(uri, externalLoader);
          return sharedImage(firstMip, () => loadDetailImage(firstMip, externalLoader))
            .then((texture) => {
              const reduced = texture.clone();
              reduced.userData.forgeFullTexture = uri;
              return reduced;
            })
            .catch(() => sharedImage(uri, () => load(sourceIndex, externalLoader)).then((texture) => texture.clone()));
        },
      });
    },
  }));
  const templateCache = new Map();
  const modelRequests = new Map();
  // The server pool (one core left free) does the converts. The page only
// parses this many glbs at once; GLTFLoader.parse is on the main thread.
let convertSlots = 8;
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
  const layerVisibility = {
    models: true,
    terrain: true,
    water: true,
    collision: true,
    behavior: true,
    effects: true,
    decorations: true,
    lights: true,
  };
  let statLayersOff = [];
  let textureAllow = true;
  let texturesShown = true;
  let texturesShownArmed = false;
  let textureGeneration = 0;
  let textureActive = 0;
  let statTexturesDone = 0;
  let statTexturesTotal = 0;
  const textureDocs = new Map();
  const textureQueue = createTextureQueue();
  let linkPairs = [];
  let showSub = false;
  let drawDistance = 0;
  let showTerrainDebug = false;
  let showBlendMask = false;
  let generation = 0;
  let sceneAbort = new AbortController();
  const terrainWorkers = new Set();
  const sceneListeners = [];
  let rebuildTimer = 0;
  let loadQueue = null;
  let modelLoads = null;
  let pendingLoads = 0;
  const loadDelta = new THREE.Vector3();
  const loadForward = new THREE.Vector3();
  let terrainRoot = null;
  let scenePreview = null;
  let modelStage = null;
  const foregroundModelNames = new Set();
  let modelPriority = new Map();
  const lastLoadDirection = new THREE.Vector3();
  const priorityPos = new THREE.Vector3();
  const priorityDir = new THREE.Vector3();
  let priorityAnchored = false;
  let streamFrame = 0;
  let statEvictions = 0;
  let statReloads = 0;
  let peakHeapBytes = 0;
  const unloadAfter = new Map();
  const recentlyEvicted = new Set();
  let assetBudgetCache = null;
  let batchChain = Promise.resolve();
  const modelFrustum = new THREE.Frustum();
  const modelProjection = new THREE.Matrix4();
  const modelBounds = new THREE.Sphere();
  let streamInteractive = false;
  let sceneStreamStats = null;
  let snapOn = false;
  // Axis captured for the current gizmo drag. pointerUp clears transform.axis
  // after dragging-changed, and a ground reply can resolve even later.
  let dragAxis = null;
  // An explicit Y lift must ignore a ground reply for that same column.
  let verticalHold = false;
  // Collision (#10) and decorations (#11) register here. They receive the same
  // pose the gizmo, the visible model, and the stored transform just used.
  const actorTransformFollowers = [];
  let terrainBanc = null;
  const groundHits = [];
  let groundWanted = null;
  let groundPump = null;
  const subTerrainMaterial = new THREE.MeshBasicMaterial({
    color: 0xa5c9bd, wireframe: true, transparent: true, opacity: 0.28, depthWrite: false,
  });
  subTerrainMaterial.userData.forgeViewportShared = true;
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

  let textureCpuReleaseDirty = true;
  let textureCpuReleaseInFlight = false;
  const textureCpuReleaseFrames = new Map();

  function collectLiveTextures() {
    const textures = new Set();
    for (const batch of batches) collectMaterialTextures(batch.mesh.material, textures);
    for (const parsed of parsedTemplates.values()) {
      for (const part of parsed.parts || []) collectMaterialTextures(part.material, textures);
    }
    if (terrainRoot) {
      terrainRoot.traverse((child) => {
        const materials = child.material ? (Array.isArray(child.material) ? child.material : [child.material]) : [];
        for (const material of materials) collectMaterialTextures(material, textures);
      });
    }
    return textures;
  }

  function sourceKey(texture) {
    return (texture.source && (texture.source.uuid || texture.source.id)) || texture.uuid || texture.id;
  }

  function flushTextureCpuRelease() {
    if (!textureCpuReleaseDirty || textureCpuReleaseInFlight) return;
    const live = [...collectLiveTextures()];
    const groups = new Map();
    for (const texture of live) {
      if (texture.userData?.forgeCpuReleased || texture.source?.userData?.forgeCpuReleased) continue;
      const key = sourceKey(texture);
      const group = groups.get(key) || [];
      group.push(texture);
      groups.set(key, group);
    }
    const ready = [];
    for (const [key, group] of groups) {
      if (group.some((texture) => texture.needsUpdate || texture.source?.needsUpdate)) {
        textureCpuReleaseFrames.delete(key);
        continue;
      }
      const frames = (textureCpuReleaseFrames.get(key) || 0) + 1;
      textureCpuReleaseFrames.set(key, frames);
      if (frames >= 3) ready.push(group[0]);
    }
    for (const key of [...textureCpuReleaseFrames.keys()]) {
      if (!groups.has(key)) textureCpuReleaseFrames.delete(key);
    }
    if (!ready.length) {
      textureCpuReleaseDirty = groups.size > 0;
      // Keep drawing a couple of extra frames so new uploads can retire.
      if ([...textureCpuReleaseFrames.values()].some((frames) => frames > 0 && frames < 3)) requestRender();
      return;
    }
    textureCpuReleaseInFlight = true;
    const device = renderer.backend && renderer.backend.device;
    const wait = device && device.queue && typeof device.queue.onSubmittedWorkDone === "function"
      ? device.queue.onSubmittedWorkDone()
      : Promise.resolve();
    void Promise.resolve(wait).catch(() => {}).then(() => {
      for (const texture of ready) {
        if (texture.needsUpdate || texture.source?.needsUpdate) continue;
        releaseTextureCpu(texture);
        textureCpuReleaseFrames.delete(sourceKey(texture));
      }
    }).finally(() => {
      textureCpuReleaseInFlight = false;
      textureCpuReleaseDirty = collectLiveTextures().size > 0
        && [...collectLiveTextures()].some((texture) => !texture.userData?.forgeCpuReleased && !texture.source?.userData?.forgeCpuReleased);
      if (textureCpuReleaseDirty) requestRender();
    });
  }

  const TEXTURE_KEYS = [
    "map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap", "alphaMap",
    "bumpMap", "displacementMap", "envMap", "lightMap", "specularMap", "anisotropyMap",
    "clearcoatMap", "clearcoatNormalMap", "clearcoatRoughnessMap", "sheenRoughnessMap",
    "sheenColorMap", "specularIntensityMap", "specularColorMap", "iridescenceMap",
    "iridescenceThicknessMap", "transmissionMap", "thicknessMap",
  ];

  function disposeCachedTexture(texture) {
    if (!texture || texture.userData?.forgeSharedLive) return;
    const image = texture.image;
    try {
      texture.dispose();
    } catch {
      /* already disposed */
    }
    if (image && typeof image.close === "function") {
      try {
        image.close();
      } catch {
        /* ImageBitmap already closed */
      }
    }
  }

  function collectMaterialTextures(material, into) {
    if (!material) return;
    for (const key of TEXTURE_KEYS) {
      if (material[key]) into.add(material[key]);
    }
    const extra = material.userData || {};
    for (const key of ["mossMap", "orm1Map", "normal1Map", "blendMap"]) {
      if (extra[key]) into.add(extra[key]);
    }
    const lit = extra.forgeLit;
    if (lit) {
      for (const key of TEXTURE_KEYS) {
        if (lit[key]) into.add(lit[key]);
      }
      if (lit.blendMap) into.add(lit.blendMap);
    }
  }

  function disposeExtractedTemplateGeometry(template) {
    if (!template) return;
    template.traverse((child) => {
      if (!child.geometry) return;
      disposeGeometry(child.geometry);
      child.geometry = undefined;
    });
  }

  function disposeGeometry(geometry) {
    if (!geometry) return;
    if (geometry.boundsTree) {
      try {
        if (typeof geometry.disposeBoundsTree === "function") geometry.disposeBoundsTree();
        else disposeBoundsTree.call(geometry);
      } catch {
        geometry.boundsTree = undefined;
      }
    }
    geometry.dispose();
  }

  function disposeMaterial(material, { textures = false, keepTextures = null } = {}) {
    if (!material || material.userData?.forgeViewportShared) return;
    if (textures) {
      const maps = new Set();
      collectMaterialTextures(material, maps);
      for (const texture of maps) {
        if (keepTextures && keepTextures.has(texture)) continue;
        disposeCachedTexture(texture);
      }
    }
    material.dispose();
  }

  function disposeObject3D(root, { textures = false, keepTextures = null } = {}) {
    if (!root) return;
    root.traverse?.((child) => {
      if (child.geometry) disposeGeometry(child.geometry);
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) disposeMaterial(material, { textures, keepTextures });
    });
    root.removeFromParent?.();
  }

  function liveTemplateTextures(urls) {
    const textures = new Set();
    for (const url of urls) {
      const parsed = parsedTemplates.get(url);
      if (!parsed?.parts) continue;
      for (const part of parsed.parts) collectMaterialTextures(part.material, textures);
      if (parsed.source) {
        parsed.source.traverse?.((child) => {
          const materials = Array.isArray(child.material) ? child.material : [child.material];
          for (const material of materials) collectMaterialTextures(material, textures);
        });
      }
    }
    return textures;
  }

  function disposeParsedTemplate(parsed, keepTextures) {
    if (!parsed) return;
    const parts = parsed.parts || [];
    for (const part of parts) {
      disposeGeometry(part.geometry);
      disposeMaterial(part.material, { textures: true, keepTextures });
    }
    if (parsed.source) {
      parsed.source.traverse?.((child) => {
        if (child.geometry) disposeGeometry(child.geometry);
      });
    }
  }

  function disposeTerrainObject(root) {
    disposeObject3D(root, { textures: true });
  }

  function clearTerrain() {
    terrainToken += 1;
    showTerrain(null);
    for (const root of recentTerrain.values()) disposeTerrainObject(root);
    recentTerrain.clear();
    terrainBanc = null;
    groundHits.length = 0;
    groundWanted = null;
    disposeGroundSurface();
  }

  function dropTerrainMeshes() {
    showTerrain(null);
    groundHits.length = 0;
    groundWanted = null;
    disposeGroundSurface();
  }

  function dropTerrain() {
    terrainToken += 1;
    dropTerrainMeshes();
  }

  function ensureTerrain() {
    if (!terrainBanc) return;
    const next = ++terrainToken;
    void loadTerrain(next, terrainBanc);
  }

  function drawLinks() {
    const positions = [];
    const hidden = layerVisibility.behavior === false;
    if (!hidden) {
      for (const pair of linkPairs) {
        const left = entries.get(pair[0]);
        const right = entries.get(pair[1]);
        if (!left || !right || !left.pivot.visible || !right.pivot.visible) continue;
        positions.push(left.pivot.position.x, left.pivot.position.y, left.pivot.position.z);
        positions.push(right.pivot.position.x, right.pivot.position.y, right.pivot.position.z);
      }
    }
    linkLines.geometry.dispose();
    linkLines.geometry = new THREE.BufferGeometry();
    if (!positions.length) {
      linkLines.visible = false;
      return;
    }
    linkLines.geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    linkLines.visible = true;
  }

  function setLinks(pairs) {
    linkPairs = Array.isArray(pairs) ? pairs : [];
    drawLinks();
    requestRender();
  }

  function setLoadState(load) {
    statLayersOff = (load && load.off) || [];
    paintFrame(lastRendering, false);
  }

  function refreshTextureMaterial(material) {
    if (!material) return;
    const map = material.map && material.map.isTexture === true ? material.map : null;
    if (material.map && !map) material.map = null;
    if (map) {
      map.colorSpace = THREE.SRGBColorSpace;
      map.wrapS = THREE.RepeatWrapping;
      map.wrapT = THREE.RepeatWrapping;
      const maxAniso = renderer.capabilities && renderer.capabilities.getMaxAnisotropy
        ? renderer.capabilities.getMaxAnisotropy()
        : 4;
      map.anisotropy = Math.min(4, maxAniso);
      if (map.isCompressedTexture) repairCompressedTexture(map);
      else map.generateMipmaps = true;
      map.needsUpdate = true;
    }
    for (const key of ["normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap"]) {
      const tex = material[key];
      if (tex && tex.isTexture === true) asDataMap(tex);
      else if (tex) material[key] = null;
    }
    // The cutout node captures the map it saw. A later assign has to build it
    // again, and only from a real three.js texture. That recompiles the
    // material. The mesh stays.
    if (mapNodes) {
      material.colorNode = null;
      material.alphaTestNode = null;
      if (material.userData) material.userData.cutoutNode = false;
      if (material.alphaTest > 0 && map) mapNodes.attachCutout(material);
    }
    material.needsUpdate = true;
  }

  function finishTextureEdit(parts) {
    const seen = new Set();
    for (const part of parts || []) {
      if (!part.material || seen.has(part.material)) continue;
      seen.add(part.material);
      refreshTextureMaterial(part.material);
    }
  }

  function loadOwnedTexture(url) {
    return sharedImage(url, () => new Promise((resolve, reject) => {
      gpuTextureLoader.load(url, (texture) => {
        if (texture && texture.isTexture === true) resolve(texture);
        else reject(new Error("decoded image is not a three.js texture"));
      }, undefined, reject);
    })).then((texture) => {
      if (!texture || texture.isTexture !== true || typeof texture.clone !== "function") return null;
      const copy = texture.clone();
      if (!copy || copy.isTexture !== true) return null;
      copy.needsUpdate = true;
      return copy;
    });
  }

  function textureManifest(gyaml) {
    const plan = planAssetFetches({
      wantGeometry: false,
      geometryResident: true,
      wantTextures: true,
      texturesResident: textureDocs.has(gyaml),
    });
    if (!plan.textures) return textureDocs.get(gyaml);
    const pending = typeof hooks.fetchTextures === "function"
      ? Promise.resolve(hooks.fetchTextures(gyaml, sceneAbort.signal)).catch((error) => ({
        materials: {},
        status: "missing",
        error: `file not found: texture request failed (${error && error.message ? error.message : error})`,
      }))
      : Promise.resolve({ materials: {} });
    textureDocs.set(gyaml, pending);
    return pending;
  }

  function textureRank(gyaml) {
    let distance = Infinity;
    let visible = false;
    for (const entry of entries.values()) {
      if (entry.gyaml !== gyaml || !entry.pivot) continue;
      const gap = entry.pivot.position.distanceToSquared(camera.position);
      if (gap < distance) distance = gap;
      if (entry.pivot.visible !== false) visible = true;
    }
    return { distance, visible };
  }

  function publishTextureProgress() {
    const progress = textureQueue.progress();
    const done = progress ? progress.done : 0;
    const total = progress ? progress.total : 0;
    if (done === statTexturesDone && total === statTexturesTotal) return;
    statTexturesDone = done;
    statTexturesTotal = total;
    paintFrame(lastRendering, false);
  }

  function pumpTextures() {
    if (!textureAllow) return;
    textureQueue.rank((job) => textureRank(job.gyaml));
    const room = textureSlotLimit({ geometryBusy: statLoading > 0 || statQueued > 0 }) - textureActive;
    if (room <= 0) return;
    const generation = textureGeneration;
    for (const job of textureQueue.take(room)) {
      textureActive += 1;
      void paintActorTextures(job.gyaml, job.url, generation).catch(() => {}).finally(() => {
        textureActive = Math.max(0, textureActive - 1);
        if (generation !== textureGeneration) return;
        textureQueue.finish(job.gyaml);
        publishTextureProgress();
        pumpTextures();
      });
    }
    publishTextureProgress();
  }

  function enqueueTexture(gyaml, url) {
    if (!textureAllow || !gyaml || !url) return;
    const rank = textureRank(gyaml);
    if (!textureQueue.note({ gyaml, url, distance: rank.distance, visible: rank.visible })) return;
    pumpTextures();
  }

  function enqueueResidentTextures() {
    const seen = new Set();
    for (const entry of entries.values()) {
      if (!entry.gyaml || !entry.templateUrl || !entry.parts || seen.has(entry.gyaml)) continue;
      seen.add(entry.gyaml);
      enqueueTexture(entry.gyaml, entry.templateUrl);
    }
  }

  function refreshBlendMaps(parts) {
    for (const part of parts || []) {
      const material = part && part.material;
      const blend = material && material.userData && material.userData.blend;
      if (!material || !blend) continue;
      if (material.userData.blendMap && material.userData.blendMapUniform) {
        material.userData.blendMapUniform.value = material.userData.blendMap;
      }
      const geometry = part.geometry;
      const hasMask = geometry && geometry.getAttribute && geometry.getAttribute("blendMask");
      if (hasMask && !material.userData.blendShader) installBlendShader(material);
      if (blendNodes && hasMask && material.userData.blendMap && material.map && !material.userData.blendNode) {
        blendNodes.attachBlend(material);
      }
    }
  }

  async function paintActorTextures(gyaml, url, generation = textureGeneration) {
    if (!url) return;
    const parsed = parsedTemplates.get(url);
    if (!parsed) return;
    const manifest = await textureManifest(gyaml);
    if (generation !== textureGeneration) return;
    if (!parsedTemplates.has(url)) return;
    let report;
    try {
      report = await applyTextureManifest(parsed.parts, manifest, loadOwnedTexture);
    } catch (error) {
      rememberTextureNote(gyaml, `decode error: ${error && error.message ? error.message : error}`);
      return;
    }
    rememberTextureNote(gyaml, formatTextureReport(report));
    refreshBlendMaps(parsed.parts);
    if (generation !== textureGeneration || !parsedTemplates.has(url)) return;
    presentTextures(parsed.parts, texturesShown);
    finishTextureEdit(parsed.parts);
    requestRender();
  }

  function presentResidentTextures() {
    const seen = new Set();
    for (const parsed of parsedTemplates.values()) {
      if (!parsed || seen.has(parsed)) continue;
      seen.add(parsed);
      presentTextures(parsed.parts, texturesShown);
      finishTextureEdit(parsed.parts);
    }
    requestRender();
  }

  function setTextureShown(on) {
    const next = Boolean(on);
    if (texturesShownArmed && next === texturesShown) return;
    texturesShownArmed = true;
    texturesShown = next;
    presentResidentTextures();
  }

  function setTextureLoad(on) {
    const next = Boolean(on);
    if (next === textureAllow) {
      if (next) pumpTextures();
      return;
    }
    textureAllow = next;
    textureQueue.setAllow(next);
    if (!next) {
      publishTextureProgress();
      return;
    }
    enqueueResidentTextures();
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
      if (batch.water) batch.mesh.visible = showWater && !scenePreview?.hidden.includes(batch.mesh);
    }
    for (const mesh of scenePreview?.waterMeshes ?? []) mesh.visible = showWater;
  }

  function clearActors(options = {}) {
    loadQueue = null;
    modelLoads?.dispose();
    modelLoads = null;
    cancelModelStage();
    clearScenePreview();
    foregroundModelNames.clear();
    detailCandidates.clear();
    for (const texture of textureDetailReady.values()) disposeCachedTexture(texture);
    textureDetailReady.clear();
    sceneStreamStats = null;
    nextDetailScan = 0;
    unloadAfter.clear();
    recentlyEvicted.clear();
    statEvictions = 0;
    statReloads = 0;
    peakHeapBytes = 0;
    streamFrame = 0;
    priorityAnchored = false;
    assetBudgetCache = null;
    transform.detach();
    clearSnapGrid();
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

  function memoryNote() {
    const gpu = renderer.info && renderer.info.memory;
    const heapBytes = (typeof performance !== "undefined" && performance.memory && performance.memory.usedJSHeapSize) || null;
    return formatMemoryNote({
      geometries: gpu ? gpu.geometries : null,
      textures: gpu ? gpu.textures : null,
      heapBytes,
    });
  }

  let memoryNotedFor = -1;
  function noteMemory(reason) {
    if (reason === "unload") memoryNotedFor = -1;
    const surface = groundSurfaceMeta();
    const wasm = surface && surface.memoryBytes ? ` · wasm ${Math.round(surface.memoryBytes / 1024)}KB` : "";
    console.info(`forge memory ${reason}: ${memoryNote()}${wasm}`);
  }

  function noteLoadedMemory() {
    if (memoryNotedFor === generation) return;
    memoryNotedFor = generation;
    noteMemory("load");
  }

  function unloadScene() {
    generation += 1;
    terrainToken += 1;
    textureGeneration += 1;
    const previousAbort = sceneAbort;
    sceneAbort = new AbortController();
    previousAbort.abort();
    cancelGeometryDecodes();
    for (const worker of terrainWorkers) worker.terminate();
    terrainWorkers.clear();
    loadQueue = null;
    modelLoads?.dispose();
    modelLoads = null;
    const meshes = [];
    if (modelStage) {
      for (const batch of modelStage.previous || []) if (batch.mesh) meshes.push(batch.mesh);
    }
    cancelModelStage();
    clearScenePreview();
    if (rebuildTimer) {
      clearTimeout(rebuildTimer);
      rebuildTimer = 0;
    }
    transform.detach();
    clearSnapGrid();
    selected = null;
    hovered = null;
    foregroundModelNames.clear();
    detailCandidates.clear();
    detailParses.length = 0;

    for (const batch of batches) if (batch.mesh) meshes.push(batch.mesh);
    const geometries = [];
    const materials = [];
    const textures = [];
    const linkedGeometries = [];
    const objects = [];
    for (const parsed of parsedTemplates.values()) {
      for (const part of parsed.parts || []) {
        if (part.geometry) {
          geometries.push(part.geometry);
          linkedGeometries.push(part.geometry);
        }
        if (part.material) materials.push(part.material);
      }
      if (parsed.source) objects.push(parsed.source);
    }
    for (const root of recentTerrain.values()) objects.push(root);
    if (terrainRoot && ![...recentTerrain.values()].includes(terrainRoot)) objects.push(terrainRoot);
    for (const texture of textureDetailReady.values()) textures.push(texture);
    textureDetailReady.clear();
    textureDetailPending.clear();
    textureDetailFailed.clear();

    const actors = new Map(entries);
    for (const entry of entries.values()) disposeEntry(entry);
    entries.clear();
    batches.length = 0;
    parsedTemplates.clear();
    templateOrder.length = 0;
    templateCache.clear();
    recentSceneTemplates.clear();
    recentTerrain.clear();
    terrainRoot = null;
    terrainBanc = null;
    clearGround();
    groundWanted = null;
    linkPairs = [];
    textureQueue.clear();
    metaCache.clear();
    modelRequests.clear();
    textureDocs.clear();
    sceneStreamStats = null;
    serverLines = [];
    serverHead = "";
    const imageUris = new Set(sceneImageUris);
    sceneImageUris.clear();
    const evicted = sharedImageCache.evict((uri) => imageUris.has(uri));
    for (const pending of evicted) {
      Promise.resolve(pending).then((texture) => {
        if (!texture || typeof texture.dispose !== "function") return;
        try {
          texture.dispose();
        } catch {
          /* already disposed */
        }
        if (texture.image && typeof texture.image.close === "function") {
          try {
            texture.image.close();
          } catch {
            /* already closed */
          }
        }
      }).catch(() => {});
    }

    const surface = groundSurfaceReady() ? { free: disposeGroundSurface } : null;
    unloadSceneResources({
      generation,
      controller: previousAbort,
      keep: new Set([boxGeometry, edgesGeometry, effectGizmoGeometry, effectGizmoMaterial, subTerrainMaterial]),
      meshes,
      geometries,
      materials,
      textures,
      objects,
      linkedGeometries,
      maps: [actors],
      lists: [groundHits],
      listeners: sceneListeners.splice(0),
      workers: [],
      surface,
      picker: gpuPicker,
      gizmo: { detach() { transform.detach(); } },
      selection: { current: selected },
    });
    // The helper aborts `previousAbort` again and allocates its own controller.
    // The live signal is the one created above, so a new load is not aborted.
    disposeGroundSurface();
    drawLinks();
    noteMemory("unload");
    if (hooks.onUnload) hooks.onUnload();
    requestRender();
  }

  function helperVisible(entry) {
    if (!entry.pivot.visible) return false;
    if (entry.key === selected || entry.key === hovered) return true;
    if (scenePreview?.root.parent) return false;
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
    if (entry.effectGizmo) {
      const scale = entry.pivot.scale;
      entry.effectGizmo.scale.set(
        0.75 / (Math.abs(scale.x) || 1),
        0.75 / (Math.abs(scale.y) || 1),
        0.75 / (Math.abs(scale.z) || 1),
      );
      entry.effectGizmo.visible = showEffectGizmos && entry.pivot.visible;
    }
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
    let effectGizmo = null;
    if (isEffectGizmo(actor)) {
      const marker = new THREE.LineSegments(effectGizmoGeometry, effectGizmoMaterial);
      marker.userData.forgeEffectGizmo = true;
      marker.raycast = () => {};
      pivot.add(marker);
      effectGizmo = marker;
    }
    const gate = {
      status: actor.status,
      present: presentByDefault(actor.params),
      category: actor.category,
      volume,
      layer: actor.layer || "",
      layersVisible: layerVisibility,
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
      present: gate.present,
      volume,
      region,
      companion: Boolean(actor.companion),
      layer: actor.layer || "",
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
      effectGizmo,
    };
    paintLabel(entry);
    if (!volume) {
      mesh.layers.enable(PICK_LAYER);
      mesh.userData.forgePickKind = 0;
      mesh.userData.forgePickId = entry.pickable && !entry.locked ? gpuPicker.idFor(key) : 0;
      bindPick(mesh);
    }
    entries.set(key, entry);
    return entry;
  }

  function setActors(actors, options = {}) {
    const reloadTerrain = shouldReloadTerrain(terrainBanc, options.banc);
    if (reloadTerrain && terrainBanc) rememberSceneTemplates(terrainBanc);
    clearActors({ keepBatches: !reloadTerrain });
    // Actor edits keep the current terrain. On a map switch, detach the
    // previous terrain before the camera and actor placements change.
    if (reloadTerrain) {
      showTerrain(null);
      pruneTemplates();
    }
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
    performance.setResourceTimingBufferSize?.(4096);
    sceneStreamStats = { start: performance.now(), firstDetail: null, slowFrames: 0, maxFrameMs: 0, compileMs: 0, maxCompileMs: 0, compileBatches: 0, editBytes: null };
    statEvictions = 0;
    statReloads = 0;
    peakHeapBytes = 0;
    unloadAfter.clear();
    recentlyEvicted.clear();
    modelStage = { previous: batches.slice(), pending: new Map(), timer: 0, done: false, failed: false };
    const previewReady = options.previewUrl ? loadScenePreview(options.previewUrl, reloadTerrain) : null;
    loadModels(token, actors.filter((actor) => wantsMesh(actor)), previewReady);
    if (reloadTerrain) {
      const banc = options.banc;
      const next = ++terrainToken;
      terrainBanc = banc;
      if (options.terrain === false) dropTerrainMeshes();
      else void loadTerrain(next, banc);
    }
  }

  function shadeScenePreview(asset) {
    const originals = new Set();
    const materials = new Map();
    for (const group of asset.children) {
      const name = group.userData.forgePreview || "";
      group.traverse((part) => {
        if (!part.isMesh) return;
        const original = part.material;
        originals.add(original);
        const water = name !== "@terrain" && isPreviewWater(original, part.userData.name || part.name, name);
        part.userData.previewWater = water;
        const label = `${part.name} ${name}`;
        let color = original.color.getHex();
        if (color === 0xffffff) {
          if (name === "@terrain" || /grass|leaf|leaves|bush|moss/i.test(label)) color = 0x677b48;
          else if (/trunk|branch|tree|wood/i.test(label)) color = 0x866b50;
          else if (/stone|rock|cliff/i.test(label)) color = 0x848275;
          else if (/water|river|pond/i.test(label)) color = 0x527e84;
          else color = 0xa79d83;
        }
        const key = `${color}:${original.side}:${original.opacity}:${original.transparent}:${name === "@terrain"}:${water}`;
        if (!materials.has(key)) {
          const properties = { color, opacity: original.opacity };
          materials.set(key, water
            ? new THREE.MeshBasicMaterial({ ...properties, transparent: true, depthWrite: false, side: THREE.DoubleSide })
            : new THREE.MeshLambertMaterial({
                ...properties, flatShading: true, side: original.side,
                transparent: original.transparent, depthWrite: !original.transparent,
              }));
        }
        part.material = materials.get(key);
      });
    }
    for (const material of originals) material.dispose();
  }

  function disposeScenePreview(asset, root) {
    root.removeFromParent();
    for (const mesh of root.children) {
      disposePickTexture(mesh);
      mesh.dispose?.();
    }
    const geometries = new Set();
    const materials = new Set();
    asset.traverse((node) => {
      if (!node.isMesh) return;
      geometries.add(node.geometry);
      materials.add(node.material);
    });
    for (const geometry of geometries) geometry.dispose();
    for (const material of materials) material.dispose();
  }

  function clearScenePreview() {
    if (!scenePreview) return;
    const { controller, asset, root, hidden } = scenePreview;
    scenePreview = null;
    controller.abort();
    if (!asset) return;
    disposeScenePreview(asset, root);
    for (const object of hidden) object.visible = true;
    applyWater();
    for (const entry of entries.values()) syncGhost(entry);
    requestRender();
  }

  async function loadScenePreview(url, terrainPending) {
    const controller = new AbortController();
    const preview = {
      controller, terrainPending, terrain: null, asset: null, root: new THREE.Group(), hidden: [],
      models: new Map(), placements: new Map(), bounds: new Map(), waterMeshes: new Set(),
    };
    scenePreview = preview;
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`Scene preview request failed (${response.status}).`);
      const data = await response.arrayBuffer();
      if (scenePreview !== preview) return;
      const { scene: asset } = await loader.parseAsync(data, "");
      const root = preview.root;
      if (scenePreview !== preview) {
        disposeScenePreview(asset, root);
        return;
      }
      preview.asset = asset;
      shadeScenePreview(asset);
      asset.updateMatrixWorld(true);
      const actorGroups = new Map();
      for (const entry of entries.values()) {
        if (entry.region || entry.slots.length) continue;
        const group = actorGroups.get(entry.gyaml) || [];
        group.push(entry);
        actorGroups.set(entry.gyaml, group);
      }
      const matrix = new THREE.Matrix4();
      let deadline = performance.now() + 8;
      for (const group of asset.children) {
        if (performance.now() >= deadline) {
          await new Promise((resolve) => window.setTimeout(resolve, 0));
          if (scenePreview !== preview) return;
          deadline = performance.now() + 8;
        }
        const name = group.userData.forgePreview;
        if (name === "@terrain") {
          if (preview.terrainPending) {
            preview.terrain = group.clone(true);
            root.add(preview.terrain);
            if (terrainRoot?.visible) {
              terrainRoot.visible = false;
              preview.hidden.push(terrainRoot);
            }
          }
          continue;
        }
        const placements = actorGroups.get(name)?.filter((entry) =>
          entries.get(entry.key) === entry && entry.gyaml === name && !entry.slots.length);
        if (!placements?.length) continue;
        preview.bounds.set(name, new THREE.Box3().setFromObject(group).getBoundingSphere(new THREE.Sphere()));
        const cells = new Map();
        for (const entry of placements) {
          entry.pivot.updateWorldMatrix(true, false);
          const { x, y, z } = entry.pivot.position;
          const cell = [x, y, z].map((value) => Math.floor(value / 96)).join("/");
          if (!cells.has(cell)) cells.set(cell, []);
          cells.get(cell).push(entry);
        }
        const meshes = [];
        preview.models.set(name, meshes);
        group.traverse((part) => {
          if (!part.isMesh) return;
          const local = part.matrixWorld.clone();
          for (const placements of cells.values()) {
            // Spatial bounds let Three cull whole batches of distant copies.
            const mesh = new THREE.InstancedMesh(part.geometry, part.material, placements.length);
            const keys = placements.map((entry) => entry.key);
            mesh.userData.previewIds = keys.slice();
            mesh.userData.previewKeys = keys;
            mesh.userData.previewName = name;
            mesh.userData.previewSurface = part.userData.name || part.name;
            mesh.layers.enable(PREVIEW_PICK_LAYER);
            mesh.userData.forgePickKind = 2;
            gpuPicker.writeInstanceIds(mesh, placements.map((entry) => (
              canPick({ shown: entry.pivot.visible, locked: entry.locked }) ? gpuPicker.idFor(entry.key) : 0
            )));
            bindPick(mesh);
            if (part.userData.previewWater) {
              mesh.renderOrder = 2;
              mesh.visible = showWater;
              preview.waterMeshes.add(mesh);
            }
            placements.forEach((entry, index) => {
              displayedActorMatrix(entry, matrix).multiply(local);
              if (!entry.pivot.visible) matrix.scale(hiddenPreviewScale);
              mesh.setMatrixAt(index, matrix);
              const slots = preview.placements.get(entry.key) || [];
              slots.push({ mesh, index, local });
              preview.placements.set(entry.key, slots);
            });
            meshes.push(mesh);
            root.add(mesh);
          }
        });
      }
      if (!root.children.length) {
        clearScenePreview();
        return;
      }
      for (const { mesh, water } of modelStage?.previous || batches) {
        if (!mesh.visible && !water) continue;
        mesh.visible = false;
        preview.hidden.push(mesh);
      }
      scene.add(root);
      refreshPondFish();
      for (const entry of entries.values()) syncGhost(entry);
      requestRender();
      return preview.models.size > 0;
    } catch (error) {
      if (scenePreview === preview) {
        clearScenePreview();
        console.warn(error);
      }
    }
  }

  const hiddenPreviewScale = new THREE.Vector3(0, 0, 0);

  function syncPreviewPlacement(entry) {
    const slots = scenePreview?.placements.get(entry.key);
    if (!slots?.length || slots[0].mesh.userData.previewName !== entry.gyaml) return;
    entry.pivot.updateWorldMatrix(true, false);
    const matrix = new THREE.Matrix4();
    for (const { mesh, index, local } of slots) {
      displayedActorMatrix(entry, matrix).multiply(local);
      if (!entry.pivot.visible) matrix.scale(hiddenPreviewScale);
      mesh.userData.previewIds[index] = entry.key;
      mesh.setMatrixAt(index, matrix);
      mesh.boundingSphere = null;
      mesh.instanceMatrix.needsUpdate = true;
    }
  }

  function removePreviewPlacement(entry) {
    const slots = scenePreview?.placements.get(entry.key);
    if (!slots) return;
    const matrix = new THREE.Matrix4();
    for (const { mesh, index } of slots) {
      mesh.getMatrixAt(index, matrix);
      mesh.setMatrixAt(index, matrix.scale(hiddenPreviewScale));
      mesh.userData.previewIds[index] = null;
      mesh.instanceMatrix.needsUpdate = true;
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
    entry.present = presentByDefault(actor.params);
    entry.category = actor.category;
    entry.gyaml = actor.gyaml;
    entry.name = actor.name || "";
    entry.companionLabel = actor.companionLabel || "";
    entry.recipeNote = (actor.recipe && actor.recipe.note) || "";
    if (!entry.parts || !entry.parts.length) entry.volume = Boolean(actor.volume);
    if (actor.region !== undefined) entry.region = Boolean(actor.region);
    entry.companion = Boolean(actor.companion);
    entry.layer = actor.layer || entry.layer || "";
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
      present: entry.present,
      category: actor.category,
      volume: entry.volume,
      layer: entry.layer,
      layersVisible: layerVisibility,
      showHidden,
      categories,
      hideVolumes,
    });
    paintLabel(entry);
    paint(entry);
    syncInstanceMatrix(entry);
    if (entry.key === selected) updateSnapGrid(entry);
  }

  function patchActors(actors) {
    if (!Array.isArray(actors)) return false;
    const rows = [];
    for (const actor of actors) {
      const entry = actor && actor.id ? entries.get(actor.id) : null;
      if (!entry) return false;
      rows.push([entry, actor]);
    }
    for (const [entry, actor] of rows) {
      applyActorRecord(entry, actor);
      syncPreviewPlacement(entry);
    }
    if (rows.some(([entry]) => fishingPondFish(entry.gyaml) || entry.gyaml === FISHING_POND_WATER)) refreshPondFish();
    prioritizeLoad();
    syncBatchVisibility();
    syncPickIds();
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
    if (modelStage) stageModel(entry);
    else queueRebuild();
    return true;
  }

  function reconcileActors(actors) {
    if (!entries.size || !Array.isArray(actors)) return false;
    const wanted = new Map();
    actors.forEach((actor, index) => wanted.set(actor.id || `locked-${index}`, { actor, index }));
    for (const [key, entry] of [...entries.entries()]) {
      if (wanted.has(key)) continue;
      hideSlots(entry);
      removePreviewPlacement(entry);
      disposeEntry(entry);
      entries.delete(key);
    }
    const fresh = [];
    for (const [key, item] of wanted) {
      const entry = entries.get(key);
      if (entry && entry.gyaml === item.actor.gyaml) {
        applyActorRecord(entry, item.actor);
        syncPreviewPlacement(entry);
        continue;
      }
      if (entry) {
        hideSlots(entry);
        removePreviewPlacement(entry);
        disposeEntry(entry);
        entries.delete(key);
      }
      fresh.push({ entry: mountActor(item.actor, item.index), actor: item.actor });
    }
    const loading = [];
    for (const item of fresh) {
      paint(item.entry);
      syncPreviewPlacement(item.entry);
      if (!adoptModel(item.entry)) loading.push(item.actor);
    }
    modelLoads?.update(
      actors.filter((actor) => wantsMesh(actor)),
      loading.filter((actor) => wantsMesh(actor)),
    );
    refreshPondFish();
    if (selected && !entries.has(selected)) selected = null;
    syncBatchVisibility();
    syncLabels();
    drawLinks();
    attach();
    requestRender();
    return true;
  }

  function report(info, foregroundSettled) {
    if (hooks.onProgress) hooks.onProgress(info);
    if (foregroundSettled && hooks.onModels) hooks.onModels();
  }

  function sortModelNames(names, cachedFirst = false, updateForeground = true, backgroundName = null) {
    if (updateForeground) foregroundModelNames.clear();
    if (!names.length && !backgroundName) return;
    camera.updateMatrixWorld();
    camera.getWorldDirection(loadForward);
    modelProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    modelFrustum.setFromProjectionMatrix(modelProjection, camera.coordinateSystem);
    const priority = new Map(names.map((name) => [name, { tier: 5, distance: Infinity, behind: false }]));
    if (backgroundName) priority.set(backgroundName, { tier: 5, distance: Infinity, behind: false });
    const selectedModel = entries.get(selected)?.gyaml;
    for (const entry of entries.values()) {
      const best = priority.get(entry.gyaml);
      if (!best || !entry.pivot.visible || entry.region) continue;
      loadDelta.subVectors(entry.pivot.position, camera.position);
      const distance = loadDelta.length();
      const bounds = metaCache.get(entry.gyaml)?.bounds;
      const coarse = scenePreview?.bounds.get(entry.gyaml);
      if (bounds?.min && bounds.max) {
        const [minX, minY, minZ] = bounds.min;
        const [maxX, maxY, maxZ] = bounds.max;
        modelBounds.center.set((minX + maxX) * 0.5, (minY + maxY) * 0.5, (minZ + maxZ) * 0.5);
        modelBounds.radius = Math.max(2, Math.hypot(maxX - minX, maxY - minY, maxZ - minZ) * 0.5);
        entry.pivot.updateWorldMatrix(true, false);
        modelBounds.applyMatrix4(entry.pivot.matrixWorld);
      } else if (coarse && coarse.radius > 0) {
        entry.pivot.updateWorldMatrix(true, false);
        modelBounds.copy(coarse).applyMatrix4(entry.pivot.matrixWorld);
      } else {
        modelBounds.center.copy(entry.pivot.position);
        modelBounds.radius = 4;
      }
      const gap = Math.max(0, modelBounds.center.distanceTo(camera.position) - modelBounds.radius);
      const tier = modelLoadTier({
        selected: entry.gyaml === selectedModel,
        inFrustum: modelFrustum.intersectsSphere(modelBounds),
        hasPreview: Boolean(scenePreview?.placements.has(entry.key)),
        radius: modelBounds.radius,
        gap,
      });
      const priorityDistance = tier <= 2 || tier === 4 ? gap : distance;
      loadDelta.copy(modelBounds.center).sub(camera.position);
      const behind = loadDelta.dot(loadForward) < 0;
      const nextPriority = { tier, distance: priorityDistance, behind };
      if (isWorsePriority(best, nextPriority)) {
        priority.set(entry.gyaml, nextPriority);
      }
      if (updateForeground && isForegroundModel(tier)) foregroundModelNames.add(entry.gyaml);
    }
    if (priority.has(selectedModel)) {
      priority.set(selectedModel, { tier: 0, distance: 0, behind: false });
      if (updateForeground) foregroundModelNames.add(selectedModel);
    }
    const needsConversion = new Set(cachedFirst
      ? names.filter((name) => metaCache.get(name)?.status !== "mesh")
      : []);
    names.sort((a, b) => {
      const left = priority.get(a);
      const right = priority.get(b);
      return left.tier - right.tier
        || Number(needsConversion.has(a)) - Number(needsConversion.has(b))
        || effectiveDistance(left) - effectiveDistance(right);
    });
    if (updateForeground) modelPriority = priority;
  }

  function needsModelGeometry(gyaml) {
    for (const entry of entries.values()) {
      if (entry.gyaml === gyaml && !entry.region) return true;
    }
    return false;
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
    if (loadQueue) sortModelNames(loadQueue, true, true, modelLoads?.backgroundName);
    modelLoads?.pump();
  }

  function fetchModelOnce(gyaml, priority) {
    const cached = metaCache.get(gyaml);
    const plan = planAssetFetches({
      wantGeometry: true,
      geometryResident: Boolean(cached && cached.status !== "queued"),
      wantTextures: false,
      texturesResident: true,
    });
    if (!plan.models) return Promise.resolve(cached);
    const existing = modelRequests.get(gyaml);
    if (existing) return existing;
    const pending = (
      hooks.fetchModel ? hooks.fetchModel(gyaml, { priority, signal: sceneAbort.signal }) : Promise.reject(new Error("Model loading is not connected."))
    ).finally(() => {
      if (modelRequests.get(gyaml) === pending) modelRequests.delete(gyaml);
    });
    modelRequests.set(gyaml, pending);
    return pending;
  }

  function heapBytes() {
    const heap = (typeof performance !== "undefined" && performance.memory && performance.memory.usedJSHeapSize) || 0;
    if (heap > peakHeapBytes) peakHeapBytes = heap;
    return heap;
  }

  function invalidateAssetBudget() {
    assetBudgetCache = null;
  }

  function currentAssetBudget() {
    if (assetBudgetCache) return assetBudgetCache;
    const report = collectMemoryReport({ entries, batches, parsedTemplates, terrainRoot });
    const parts = budgetAssetBytes(report);
    assetBudgetCache = { ...parts, byModel: residentModelBytes(entries) };
    return assetBudgetCache;
  }

  function residentPressure() {
    const budget = currentAssetBudget();
    return {
      count: residentMeshNames().size,
      heap: heapBytes(),
      assetBytes: budget.total,
      geometryBytes: budget.geometry,
      bc7Bytes: budget.bc7,
      byModel: budget.byModel,
    };
  }

  function softCapped(pressure = residentPressure()) {
    return overResidentSoftCap(pressure.count, pressure.heap, pressure.assetBytes);
  }

  function hardCapped(pressure = residentPressure()) {
    return overResidentHardCap(pressure.count, pressure.heap, pressure.assetBytes);
  }

  function residentMeshNames() {
    return uniqueResidentNames(entries.values());
  }

  function templateStillInUse(url) {
    if (!url) return false;
    for (const entry of entries.values()) {
      if (entry.templateUrl === url && entry.parts && entry.parts.length) return true;
    }
    return false;
  }

  function unloadResidentModel(gyaml) {
    invalidateAssetBudget();
    statEvictions += 1;
    recentlyEvicted.add(gyaml);
    unloadAfter.delete(gyaml);
    textureQueue.forget(gyaml);
    const urls = new Set();
    for (const entry of entries.values()) {
      if (entry.gyaml !== gyaml) continue;
      if (entry.templateUrl) urls.add(entry.templateUrl);
      hideSlots(entry);
      entry.slots = [];
      entry.parts = null;
      entry.farNames = null;
    }
    modelLoads?.requeue(gyaml);
    const keepUrls = [...parsedTemplates.keys()].filter((url) => templateStillInUse(url));
    const keepTextures = liveTemplateTextures(keepUrls);
    for (const url of urls) {
      if (templateStillInUse(url)) continue;
      // A parse that has not produced parts yet still owns this url.
      if (templateCache.has(url) && !parsedTemplates.has(url)) continue;
      disposeParsedTemplate(parsedTemplates.get(url), keepTextures);
      parsedTemplates.delete(url);
      templateCache.delete(url);
      const at = templateOrder.indexOf(url);
      if (at >= 0) templateOrder.splice(at, 1);
    }
  }

  function residentCandidate(name, entry) {
    const pri = modelPriority.get(name);
    const distance = pri && Number.isFinite(pri.distance)
      ? pri.distance
      : Math.sqrt(entry.pivot.position.distanceToSquared(camera.position));
    const budget = currentAssetBudget();
    return {
      name,
      distance,
      behind: Boolean(pri && pri.behind),
      bytes: budget.byModel.get(name) || 0,
      cooldownUntil: unloadAfter.has(name) ? unloadAfter.get(name) : undefined,
    };
  }

  function commitEvictions(candidates, options) {
    const plan = planEvictions(candidates, { cooldown: true, now: streamFrame, ...options });
    for (const name of plan.arm) {
      if (!unloadAfter.has(name)) unloadAfter.set(name, streamFrame + RESIDENT_UNLOAD_COOLDOWN_FRAMES);
    }
    return plan.evict;
  }

  function proposeEvictForIncoming(incoming) {
    if (!incoming) return [];
    const pressure = residentPressure();
    if (!softCapped(pressure)) return [];
    const incomingPri = modelPriority.get(incoming) || { tier: 5, distance: Infinity, behind: false };
    const selectedModel = entries.get(selected)?.gyaml;
    const candidates = [];
    const seen = new Set();
    for (const entry of entries.values()) {
      if (!entry.parts || !entry.parts.length || !entry.gyaml || seen.has(entry.gyaml)) continue;
      seen.add(entry.gyaml);
      if (entry.gyaml === incoming || entry.gyaml === selectedModel) {
        unloadAfter.delete(entry.gyaml);
        continue;
      }
      const pri = modelPriority.get(entry.gyaml) || { tier: 0, distance: 0, behind: false };
      if (!isWorsePriority(pri, incomingPri)) {
        unloadAfter.delete(entry.gyaml);
        continue;
      }
      candidates.push(residentCandidate(entry.gyaml, entry));
    }
    const countCap = pressure.count >= RESIDENT_MODEL_SOFT_CAP || pressure.heap >= RESIDENT_HEAP_SOFT;
    const byteCap = pressure.assetBytes >= RESIDENT_BYTE_SOFT;
    return commitEvictions(candidates, {
      keep: new Set(),
      count: pressure.count,
      heapBytes: pressure.heap,
      assetBytes: pressure.assetBytes,
      targetCount: countCap ? pressure.count - 1 : pressure.count,
      targetBytes: byteCap ? RESIDENT_BYTE_TARGET : pressure.assetBytes,
      maxEvict: 4,
    });
  }

  function evictFarResidentModels() {
    const pressure = residentPressure();
    // Launching enforces the soft byte and count caps. This path is the emergency brake.
    // A missing JS heap reading still used to skip the count brake; a byte overrun does not.
    const byteEmergency = pressure.assetBytes >= RESIDENT_BYTE_HARD;
    if (!byteEmergency && (pressure.heap < 1 || !hardCapped(pressure))) return 0;
    const keep = new Set(foregroundModelNames);
    const selectedModel = entries.get(selected)?.gyaml;
    if (selectedModel) keep.add(selectedModel);
    for (const name of keep) unloadAfter.delete(name);
    const nearest = new Map();
    for (const entry of entries.values()) {
      if (!entry.parts || !entry.parts.length || !entry.gyaml) continue;
      const candidate = residentCandidate(entry.gyaml, entry);
      const prev = nearest.get(entry.gyaml);
      if (!prev || candidate.distance < prev.distance) nearest.set(entry.gyaml, candidate);
    }
    const candidates = [...nearest.values()];
    const evictionOptions = {
      keep,
      count: pressure.count,
      heapBytes: pressure.heap,
      assetBytes: pressure.assetBytes,
      ignoreCooldown: pressure.heap >= RESIDENT_HEAP_HARD,
    };
    let evict = commitEvictions(candidates, evictionOptions);
    if (!evict.length && hardCapped(pressure)) {
      const pinned = new Set();
      if (selectedModel) pinned.add(selectedModel);
      evict = commitEvictions(candidates, { ...evictionOptions, keep: pinned });
    }
    for (const name of evict) unloadResidentModel(name);
    if (evict.length) {
      queueRebuild();
      textureCpuReleaseDirty = true;
      requestRender();
    }
    return evict.length;
  }

  function loadModels(token, actors, previewReady) {
    let names = new Set(actors.map((actor) => actor.gyaml).filter(Boolean));
    const queue = [...names];
    let backgroundTimer = 0;
    let backgroundName = null;
    const resolved = new Set();
    const boxes = new Set();
    const active = new Set();
    let started = false;
    let previewPending = Boolean(previewReady);
    let demand = previewPending;
    let idle = false;
    loadQueue = queue;
    statQueued = 0;
    statDeferred = 0;
    statBackgroundLoading = 0;
    statLoading = 0;
    statLoaded = 0;
    statConvert = 0;
    statTexture = 0;
    statDownload = 0;
    statUpload = 0;
    timedModels.clear();
    const eligible = (name) => Boolean(name) && (!demand || foregroundModelNames.has(name));
    const backgroundInFlight = () => Number(backgroundName !== null && !eligible(backgroundName));
    const foregroundInFlight = () => active.size - backgroundInFlight();
    const publish = (foregroundSettled) => {
      const atCap = softCapped();
      let queued = 0;
      for (const name of queue) {
        if (!eligible(name)) continue;
        if (atCap && !proposeEvictForIncoming(name).length) break;
        queued += 1;
      }
      const loading = foregroundInFlight();
      const deferred = queue.length - queued;
      const backgroundLoading = backgroundInFlight();
      if (!foregroundSettled && queued === statQueued && loading === statLoading
          && deferred === statDeferred && backgroundLoading === statBackgroundLoading && resolved.size === statLoaded) return;
      statQueued = queued;
      statDeferred = deferred;
      statLoading = loading;
      statBackgroundLoading = backgroundLoading;
      statLoaded = resolved.size;
      report({
        done: resolved.size,
        total: names.size,
        boxes: boxes.size,
        deferred: statDeferred + statBackgroundLoading,
        finished: idle,
        label: names.size ? `Models ${resolved.size} of ${names.size}` : "No actors",
        resident: residentMeshNames().size,
        residentCapped: softCapped() && queue.length > 0,
      }, foregroundSettled);
      requestRender();
      pumpTextures();
    };
    const failEntry = (entry, meta, message) => {
      entry.modelMeta = { ...(meta || entry.modelMeta || {}), status: "box", reason: message };
      paintLabel(entry);
      syncGhost(entry);
      fitBounds(entry, entry.modelMeta.bounds);
      if (hooks.onModelError) hooks.onModelError(entry.gyaml, message);
    };
    function deliver(gyaml, meta, template) {
      invalidateAssetBudget();
      unloadAfter.delete(gyaml);
      if (recentlyEvicted.delete(gyaml)) statReloads += 1;
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
        if (!showAo) paintAo(entry.pivot);
      }
      if (gyaml === FISHING_POND_WATER || fishingPondFish(gyaml)) refreshPondFish();
    }
    async function loadOne(gyaml, priority) {
      let meta = metaCache.get(gyaml);
      try {
        if (!meta || meta.status === "queued") {
          meta = await fetchModelOnce(gyaml, priority);
          if (token !== generation) return;
          metaCache.set(gyaml, meta);
        }
        if (meta.status !== "mesh" || !meta.url) {
          if (meta.status === "box" || meta.status === "skipped") boxes.add(gyaml);
          for (const entry of entries.values()) {
            if (entry.gyaml !== gyaml) continue;
            entry.modelMeta = meta;
            paintLabel(entry);
            fitBounds(entry, meta.bounds);
          }
          return;
        }
        const template = needsModelGeometry(gyaml) ? await ensureTemplate(meta.url, meta, priority) : null;
        if (token !== generation) {
          disposeObject3D(template, { textures: true });
          return;
        }
        deliver(gyaml, meta, template);
        if (textureAllow && template) enqueueTexture(gyaml, meta.url);
      } catch (error) {
        if (token !== generation || (error && error.name === "AbortError")) return;
        const message = error.message || "The model did not load.";
        if (!isMarker(gyaml)) console.error(`${gyaml}: ${message}`);
        boxes.add(gyaml);
        for (const entry of entries.values()) {
          if (entry.gyaml === gyaml) failEntry(entry, meta, message);
        }
      }
    }

    function pauseBackground() {
      window.clearTimeout(backgroundTimer);
      backgroundTimer = 0;
    }

    function canFillMap() {
      return token === generation && demand && !previewPending && queue.length > 0
        && !streamInteractive && !scenePreview?.terrainPending && !active.size
        && !eligible(queue[0]) && !modelStage?.pending.size
        && !softCapped();
    }

    function background() {
      if (!canFillMap()) {
        pauseBackground();
        return;
      }
      if (backgroundTimer) return;
      // Let the new detail render and wait out short pauses in camera movement.
      backgroundTimer = window.setTimeout(() => {
        backgroundTimer = 0;
        if (!canFillMap()) return;
        backgroundName = queue.shift();
        launch(backgroundName, "low");
        publish(false);
      }, 125);
    }

    function launch(name, priority) {
      active.add(name);
      retainLoad();
      void loadOne(name, priority).finally(() => {
        releaseLoad();
        if (token !== generation) return;
        active.delete(name);
        if (name === backgroundName) backgroundName = null;
        if (names.has(name)) resolved.add(name);
        else boxes.delete(name);
        pump();
      });
    }

    function pump() {
      if (!started || token !== generation) return;
      invalidateAssetBudget();
      const limit = Math.min(2, convertSlots);
      while (foregroundInFlight() < limit && eligible(queue[0])) {
        const incoming = queue[0];
        if (softCapped()) {
          const evict = proposeEvictForIncoming(incoming);
          if (!evict.length) break;
          for (const name of evict) unloadResidentModel(name);
          queueRebuild();
        }
        if (softCapped()) break;
        launch(queue.shift());
      }
      const cannotLaunchMore = !eligible(queue[0])
        || (softCapped() && !proposeEvictForIncoming(queue[0]).length);
      const settled = !foregroundInFlight() && cannotLaunchMore;
      const foregroundSettled = settled && !idle;
      idle = settled;
      publish(foregroundSettled);
      background();
      evictFarResidentModels();
      if (modelStage) {
        const keepPreview = previewPending || (demand && (!cannotLaunchMore || active.size > 0));
        if (settled && (!modelStage.done || modelStage.keepPreview !== keepPreview)) scheduleModelStage();
        modelStage.done = settled;
        modelStage.keepPreview = keepPreview;
      } else if (foregroundSettled) {
        queueRebuild();
      }
    }

    modelLoads = {
      pump,
      background,
      get backgroundName() { return backgroundName; },
      dispose: pauseBackground,
      requeue(gyaml) {
        if (!gyaml) return;
        resolved.delete(gyaml);
        boxes.delete(gyaml);
        if (names.has(gyaml) && !active.has(gyaml) && !queue.includes(gyaml)) queue.push(gyaml);
        idle = false;
      },
      update(nextActors, loading) {
        names = new Set(nextActors.map((actor) => actor.gyaml).filter(Boolean));
        for (let i = queue.length - 1; i >= 0; i -= 1) {
          if (!names.has(queue[i])) queue.splice(i, 1);
        }
        for (const name of resolved) {
          if (names.has(name)) continue;
          resolved.delete(name);
          boxes.delete(name);
        }
        for (const { gyaml } of loading) {
          if (!gyaml || active.has(gyaml) || queue.includes(gyaml)) continue;
          queue.push(gyaml);
        }
        idle = false;
        prioritizeLoad();
      },
    };

    if (previewPending) {
      // Let the coarse scene and nearby detail race. Until the preview is
      // ready, request only models useful to the current camera or selection.
      started = true;
      prioritizeLoad();
      retainLoad();
      void previewReady.then((ready) => {
        if (token !== generation) return;
        previewPending = false;
        demand = Boolean(ready);
        idle = false;
        prioritizeLoad();
      }).finally(releaseLoad);
    } else {
      const missing = queue.filter((name) => !metaCache.has(name));
      if (missing.length && missing.length <= 128 && hooks.fetchModels) {
        retainLoad();
        void hooks.fetchModels(missing, false, { signal: sceneAbort.signal }).then((batch) => {
          if (token !== generation) return;
          for (const [name, meta] of Object.entries(batch || {})) metaCache.set(name, meta);
        }).catch((error) => {
          if (!error || error.name !== "AbortError") console.error(error);
        }).finally(() => {
          releaseLoad();
          if (token !== generation) return;
          started = true;
          prioritizeLoad();
        });
      } else {
        started = true;
        prioritizeLoad();
      }
    }
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

  function ensureTemplate(url, meta, priority) {
    let pending = templateCache.get(url);
    if (!pending) {
      noteServerTiming(meta);
      pending = (async () => {
        const started = performance.now();
        const response = await fetch(url, { priority, signal: sceneAbort.signal });
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
    if (map.userData?.forgeCpuReleased || map.source?.userData?.forgeCpuReleased) return;
    map.colorSpace = THREE.LinearSRGBColorSpace;
    map.wrapS = THREE.RepeatWrapping;
    map.wrapT = THREE.RepeatWrapping;
    if (map.isCompressedTexture) repairCompressedTexture(map);
    else map.generateMipmaps = true;
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
      const identity = modelLod(child);
      // The glTF node transform belongs to the mesh, not to one placed actor.
      // Bake it into a clone so a later change to the template cannot slide
      // every copy, and so the instance matrix stays the actor matrix alone.
      local.multiplyMatrices(rootInverse, child.matrixWorld);
      const geometry = child.geometry.clone();
      if (!isIdentityMatrix(local)) geometry.applyMatrix4(local);
      geometry.computeBoundingSphere();
      const source = Array.isArray(child.material) ? child.material[0] : child.material;
      if (!source) return;
      let material = prepareMaterial(source);
      if (material.map && (material.userData.banc || material.name) === "water") {
        ensureWaterUv(geometry, material.map.channel);
      }
      if (material.userData && material.userData.blend && geometry.getAttribute("color")) {
        material = cloneMaterialPreservingTextures(material);
        geometry.setAttribute("blendMask", geometry.getAttribute("color"));
        geometry.deleteAttribute("color");
        const blendUv = material.userData.blend.uv;
        if ((blendUv === "_u1" || blendUv === "u1") && geometry.getAttribute("uv1")) {
          geometry.setAttribute("blendUv", geometry.getAttribute("uv1"));
        }
        material.vertexColors = false;
        if (material.userData.blendMap) {
          installBlendShader(material);
          if (blendNodes) blendNodes.attachBlend(material);
        }
      }
      const sphere = geometry.boundingSphere ? geometry.boundingSphere.clone() : new THREE.Sphere(new THREE.Vector3(), 1);
      const keepCpu = isPreviewWater(material, child.name || "", url || "");
      if (keepCpu) geometry.userData.forgeKeepCpu = true;
      parts.push({
        lod: identity.lod,
        family: identity.family,
        // child.name has had ':' stripped. The manifest key is the family
        // from the original node name (`Body:0`), which modelLod reads off
        // userData.name.
        name: child.name || "",
        materialKey: identity.family,
        geometry,
        material,
        keepCpu,
        sphere,
        radius: sphere.radius,
        key: `${url}|${identity.lod}|${identity.family}|${index}`,
        // Same material key as WebGL. WebGPU groups copies of one geometry into
        // one instanced draw inside that mesh instead of a mesh per geometry.
        batchKey: batchKeyFor(material, geometry),
      });
      index += 1;
    });
    // A stale flat glb can still share one material across shape keys. Split
    // that object before the batch key is recorded, and keep the key on the
    // shape so those batches do not fold back onto the first material.
    separateSharedMaterials(parts);
    for (const part of parts) {
      part.batchKey = `${batchKeyFor(part.material, part.geometry)}|${part.materialKey}`;
    }
    return { parts, farNames: new Set(parts.filter((part) => part.lod > 0).map((part) => part.family)) };
  }

  function rememberTemplate(url, parsed) {
    parsedTemplates.set(url, parsed);
    const existing = templateOrder.indexOf(url);
    if (existing >= 0) templateOrder.splice(existing, 1);
    templateOrder.push(url);
  }

  function rememberSceneTemplates(banc) {
    const distances = new Map();
    for (const entry of entries.values()) {
      const url = entry.templateUrl;
      if (!url || !entry.pivot.visible) continue;
      const distance = entry.key === selected ? -1 : entry.pivot.position.distanceToSquared(camera.position);
      if (distance < (distances.get(url) ?? Infinity)) distances.set(url, distance);
    }
    if (!distances.size) return;
    const nearby = [...distances].sort((a, b) => a[1] - b[1]).slice(0, 64).map(([url]) => url);
    recentSceneTemplates.delete(banc);
    recentSceneTemplates.set(banc, nearby);
    if (recentSceneTemplates.size > 2) recentSceneTemplates.delete(recentSceneTemplates.keys().next().value);
  }

  function pruneTemplates() {
    const pinned = new Set([...recentSceneTemplates.values()].flat());
    const spare = new Set(templateOrder.filter((url) => !pinned.has(url)).slice(-48));
    const keep = [];
    const evicted = [];
    for (const url of templateOrder) {
      if (pinned.has(url) || spare.has(url)) keep.push(url);
      else evicted.push(url);
    }
    const keepTextures = liveTemplateTextures(keep);
    for (const url of evicted) {
      disposeParsedTemplate(parsedTemplates.get(url), keepTextures);
      parsedTemplates.delete(url);
      templateCache.delete(url);
    }
    templateOrder.length = 0;
    for (const url of keep) templateOrder.push(url);
  }

  function clearBatches() {
    for (const batch of batches) {
      batch.mesh.removeFromParent();
      disposePickTexture(batch.mesh);
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
    displayedActorMatrix(entry, actorMatrix);
    applyActorMatrix(entry.slots, entry.key, actorMatrix);
  }

  function displayedActorMatrix(entry, target) {
    target.copy(entry.pivot.matrixWorld);
    target.elements[13] += entry.fishPreviewOffset || 0;
    return target;
  }

  function refreshPondFish() {
    const pond = [...entries.values()].find((entry) => entry.gyaml === FISHING_POND_WATER);
    if (!pond && !pondFishShown) return;
    pondFishShown = Boolean(pond);
    const surfaces = [];
    if (pond) {
      pond.pivot.updateWorldMatrix(true, false);
      for (const part of pond.parts || []) {
        if (part.lod || !isPreviewWater(part.material, part.name, pond.gyaml)) continue;
        surfaces.push({ geometry: part.geometry, matrix: pond.pivot.matrixWorld });
      }
      if (!surfaces.length) {
        for (const { mesh, index } of scenePreview?.placements.get(pond.key) || []) {
          if (!scenePreview.waterMeshes.has(mesh)) continue;
          const matrix = new THREE.Matrix4();
          mesh.getMatrixAt(index, matrix);
          surfaces.push({ geometry: mesh.geometry, matrix });
        }
      }
    }
    for (const entry of entries.values()) {
      if (!fishingPondFish(entry.gyaml)) continue;
      const { x, y, z } = entry.pivot.position;
      const hits = [];
      pondRay.ray.origin.set(x, y + 20, z);
      for (const { geometry, matrix } of surfaces) {
        pondProbe.geometry = geometry;
        pondProbe.matrixWorld.copy(matrix);
        pondProbe.raycast(pondRay, hits);
      }
      const height = Math.max(-Infinity, ...hits.map(({ point }) => point.y));
      const top = (entry.modelMeta?.bounds?.max?.[1] || 0) * Math.abs(entry.pivot.scale.y);
      const offset = fishingPondPreviewOffset(y, height, top);
      if (offset === (entry.fishPreviewOffset || 0)) continue;
      entry.fishPreviewOffset = offset;
      syncPreviewPlacement(entry);
      syncInstanceMatrix(entry);
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
    let pickIdsDirty = false;
    // Flying out must not clip the field on a far plane framed for the village.
    const farNeed = Math.max(8000, camera.position.distanceTo(orbit.target) * 6);
    if (camera.far < farNeed) {
      camera.far = farNeed;
      camera.updateProjectionMatrix();
    }
    cullMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    cullFrustum.setFromProjectionMatrix(cullMatrix, camera.coordinateSystem);
    let instances = 0;
    let visible = 0;
    for (const entry of entries.values()) {
      if (!entry.slots || !entry.slots.length) continue;
      entry.pivot.updateWorldMatrix(true, false);
      const elements = displayedActorMatrix(entry, actorMatrix).elements;
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
          pickIdsDirty = true;
        }
        if (show) visible += 1;
      }
    }
    statInstances = instances;
    statCulled = instances - visible;
    if (pickIdsDirty) syncPickIds();
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

  function rebuildBatches(stagedEntries = null) {
    const job = batchChain.then(
      () => rebuildBatchesNow(stagedEntries),
      () => rebuildBatchesNow(stagedEntries),
    );
    batchChain = job.then(() => {}, () => {});
    return job;
  }

  async function rebuildBatchesNow(stagedEntries = null) {
    const sceneToken = generation;
    uvDirty = true;
    const previous = stagedEntries ? [] : batches.slice();
    const nextBatches = [];
    const pendingMeshes = [];
    const actors = stagedEntries || [...entries.values()];
    for (const entry of actors) entry.slots = [];
    const groups = new Map();
    for (const entry of actors) {
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
      const dropped = [];
      for (const geometry of group.geometries.values()) {
        if (!ensureGeometryCpu(geometry)) dropped.push(geometry.uuid);
      }
      for (const uuid of dropped) group.geometries.delete(uuid);
      if (dropped.length) {
        group.records = group.records.filter((record) => group.geometries.has(record.part.geometry.uuid));
        console.warn("Skipped a mesh whose CPU copy could not be restored from its batch.");
      }
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
      const previousRender = mesh.onBeforeRender.bind(mesh);
      const webgpuBatch = Boolean(renderer.backend && renderer.backend.isWebGPUBackend === true);
      mesh.onBeforeRender = function pickAndGroup(rendererArg, sceneArg, cameraArg, geometry, material, ...rest) {
        // A quiet frame leaves the grouped ranges in place. Grouping them
        // again would treat each run as one instance and restart the next
        // geometry at slot 1.
        const picking = Boolean(sceneArg.overrideMaterial && sceneArg.overrideMaterial.userData && sceneArg.overrideMaterial.userData.forgePick);
        if (picking) gpuPicker.prepareObject(mesh, mesh.material);
        const dirty = mesh._visibilityChanged || mesh.perObjectFrustumCulled || mesh.sortObjects;
        previousRender(rendererArg, sceneArg, cameraArg, geometry, picking ? mesh.material : material, ...rest);
        if (webgpuBatch && dirty) groupGeometryDraws(mesh);
      };
      mesh.layers.enable(PICK_LAYER);
      mesh.userData.forgePickKind = 1;
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
        disposePickTexture(mesh);
        mesh.dispose();
        continue;
      }
      for (const [uuid, geometry] of group.geometries) {
        const geometryId = geomIds.get(uuid);
        if (geometryId !== undefined) {
          noteGeometryBatch(geometry, mesh, geometryId);
          const linked = mesh.userData.forgeLinkedGeometries || (mesh.userData.forgeLinkedGeometries = []);
          if (!linked.includes(geometry)) linked.push(geometry);
        }
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
        const placed = displayedActorMatrix(record.entry, new THREE.Matrix4());
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
      mesh.userData.forgeIndirect = mesh._indirectTexture;
      mesh.computeBoundingSphere();
      pendingMeshes.push({ mesh, ids, water });
    }
    const compileStarted = performance.now();
    await Promise.all(pendingMeshes.map(async ({ mesh }) => {
      try {
        await compileObjectPipeline(renderer, mesh, camera, scene);
      } catch (error) {
        console.error(error);
      }
    }));
    if (sceneStreamStats && pendingMeshes.length) {
      const waited = performance.now() - compileStarted;
      sceneStreamStats.compileMs = (sceneStreamStats.compileMs || 0) + waited;
      sceneStreamStats.compileBatches = (sceneStreamStats.compileBatches || 0) + pendingMeshes.length;
      const rounded = Math.round(waited * 10) / 10;
      if (rounded > (sceneStreamStats.maxCompileMs || 0)) sceneStreamStats.maxCompileMs = rounded;
    }
    if (sceneToken !== generation) {
      for (const item of pendingMeshes) {
        item.mesh.removeFromParent();
        disposePickTexture(item.mesh);
        item.mesh.dispose();
      }
      return false;
    }
    for (const item of pendingMeshes) {
      const mesh = item.mesh;
      if (mesh.parent) continue;
      scene.add(mesh);
      nextBatches.push(item);
    }
    if (stagedEntries) {
      for (const batch of nextBatches) batches.push(batch);
      syncPickIds();
      releaseUnselectedPartGeometry();
      return actors.every((entry) => !entry.parts?.length || entry.slots.length);
    }
    const expectMeshes = actors.some((entry) => entry.parts && entry.parts.length);
    if (!nextBatches.length && expectMeshes && previous.length) {
      for (const entry of entries.values()) entry.slots = [];
      console.error("Actor batches failed to build; the meshes already on screen stay up.");
      return false;
    }
    batches.length = 0;
    for (const batch of nextBatches) batches.push(batch);
    naiveDraws = looseParts();
    disposeBatches(previous);
    syncBatchVisibility();
    syncPickIds();
    releaseUnselectedPartGeometry();
    guardMissingUv();
    return true;
  }

  function syncPickIds() {
    for (const batch of batches) {
      const pick = batch.mesh.userData.pick;
      if (!pick) continue;
      const numeric = new Array(pick.count);
      for (let i = 0; i < pick.count; i += 1) {
        const key = pick.ids[i];
        const entry = key ? entries.get(key) : null;
        const visible = typeof batch.mesh.getVisibleAt === "function" ? batch.mesh.getVisibleAt(i) : true;
        numeric[i] = visible && entry && canPick({ shown: entry.pivot.visible, locked: entry.locked })
          ? gpuPicker.idFor(key) : 0;
      }
      gpuPicker.writeInstanceIds(batch.mesh, numeric);
      batch.mesh.userData.forgeIndirect = batch.mesh._indirectTexture;
    }
    if (scenePreview) {
      for (const meshes of scenePreview.models.values()) {
        for (const mesh of meshes) {
          const keys = mesh.userData.previewIds || [];
          gpuPicker.writeInstanceIds(mesh, keys.map((key) => {
            const entry = key ? entries.get(key) : null;
            return entry && canPick({ shown: entry.pivot.visible, locked: entry.locked }) ? gpuPicker.idFor(key) : 0;
          }));
        }
      }
    }
    for (const entry of entries.values()) {
      const placeholder = !entry.volume && (!entry.parts || !entry.parts.length);
      entry.mesh.userData.forgePickId = placeholder && canPick({ shown: entry.pivot.visible, locked: entry.locked })
        ? gpuPicker.idFor(entry.key) : 0;
    }
  }

  function releaseUnselectedPartGeometry() {
    const keep = new Set();
    const current = selected ? entries.get(selected) : null;
    if (current && current.parts) {
      for (const part of current.parts) if (part.geometry) keep.add(part.geometry);
    }
    const seen = new Set();
    for (const parsed of parsedTemplates.values()) {
      for (const part of parsed.parts || []) {
        const geometry = part.geometry;
        if (!geometry || seen.has(geometry)) continue;
        seen.add(geometry);
        if (part.keepCpu) geometry.userData.forgeKeepCpu = true;
        if (geometry.userData.forgeKeepCpu || keep.has(geometry)) {
          ensureGeometryCpu(geometry);
          continue;
        }
        const link = geometry.userData.forgeBatch;
        if (!link || !link.mesh || !link.mesh.parent) continue;
        releaseGeometryCpu(geometry);
      }
    }
  }

  function disposeBatches(oldBatches) {
    for (const { mesh } of oldBatches) {
      mesh.removeFromParent();
      disposePickTexture(mesh);
      mesh.dispose();
    }
  }

  function cancelModelStage() {
    if (!modelStage) return;
    window.cancelAnimationFrame(modelStage.timer);
    modelStage = null;
  }

  function scheduleModelStage() {
    const stage = modelStage;
    if (!stage || stage.timer || stage.busy) return;
    stage.timer = window.requestAnimationFrame(() => {
      stage.timer = 0;
      stage.busy = true;
      void (async () => {
      if (modelStage !== stage) return;
      const ready = [];
      const names = [];
      const deadline = performance.now() + (streamInteractive ? 2 : 4);
      const order = [...stage.pending.keys()];
      sortModelNames(order, false, false);
      for (const name of order) {
        if (modelStage !== stage) return;
        const waiting = stage.pending.get(name);
        if (!waiting) continue;
        const actors = [...waiting].filter((entry) => entries.get(entry.key) === entry);
        stage.pending.delete(name);
        names.push(name);
        for (const actor of actors) ready.push(actor);
        stage.failed = !(await rebuildBatches(actors)) || stage.failed;
        if (names.length >= (streamInteractive ? 1 : 3) || performance.now() >= deadline) break;
      }
      if (modelStage !== stage) return;
      if (ready.length) {
        if (ready.some((entry) => entry.slots.length) && stage.previous.length) {
          const previous = new Set(stage.previous);
          const kept = batches.filter((batch) => !previous.has(batch));
          batches.length = 0;
          for (const batch of kept) batches.push(batch);
          disposeBatches(stage.previous);
          stage.previous = [];
        }
        for (const name of names) {
          if (ready.some((entry) => entry.gyaml === name && !entry.slots.length)) continue;
          const meshes = scenePreview?.models.get(name) || [];
          for (const mesh of meshes) {
            mesh.removeFromParent();
            disposePickTexture(mesh);
            mesh.dispose();
            scenePreview.waterMeshes.delete(mesh);
            for (const key of mesh.userData.previewKeys) scenePreview.placements.delete(key);
          }
          scenePreview?.models.delete(name);
        }
        syncBatchVisibility();
        if (sceneStreamStats?.firstDetail === null && ready.some((entry) =>
          entry.pivot.position.distanceToSquared(camera.position) < 80 * 80 && entry.slots.some((slot) => slot.shown))) {
          sceneStreamStats.firstDetail = performance.now() - sceneStreamStats.start;
        }
        nextDetailScan = 0;
        requestRender();
      }
      stage.busy = false;
      if (stage.pending.size) {
        scheduleModelStage();
      } else if (stage.done) {
        modelLoads?.background();
        if (stage.keepPreview) {
          if (stage.previous.length) {
            const previous = new Set(stage.previous);
            const kept = batches.filter((batch) => !previous.has(batch));
            batches.length = 0;
            for (const batch of kept) batches.push(batch);
            disposeBatches(stage.previous);
            stage.previous = [];
          }
        } else {
          modelStage = null;
          if (stage.failed || stage.previous.length) {
            queueRebuild();
            return;
          }
          if (!scenePreview?.models.size) clearScenePreview();
        }
        naiveDraws = looseParts();
        requestRender();
        if (!stage.notified) {
          stage.notified = true;
          hooks.onModelsReady?.({ batchesBuilt: true });
          noteLoadedMemory();
        }
      }
      })().catch((error) => {
        console.error(error);
        stage.failed = true;
      }).finally(() => {
        stage.busy = false;
      });
    });
  }

  function queueRebuild() {
    if (rebuildTimer) clearTimeout(rebuildTimer);
    rebuildTimer = window.setTimeout(() => {
      rebuildTimer = 0;
      void (async () => {
        clearScenePreview();
        const ready = await rebuildBatches();
        requestRender();
        if (hooks.onModelsReady) hooks.onModelsReady({ batchesBuilt: ready });
        if (ready) noteLoadedMemory();
      })().catch((error) => console.error(error));
    }, 100);
  }

  function attachModel(entry, template, url) {
    if (entry.parts) return;
    let parsed = parsedTemplates.get(url);
    if (!parsed) {
      parsed = partsFromTemplate(template, url);
      parsed.source = template;
      rememberTemplate(url, parsed);
      disposeExtractedTemplateGeometry(template);
    }
    entry.parts = parsed.parts;
    entry.farNames = parsed.farNames;
    entry.templateUrl = url;
    entry.volume = false;
    textureCpuReleaseDirty = true;
    stageModel(entry);
  }

  function stageModel(entry) {
    if (modelStage && entry.parts.length) {
      let actors = modelStage.pending.get(entry.gyaml);
      if (!actors) {
        actors = new Set();
        modelStage.pending.set(entry.gyaml, actors);
      }
      actors.add(entry);
      scheduleModelStage();
    }
  }

  const actorMatrix = new THREE.Matrix4();
  const pondRay = new THREE.Raycaster(new THREE.Vector3(), new THREE.Vector3(0, -1, 0));
  const pondProbe = new THREE.Mesh(undefined, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  let pondFishShown = false;

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
  vec3 low = diffuseColor.rgb;
  if (blendSkin > 0.5) {
    low = blendSkinColor;
    if (blendSkinVertex > 0.5) low *= vBlendMask.rgb;
  }
  diffuseColor.rgb = mix(low, moss.rgb, blendWeight);
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
    const skinColor = blend.skinColor || [1, 1, 1];
    material.userData.blendSkinUniform = { value: blend.skin ? 1 : 0 };
    material.userData.blendSkinVertexUniform = { value: blend.skinVertex ? 1 : 0 };
    material.userData.blendSkinColorUniform = {
      value: new THREE.Vector3(Number(skinColor[0] ?? 1), Number(skinColor[1] ?? 1), Number(skinColor[2] ?? 1)),
    };
    if (!material.userData.blendMapUniform) {
      material.userData.blendMapUniform = { value: material.userData.blendMap || null };
    }
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
    material.userData.blendMapUniform.value = material.userData.blendMap || null;
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
      shader.uniforms.blendSkin = material.userData.blendSkinUniform;
      shader.uniforms.blendSkinVertex = material.userData.blendSkinVertexUniform;
      shader.uniforms.blendSkinColor = material.userData.blendSkinColorUniform;
      shader.uniforms.blendMap = material.userData.blendMapUniform;
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
uniform float blendSkin;
uniform float blendSkinVertex;
uniform vec3 blendSkinColor;
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
      shader.uniforms.terrainLayerMissing = material.userData.terrainLayerUniform;
    };
  }

  function addTerrainPart(root, geometry, material) {
    const marks = geometry.getAttribute("uv3");
    const indices = geometry.index?.array;
    if (material.userData.terrainShader && marks && indices) {
      const ground = [], sub = [];
      for (let i = 0; i < indices.length; i += 3) {
        const a = indices[i], b = indices[i + 1], c = indices[i + 2];
        const isSub = marks.getY(a) > 0.5 || marks.getY(b) > 0.5 || marks.getY(c) > 0.5;
        (isSub ? sub : ground).push(a, b, c);
      }
      if (sub.length) {
        const outline = new THREE.BufferGeometry();
        outline.setAttribute("position", geometry.getAttribute("position"));
        outline.setIndex(new THREE.BufferAttribute(indices.constructor.from(sub), 1));
        const mesh = new THREE.Mesh(outline, subTerrainMaterial);
        mesh.userData.subTerrain = true;
        mesh.visible = showSub;
        root.add(mesh);
        if (!ground.length) {
          geometry.dispose();
          return;
        }
        geometry.setIndex(new THREE.BufferAttribute(indices.constructor.from(ground), 1));
      }
    }
    const mesh = new THREE.Mesh(geometry, material);
    if (material.userData.terrainShader) bindTerrainMesh(mesh);
    root.add(mesh);
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
        material = cloneMaterialPreservingTextures(material);
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
          for (const item of group.geometries) addTerrainPart(root, item, group.material);
          continue;
        }
      }
      if (geometry) addTerrainPart(root, geometry, group.material);
    }
    return root;
  }

  function terrainCurrent(token) {
    return token === terrainToken;
  }

  async function indexTerrain(root, token) {
    const geometries = root.children.filter((child) => !child.userData.subTerrain).map((child) => child.geometry);
    if (!geometries.length || typeof Worker === "undefined") return;
    const worker = new Worker(new URL("./terrain-index-worker.js", import.meta.url), { type: "module" });
    terrainWorkers.add(worker);
    try {
      for (const geometry of geometries) {
        if (!terrainCurrent(token)) break;
        const position = geometry.getAttribute("position");
        const index = geometry.index;
        if (position.isInterleavedBufferAttribute || index?.isInterleavedBufferAttribute) continue;
        const result = await new Promise((resolve, reject) => {
          worker.onmessage = ({ data }) => data.error ? reject(new Error(data.error)) : resolve(data);
          worker.onerror = (event) => reject(new Error(event.message));
          worker.postMessage({ position: position.array, index: index?.array });
        });
        if (terrainCurrent(token)) geometry.boundsTree = MeshBVH.deserialize(result, geometry, { setIndex: false });
      }
    } finally {
      terrainWorkers.delete(worker);
      worker.terminate();
    }
  }

  async function warmTerrain(root) {
    await warmWebGLPrograms(renderer, root, camera, scene);
    await compileObjectPipeline(renderer, root, camera, scene);
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
    if (previous && previous !== nextRoot) {
      previous.removeFromParent();
      if (![...recentTerrain.values()].includes(previous)) disposeTerrainObject(previous);
    }
    if (nextRoot) {
      markTerrainPick(nextRoot);
      scene.add(nextRoot);
      applyTerrainUniforms();
      textureCpuReleaseDirty = true;
    }
    clearGround();
    applyGround();
    requestRender();
  }

  function rememberTerrain(url, root) {
    recentTerrain.delete(url);
    recentTerrain.set(url, root);
    if (recentTerrain.size > 2) {
      const oldest = recentTerrain.keys().next().value;
      const evicted = recentTerrain.get(oldest);
      recentTerrain.delete(oldest);
      templateCache.delete(oldest);
      if (evicted !== terrainRoot) disposeTerrainObject(evicted);
    }
  }

  async function loadTerrain(token, banc) {
    if (!banc || !hooks.fetchTerrain) return;
    retainLoad();
    try {
      await loadTerrainBody(token, banc);
    } finally {
      releaseLoad();
      if (terrainCurrent(token)) {
        if (scenePreview) {
          scenePreview.terrainPending = false;
          scenePreview.terrain?.removeFromParent();
        }
        if (hooks.onTerrainReady) hooks.onTerrainReady();
        modelLoads?.background();
      }
    }
  }

  async function loadTerrainBody(token, banc) {
    let meta;
    try {
      meta = await hooks.fetchTerrain(banc, false, sceneAbort.signal);
    } catch (error) {
      if (error && error.name === "AbortError") return;
      meta = { status: "none", reason: error.message || "The map model request failed." };
    }
    if (!terrainCurrent(token)) return;
    if (hooks.onTerrain) hooks.onTerrain(meta);
    if (meta.status !== "mesh" || !meta.url) {
      showTerrain(null);
      return;
    }
    await installTerrainModel(token, meta);
    if (terrainCurrent(token) && meta.fitPending) void refineTerrain(token, banc, meta.url);
    if (hooks.fetchSurface) void loadLocalGround(token, banc);
    if (terrainCurrent(token)) void requestGround(orbit.target.x, orbit.target.z);
  }

  async function loadLocalGround(token, banc) {
    if (!hooks.fetchSurface || !banc) return;
    const before = performance.memory ? performance.memory.usedJSHeapSize : 0;
    const started = performance.now();
    try {
      const meta = await loadGroundSurface(hooks.fetchSurface(banc), { signal: sceneAbort.signal });
      if (!terrainCurrent(token) || !meta) return;
      const after = performance.memory ? performance.memory.usedJSHeapSize : 0;
      console.info("Local ground surface ready", {
        ...meta,
        loadMs: performance.now() - started,
        jsHeapDeltaMB: (after - before) / 1048576,
      });
      requestRender();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      console.warn("Could not load the local ground surface", error);
    }
  }

  async function refineTerrain(token, banc, previousUrl) {
    try {
      const meta = await hooks.fetchTerrain(banc, true, sceneAbort.signal);
      if (terrainCurrent(token) && meta.status === "mesh" && meta.url && meta.url !== previousUrl) {
        await installTerrainModel(token, meta);
      }
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (terrainCurrent(token)) console.warn("Could not refine the scene terrain", error);
    }
  }

  async function installTerrainModel(token, meta) {
    const cachedTerrain = recentTerrain.get(meta.url);
    if (cachedTerrain) {
      rememberTerrain(meta.url, cachedTerrain);
      showTerrain(cachedTerrain);
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
        child.receiveShadow = showShadows && !child.userData.subTerrain;
        child.frustumCulled = false;
        child.renderOrder = -1;
        if (child.userData.subTerrain) return;
        if (child.isMesh && child.material?.userData.terrainShader) bindTerrainMesh(child);
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
        await Promise.allSettled([indexTerrain(nextRoot, token), warmTerrain(nextRoot)]).then((results) => {
          for (const result of results) if (result.status === "rejected") console.warn(result.reason);
        });
      } catch (error) {
        console.warn(error);
      }
      if (!terrainCurrent(token)) {
        disposeTerrainObject(nextRoot);
        return;
      }
      rememberTerrain(meta.url, nextRoot);
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
    releaseUnselectedPartGeometry();
    nextDetailScan = 0;
    prioritizeLoad();
    for (const entry of entries.values()) paint(entry);
    syncBatchVisibility();
    attach();
    syncLabels();
    updateSnapGrid(selected ? entries.get(selected) : null);
    requestRender();
  }

  function aimOrbit(event) {
    const ndc = setPointer(event);
    const roots = [];
    for (const batch of batches) roots.push(batch.mesh);
    for (const entry of entries.values()) {
      if (entry.pivot.visible) roots.push(entry.pivot);
    }
    const hits = roots.length ? raycaster.intersectObjects(roots, true) : [];
    const hit = hits.find((item) => item.point);
    if (hit) orbit.target.copy(hit.point);
    else {
      const entry = selected ? entries.get(selected) : null;
      if (entry) entry.pivot.getWorldPosition(orbit.target);
      else {
        const forward = new THREE.Vector3();
        camera.getWorldDirection(forward);
        orbit.target.copy(camera.position).add(forward.multiplyScalar(8));
      }
    }
    const token = ++orbitToken;
    enqueuePick(async () => {
      try {
        const point = await gpuPicker.pickDistance({ ndcX: ndc.x, ndcY: ndc.y, terrain: true, preview: false });
        if (token !== orbitToken || !point) return;
        orbit.target.copy(point);
        requestRender();
      } catch (error) {
        warnPickFallback(error);
      }
    });
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
    transform.setSpace(spaceForMode(mode, gizmoSpace));
  }

  function captureDragOrigin(entry) {
    const quaternion = entry.pivot.quaternion.toArray();
    dragOrigin = {
      position: entry.pivot.position.toArray(),
      quaternion,
      scale: [entry.pivot.scale.x, entry.pivot.scale.y, entry.pivot.scale.z],
      basis: basisFromQuaternion(quaternion),
    };
  }

  function restoreDragRaw(entry) {
    if (!dragRaw || !entry) return;
    entry.pivot.position.fromArray(dragRaw.position);
    entry.pivot.quaternion.fromArray(dragRaw.quaternion);
    entry.pivot.scale.fromArray(dragRaw.scale);
    if (entry.pivot.rotation.order !== "ZYX") entry.pivot.rotation.order = "ZYX";
    entry.pivot.updateMatrixWorld(true);
    transform._root.updateMatrixWorld(true);
  }

  function applyGridSnap(entry) {
    if (!entry) return;
    dragRaw = {
      position: entry.pivot.position.toArray(),
      quaternion: entry.pivot.quaternion.toArray(),
      scale: [entry.pivot.scale.x, entry.pivot.scale.y, entry.pivot.scale.z],
    };
    if (!transform.dragging || !dragOrigin || !gridSnapEngaged(gridSettings.enabled, ctrlSnap)) return;
    const axis = transform.axis || dragAxis;
    if (!axis) return;
    if (transform.mode === "translate") {
      const groundOwnsY = snapOn && !isExplicitLift(axis) && shouldSnapTranslate(axis, "move");
      const next = snapTranslation({
        position: dragRaw.position,
        origin: dragOrigin.position,
        axis,
        space: gizmoSpace,
        basis: dragOrigin.basis,
        increment: gridSettings.move,
        lockY: groundOwnsY,
      });
      entry.pivot.position.fromArray(next);
      return;
    }
    if (transform.mode === "rotate") {
      const next = snapRotation({
        current: dragRaw.quaternion,
        origin: dragOrigin.quaternion,
        axis,
        space: effectiveRotationSpace(gizmoSpace, axis),
        incrementRadians: gridSettings.rotate * Math.PI / 180,
      });
      entry.pivot.quaternion.fromArray(next);
      if (entry.pivot.rotation.order !== "ZYX") entry.pivot.rotation.order = "ZYX";
      return;
    }
    if (transform.mode === "scale" && gridSettings.scaleEnabled) {
      const next = snapScale({
        current: dragRaw.scale,
        origin: dragOrigin.scale,
        axis,
        increment: gridSettings.scale,
      });
      entry.pivot.scale.fromArray(next);
    }
  }

  function reapplyDragSnap() {
    if (!transform.dragging || !dragRaw) return;
    const entry = selected ? entries.get(selected) : null;
    if (!entry) return;
    restoreDragRaw(entry);
    applyGridSnap(entry);
    if (transform.mode === "translate") maybeSnap(entry, "move");
    applySelectedActorTransform(entry, { commit: false });
    updateSnapGrid(entry);
  }

  function clearSnapGrid() {
    if (!snapGrid) return;
    scene.remove(snapGrid);
    snapGrid.geometry.dispose();
    const materials = Array.isArray(snapGrid.material) ? snapGrid.material : [snapGrid.material];
    for (const material of materials) material.dispose();
    snapGrid = null;
  }

  function updateSnapGrid(entry) {
    if (!gridSettings.visible || !entry || !entry.pivot) {
      if (snapGrid) snapGrid.visible = false;
      return;
    }
    const layout = snapGridLayout(gridSettings.move);
    if (!snapGrid || snapGrid.userData.cell !== layout.cell || snapGrid.userData.divisions !== layout.divisions) {
      clearSnapGrid();
      snapGrid = new THREE.GridHelper(layout.size, layout.divisions, 0x8ea184, 0x3c4a40);
      snapGrid.userData.cell = layout.cell;
      snapGrid.userData.divisions = layout.divisions;
      const materials = Array.isArray(snapGrid.material) ? snapGrid.material : [snapGrid.material];
      for (const material of materials) {
        material.transparent = true;
        material.opacity = 0.85;
        material.depthWrite = false;
      }
      snapGrid.raycast = () => {};
      snapGrid.traverse((child) => {
        child.raycast = () => {};
        child.layers.disable(PICK_LAYER);
        child.layers.disable(PREVIEW_PICK_LAYER);
        child.layers.disable(TERRAIN_PICK_LAYER);
      });
      scene.add(snapGrid);
    }
    snapGrid.visible = true;
    snapGrid.position.copy(entry.pivot.position);
    if (gizmoSpace === "local") snapGrid.quaternion.copy(entry.pivot.quaternion);
    else snapGrid.quaternion.identity();
    snapGrid.translateY(0.02);
  }

  function pivotScale(pivot) {
    return [pivot.scale.x, pivot.scale.y, pivot.scale.z];
  }

  function applyTerrainUniforms() {
    if (!terrainRoot) return;
    const debug = showTerrainDebug ? 1 : 0;
    terrainRoot.traverse((child) => {
      if (child.userData.subTerrain) child.visible = showSub;
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) {
        if (!material || !material.userData) continue;
        if (material.userData.terrainDebugUniform) material.userData.terrainDebugUniform.value = debug;
      }
    });
  }

  function applyColor(color, value) {
    if (typeof value === "string") color.set(value);
    else if (Array.isArray(value) && value.length >= 3) color.setRGB(value[0], value[1], value[2]);
  }

  function paintAo(root) {
    if (!root) return;
    const intensity = showAo ? 1 : 0;
    root.traverse((child) => {
      const materials = child.material ? (Array.isArray(child.material) ? child.material : [child.material]) : [];
      for (const material of materials) {
        if (material && material.aoMap) material.aoMapIntensity = intensity;
      }
    });
  }

  function paintShadows() {
    renderer.shadowMap.enabled = showShadows;
    sun.castShadow = showShadows;
    if (sun.shadow) sun.shadow.needsUpdate = true;
    if (terrainRoot) {
      terrainRoot.traverse((child) => {
        child.castShadow = false;
        child.receiveShadow = showShadows && !child.userData.subTerrain;
      });
    }
  }

  function paintLights(resolved) {
    const lighting = resolved.lighting;
    applyColor(hemi.color, lighting.hemisphere.sky);
    applyColor(hemi.groundColor, lighting.hemisphere.ground);
    hemi.intensity = Number(lighting.hemisphere.intensity) || 0;
    applyColor(sun.color, lighting.sun.color);
    sun.intensity = Number(lighting.sun.intensity) || 0;
    const position = lighting.sun.position || [18, 36, 14];
    sun.position.set(position[0], position[1], position[2]);
    applyColor(ambient.color, lighting.ambient.color);
    ambient.intensity = Number(lighting.ambient.intensity) || 0;
    const name = tonemapName(lighting.tonemap);
    renderer.toneMapping = name === "reinhard" ? THREE.ReinhardToneMapping
      : name === "cineon" ? THREE.CineonToneMapping
        : name === "linear" || name === "none" ? THREE.NoToneMapping
          : THREE.ACESFilmicToneMapping;
    const exposure = Number(lighting.exposure);
    renderer.toneMappingExposure = Number.isFinite(exposure) ? Math.min(8, Math.max(0.05, exposure)) : 1.05;
    const fog = lighting.fog;
    if (fog && fog.kind === "exp2" && Number.isFinite(Number(fog.density))) {
      scene.fog = new THREE.FogExp2(0xffffff, Number(fog.density));
      applyColor(scene.fog.color, fog.color);
    } else if (fog && Number.isFinite(Number(fog.near)) && Number.isFinite(Number(fog.far))) {
      scene.fog = new THREE.Fog(0xffffff, Number(fog.near), Number(fog.far));
      applyColor(scene.fog.color, fog.color);
    } else {
      scene.fog = null;
    }
    const background = (lighting.sky && lighting.sky.color) || (lighting.env && lighting.env.color);
    if (background) applyColor(scene.background, background);
    else scene.background.copy(editorBackground);
    lightingSample = resolved.sample;
    requestRender();
  }

  function applyLighting(payload, focus) {
    lightingState = payload;
    const resolved = resolveLighting(payload, focus || { x: orbit.target.x, z: orbit.target.z }, {
      editorDefaults: showEditorDefaults,
    });
    paintLights(resolved);
    lastLightingNote = resolved.note;
    return resolved.note;
  }

  function maybeRelight() {
    if (!lightingState || !lightingState.samples || !lightingState.samples.length) return;
    const resolved = resolveLighting(lightingState, { x: orbit.target.x, z: orbit.target.z }, {
      editorDefaults: showEditorDefaults,
    });
    if (resolved.sample === lightingSample) return;
    paintLights(resolved);
    lastLightingNote = resolved.note;
  }

  function setFlags({ hidden, labels, categories: next, volumes, ground, water, boxes, terrainDebug, subTerrain, blendMask, distance, visibleLayers, shadows, ao, effectGizmos, editorDefaults }) {
    if (hidden !== undefined) showHidden = hidden;
    if (labels !== undefined) showLabels = labels;
    if (volumes !== undefined) hideVolumes = volumes;
    if (ground !== undefined) showGround = Boolean(ground);
    if (water !== undefined) showWater = Boolean(water);
    if (terrainDebug !== undefined) showTerrainDebug = Boolean(terrainDebug);
    if (subTerrain !== undefined) showSub = Boolean(subTerrain);
    if (blendMask !== undefined) showBlendMask = Boolean(blendMask);
    if (distance !== undefined) drawDistance = Number(distance) || 0;
    if (visibleLayers) Object.assign(layerVisibility, visibleLayers);
    if (shadows !== undefined) {
      showShadows = Boolean(shadows);
      uvDirty = true;
    }
    if (ao !== undefined) {
      showAo = Boolean(ao);
      uvDirty = true;
    }
    if (effectGizmos !== undefined) showEffectGizmos = Boolean(effectGizmos);
    if (editorDefaults !== undefined) {
      showEditorDefaults = Boolean(editorDefaults);
      if (lightingState) applyLighting(lightingState);
    }
    if (next) Object.assign(categories, next);
    if (boxes) Object.assign(boxCategories, boxes);
    for (const entry of entries.values()) {
      const gate = {
        status: entry.status,
        present: entry.present,
        category: entry.category,
        volume: entry.volume,
        layer: entry.layer,
        layersVisible: layerVisibility,
        showHidden,
        categories,
        hideVolumes,
        companion: entry.companion,
        pickable: entry.pickable,
      };
      entry.locked = actorLocked(gate);
      entry.pivot.visible = actorShown(gate);
      syncPreviewPlacement(entry);
      syncGhost(entry);
    }
    syncBatchVisibility();
    syncPickIds();
    applyGround();
    applyWater();
    applyTerrainUniforms();
    applyBlendUniforms();
    drawLinks();
    if (shadows !== undefined) paintShadows();
    if (ao !== undefined) {
      paintAo(terrainRoot);
      for (const entry of entries.values()) if (entry.pivot) paintAo(entry.pivot);
      for (const batch of batches) paintAo(batch.mesh);
    }
    attach();
    syncLabels();
    prioritizeLoad();
    requestRender();
  }

  function rememberTextureNote(gyaml, note) {
    const text = note || "";
    let changed = false;
    for (const entry of entries.values()) {
      if (entry.gyaml !== gyaml || entry.textureNote === text) continue;
      entry.textureNote = text;
      changed = true;
    }
    if (changed && hooks.onModels) hooks.onModels();
  }

  function modelInfo(id) {
    const entry = entries.get(id);
    if (!entry) return null;
    const info = entry.modelMeta || { status: "loading", reason: "Looking up the model.", source: "", triangles: 0, textures: 0 };
    if (!entry.textureNote) return info;
    return { ...info, textureNote: entry.textureNote };
  }

  function auditPlacements() {
    const actual = new THREE.Matrix4();
    const expected = new THREE.Matrix4();
    const differed = () => actual.elements.some((value, i) => Math.abs(value - expected.elements[i]) > 0.01);
    const placements = [];
    for (const entry of entries.values()) {
      if (!entry.id || entry.region || isMarker(entry.gyaml)) continue;
      const preview = scenePreview?.placements.get(entry.key) || [];
      const lods = [...new Set(entry.slots.filter((slot) => slot.shown).map((slot) => slot.lod))].sort((a, b) => a - b);
      let mismatched = false;
      if (entry.pivot.visible) {
        entry.pivot.updateWorldMatrix(true, false);
        displayedActorMatrix(entry, expected);
        for (const slot of entry.slots) {
          if (!slot.shown) continue;
          slot.mesh.getMatrixAt(slot.index, actual);
          mismatched ||= differed();
        }
        for (const slot of preview) {
          if (slot.mesh.userData.previewIds[slot.index] !== entry.key) continue;
          slot.mesh.getMatrixAt(slot.index, actual);
          displayedActorMatrix(entry, expected).multiply(slot.local);
          mismatched ||= differed();
        }
      }
      const status = !entry.pivot.visible ? "hidden" : entry.slots.length ? "detail"
        : preview.length ? "preview" : entry.modelMeta?.status === "box" ? "box" : "deferred";
      placements.push({ id: entry.id, gyaml: entry.gyaml, source: entry.modelMeta?.source || "",
        position: entry.pivot.position.toArray(), rotation: fileRotate(entry.pivot), scale: pivotScale(entry.pivot),
        status, visibleLods: lods, mismatched,
        ...(entry.fishPreviewOffset ? { previewY: entry.pivot.position.y + entry.fishPreviewOffset } : {}) });
    }
    return placements;
  }

  function auditView() {
    camera.updateMatrixWorld();
    const surface = (name, geometry, material, visible, lod) => {
      const view = geometryAttributeView(geometry);
      return {
        name,
        lod,
        indices: view.indexCount,
        attributes: Object.fromEntries(Object.entries(view.attributes).map(([key, attribute]) =>
          [key, { components: attribute.itemSize, count: attribute.count, normalized: attribute.normalized }])),
        material: {
          name: material.name || material.type, textured: Boolean(material.map),
          alphaTest: material.alphaTest, transparent: material.transparent, side: material.side,
        },
        visible,
      };
    };
    const entry = entries.get(selected);
    const detail = (entry?.slots || []).map((slot) => surface(slot.family,
      slot.mesh.userData.pick.geometries[slot.index], slot.mesh.material, slot.shown && slot.mesh.visible, slot.lod));
    const preview = (scenePreview?.placements.get(selected) || []).map(({ mesh, index }) =>
      surface(mesh.userData.previewSurface, mesh.geometry, mesh.material,
        mesh.userData.previewIds[index] === selected && mesh.visible && Boolean(scenePreview.root.parent),
        Number(/^lod(\d+):/.exec(mesh.userData.previewSurface)?.[1] ?? 0)));
    return {
      camera: {
        position: camera.position.toArray(), target: orbit.target.toArray(),
        viewMatrix: camera.matrixWorldInverse.toArray(), projectionMatrix: camera.projectionMatrix.toArray(),
        viewport: [renderer.domElement.width, renderer.domElement.height],
        fov: camera.fov, aspect: camera.aspect, near: camera.near, far: camera.far,
      },
      selected: entry ? { id: entry.id, gyaml: entry.gyaml, detail, preview } : null,
      centerSurface: auditCenterSurface(),
      streaming: { queued: statQueued, loading: statLoading, deferred: statDeferred, backgroundLoading: statBackgroundLoading },
    };
  }

  function auditCenterSurface() {
    const roots = batches.map(({ mesh }) => mesh).filter((mesh) => mesh.visible && mesh.parent);
    if (scenePreview?.root.parent && scenePreview.root.visible) {
      for (const meshes of scenePreview.models.values()) roots.push(...meshes.filter((mesh) => mesh.visible));
    }
    const centerRay = new THREE.Raycaster(undefined, undefined, camera.near, camera.far);
    centerRay.firstHitOnly = true;
    centerRay.setFromCamera(new THREE.Vector2(), camera);
    for (const hit of centerRay.intersectObjects(roots, false)) {
      const entry = entries.get(idOfHit(hit));
      if (!entry?.pivot.visible) continue;
      const preview = hit.object.userData.previewSurface;
      const slot = entry.slots.find(({ mesh, index }) => mesh === hit.object && index === hit.batchId);
      return {
        id: entry.id,
        gyaml: entry.gyaml,
        source: entry.modelMeta?.source || "",
        modelState: entry.modelMeta?.status || "pending",
        reason: entry.modelMeta?.reason || "",
        stage: preview !== undefined ? "preview" : "detail",
        surface: preview ?? slot?.family ?? hit.object.name,
        lod: preview !== undefined ? Number(/^lod(\d+):/.exec(preview)?.[1] ?? 0) : slot?.lod,
        distance: hit.distance,
        point: hit.point.toArray(),
      };
    }
    return null;
  }

  function frame(id) {
    if (id && entries.has(id)) frameObjects([entries.get(id).pivot]);
    else frameObjects([...entries.values()].filter((entry) => entry.pivot.visible).map((entry) => entry.pivot));
  }

  function isCollisionEntry(entry) {
    return actorLayer(entry && entry.gyaml) === "collision";
  }

  function rememberedGround(x, z) {
    return nearestGroundSample(groundHits, x, z, GROUND_SNAP_RADIUS);
  }

  function requestGround(x, z, entry) {
    if (groundSurfaceReady()) return null;
    if (!hooks.fetchGround || !terrainBanc) return null;
    groundWanted = { x, z, banc: terrainBanc, exclude: snapExcludeId(entry), entry };
    if (!groundPump) groundPump = pumpGround();
    return groundPump;
  }

  async function pumpGround() {
    try {
      while (groundWanted) {
        const job = groundWanted;
        groundWanted = null;
        try {
          const data = await hooks.fetchGround(job.banc, job.x, job.z, job.exclude, sceneAbort.signal);
          if (terrainBanc !== job.banc) continue;
          if (Number.isFinite(data && data.worldY)) {
            groundHits.push({ x: job.x, z: job.z, y: data.worldY });
            if (groundHits.length > 200) groundHits.splice(0, groundHits.length - 200);
            if (job.entry) {
              const latest = groundWanted;
              applyComposedSnap(job.entry, job.x, job.z, data.worldY, latest);
            }
          }
        } catch (error) {
          if (error && error.name === "AbortError") continue;
          console.warn("Could not sample the composed ground", error);
        }
      }
    } finally {
      const leftover = groundWanted;
      groundPump = null;
      if (leftover) groundPump = pumpGround();
    }
  }

  function rejectsDraggedHit(hit, ignoreId) {
    if (!ignoreId) return false;
    const dragged = entries.get(ignoreId);
    const ids = [ignoreId];
    if (dragged) ids.push(dragged.id, dragged.key);
    const owned = (value) => ids.some((candidate) => isSameActor(value, candidate));
    const id = idOfHit(hit);
    if (owned(id)) return true;
    const entry = id ? entries.get(id) : null;
    if (entry && (owned(entry.id) || owned(entry.key))) return true;
    // Submeshes, skinned meshes, and proxies parented to the pivot, plus a
    // companion banc copy of the same hash.
    let object = hit && hit.object;
    while (object) {
      const mark = object.userData && (object.userData.actorId || object.userData.snapOwner);
      if (owned(mark)) return true;
      if (dragged && object === dragged.pivot) return true;
      object = object.parent;
    }
    return false;
  }

  function modelSnapRoots() {
    const roots = [];
    for (const batch of batches) {
      if (!batch.mesh || !batch.mesh.visible || batch.water) continue;
      roots.push(batch.mesh);
    }
    if (scenePreview && scenePreview.models) {
      for (const meshes of scenePreview.models.values()) {
        for (const mesh of meshes || []) {
          if (mesh && mesh.visible) roots.push(mesh);
        }
      }
    }
    return roots;
  }

  function surfaceAt(x, y, z, ignoreId) {
    snapRay.ray.origin.set(x, y + 80, z);
    snapRay.ray.direction.set(0, -1, 0);
    const dragged = ignoreId ? entries.get(ignoreId) : null;
    // The dragged mesh, including a Land_ piece and its proxy, is not a hit.
    const exclude = snapExcludeId(dragged) || (ignoreId ? String(ignoreId) : "");
    const ground = [];
    const local = sampleGroundSurface(x, z, exclude);
    if (local != null && Number.isFinite(local)) ground.push({ y: local });
    // A real heightmap is the wrong fallback: it misses island floors, and
    // chooseSnap then prefers the lower slope. The sample pack has no walkable
    // triangles, so the visible stand-in plane is the ground the user sees.
    const pack = groundSurfaceMeta();
    const emptyPack = groundSurfaceReady() && Number(pack && pack.triangles) === 0;
    if (local == null && terrainRoot && (emptyPack || !hooks.fetchGround)) {
      for (const hit of columnHits([terrainRoot], x, y, z)) ground.push({ y: hit.point.y });
    }
    const collision = [];
    for (const candidate of entries.values()) {
      if (!isCollisionEntry(candidate) || !candidate.pivot.visible) continue;
      if (isSnapSelfHit(candidate.id, ignoreId) || isSnapSelfHit(candidate.key, ignoreId)) continue;
      const top = volumeSurfaceY(candidate, snapRay);
      if (top != null && Number.isFinite(top)) collision.push({ y: top });
    }
    // Rooms such as Boss Rush Internal Room have no heightmap. The floor is
    // the static map mesh or a batched instance. A miss stays empty so the
    // actor keeps its height instead of falling to y = 0.
    let models = [];
    if (!ground.length && !collision.length) {
      models = columnHits(modelSnapRoots(), x, y, z, (hit) => !rejectsDraggedHit(hit, ignoreId))
        .map((hit) => ({ y: hit.point.y }));
    }
    let hits = selectSnapHits({ ground, collision, models });
    if (!hits.length && local == null) {
      const remembered = rememberedGround(x, z);
      if (remembered && Number.isFinite(remembered.y)) hits = [{ y: remembered.y }];
    }
    return chooseSnap(y, hits);
  }

  function applySelectedActorTransform(entry, { commit = false } = {}) {
    if (!entry || !entry.pivot) return null;
    // The pivot is the source of truth. The gizmo is attached to it. The
    // visible model is a batched instance (or a preview instance) and does not
    // move with the pivot unless this copy runs. Collision (#10) and
    // decorations (#11) should follow this same pose instead of reading the
    // gizmo or the batch on their own.
    syncPreviewPlacement(entry);
    syncInstanceMatrix(entry);
    const translate = [entry.pivot.position.x, entry.pivot.position.y, entry.pivot.position.z];
    const rotate = fileRotate(entry.pivot);
    const scale = pivotScale(entry.pivot);
    const pose = { translate, rotate, scale, commit };
    for (const follow of actorTransformFollowers) follow(entry, pose);
    if (commit) hooks.onTransform(entry.id, translate, rotate, scale);
    else hooks.onPreview(entry.id, translate, rotate, scale);
    requestRender();
    return pose;
  }

  function applyComposedSnap(entry, requestX, requestZ, worldY, latest) {
    if (!snapOn || verticalHold || !entries.get(entry.id) || !Number.isFinite(worldY)) return false;
    const cur = entry.pivot.position;
    if (!shouldApplyGroundReply(requestX, requestZ, cur.x, cur.z, latest && latest.x, latest && latest.z)) return false;
    const next = surfaceAt(cur.x, worldY, cur.z, snapExcludeId(entry) || entry.id);
    cur.y = next == null ? worldY : next;
    applySelectedActorTransform(entry, { commit: false });
    return true;
  }

  function maybeSnap(entry, phase, axis = null) {
    if (!snapOn || !entry || transform.mode !== "translate") return null;
    const used = axis || (phase === "release" ? dragAxis : null) || transform.axis;
    const p = entry.pivot.position;
    const surfaceY = isExplicitLift(used) ? null : surfaceAt(p.x, p.y, p.z, snapExcludeId(entry));
    const posed = commitTranslateDrag({
      position: p,
      axis: used,
      phase,
      snapOn: true,
      surfaceY,
      write: () => {
        // Compose after the snap writes Y. matrixWorld is stale until then,
        // and the batched instance is not a child of the pivot.
        entry.pivot.updateWorldMatrix(true, false);
        return applyActorMatrix(entry.slots, entry.key, displayedActorMatrix(entry, actorMatrix));
      },
    });
    // The instance write above is synchronous. A ground reply may still move Y
    // later; it goes through applyComposedSnap, which writes the instance again.
    if (posed.hold) verticalHold = true;
    else if (posed.snapped) verticalHold = false;
    if (!posed.snapped) return null;
    return requestGround(p.x, p.z, entry);
  }

  function spawnPoint() {
    const point = [round(orbit.target.x), round(orbit.target.y), round(orbit.target.z)];
    if (!snapOn) return point;
    void requestGround(point[0], point[2], null);
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

  function setPointer(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    const ndc = ndcFromClient(event.clientX, event.clientY, rect);
    pointer.x = ndc.x;
    pointer.y = ndc.y;
    raycaster.setFromCamera(pointer, camera);
    return ndc;
  }

  function warnPickFallback(error) {
    if (pickFallbackWarned) return;
    pickFallbackWarned = true;
    console.warn("GPU picking failed; using the batch mesh on the CPU.", error);
  }

  function aimed(event, includePreview = false) {
    setPointer(event);
    const roots = [];
    for (const batch of batches) roots.push(batch.mesh);
    if (includePreview && scenePreview?.root.parent) {
      for (const meshes of scenePreview.models.values()) roots.push(...meshes);
    }
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
      return hit.object.userData.previewIds?.[index] || null;
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

  function applyHover(key) {
    const next = key || null;
    if (next === hovered) return;
    const previous = hovered ? entries.get(hovered) : null;
    hovered = next;
    if (previous) syncGhost(previous);
    const nextEntry = hovered ? entries.get(hovered) : null;
    if (nextEntry) syncGhost(nextEntry);
    syncLabels();
    requestRender();
  }

  async function groupsAt(event, { preview, peel }) {
    const ndc = setPointer(event);
    try {
      const keys = await gpuPicker.pickIds({ ndcX: ndc.x, ndcY: ndc.y, preview, peel });
      return groupsFromPick(keys, volumeRayHits(raycaster));
    } catch (error) {
      warnPickFallback(error);
      return aimed(event, preview);
    }
  }

  function scheduleHover(event) {
    hoverPending = { clientX: event.clientX, clientY: event.clientY };
    if (hoverScheduled) return;
    hoverScheduled = true;
    hoverFrame = requestAnimationFrame(() => {
      hoverFrame = 0;
      const pending = hoverPending;
      hoverPending = null;
      if (!pending || looking || transform.dragging) {
        hoverScheduled = false;
        if (hoverPending) scheduleHover(hoverPending);
        return;
      }
      const token = ++hoverToken;
      enqueuePick(async () => {
        try {
          const groups = await groupsAt(pending, { preview: false, peel: false });
          if (token === hoverToken) applyHover(chooseHit(groups));
        } finally {
          hoverScheduled = false;
          if (hoverPending) scheduleHover(hoverPending);
        }
      });
    });
  }

  function pick(event) {
    const snapshot = { clientX: event.clientX, clientY: event.clientY, altKey: event.altKey };
    hoverToken += 1;
    enqueuePick(() => pickAt(snapshot));
  }

  async function pickAt(event) {
    const ndc = setPointer(event);
    try {
      const frontKeys = await gpuPicker.pickIds({ ndcX: ndc.x, ndcY: ndc.y, preview: true, peel: false });
      const front = frontKeys[0] || null;
      const repeat = Boolean(
        lastClick && Math.hypot(event.clientX - lastClick.x, event.clientY - lastClick.y) < 6 && lastClick.front === front,
      );
      const peel = Boolean(event.altKey || repeat);
      const keys = peel
        ? await gpuPicker.pickIds({ ndcX: ndc.x, ndcY: ndc.y, preview: true, peel: true })
        : frontKeys;
      const groups = groupsFromPick(keys, volumeRayHits(raycaster));
      lastClick = { x: event.clientX, y: event.clientY, front, signature: signature(groups) };
      hooks.onSelect(chooseHit({ ...groups, selected, alt: event.altKey, repeat }));
    } catch (error) {
      warnPickFallback(error);
      const groups = aimed(event, true);
      const repeat = Boolean(
        lastClick && Math.hypot(event.clientX - lastClick.x, event.clientY - lastClick.y) < 6 && lastClick.signature === signature(groups),
      );
      lastClick = { x: event.clientX, y: event.clientY, front: groups.solids[0] || null, signature: signature(groups) };
      hooks.onSelect(chooseHit({ ...groups, selected, alt: event.altKey, repeat }));
    }
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
      renderer.domElement.setPointerCapture(event.pointerId);
      requestLookLock();
      event.preventDefault();
      return;
    }
    if (event.button !== 0) return;
    lastPointer = { x: event.clientX, y: event.clientY, moved: false };
    gizmoUsed = Boolean(transform.axis);
    if (transform.axis) dragAxis = transform.axis;
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
    scheduleHover(event);
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
    if (sceneStreamStats?.editBytes === null) {
      sceneStreamStats.editBytes = performance.getEntriesByType("resource").reduce((bytes, resource) => {
        const path = new URL(resource.name, location.href).pathname;
        return bytes + (resource.startTime >= sceneStreamStats.start
          && (path.startsWith("/api/models/") || path.startsWith("/api/model-textures/")) ? resource.transferSize : 0);
      }, 0);
    }
    const entry = selected ? entries.get(selected) : null;
    if (!entry) return;
    dragAxis = transform.axis || dragAxis;
    // Record the controls' Y before Snap replaces it. The next pointer sample
    // puts the pivot back here so the drag plane, and therefore X and Z, stay
    // on the handle instead of on the snapped surface.
    if ((transform.axis || dragAxis) !== "Y") dragPlaneY = entry.pivot.position.y;
    applyGridSnap(entry);
    maybeSnap(entry, "move");
    applySelectedActorTransform(entry, { commit: false });
    updateSnapGrid(entry);
  });

  let dragSerial = 0;
  transform.addEventListener("dragging-changed", (event) => {
    orbit.enabled = !event.value;
    if (event.value) {
      gizmoUsed = true;
      dragSerial += 1;
      dragAxis = transform.axis || dragAxis;
      dragPlaneY = null;
      dragRaw = null;
      const started = selected ? entries.get(selected) : null;
      if (started) captureDragOrigin(started);
      else dragOrigin = null;
      return;
    }
    dragRaw = null;
    dragOrigin = null;
    const entry = selected ? entries.get(selected) : null;
    const serial = dragSerial;
    const axis = transform.axis || dragAxis;
    if (!entry || !entry.id) {
      dragAxis = null;
      return;
    }
    dragAxis = axis;
    const pending = maybeSnap(entry, "release", axis);
    // maybeSnap already wrote the instance. Write again before awaiting so a
    // ground reply cannot be the first time the mesh hears about this pose,
    // and so preview placements follow the same pivot.
    applySelectedActorTransform(entry, { commit: false });
    const finish = () => {
      // A newer drag owns the pivot. Committing here would save that pose early.
      if (serial !== dragSerial || transform.dragging) return;
      dragAxis = null;
      dragPlaneY = null;
      applySelectedActorTransform(entry, { commit: true });
    };
    const wait = pending && typeof pending.then === "function"
      ? pending.then(() => {
        if (serial !== dragSerial) return null;
        // A leftover request may have started as the pump finished.
        return groundPump || maybeSnap(entry, "release", axis);
      })
      : null;
    if (wait && typeof wait.then === "function") void wait.then(finish, finish);
    else finish();
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

  function scanTextureDetail() {
    const candidates = new Map();
    const ordered = [];
    for (const entry of entries.values()) {
      if (!entry.parts?.length || !entry.slots.length || !entry.pivot.visible) continue;
      const chosen = entry.key === selected;
      const distance = entry.pivot.position.distanceToSquared(camera.position);
      if (!chosen && (distance > 45 * 45 || !entry.slots.some((slot) => slot.shown))) continue;
      ordered.push({ entry, distance: chosen ? -1 : distance });
    }
    ordered.sort((a, b) => a.distance - b.distance);
    const seen = new Set();
    const inspected = new Set();
    for (const { entry } of ordered) {
      if (inspected.has(entry.parts)) continue;
      if (inspected.size >= 48) break;
      inspected.add(entry.parts);
      for (const { material } of entry.parts) {
        if (seen.has(material)) continue;
        seen.add(material);
        for (const texture of [...Object.values(material), ...Object.values(material.userData)]) {
          const uri = texture?.isTexture && texture.userData.forgeFullTexture;
          if (!uri || textureDetailFailed.has(uri)) continue;
          const targets = candidates.get(uri) || new Set();
          targets.add(texture);
          candidates.set(uri, targets);
        }
      }
    }
    detailCandidates = candidates;
  }

  function installTextureDetail(texture, full) {
    // WebGL tracks allocations by Source. Release the low allocation first so
    // replacing it with a larger Source cannot reuse a texture of the old size.
    texture.dispose();
    texture.source = full.source;
    texture.mipmaps = full.mipmaps;
    texture.format = full.format;
    texture.type = full.type;
    texture.isCompressedTexture = Boolean(full.isCompressedTexture);
    if (texture.isCompressedTexture) {
      repairCompressedTexture(texture);
    } else {
      texture.generateMipmaps = true;
      texture.needsUpdate = true;
    }
    delete texture.userData.forgeFullTexture;
    textureCpuReleaseDirty = true;
  }

  function updateTextureDetail(now, parsedDetail) {
    // Keep the bandwidth and decode slots available to nearby geometry and
    // its first texture tier. Sharpen the textures once that work drains.
    const geometryBusy = Boolean(statQueued || statLoading || modelStage?.pending.size);
    if (geometryBusy && !textureDetailReady.size) return;
    if (now >= nextDetailScan) {
      nextDetailScan = now + 160;
      scanTextureDetail();
    }
    let applied = parsedDetail;
    for (const [uri, textures] of detailCandidates) {
      const ready = textureDetailReady.get(uri);
      if (ready) {
        if (applied) {
          requestRender();
          continue;
        }
        applied = true;
        detailCandidates.delete(uri);
        textureDetailReady.delete(uri);
        for (const texture of textures) installTextureDetail(texture, ready);
        continue;
      }
      if (geometryBusy || textureDetailPending.size >= 2 || textureDetailPending.has(uri) || textureDetailFailed.has(uri)) continue;
      const imageLoader = textureDetailLoaders.get(uri);
      if (!imageLoader) continue;
      textureDetailPending.add(uri);
      sharedImage(uri, () => loadDetailImage(uri, imageLoader, true)).then((texture) => {
        textureDetailReady.set(uri, texture);
        if (textureDetailReady.size > 64) {
          const first = textureDetailReady.keys().next().value;
          const evicted = textureDetailReady.get(first);
          textureDetailReady.delete(first);
          if (first !== uri) disposeCachedTexture(evicted);
        }
      }).catch(() => {
        textureDetailFailed.add(uri);
      }).finally(() => {
        textureDetailPending.delete(uri);
        requestRender();
      });
    }
  }

  function updateFly(dt) {
    const speed = (keys.has("shift") || keys.has("x") ? 32 : 10) * dt;
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
  let statDeferred = 0;
  let statBackgroundLoading = 0;
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

  function profileCounters() {
    const info = (renderer.info && renderer.info.render) || {};
    const webgpu = Boolean(renderer.isWebGPURenderer);
    const subDraws = renderer.backend && renderer.backend.isWebGPUBackend === true
      ? batches.reduce((sum, batch) => sum + (batch.mesh._multiDrawCount || 0), 0)
      : 0;
    const drawCalls = webgpu ? Number(info.drawCalls) || 0 : Number(info.calls) || 0;
    const batchCount = batches.length;
    return {
      objects: webgpu ? Math.max(0, drawCalls - batchCount) : drawCalls,
      batches: batchCount,
      subDraws,
      draws: drawTotal(info, webgpu, subDraws),
      triangles: Number(info.triangles) || 0,
    };
  }

  function profileTextureCount() {
    const materials = [];
    for (const batch of batches) {
      const material = batch.mesh && batch.mesh.material;
      if (Array.isArray(material)) materials.push(...material);
      else materials.push(material);
    }
    if (terrainRoot) {
      terrainRoot.traverse((child) => {
        if (!child.material) return;
        if (Array.isArray(child.material)) materials.push(...child.material);
        else materials.push(child.material);
      });
    }
    return countTextures(materials);
  }

  function profileGpuState() {
    if (!frameTimer.supported || frameTimer.mode === "none") return "unsupported";
    if (frameTimer.disjoint()) return "disjoint";
    if (frameTimer.latest() == null) return "pending";
    return "ready";
  }

  function profileSnapshot() {
    const summary = profileWindow.summary();
    const gpuState = profileGpuState();
    const latest = profileWindow.latest();
    return {
      enabled: showProfile,
      gpuTiming: frameTimer.mode,
      gpuState,
      cpuMs: lastCpu ? lastCpu.cpuMs : null,
      updateMs: lastCpu ? lastCpu.updateMs : null,
      cullMs: lastCpu ? lastCpu.cullMs : null,
      submitMs: lastCpu ? lastCpu.submitMs : null,
      gpuMs: frameTimer.latest(),
      objects: latest ? latest.objects : null,
      batches: latest ? latest.batches : null,
      subDraws: latest ? latest.subDraws : null,
      summary,
      recommendation: recommendNext(summary, { gpuState }),
    };
  }

  function paintProfile() {
    if (!showProfile) return;
    const snapshot = profileSnapshot();
    const latest = profileWindow.latest() || {};
    profileText.textContent = formatProfileHud({
      backend: backendName,
      revision: buildStamp,
      mode: frameTimer.mode,
      gpuState: snapshot.gpuState,
      latest: { ...latest, gpuMs: frameTimer.latest() },
      summary: snapshot.summary,
      recommendation: snapshot.recommendation,
      detail: profileDetail(),
    });
  }

  function profileDetail() {
    const backgroundLoading = statBackgroundLoading;
    return {
      rendering: lastRendering,
      fps,
      instances: statInstances,
      culled: statCulled,
      separate: naiveDraws,
      queued: statQueued,
      loading: statLoading,
      loaded: statLoaded,
      deferred: statDeferred,
      backgroundLoading,
      convertMs: statConvert,
      textureMs: statTexture,
      downloadMs: statDownload,
      uploadMs: statUpload,
      warmDone: statWarmDone,
      warmTotal: statWarmTotal,
      sceneStream: sceneStreamStats,
      server: serverHead,
    };
  }

  function dumpProfile() {
    return buildCapture({
      backend: backendName,
      revision: buildStamp,
      gpuMode: frameTimer.mode,
      gpuState: profileGpuState(),
      samples: profileWindow.samples(),
      server: serverHead ? { head: serverHead, lines: serverLines.slice() } : null,
    });
  }

  function publishProfile() {
    const capture = dumpProfile();
    const json = JSON.stringify(capture, null, 2);
    console.info(json);
    const clipboard = globalThis.navigator && navigator.clipboard;
    if (clipboard && typeof clipboard.writeText === "function") {
      clipboard.writeText(json).catch(() => {});
    }
    return capture;
  }

  frameTimer.onResult((ms) => {
    const list = profileWindow.samples();
    for (let index = list.length - 1; index >= 0; index -= 1) {
      if (list[index].gpuMs == null) {
        list[index].gpuMs = ms;
        break;
      }
    }
    if (showProfile) paintFrame(false, false);
  });

  function pushProfileSample() {
    const counters = profileCounters();
    const heapBytes = (typeof performance !== "undefined" && performance.memory && performance.memory.usedJSHeapSize) || null;
    profileWindow.push({
      cpuMs: lastCpu.cpuMs,
      updateMs: lastCpu.updateMs,
      cullMs: lastCpu.cullMs,
      submitMs: lastCpu.submitMs,
      gpuMs: null,
      frameMs,
      fps,
      objects: counters.objects,
      batches: counters.batches,
      subDraws: counters.subDraws,
      draws: counters.draws,
      triangles: counters.triangles,
      meshes: residentMeshNames().size,
      textures: profileTextureCount(),
      heapBytes,
    });
  }

  function paintFrame(rendering, record = true) {
    lastRendering = Boolean(rendering);
    if (record) {
      const now = performance.now();
      frameTimes.push(now);
      while (frameTimes.length && now - frameTimes[0] > 500) frameTimes.shift();
      if (frameTimes.length >= 4) {
        fps = Math.round(((frameTimes.length - 1) * 1000) / (now - frameTimes[0]));
      }
      if (showProfile && lastCpu) pushProfileSample();
    }
    const info = renderer.info && renderer.info.render;
    const draws = drawTotal(
      info,
      Boolean(renderer.isWebGPURenderer),
      renderer.backend && renderer.backend.isWebGPUBackend === true
        ? batches.reduce((sum, batch) => sum + (batch.mesh._multiDrawCount || 0), 0)
        : 0,
    );
    const background = (statDeferred || 0) + (statBackgroundLoading || 0);
    statusEl.textContent = formatStatusLabel({
      backend: backendName,
      build: buildStamp,
      draws,
      queued: statQueued,
      loading: statLoading,
      background,
      texturesDone: statTexturesDone,
      texturesTotal: statTexturesTotal,
      layersOff: statLayersOff,
      memory: {
        geometries: renderer.info && renderer.info.memory ? renderer.info.memory.geometries : null,
        textures: renderer.info && renderer.info.memory ? renderer.info.memory.textures : null,
        heapBytes: (typeof performance !== "undefined" && performance.memory && performance.memory.usedJSHeapSize) || null,
      },
    });
    if (showProfile) paintProfile();
    if (hooks.onFrame) hooks.onFrame();
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
    const wasInteractive = streamInteractive;
    streamInteractive = Boolean(flying || damped || looking || orbitGesture || transform.dragging);
    if (wasInteractive !== streamInteractive) modelLoads?.background();
    if (sceneStreamStats && wasInteractive && streamInteractive && (statLoading || modelStage?.pending.size || textureDetailPending.size) && gap > 25) {
      sceneStreamStats.slowFrames += 1;
    }
    streamFrame += 1;
    camera.getWorldDirection(loadForward);
    const streaming = statLoading > 0 || statQueued > 0 || pendingLoads > 0 || statBackgroundLoading > 0
      || (modelStage && modelStage.pending && modelStage.pending.size > 0);
    if (sceneStreamStats && streaming && gap > (sceneStreamStats.maxFrameMs || 0)) {
      sceneStreamStats.maxFrameMs = Math.round(gap * 10) / 10;
    }
    if (now - lastCullAt > 100
        && (camera.position.distanceToSquared(lastCull) > 1 || lastLoadDirection.dot(loadForward) < 0.998)) {
      lastCullAt = now;
      lastCull.copy(camera.position);
      lastLoadDirection.copy(loadForward);
      syncLabels();
    }
    if (!priorityAnchored || cameraNeedsPriorityUpdate(
      { position: priorityPos, direction: priorityDir },
      { position: camera.position, direction: loadForward },
    )) {
      priorityPos.copy(camera.position);
      priorityDir.copy(loadForward);
      priorityAnchored = true;
      prioritizeLoad();
    }
    const updateMs = performance.now() - tickAt;
    const cullStart = performance.now();
    syncBatchVisibility();
    const cullMs = performance.now() - cullStart;
    const tailStart = performance.now();
    const parseDetail = detailParses.shift();
    if (parseDetail) parseDetail();
    if (detailParses.length) requestRender();
    updateTextureDetail(now, Boolean(parseDetail));
    guardMissingUv();
    const entry = selected ? entries.get(selected) : null;
    if (entry) {
      const distance = camera.position.distanceTo(entry.pivot.position);
      transform.size = THREE.MathUtils.clamp(distance / 14, 0.55, 2.4);
    }
    const updateTail = performance.now() - tailStart;
    if (renderer.isWebGPURenderer && renderer.info && typeof renderer.info.reset === "function") renderer.info.reset();
    const profiling = showProfile;
    let submitMs = 0;
    const submitStart = performance.now();
    if (profiling) frameTimer.beginFrame();
    try {
      renderer.render(scene, camera);
    } finally {
      if (profiling) frameTimer.endFrame();
      submitMs = performance.now() - submitStart;
    }
    const afterStart = performance.now();
    flushTextureCpuRelease();
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
    const extra = performance.now() - afterStart;
    const roundMs = (value) => Math.max(0, Math.round(value * 100) / 100);
    lastCpu = {
      updateMs: roundMs(updateMs + updateTail + extra),
      cullMs: roundMs(cullMs),
      submitMs: roundMs(submitMs),
      cpuMs: roundMs(updateMs + updateTail + cullMs + submitMs + extra),
    };
    const again = keepRendering({
      flying,
      damped,
      looking,
      dragging: transform.dragging,
    });
    if (again && gap > 0 && gap < 250) frameMs = Math.round(gap * 10) / 10;
    paintFrame(again);
    if (again) requestRender();
    else if (showProfile && frameTimer.mode === "webgl" && frameTimer.latest() == null) scheduleGpuPoll();
  }

  function scheduleGpuPoll() {
    if (gpuPoll || !running || !showProfile) return;
    let left = 8;
    const step = () => {
      gpuPoll = 0;
      if (!running || !showProfile) return;
      frameTimer.poll();
      left -= 1;
      if (left > 0 && frameTimer.latest() == null) gpuPoll = requestAnimationFrame(step);
    };
    gpuPoll = requestAnimationFrame(step);
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
    maybeRelight();
    requestRender();
  });
  orbit.addEventListener("change", () => requestRender());
  requestRender();


  function currentDraws() {
    const info = renderer.info && renderer.info.render;
    return drawTotal(
      info,
      Boolean(renderer.isWebGPURenderer),
      renderer.backend && renderer.backend.isWebGPUBackend === true
        ? batches.reduce((sum, batch) => sum + (batch.mesh._multiDrawCount || 0), 0)
        : 0,
    );
  }

  function getMemoryReport() {
    const report = collectMemoryReport({
      renderer,
      entries,
      batches,
      parsedTemplates,
      terrainRoot,
      stats: {
        fps,
        frameMs,
        draws: currentDraws(),
        instances: statInstances,
        culled: statCulled,
        cpuMs: lastCpu ? lastCpu.cpuMs : null,
        updateMs: lastCpu ? lastCpu.updateMs : null,
        cullMs: lastCpu ? lastCpu.cullMs : null,
        submitMs: lastCpu ? lastCpu.submitMs : null,
        gpuMs: frameTimer.latest(),
        objects: profileWindow.latest() ? profileWindow.latest().objects : null,
        batchCount: profileWindow.latest() ? profileWindow.latest().batches : null,
        subDraws: profileWindow.latest() ? profileWindow.latest().subDraws : null,
        profile: profileSnapshot(),
        wasmSurfaceMB: (() => {
          const surface = groundSurfaceMeta();
          return surface && surface.memoryBytes ? surface.memoryBytes / 1048576 : 0;
        })(),
      },
    });
    report.load = {
      queued: statQueued,
      loading: statLoading,
      loaded: statLoaded,
      deferred: statDeferred,
      backgroundLoading: statBackgroundLoading,
    };
    report.backend = backendName;
    report.gpuCaps = gpuCaps;
    const pressure = residentPressure();
    report.resident = {
      uniqueMeshes: pressure.count,
      heapBytes: pressure.heap,
      peakHeapBytes,
      geometryBytes: pressure.geometryBytes,
      bc7Bytes: pressure.bc7Bytes,
      assetBytes: pressure.assetBytes,
      byteSoft: RESIDENT_BYTE_SOFT,
      byteTarget: RESIDENT_BYTE_TARGET,
      countSoft: RESIDENT_MODEL_SOFT_CAP,
      evictions: statEvictions,
      reloads: statReloads,
      cooldown: unloadAfter.size,
      softCapped: softCapped(pressure),
      hardCapped: hardCapped(pressure),
    };
    report.stream = sceneStreamStats ? {
      maxFrameMs: sceneStreamStats.maxFrameMs || 0,
      slowFrames: sceneStreamStats.slowFrames || 0,
      firstDetailMs: sceneStreamStats.firstDetail,
      compileMs: sceneStreamStats.compileMs || 0,
      maxCompileMs: sceneStreamStats.maxCompileMs || 0,
      compileBatches: sceneStreamStats.compileBatches || 0,
    } : null;
    return report;
  }

  globalThis.__forgeProfile = dumpProfile;
  if (import.meta.env && import.meta.env.DEV) {
    globalThis.__forgeSnapDrag = (axis, y, x) => {
      const entry = selected ? entries.get(selected) : null;
      if (!entry) return null;
      if (Number.isFinite(y)) entry.pivot.position.y = y;
      if (Number.isFinite(x)) entry.pivot.position.x = x;
      dragAxis = axis;
      const pending = maybeSnap(entry, "release", axis);
      applySelectedActorTransform(entry, { commit: false });
      const probe = new THREE.Matrix4();
      const reads = (entry.slots || []).map((slot) => {
        let gpuY = null;
        if (slot.mesh?.getMatrixAt) {
          slot.mesh.getMatrixAt(slot.index, probe);
          gpuY = probe.elements[13];
        }
        return { index: slot.index, shown: slot.shown, gpuY, cpuY: slot.matrix ? slot.matrix.elements[13] : null };
      });
      const canvas = renderer.domElement;
      const project = (offsetY) => {
        const world = entry.pivot.getWorldPosition(new THREE.Vector3());
        world.y += offsetY;
        world.project(camera);
        return {
          x: (world.x * 0.5 + 0.5) * canvas.width,
          y: (-world.y * 0.5 + 0.5) * canvas.height,
        };
      };
      return {
        id: entry.id,
        gyaml: entry.gyaml,
        pivotX: entry.pivot.position.x,
        pivotY: entry.pivot.position.y,
        pivotZ: entry.pivot.position.z,
        reads,
        pending: Boolean(pending && pending.then),
        base: project(0.4),
        width: canvas.width,
        height: canvas.height,
      };
    };
    globalThis.__forgePose = () => {
      const entry = selected ? entries.get(selected) : null;
      if (!entry) return null;
      const probe = new THREE.Matrix4();
      const reads = (entry.slots || []).map((slot) => {
        let gpuY = null;
        if (slot.mesh?.getMatrixAt) {
          slot.mesh.getMatrixAt(slot.index, probe);
          gpuY = probe.elements[13];
        }
        return { index: slot.index, shown: slot.shown, gpuY, cpuY: slot.matrix ? slot.matrix.elements[13] : null };
      });
      return { pivotX: entry.pivot.position.x, pivotY: entry.pivot.position.y, pivotZ: entry.pivot.position.z, reads };
    };
    // Screen point on the positive translate handle, for a real pointer drag.
    globalThis.__forgeHandleClient = (axis) => {
      const picker = transform._gizmo && transform._gizmo.picker && transform._gizmo.picker.translate;
      if (!picker) return null;
      picker.updateWorldMatrix(true, true);
      let best = null;
      picker.traverse((object) => {
        if (object.name !== axis || !object.geometry) return;
        if (!object.geometry.boundingBox) object.geometry.computeBoundingBox();
        const center = object.geometry.boundingBox.clone().applyMatrix4(object.matrixWorld).getCenter(new THREE.Vector3());
        const score = axis === "X" ? center.x : axis === "Z" ? center.z : center.y;
        if (!best || score > best.score) best = { point: center, score };
      });
      if (!best) return null;
      const canvas = renderer.domElement;
      const rect = canvas.getBoundingClientRect();
      best.point.project(camera);
      return {
        x: rect.left + (best.point.x * 0.5 + 0.5) * rect.width,
        y: rect.top + (-best.point.y * 0.5 + 0.5) * rect.height,
      };
    };
  }
  if (import.meta.env && import.meta.env.DEV) {
    globalThis.__forgeMemory = getMemoryReport;
  }

  return {
    backend: backendName,
    backendNote,
    backendReason,
    setActors,
    patchActors,
    reconcileActors,
    dropTerrain,
    ensureTerrain,
    setLinks,
    setLoadState,
    setTextureShown,
    setTextureLoad,
    setSnap(on) {
      snapOn = Boolean(on);
    },
    setGizmoSpace(space) {
      gizmoSpace = normalizeSpace(space);
      transform.setSpace(spaceForMode(transform.mode, gizmoSpace));
      updateSnapGrid(selected ? entries.get(selected) : null);
      requestRender();
    },
    setGridSnap(settings) {
      gridSettings = normalizeGridSettings(settings);
      updateSnapGrid(selected ? entries.get(selected) : null);
      requestRender();
    },
    setSnapModifier(held) {
      const next = Boolean(held);
      if (next === ctrlSnap) return;
      ctrlSnap = next;
      if (transform.dragging) reapplyDragSnap();
    },
    followActorTransform(follow) {
      if (typeof follow !== "function") return () => {};
      actorTransformFollowers.push(follow);
      return () => {
        const index = actorTransformFollowers.indexOf(follow);
        if (index >= 0) actorTransformFollowers.splice(index, 1);
      };
    },
    select,
    setGizmo,
    setFlags,
    applyLighting,
    lightingStatus() {
      return lastLightingNote;
    },
    modelInfo,
    auditPlacements,
    auditView,
    shaderDiagnostics,
    frame,
    spawnPoint,
    resize,
    setConvertSlots(count) {
      const parsed = Number(count);
      const cores = typeof navigator !== "undefined" && navigator.hardwareConcurrency ? navigator.hardwareConcurrency - 1 : 8;
      const wanted = Number.isFinite(parsed) && parsed > 0 ? parsed : cores;
      convertSlots = Math.max(1, Math.min(16, wanted));
    },
    setWarm(status) {
      const total = status && Number(status.total);
      statWarmDone = total ? Number(status.done) || 0 : null;
      statWarmTotal = total || null;
      requestRender();
    },
    getMemoryReport,
    unloadScene,
    setServerStatus(status) {
      serverHead = status && status.head ? String(status.head) : "";
      serverLines = status && Array.isArray(status.lines) ? status.lines.slice() : [];
      if (showProfile) paintProfile();
    },
    setShowProfile(on) {
      const next = Boolean(on);
      if (next && !showProfile) profileWindow.clear();
      showProfile = next;
      frameTimer.setEnabled(next);
      profileEl.hidden = !next;
      if (!next && gpuPoll) {
        cancelAnimationFrame(gpuPoll);
        gpuPoll = 0;
      }
      if (next) {
        paintProfile();
        requestRender();
      }
    },
    destroy() {
      running = false;
      unloadScene();
      hoverToken += 1;
      if (hoverFrame) cancelAnimationFrame(hoverFrame);
      if (rebuildTimer) clearTimeout(rebuildTimer);
      if (frameHandle) cancelAnimationFrame(frameHandle);
      gpuPicker.dispose();
      if (gpuPoll) cancelAnimationFrame(gpuPoll);
      if (keyWindow) {
        keyWindow.removeEventListener("keydown", onFlyKey);
        keyWindow.removeEventListener("keyup", onFlyUp);
        keyWindow.removeEventListener("blur", onFlyBlur);
      }
      observer.disconnect();
      frameTimer.dispose();
      statusEl.remove();
      profileEl.remove();
      observer.disconnect();
      pondProbe.material.dispose();
      clearGrid();
      boxGeometry.dispose();
      edgesGeometry.dispose();
      effectGizmoGeometry.dispose();
      effectGizmoMaterial.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      labels.domElement.remove();
      if (globalThis.__forgeMemory === getMemoryReport) delete globalThis.__forgeMemory;
      if (globalThis.__forgeProfile === dumpProfile) delete globalThis.__forgeProfile;
    },
  };
}

function isTyping(event) {
  const tag = event.target && event.target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (event.target && event.target.isContentEditable);
}
