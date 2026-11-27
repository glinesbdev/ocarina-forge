/**
 * 3D Tiles for one baked scene. Imported only after the tiles toggle is on.
 * The core renderer and the glTF extension plugin are the only registrations.
 * Batching, fade, and metadata plugins are WebGL and stay out.
 */

const TILE_CACHE_BYTES = 2 * 1024 ** 3;

export async function createTileLayer({ url, scene, camera, renderer, pickLayer, prepare }) {
  const { TilesRenderer } = await import("3d-tiles-renderer/src/three/renderer/tiles/TilesRenderer.js");
  const { GLTFExtensionsPlugin } = await import("3d-tiles-renderer/src/three/plugins/GLTFExtensionsPlugin.js");
  const { LRUCache } = await import("3d-tiles-renderer/src/core/renderer/utilities/LRUCache.js");
  const { MeshoptDecoder } = await import("meshoptimizer/decoder");
  await MeshoptDecoder.ready;
  const tiles = new TilesRenderer(url);
  const cache = new LRUCache();
  cache.minBytesSize = TILE_CACHE_BYTES / 2;
  cache.maxBytesSize = TILE_CACHE_BYTES;
  tiles.lruCache = cache;
  tiles.registerPlugin(new GLTFExtensionsPlugin({ meshoptDecoder: MeshoptDecoder }));
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
      tiles.dispose();
      tiles.group.removeFromParent();
    },
  };
}
