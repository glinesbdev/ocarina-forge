# Tiles feasibility

Verdict: **go with changes**. The core of `3d-tiles-renderer` 0.5.4 runs on three 0.187.0 `WebGPURenderer` if the batching plugin is left out. The batching plugin does not. Those are different code paths, and the headless run below tested them separately.

This is a feasibility note for issue #5. The scripts under `tools/spike-*.mjs` are spikes. They use synthetic geometry. This machine has no game dump and no `/dev/dri`, so nothing here is a Field measurement.

Library audit is tag `v0.5.4` (`782613cf`). three line numbers are `0.187.0` as installed for the spike.

## 1. Core TilesRenderer on WebGPU

The core is `TilesRenderer` (`src/three/renderer/tiles/TilesRenderer.js`) on `TilesRendererBase` (`src/core/renderer/tiles/TilesRendererBase.js`), the LRU (`src/core/renderer/utilities/LRUCache.js`), and glTF loading through three's `GLTFLoader` plus `GLTFExtensionsPlugin` (KTX2 and meshopt). The three examples do not show this. `example/three/index.js:130` constructs a `WebGLRenderer`. There is no `WebGPURenderer` under `example/three/`. That is an example gap. It is not a WebGL call in the core.

`node tools/spike-3dtiles-webgpu.mjs` loaded a two-level tileset in headless Chrome (SwiftShader, `--enable-unsafe-webgpu --use-angle=swiftshader --enable-unsafe-swiftshader --enable-features=Vulkan`). The page import map points `three` at `three.module.js` and `three/webgpu` at `three.webgpu.js`. `THREE.Mesh === WebGPU.Mesh` was true. The renderer reported `WebGPUBackend`, `isWebGPUBackend: true`, and `texture-compression-bc` and `texture-compression-astc` both true. `TilesRenderer.setResolutionFromRenderer` returned true. That method only calls `renderer.getSize` (`TilesRenderer.js:414-418`).

The tileset is `REPLACE`. The coarse tile is geometric error 8 and an unlit red meshopt glB (`plain.glb`, feature id 42). The leaf is geometric error 0 and an unlit meshopt glB whose base color is white times a 4×4 BC7 KTX2 (`ktx.glb`, feature id 7). `errorTarget` was 16. The tileset URL was `/tiles/tileset.json`, which has a directory. The server log was `GET /tiles/tileset.json`, then `/tiles/plain.glb`, then `/tiles/ktx.glb`.

| Check | Result |
| --- | --- |
| Far camera, z = 120 | Only `plain` was in the tiles group. `ktx.glb` had not been requested. `stats.loaded` 1, `failed` 0, `visible` 1, cache 52 bytes. |
| Near camera, z = 2.5 | Only `ktx` was in the tiles group (`REPLACE`). Both files were cached. `stats.loaded` 2, `failed` 0, `visible` 1, `used` 3, cache 120 bytes. |
| Materials | Both `MeshBasicMaterial` (KHR unlit). `extras.forge` was `plain` and `ktx`. |
| meshopt | Both glBs were written with `EXT_meshopt_compression`. Both parsed. `_feature_id_0` was 42 and 7. meshopt's quantize logged `Skipping _FEATURE_ID_0; out of [-1,1] range.` and left the values in place. |
| KTX2 | The leaf material had `map.isCompressedTexture` true and `map.format` 36492, which is three's `RGBA_BPTC_Format` (`constants.js`). Raw BC7 uses the WebGPU branch of `KTX2Loader.detectSupport` (`KTX2Loader.js:239-251`), which reads `renderer.hasFeature('texture-compression-bc')`. |
| Draw | The first renders of the tiles scene recorded 0 draw calls, and Chrome logged `OperationError: Instance dropped in popErrorScope`. The next render of that same scene recorded 2 draw calls and 3 triangles. The mesh has 4 vertices. |
| LRU | After the near view, `maxBytesSize` was set to 1 and `minBytesSize` to 0, the camera looked away, and `update` ran. After a 50 ms turn, `cachedBytes` was 0. It had been 120. The default cap is `0.4 * 2^30` bytes (`LRUCache.js:89`, the comment says ~430MB). |
| Feature-id helper | `mesh.userData.meshFeatures.getFeatures` returned `[7]`. That call still constructs a `WebGLRenderer`. See the list below. Do not use it for Forge picking. |

Pixel readback did not work on this SwiftShader. `readRenderTargetPixelsAsync` failed with `mapAsync` / `A valid external Instance reference no longer exists`. The color of the tile was not read. The draw evidence is `renderer.info.render` on the second pass.

