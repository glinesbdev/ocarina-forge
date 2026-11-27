/**
 * 3D Tiles for one baked scene. Imported only after the tiles toggle is on.
 *
 * The glTF loader is ours. `GLTFExtensionsPlugin` always imports the mesh
 * features reader, and that file constructs a WebGLRenderer. The WebGPU build
 * of three does not export that class, so the production build fails. Feature
 * ids stay ordinary `_feature_id_0` attributes. The pick pass reads them
 * directly and does not use the metadata extension.
 *
 * GLTFLoader, KTX2Loader, and MeshoptDecoder are imported here, in the module
 * that constructs them. A dynamic import inside another function does not
 * bind those names for `installTileLoaders`.
 */

import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { MeshoptDecoder } from "meshoptimizer/decoder";

const TILE_CACHE_BYTES = 2 * 1024 ** 3;
const GLTF_FILE = /\.(gltf|glb)$/i;

/**
 * A fresh LRUCache leaves `unloadPriorityCallback` null. TilesRenderer sorts
 * the download queue with that callback and throws if it is null.
 */
export function tileCache(LRUCache, previous) {
  const cache = new LRUCache();
  cache.minBytesSize = TILE_CACHE_BYTES / 2;
  cache.maxBytesSize = TILE_CACHE_BYTES;
  const callback = previous && previous.unloadPriorityCallback;
  cache.unloadPriorityCallback = typeof callback === "function" ? callback : (a, b) => 0;
  return cache;
}

export async function createTileLayer({ url, scene, camera, renderer, pickLayer, prepare, onModel }) {
  const { TilesRenderer } = await import("3d-tiles-renderer/three");
  const { LRUCache } = await import("3d-tiles-renderer/core");
  await MeshoptDecoder.ready;
  const tiles = new TilesRenderer(url);
  let releaseLoaders = () => {};
  try {
    const cache = tileCache(LRUCache, tiles.lruCache);
    tiles.lruCache = cache;
    releaseLoaders = installTileLoaders(tiles, renderer);
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
      if (onModel) onModel(String(event.url || ""));
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
  } catch (error) {
    try { releaseLoaders(); } catch { /* the loader setup is what failed */ }
    try { tiles.dispose(); } catch { /* the renderer was never attached */ }
    if (tiles.group && tiles.group.parent) tiles.group.removeFromParent();
    throw error;
  }
}

export function installTileLoaders(tiles, renderer) {
  const loader = new GLTFLoader(tiles.manager);
  loader.setMeshoptDecoder(MeshoptDecoder);
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
