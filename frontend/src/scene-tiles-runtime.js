/**
 * 3D Tiles for one baked scene. Imported only after the tiles toggle is on.
 *
 * The glTF loader is ours. `GLTFExtensionsPlugin` always imports the mesh
 * features reader, and that file constructs a WebGLRenderer. The WebGPU build
 * of three does not export that class, so the production build fails. Feature
 * ids stay ordinary `_feature_id_0` attributes. The pick pass reads them
 * directly and does not use the metadata extension.
 */

const TILE_CACHE_BYTES = 2 * 1024 ** 3;
const GLTF_FILE = /\.(gltf|glb)$/i;

export async function createTileLayer({ url, scene, camera, renderer, pickLayer, prepare }) {
  const { TilesRenderer } = await import("3d-tiles-renderer/three");
  const { LRUCache } = await import("3d-tiles-renderer/core");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
  const { KTX2Loader } = await import("three/addons/loaders/KTX2Loader.js");
  const { MeshoptDecoder } = await import("meshoptimizer/decoder");
  await MeshoptDecoder.ready;
  const tiles = new TilesRenderer(url);
  const cache = new LRUCache();
  cache.minBytesSize = TILE_CACHE_BYTES / 2;
  cache.maxBytesSize = TILE_CACHE_BYTES;
  tiles.lruCache = cache;
  const releaseLoaders = installTileLoaders(tiles, MeshoptDecoder, renderer);
  tiles.setCamera(camera);
  tiles.setResolutionFromRenderer(camera, renderer);
  tiles.addEventListener("load-model", (event) => {
    const model = event.scene;
    if (!model) return;
    model.userData.forgeTileUrl = String(event.url || "");
    model.traverse((child) => {
      if (!child.isMesh) return;
      if (pickLayer != null && child.layers && typeof child.layers.enable === "function") {
        child.layers.enable(pickLayer);
      }
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) {
        if (material && prepare) prepare(material, child);
      }
    });
  });
  scene.add(tiles.group);
  return {
    group: tiles.group,
    update(nextCamera, nextRenderer) {
      tiles.setCamera(nextCamera);
      tiles.setResolutionFromRenderer(nextCamera, nextRenderer);
      tiles.update();
    },
    stats() {
      const stats = tiles.stats || {};
      const cachedBytes = tiles.lruCache && tiles.lruCache.cachedBytes ? tiles.lruCache.cachedBytes : 0;
      return {
        loaded: stats.loaded || 0,
        visible: stats.visible || 0,
        cachedBytes,
      };
    },
    hide(uris) {
      const opened = uris || [];
      tiles.group.traverse((child) => {
        const tileUrl = child.userData && child.userData.forgeTileUrl;
        if (!tileUrl) return;
        child.visible = !opened.some((uri) => tileUrl.endsWith(uri));
      });
    },
    dispose() {
      releaseLoaders();
      tiles.dispose();
      tiles.group.removeFromParent();
    },
  };
}

function installTileLoaders(tiles, meshoptDecoder, renderer) {
  const loader = new GLTFLoader(tiles.manager);
  loader.setMeshoptDecoder(meshoptDecoder);
  const ktx2 = new KTX2Loader(tiles.manager).setTranscoderPath("/basis/");
  try {
    if (renderer && typeof ktx2.detectSupport === "function") ktx2.detectSupport(renderer);
  } catch {
    // detectSupport reads device features. Geometry still parses if it throws.
  }
  loader.setKTX2Loader(ktx2);
  tiles.manager.addHandler(GLTF_FILE, loader);
  return () => {
    tiles.manager.removeHandler(GLTF_FILE);
    if (typeof ktx2.dispose === "function") ktx2.dispose();
  };
}