During those tile renders, three's geometry upload threw `createBuffer failed, size (32) is too large ... mappedAtCreation == true` for usage 44 (`VERTEX | COPY_SRC | COPY_DST`) and the same for size 12 usage 28 (`INDEX | COPY_SRC | COPY_DST`). three r187 sets those usages at `three.webgpu.js:51496` and `:51515`. The same sizes and usages succeeded when the page created them directly, before any tile render (`bufferProbe`: `geometry 32:ok`, `index 12:ok`). A plain `MeshBasicMaterial` plane, uploaded by three itself, drew 2 calls and 3 triangles before the tileset ran. The spike then caught the later `mappedAtCreation` throw and copied with `queue.writeBuffer`, and the following tiles-scene render submitted the 2 calls. That copy is test-harness code in the page. It is not a patch to `3d-tiles-renderer`, and this VM has no hardware GPU to compare. A desktop WebGPU run was not done here.

## WebGL call sites

Searched `src/three` of v0.5.4 for `WebGLRenderer`, `getContext(`, `getParameter`, `ShaderMaterial`, `WebGLRenderTarget`, `WebGLArrayRenderTarget`, and `onBeforeCompile`. Core traversal, the LRU, and `GLTFExtensionsPlugin`'s loader wiring are not in this list. `onBeforeCompile` is GLSL injection. WebGPU does not run it. An earlier headless run logged `NodeBuilder: Material "ShaderMaterial" is not compatible.` from `three.webgpu.js:16027`.

These throw or build a WebGL resource:

| File | Line | What it does |
| --- | --- | --- |
| `src/three/plugins/batched/BatchedTilesPlugin.js` | 61 | `renderer.getContext()` |
| `src/three/plugins/batched/BatchedTilesPlugin.js` | 69 | `gl.getParameter(gl.MAX_3D_TEXTURE_SIZE)` |
| `src/three/plugins/batched/BatchedTilesPlugin.js` | 2, 111, 270 | `WebGLArrayRenderTarget` |
| `src/three/plugins/batched/utilities.js` | 12 | `onBeforeCompile`, `sampler2DArray` |
| `src/three/plugins/gltf/metadata/utilities/TextureReadUtility.js` | 11 | `new WebGLRenderer()` |
| `src/three/plugins/gltf/metadata/utilities/TextureReadUtility.js` | 12-13 | `WebGLRenderTarget` |
| `src/three/plugins/gltf/metadata/utilities/TextureReadUtility.js` | 16 | `ShaderMaterial` |
| `src/three/plugins/gltf/metadata/utilities/TextureReadUtility.js` | 93-110 | The lazy wrapper that constructs the renderer on first use |
| `src/three/plugins/gltf/metadata/classes/MeshFeatures.js` | 132, 193 | `getFeatures` calls `increaseSizeTo` and `readData` before and after the attribute branch, so an attribute id still enters that WebGL helper |
| `src/three/renderer/controls/PivotPointMesh.js` | 34 | `ShaderMaterial` |
| `src/three/renderer/controls/EnvironmentControls.js` | 290 | Constructs `PivotPointMesh` |
| `src/three/plugins/fade/wrapFadeMaterial.js` | 26 | `onBeforeCompile` |
| `src/three/plugins/fade/FadeBatchedMesh.js` | 14 | Calls that wrap |
| `src/three/plugins/fade/FadeMaterialManager.js` | 102 | Calls that wrap |
| `src/three/plugins/fade/TilesFadePlugin.js` | 186 | Copies `onBeforeCompile` onto another material |
| `src/three/plugins/images/overlays/wrapOverlaysMaterial.js` | 25 | `onBeforeCompile` |
| `src/three/plugins/images/ImageOverlayPlugin.js` | 997 | Calls that wrap |
| `src/three/plugins/images/terrain-rgb/TerrainBasicMaterial.js` | 15 | `onBeforeCompile` |
| `src/three/plugins/images/terrain-rgb/TerrainLambertMaterial.js` | 11 | `onBeforeCompile` |
| `src/three/plugins/mvt/MVTGlyphMaterial.js` | 76 | `onBeforeCompile` |
| `src/three/plugins/pointcloud/PointCloudMaterial.js` | 164 | `onBeforeCompile` |
| `src/three/plugins/pointcloud/PointCloudEffectsPlugin.js` | 11, 189 | Imports and constructs `WebGLRenderTarget` |

`DebugTilesPlugin.js` was checked. It builds `MeshBasicMaterial`, `MeshStandardMaterial`, and `PointsMaterial` (`:823`, `:1048-1059`). It is not in the table.

Canvas 2D `getContext('2d')` shows up in image and MVT sources (`GeoJSONImageSource.js:207`, `MVTImageSource.js:277`, `GridCache.js:13`, `MVTGlyphAtlasTexture.js:379`, `OccupancyGridOverlay.js:60`, `ImageOverlayPlugin.js:717`, `TiledTextureComposer.js:18`). That is not a WebGL call. Those plugins are unrelated to a glTF tileset.

The batching plugin was constructed only after the core result was stored. The thrown message was `t.getParameter is not a function`. That matches `BatchedTilesPlugin.js:69`. The draw-call cut for scenery is the bake (fewer meshes), which is the same reason `docs/performance.md` gives for the Field submit time: about 44.5 ms at 1004 batch keys, and 31.2 ms at 466 keys when the array upload was valid, against a few milliseconds of GPU time. This study did not re-measure Field.

`example/three/src/MeshFeaturesMaterial.js:267` is `onBeforeCompile`. It is an example, not the package runtime.

`example/babylonjs/fadingTiles.js` and `example/playcanvas/index.js` have a `?webgpu` path. They were not run. They are other engines.

## 2. Patches

The core did not need a source patch for the run above.

One footgun is real and small. `TilesRendererBase.preprocessTileset` does this (`TilesRendererBase.js:1512-1513`):

```js
let basePath = url.replace(/\/[^/]*$/, '');
basePath = new URL(basePath, window.location.href).toString();
```

Content is then `new URL(tile.content.uri, tile.internal.basePath + '/')` (`:1567`). A tileset URL at the host root (`/tileset.json` or `http://host/tileset.json`) strips to the origin, `new URL` adds a trailing slash, and the extra `/` makes `http://host//plain.glb`. The first spike served that as the HTML page (`Content type "<!do"`). A tileset under a directory does not hit it. `/tiles/tileset.json` resolved to `/tiles/plain.glb` in the successful run.

The fix is to take the directory with `new URL('.', new URL(url, location))` and stop appending a second slash. That is a few lines plus a test. It is upstreamable. Forge can also just publish the tileset under a path that already has a directory, and ship no patch.

Nothing in the core has to be forked for WebGPU. Skipping the plugins in the table is a call-site choice (`registerPlugin`), not a patch.

## 3. This is not a no-go

The fallback would be the same offline glTF bake (the bake spike) plus a small Forge-owned streamer that does screen-space error and an LRU. That streamer would reimplement the traversal this run already executed. It is the path to take if we later need a plugin from the table. We do not need `BatchedTilesPlugin` for the object-count cut. The bake is that cut.

Babylon's and PlayCanvas's WebGPU switches were not executed here, so this note does not treat them as confirmed. Switching engines would drop Forge's TSL materials. `docs/playcanvas-lessons.md` already took the ideas without the port.

## Materials

Stock glTF that `GLTFLoader` already understands can go through the core: unlit, standard PBR, alpha, meshopt, raw BC7, and `EXT_mesh_gpu_instancing`. The spike's unlit materials arrived as `MeshBasicMaterial`, which `WebGPURenderer` draws through the node library. The second pass is that draw.

These cannot be stock glTF. They stay on the current path, or get the existing TSL hook after load:

- Blend and moss (`frontend/src/blend-node.js`) read `blendMask`, a second UV, and the moss maps. `ocarina_forge/formats/gltf.py` already writes those in extras. The stock loader will not call `attachBlend`. A material whose `userData` names the blend case gets the existing node material after `load-model`.
- `COLOR_0` is aliased to `blendMask` in the viewport, and vertex colors are turned off. A stock loader would multiply vertex colors. The bake should not write that alias as `COLOR_0`.
- Terrain (`frontend/src/terrain-node.js`) is a triplanar atlas from world position and a per-mesh matrix. It stays on the current path.
- Water stays on the current path.

## Picking

Forge's id pass packs a 24-bit actor id (`frontend/src/gpu-pick.js`). The loaded geometry exposed `_feature_id_0` (glTF loaders lowercase the accessor name) and `getX(0)` was the id written at bake time. The id node can read that attribute the same way it already reads an instance or a batch index. `MeshFeatures.getFeatures` returned the right id in the spike and also constructed a `WebGLRenderer`. The id pass should read the attribute and not call that helper.

meshopt quantize skips `_FEATURE_ID_0` when the values are outside [-1, 1]. The spike's read-back was still the original id. Keep actor ids as plain floats (a 24-bit integer is exact) and do not scale them into the quantize range.

`EXT_mesh_gpu_instancing` extra attributes become `InstancedBufferAttribute` in three's loader. A repeated prop can carry the actor id per instance. The bake spike runs `instance()` before `join()`; the other order baked the copies away and instance found nothing.

## Bake numbers

`node tools/spike-tile-bake.mjs`, synthetic cells, no textures, no dump. Times are one run on this machine.

| Cell | Actors | Input tris | Meshes after join | Instanced nodes | HLOD tris | Bytes | Total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sparse | 64 | 27,200 | 6 | 2 (40 instances) | 1,567 from 19,600 | 15,264 | 81 ms |
| dense | 200 | 248,000 | 16 | 4 (120 instances) | 16,249 from 203,280 | 73,964 | 206 ms |
| dense again | 200 | 248,000 | 16 | 4 | 16,249 | 73,964 | 150 ms |

The dense cell split as build 26 ms, join+instance 88 ms, simplify 59 ms, meshopt+write 33 ms. The repeat was 150 ms. Python BC7 (`encode_bc7`, no native encoder) was 13.5 ms / 4,096 bytes at 64² and 101 ms / 65,536 bytes at 256². A rebake should reuse a content-addressed KTX2. These are not Field tiles.

## Phases

Each phase is a toggle and a measurement. The bar is a Field fly with correct rendering. The known-good path after the texture-array rollback is the one to beat: CPU submit around 26–30 ms, about 1000 batches, GPU near 1 ms. Phase 0 is the camera-path benchmark. The experimental tileset behind **Tiles (experimental)** and `?tiles=1` bakes static scenery, streams it with core `TilesRenderer`, and reads `_feature_id_0` in the existing pick pass. The glTF loader is Forge's: `GLTFExtensionsPlugin` imports a WebGL metadata reader, and `three.webgpu.js` does not export `WebGLRenderer`. That is the work the list below split across phases 1–3. It stays off unless the checkbox or the query flag is set.

0. **Measure.** The Field camera-path benchmark is the **Field benchmark** button on the Profile panel, and `?benchmark=1`. It flies the fixed 160 m loop and prints JSON (`submit`, `update`, `priority`, `labels`, `cull`, `writeTex`, `indirectFills`, `lodFlips`, heap avg/final, `capped`, `draws`, `batches`, and the tile counters `tilesLoaded`, `tilesVisible`, `tilesBytes`, `bakeMs`). How to run it and read the JSON is in [performance.md](performance.md#field-camera-path-benchmark). It stays off until Profile or that query flag. With Tiles off the tile counts are zero.
1. **Bake.** Static scenery is grouped into 256 m cells. Each cell is instanced, joined per material image, meshopt-compressed, and given one coarser HLOD. The cache key is the cell content hash, so an unchanged cell is not written again. Glb paths are read and written as files, including a Windows drive path. The baker does not load `sharp`; `@gltf-transform/functions` would otherwise import that native module and Node would exit before the job. `node frontend/tools/bake-scene-tiles.mjs --selftest` prints the baker's own result. Bytes below are still the synthetic spike, not Field.
2. **Stream.** Core `TilesRenderer` only. Forge registers its own `GLTFLoader` with `KTX2Loader` and `MeshoptDecoder` on the tiles loading manager. The metadata plugin is not imported. The tileset URL is `/api/scene-tiles/<hash>/tileset.json`. The LRU is raised to 2 GB at the call site. The tileset sets `gltfUpAxis` to `z` so the library does not apply the +90° X correction it uses for `y`. On load, materials are rebound to the Forge maps already in the scene, including the blend TSL hook when the live material has one. Terrain, water, and dynamic actors stay. `BatchedTilesPlugin` is not registered.
3. **Pick and edit.** The id node reads `_feature_id_0` before the batch or instance id. Selecting an actor hides that cell's leaf and HLOD and shows the cell's per-actor objects. A saved transform rebakes the cells whose hash changed.

Still open: HLOD quality on a real Field cell, and a per-actor hide inside a merged mesh instead of swapping the whole cell back to instances.

## Risks

- Field was not baked or rendered here. The camera-path benchmark is what makes a later number mean something.
- Registering any plugin in the WebGL table puts a WebGL call on the WebGPU device. The batching plugin already threw in the spike.
- The default LRU (about 430 MB) is smaller than the Field working set described in `docs/performance.md`. Raise it at the call site.
- Actor ids outside [-1, 1] are skipped by meshopt quantize. They survived as uncompressed floats. Scaling them into that range would destroy the id.
- A tileset URL at the host root hits the base-path join. The bake is served under `/api/scene-tiles/<hash>/`. The library is not patched.
- The first tiles render on this SwiftShader recorded 0 draws and a `popErrorScope` error, and pixel readback never returned. The second render submitted the mesh. That sequence is this software GPU. It is not a change to make inside `TilesRenderer`.

## What stays put

Dynamic actors, terrain, and water stay on the current path through phase 3. The tileset is an editor cache. `docs/formats.md` records that Longshot's API does not load one.
