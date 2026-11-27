# Rendering performance

This note is the Field-scale memory and draw path: what the viewer already does, what this pass fixed, and which game-engine techniques are still on the table. Measured numbers at the end are from a headless Chrome load of Hyrule Field Child on the `ray-test` dump with the editor on ports 47241/47242. `docs/performance.md` covers the earlier Kokiri-scale instancing and on-demand render work.

## What was blowing the tab

A previous measurement (25 Nov 2026, America/New_York) of Hyrule Field Child, after the Snap dispose fixes, reached **2.31 GB JS heap** (limit 4.19 GB) and a **15.0 GB Chrome working set** (~29 GB private) at **567 of 852** unique scene models. The tab could crash with Out of Memory once the Field finished streaming.

The largest cause on WebGPU was that packed KTX2 textures never actually uploaded as BC1/BC7. `textureSupport` read `renderer.getContext()`, which is a `GPUCanvasContext` with no `getExtension`. Every compressed cap came back false, so `chooseTexture` decoded the JPEG/PNG fallback into an RGBA `ImageBitmap`. Full-resolution RGBA for hundreds of unique maps is several times larger on the GPU than BC7 and also keeps a decoded CPU copy. A second, independent gap on three r170: WebGPU `getFormat` mapped DXT, ETC2, and ASTC, but not `RGBA_BPTC_Format` (BC7, 36492). r187 maps that case to `bc7-rgba-unorm` / `bc7-rgba-unorm-srgb`. The Vite plugin and `patchWebgpuBc7Format` are gone.

## Fixes in this pass

- **WebGPU compressed textures.** `textureSupport(gl, renderer)` uses `renderer.backend.hasFeature("texture-compression-bc")` and `"texture-compression-astc"` after `WebGPURenderer.init()` (already awaited). `canUploadCompressed` does not call `compressedTexImage2D` on a context that has no WebGL probe; alignment is still checked. Packed maps upload as BC7/BC1 when the adapter supports `texture-compression-bc`.
- **BC7 format mapping.** three r187's `WebGPUTextureUtils.getFormat` handles `RGBA_BPTC_Format` with `GPUTextureFormat.BC7RGBAUnorm` and `BC7RGBAUnormSRGB`. The r170 string `bc7-rgba-srgb` is not in that file. Forge no longer patches `getFormat`.
- **Normals, ORM, and other data maps are packed too.** Albedos already went through `gpu_image`. Extra maps used `_encode_cached(..., gpu=False)` and stayed PNG, which is most of the unique Field textures (thousands of PNG files in the shared-v1 store vs ~1.3k packed KTX2). Those now pack as BC7 as well. `asDataMap` keeps them in linear color space and repairs the compressed sampler. Model caches are `shared-v3` / `models-53` so existing GLBs rebuild.
- **Template geometry.** After `partsFromTemplate` clones and bakes a mesh, the source template geometries are disposed. `BatchedMesh.addGeometry` keeps the copy it uploaded. The part clone is dropped once that copy exists; see GPU picking below.
- **Instrumentation.** In development, `window.__forgeMemory()` (and `viewport.getMemoryReport()`) returns a per-category breakdown: textures by format (unique sources, not clones), geometries by role, unique models vs instances, draw counts, and frame time. The Profile HUD splits that frame into CPU and GPU. It stays closed until opened. See [Profiling HUD](#profiling-hud).

CPU mip and ImageBitmap copies are dropped after the GPU upload (`releaseTextureCpu`). The source is marked so clones do not re-upload emptied mips. `repairCompressedTexture` will not set `needsUpdate` on a released texture.

## Techniques already in the viewer

### GPU instancing

Identical models already share one `BatchedMesh` per material (`batchKey`). The mesh family is not part of that key. On WebGPU, compressed images of one size class share an array and the key names the class, not the image (`frontend/src/texture-array.js`). Geometry is uploaded once; each actor is an instance with its own matrix, tint, layer, and visibility. As models stream in, a key that is already on screen grows that mesh (capacity doubles) instead of allocating another exact-fit mesh. A texture that changes a key restages the actors that use it. Picking reads an actor id from an offscreen target, including the per-instance id for preview `InstancedMesh` draws. Those ids are written when a batch is filled and again just before a pick, not on every orbit frame. The gizmo writes `setMatrixAt` for the selected actor only. There is no need to break an instance out of the batch to move or edit it. **Profile** shows `inst` for the live instance count, `keys` for the distinct batch keys, and `pick` for id-texture uploads. The measured before/after is in [performance.md](performance.md).

### Static batching (merge vs instances)

True static batching — merging never-moving scenery into one mesh per material and world chunk — is **not** enabled. The instance path already folds those draws while leaving each actor addressable, which is what picking and editing need. A future merge would:

1. Combine static, unselected scenery per material and chunk.
2. On select or move, break that entry out of the merged mesh into a regular or instanced draw.
3. Optionally re-merge when the actor is deselected and still static.

Estimated extra gain on top of `BatchedMesh`: about **10–30% fewer draws** in dense Field scenery, little memory win (vertices still exist, just concatenated). Risk is to picking and to edit/undo. Documented, not implemented.

### Dynamic batching

CPU-merging small, unique, moving meshes every frame is a poor fit here. Most Field cost is unique gyamls and textures; repeats are already instanced. Estimated gain: **low**. Not implemented.

### Indirect / multi-draw

WebGPU supports `drawIndirect` and `multiDrawIndirect`. r187's WebGPU backend draws `BatchedMesh` from `_mergedDrawStarts`, `_mergedDrawCounts`, and `_mergedDrawInstanceCounts`, and advances `firstInstance` by the run. The batch shader indexes `_indirectTexture` with that instance index, which is how a hidden instance stays on its own matrix. `BatchedMesh.onBeforeRender` does not call `_updateDrawParameters(..., 'merged')` on WebGPU, so Forge does, from the mesh's `onBeforeRender` (`scheduleMergedDraws`). The r170 hand fold (`groupGeometryDraws` / `patchBatchedDraw`) is gone: wrapping `drawIndexed` would replace the running `firstInstance` and read the wrong matrices. A custom GPU-written indirect buffer would matter more once cull results are written on the GPU. Estimated gain on the current CPU cull: **small**. Documented, not implemented as a custom buffer.

### Frustum culling

Already implemented in `frontend/src/cull.js`. Off-screen instances are hidden; **Profile** shows `culled`.

## Further wins (researched, not all implemented)

| Technique | Estimated gain | Status |
| --- | --- | --- |
| Compressed GPU textures (BC7/BC1) | **4–8×** texture memory vs RGBA; often the difference between a Field tab surviving and OOM | Implemented this pass |
| Dispose leftover CPU geometry after extract | One of three vertex copies gone; tens to a few hundred MB on Field | Implemented. Template sources are disposed, and part clones are released after the batch copy exists (GPU picking, below) |
| Frustum culling | Already removing off-screen instance draws | Already present |
| Distance streaming / unload far unique models | Stops the Field tab from pulling all 852 unique meshes into JS; nearby models stay loaded and the rest stream as you move | Implemented. Soft budget is **1.2 GB** of geometry plus BC7 (`__forgeMemory()`). The JS heap and a 4096-mesh count are last-resort caps. See [Residency budget](#residency-budget) |
| Occlusion culling (Hi-Z / GPU queries) | **10–30%** fewer shaded pixels and some draws in dense Field views | Not implemented |
| HLOD / impostors for far actors | Large GPU save at distance; visible change if the billboard is wrong | Not implemented |
| Texture arrays / atlases | Fewer binds, more meshes in one batch; needs authoring or a pack step | WebGPU packs compressed images at runtime by size, format, and color space. WebGL stays per image. See [performance.md](performance.md) |
| Meshlets / cluster culling | Large for huge unique meshes; mesh shaders are not how three r170 draws `BatchedMesh` | Research only |
| GPU-driven indirect draws | Pays off after GPU culling writes the indirect buffer | Not implemented |
| True static merge with breakout on edit | Modest draw reduction on top of instancing | Not implemented |
| WebGPU render bundles per 64 m cell | Submit CPU, if a cell is recorded once and then only shown or hidden | Spike, default off. See [Render bundles per cell](#render-bundles-per-cell). Not measured on the Field |
| Async pipeline compile before a batch is attached | Removes 50–300 ms stream-in hitches. No steady-state frame change | Implemented. WebGPU awaits one `compileAsync` at a time, then attaches. See [Shader pipelines](#shader-pipelines) |

## Residency budget

The viewer used to stop background loads at **240** unique meshes (hard evict at 280) or at **2.2 GB** of JS heap (hard **2.8 GB**), nearest first, at most 8 evictions a pass. One Field model can be about ten times another, so a count fills the tab with whatever happened to be nearby.

The soft limit is now bytes. `__forgeMemory()` sums geometry CPU bytes and BC7 texture GPU bytes (`geometries.cpuBytes` + `textures.byFormat.BC7.gpu`). That total is the residency budget:

| Limit | Value | What it does |
| --- | --- | --- |
| Soft byte budget | 1.2 GB | Stop loading. Evict farthest residents down toward 1.0 GB |
| Hard byte budget | 1.5 GB | Emergency eviction, still after the unload cooldown |
| Count safety cap | 4096 soft / 4608 hard | Last resort for a scene of many tiny meshes. Kokiri (~470) and the Field (852) are not stopped by this |
| JS heap safety cap | 2.2 GB soft / 2.8 GB hard | The V8 limit. A hard heap overrun evicts immediately |

Models behind the camera have their distance multiplied by **1.5**, so the view ahead is loaded and kept first. The queue is not re-sorted on every small orbit step. Priorities change only after the camera moves more than **2 m** or turns more than **30°**. A model that falls out of the wanted set is armed and then left alone for **90 frames**. Looking back at it cancels that wait, so a swing of the camera does not evict and reload the same mesh. The JS-heap emergency brake does not wait.

`__forgeMemory().resident` reports `assetBytes`, `geometryBytes`, `bc7Bytes`, `evictions`, `reloads`, and whether the soft or hard cap is holding. `__forgeMemory().stream.maxFrameMs` is the longest tick gap while models are still streaming. `maxCompileMs` is the longest wait for `compileAsync` before a batch was attached. That wait is off the draw path.

Far proxies (a 2–5% mesh kept resident so the cap never shows a box) are not in this change. Building one per Field model is a cache-format change, and the meshes that already have a coarse level still use it.

### Policy simulation (not a Field tab)

This checkout has no game dump, so Hyrule Field and Kokiri Forest were not loaded in a browser for this change. The comparison below is `simulateFieldOrbit()` in `frontend/src/resident-budget.js`: 852 unique models on a 600 m disk, 30% of them 12–40 MB and the rest 1–5 MB, camera orbiting a 25 m radius for 3 turns at 3° per frame (360 frames). The "before" column is the old rule (240 nearest, re-sort after 1 m or about 3.6°). The "after" column is the byte budget, the 1.5× behind penalty, the 2 m / 30° gate, and the 90-frame cooldown. A rapid reload is an unload and a load of the same name within 60 frames.

| | Evictions | Reloads | Rapid reloads | Priority updates | Peak asset bytes | Peak unique meshes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Before | 243 | 201 | 99 | 360 | 2.46 GB | 240 |
| After | 0 | 0 | 0 | 180 | 1.20 GB | 122 |

The drop is large here because the size mix makes the byte budget bind before the count cap. On the measured Field (25 Nov 2026) 241 unique meshes were about **1.0 GB** of geometry plus BC7. That run stopped because the count cap was 240, not because the byte budget was full. The count cap is now 4096, so a Field load continues until geometry plus BC7 reaches 1.2 GB or the JS heap reaches 2.2 GB.

Kokiri Forest Child is about 470–482 unique models (`262 loaded` + `207 background` on an idle view, and 482 in the 25 Nov load). At 241 resident meshes the heap was **386 MB**. The 240 cap was holding the other models, including ones in view, and the HUD called that remainder `background` with `0 queued` because `canFillMap` refuses to run while the soft cap holds. `main` has the same 240 cap (`overResidentSoftCap` in `resident-budget.js`) and the same `canFillMap` check, so an idle Kokiri on main also keeps boxes for every unique model past the nearest 240. A model in the current view (tier below 4) now evicts a farther resident without waiting out the unload cooldown. Models the budget is actually holding show as `N capped` instead of `0 queued`.

## Shader pipelines

three still builds a render pipeline on the thread that is drawing when the cache misses. `Pipelines` publishes the cache entry before `createRenderPipelineAsync` assigns `pipelineData.pipeline`. A draw in that window calls `setPipeline(undefined)`. `warmWebGLPrograms` stays on WebGL and does not call `compileAsync` on the live WebGPU renderer.

A new `BatchedMesh` is compiled with `compileAsync(batch, camera, scene)` (the live scene is the third argument, so its lights are included) and only then added. **One** of those compiles runs at a time. r187 stores the precompile in a single `renderer._precompilationState`. `compileAsync` awaits `buildAsync`, which yields between shader stages, and `_finishPreCompile` destructures that field. A second call in the gap sets it to null and the first throws `Cannot destructure property 'useFrameBufferTarget'`. That is [three.js #35315](https://github.com/mrdoob/three.js/issues/35315), fixed upstream after r187. The call still yields, so the frame loop is not blocked, and three still compiles that call's own pipelines together. Terrain uses the same wait inside `warmTerrain`, after the WebGL warm call, and is still added only once that returns. `guardWebgpuSetPipeline` wraps `backend.draw` and skips a render object whose native pipeline is still missing. That guard stays: r187 still has open reports where a compile resolves before the native pipeline exists ([#35556](https://github.com/mrdoob/three.js/issues/35556), [#35659](https://github.com/mrdoob/three.js/issues/35659)).

r187's `compileAsync` calls `_renderTransparents(transparentObjects, transparentDoublePassObjects, camera, sceneRef, lightsNode)`, the same argument order as `render()`. The r170 call omitted the double-pass list, so the lights node arrived as the scene and `overrideMaterial` threw `isShadowNodeMaterial`. `repairCompileAsyncTransparents` is gone. A throw before three restores `_handleObjectFunction` is still caught in `compileObjectPipeline`: the draw path is put back, the failure is logged once, and the batch is attached anyway.

The id-buffer pack is a native `pack24` function (`value / 256u`, `value / 65536u`, `& 255u`). The Field fragment had written `render.cameraProjectionMatrix` and `render.cameraViewMatrix` in place of two unpack operands. `reference`, `materialReference`, `Fn`, and the other TSL helpers are exports of `three.tsl.js`. `three.webgpu.js` imports them and does not re-export them. A Linux production bundle has one `three.tsl` chunk. The `?three=webgpu` graph used to resolve `three/tsl` by asking Vite for `./three.tsl.js` and keeping whatever file came back. On Linux that file is `three.tsl.js`. The plugin only special-cased the exact specifier `three/tsl`. A nested `./three.tsl.js` from a marked importer was not that specifier, so it fell through to `sameThreeBuild`, which maps every other three build file onto `three.webgpu.js`. Bradyn's Windows build bound `terrain-node.js?three=webgpu`'s `reference` import to that file, and Rollup stopped: `three.webgpu.js` does not export `reference`. The unmarked graph never entered the plugin, which is why a Linux build of that commit still passed. `three/tsl` and `./three.tsl.js` now return one `three.tsl.js` id, with or without the query, and a result that is not that file is ignored. The build rejects a `src` import whose name that entry does not export. If the two `Node` constructors still differ at runtime, the editor logs once and clicks use the CPU raycast; that check does not throw. `THREE.Clock` is gone; the frame loop uses `THREE.Timer` (`update`, then `getDelta`).

### three.js r187

Forge pins `three@0.187.0` (30 Oct 2026). Checked against that package's sources, not the release notes alone:

| r170 workaround | r187 source | What Forge does |
| --- | --- | --- |
| Vite plugin and `patchWebgpuBc7Format` | `getFormat` case `RGBA_BPTC_Format` returns `BC7RGBAUnorm` / `BC7RGBAUnormSRGB`. The string `bc7-rgba-srgb` is absent | Removed |
| `repairCompileAsyncTransparents` | `compileAsync` passes `transparentDoublePassObjects`. `_precompilationState` is one field and `_finishPreCompile` throws when it is null | Removed. Compiles are serial. The attach-on-failure catch stays |
| `groupGeometryDraws` / `patchBatchedDraw` | WebGPU `_draw` reads `_mergedDraw*` and adds `instanceCount` onto `firstInstance`. Nothing calls `_updateDrawParameters(..., 'merged')`, so `_mergedDrawCount` stays 0 and the mesh would draw nothing | `scheduleMergedDraws` calls it. The hand patch is removed so it cannot override `firstInstance` |
| No per-layer compressed array upload | `_copyCompressedBufferToTexture` writes `texture.layerUpdates` through `writeTextureLayer` (`origin.z = layerIndex`). The WebGL path uses `compressedTexSubImage3D` when `isCompressedArrayTexture` | Used on WebGPU. A new layer calls `addLayerUpdate`. Growing the depth allocates a new `CompressedArrayTexture` because the GPU layer count is fixed. The array is sampled with `.depth(layer)` from `userData`. It is not assigned to `material.map` or the other stock slots: `CompressedArrayTexture` is a `texture_2d_array` and is not `isArrayTexture`, so a stock `textureSample` omits `array_index` and the command buffer is dropped. WebGL does not build arrays |

`three-mesh-bvh` `0.8.3` accepts `three >= 0.159.0`, so the peer range includes r187. `three/tsl` is `three.tsl.js`. `three.webgpu.js` imports it and does not re-export `reference`, so the one-three plugin keeps TSL imports on `three.tsl.js`. Both files import `three.core.js`, which is the shared copy.

Residency is unchanged: soft byte budget 1.2 GB of geometry plus BC7 (target about 1.0 GB), hard 1.5 GB, count caps 4096 / 4608, JS heap 2.2 GB soft / 2.8 GB hard.

### Untextured Field

On main, Hyrule Field drew with Profile `tex 3` while `__forgeMemory()` estimated about 8.8 GB of BC7, 2233 batches, and about 58 ms of CPU. Those two numbers look at different sets. `tex` is `countTextures` on batch materials and terrain, one level deep, and it does not walk `userData.forgeLit`. The memory report also walks part materials, template materials, and `forgeLit`, and `gpuTextureBytes` estimates a GPU size from width and height. It does not prove the texture is bound.

The geometry glb leaves the images out. `partsFromTemplate` records a flat batch key, and the staged `BatchedMesh` is compiled before `applyTextureManifest` assigns the BC7. Two bugs kept the draw on that flat material:

- `imageSignature` called `drawImage` on a compressed image (`{width, height, data}`). That throws, and the catch labeled every same-size map `WxH:bitmap`. The batch key also includes the node family (`materialKey`), not the model name, so different models with the same family and the same texture size shared one mesh. The mesh kept the first material. The manifest wrote each BC7 onto its own material, which is what the memory report counted.
- When the mesh and the part did share one material, the pipeline had already been compiled with `map` null. Putting the map on the material afterwards did not rebuild that mesh.

After the upload, the batch key is the compressed texture's source id (clones share `source`). If the key changed, or the mesh's `forgeAlbedo` stamp is not the map now on the material, or no resident mesh holds that material, the batches rebuild. `materialForBatch` picks a material that holds the map. `compileAsync` then runs with the map assigned. `releaseTextureCpu` waits until `textureGpuReady` sees `backend.get(texture).texture` or `externalTexture`, so emptied mips are not what the next compile samples.

Material tint stays in the batch key. `setColorAt` is already the selection tint. Taking the material color out of the key means a shared white material, and building that before the map is on the compiled material is what painted models a flat cream. That stays a follow-up (issue #4).

Both checkboxes have to be on for a textured read. **Load textures** in the Actors panel downloads the images. **Textures** on the stage bar shows or hides images that are already loaded. Unchecking only the stage bar strips the maps onto `forgeLit` and leaves `tex` at the terrain leftovers while the memory report still counts the BC7. The status line then says `textures hidden`. `textures 616/617` is the download queue, not the number of bound images. `gpu N tex` is `renderer.info.memory.textures`, an object count. A field of about 1800 batches reports roughly four internal `DataTexture`s per `BatchedMesh` (matrices, indirect, colours, pick ids) plus a few leftovers, which is where `7442 tex` comes from. It is not 7.4 GB of BC7, and it does not mean the albedo uploaded. Profile `tex` is the count that says a drawn material has a map.

A geometry glb is still compiled while `map` is null. r187's program cache key follows the colour node, not a map assigned later, so the fragment stays the flat material colour even after the BC7 lands on the material. `attachAlbedo` builds that colour node from the map that is on the material now, and `rebindDrawnMaps` points a mesh that is still holding the flat clone at the part material that holds the image. The full rebuild still runs so `compileAsync` sees the map. It no longer has to finish every batch before the mesh already on screen samples it.

`three` and `three/webgpu` both construct `CompressedTexture` from `three.core.js`, so a KTX2 loader on the marked `three` import is the same class as the renderer. `adoptRendererTexture` still rebuilds a texture whose constructor is not that class and warns once. `?texdebug=1` (or `localStorage.ocarinaforge.texdebug = "1"`) logs one `forge texdebug` line when a texture edit, a rebuild, or the stage-bar toggle changes the counts: mesh maps, part maps, maps sitting only in `forgeLit`, missing uv, a foreign constructor, and whether `textureGpuReady` sees an uploaded image. It also counts `requested`, `fetched`, `decoded`, and `assigned`, and appends the first `error`. The actor manifest request sends `textures=1` whenever Load textures is on. Without that flag the server reads a Layout-shaped layer list as textures off and returns an empty manifest, while terrain, which already sent the flag, still draws. A failure in that chain logs one `Texture pipeline:` warning instead of disappearing into an empty catch.

## GPU picking

Clicks and hovers draw the visible pickable meshes into an offscreen RGBA8 target instead of raycasting a CPU copy of every part. `frontend/src/gpu-pick.js` packs a 24-bit actor id into the color (little-endian RGB, alpha 0 means a miss). A second mode packs the camera-to-surface distance into the same 24 bits so alt-drag can focus the point under the cursor. The readback is asynchronous. Hover is coalesced to one pick per frame; a click picks immediately. WebGL reads a single pixel. WebGPU reads a 64-pixel-wide strip, because three r170's `copyTextureToBuffer` aligns rows to 256 bytes and rejects a 1-pixel RGBA8 row. The cursor pixel is one texel of that strip.

Instanced preview meshes sample a per-instance id texture (`gl_InstanceID` / `instanceIndex`). Batches sample the same texture through the indirect draw id. The WebGPU node material only passes a `THREE.Texture` into `texture()` / `textureLoad()`: a render target contributes its `.texture`, and the peel table is a texture node that follows the uniform. A reference node or the render target itself is not a texture, and the shader build throws `TextureNode: Need a three.js texture`. Placeholder boxes that have no mesh yet write one id. The gizmo stays on layer 0, and the pick camera never enables that layer. The editor has no marquee gesture. `uniqueIdsFromBuffer` is the decode a box select would use over a rectangle of the same target.

Alpha-tested materials discard in the id shader before the id is written, so a foliage hole is not a solid hit. Alt-click, and a second click on the same front actor, peel up to eight ids front to back. `chooseHit` still walks that stack together with volume edges.

After `addGeometry` the part clone's typed arrays are released (`geometry-retain.js`). A later rebuild copies them back out of the batch that owns them. `__forgeMemory()` counts `array.byteLength`, so a released part reports about 0 bytes. These stay on the CPU on purpose:

- The `BatchedMesh` arrays. They are the uploaded copy and the source for the next rebuild.
- The selected actor's model, so selecting it does not depend on a round-trip and the mesh audit still sees its vertices.
- Water parts. Pond fish placement raycasts the water surface.
- Terrain meshes and their BVH. Snap uses the wasm ground sampler (`sampleGroundSurface`). It falls back to a heightmap raycast only when that sampler is absent, and it does not use the id buffer.
- Volume and collision boxes. The test is a unit box. A volume fill is not drawn into the id target, so it cannot occlude the actors inside it.
- Preview instance geometry, which the preview draw already needs.
- The sync center-view audit, and the immediate alt-drag focus, raycast `BatchedMesh`'s own geometry rather than the released part clone. If the id readback throws, selection uses that same path.

## Profiling HUD

A one-line status label stays on the viewport. It names the backend that actually started (`WebGPU`, `WebGPU (WebGL2 fallback: …)`, or `WebGL (WebGPU unavailable: …)`), the page's short git revision, and the draw count. While models are still streaming it also shows `queued`, `loading`, or `background` when those counts are above zero. There is no Stats toggle. An old `ocarinaforge.stats` value is ignored. When a load filter left a layer out, the label ends with `off: …`. When textures were left out of a mesh layer, a banner says Load textures is off and names the Actors panel checkbox. The stage-bar Textures checkbox only shows or hides images already loaded.

The Profile HUD is the rest of that readout, plus the CPU and GPU split. It stays off until you open it, so timestamp queries are not recorded during ordinary editing.

Turn it on with the **Profile** checkbox on the viewport toolbar, with `P`, from the command palette ("Toggle frame profile"), or by opening the page with `?profile=1`. The checkbox is remembered in `ocarinaforge.profile`. Fly or orbit so the view keeps drawing. A still camera records the frames that were actually drawn. The GPU number fills in when the readback lands, a frame or two behind the CPU numbers.

The HUD shows the latest frame and the rolling average and p95 over the last 180 frames:

- **CPU** — JavaScript in the frame. Update is fly, streaming, and labels. Cull is `syncBatchVisibility`. Submit is `renderer.render` on the CPU.
- **GPU** — On WebGPU, a `timestamp-query` query set is written at the render-pass boundaries, resolved into a buffer, and mapped with `mapAsync`. On WebGL, and on the WebGPU renderer's WebGL2 fallback, the timer is `EXT_disjoint_timer_query_webgl2`. The line reads `n/a` when the adapter has neither, and when a WebGL disjoint sync throws the result away. It reads `…` until the first result arrives.
- **obj / batches / sub / draws** — `obj` is render objects other than batches. On WebGPU that is real GPU draws minus the batch count. With bundles off, real GPU draws are `drawCalls`. With bundles on, a replay does not call `info.update`, so the HUD adds the merged ranges of cells that were not re-recorded. `batches` is the `BatchedMesh` count (one mesh per cell when bundles are on, so this rises above `keys`). `keys` is the distinct material batch keys, with the cell and loose suffixes stripped. `pick` is id-texture uploads since the previous sample. `sub` is the sum of `_mergedDrawCount` once `scheduleMergedDraws` has filled it, otherwise `_multiDrawCount`. `draws` is `drawCalls` plus `sub`. It still double-counts merged ranges, and it falls by the replayed ranges because those ranges left `drawCalls`. Compare GPU work on the `gpu draws` line.
- **gpu draws / writeTex / pipelines / versions / binds / bundles** — `gpu draws` is the real GPU draw count for that frame (`drawCalls`, plus replayed bundle ranges). `writeTex` is `queue.writeTexture` calls. `pipelines` is render pipelines created. `versions` is materials whose `version` changed. `binds` is `createBindGroup` calls. `bundles` is how many cells were re-recorded. A camera move that only hides a whole cell stays at 0. The line is omitted until a sample has one of those fields.
- **geom / bc7** — geometry CPU bytes and BC7 GPU bytes from the residency budget cache (`budgetAssetBytes`). The line is omitted until that cache has run.
- **fps, inst, culled, separate** — frame rate (`idle` when the view has settled), live instances, hidden instances, and the draw count those actors would cost without batching.
- **queued, loading, loaded, background** — the model queue. `conv`, `tex`, `dl`, and `up` are this scene's convert, texture, download, and upload times. `warm` is the server warm-up. `detail` is the wait for the first nearby detailed batch. `compile` is the longest wall-clock wait while one fill's meshes were compiled, when that wait has run. It is outside the submit timer. The server convert summary is the last of those lines, and **Dump JSON** keeps the recent convert log with it.
- **tris, meshes, tex, heap** — triangles, resident unique meshes, resident textures on batches and terrain, and `performance.memory` when the browser exposes it. Otherwise the heap reads `n/a`.
- **avg / p95** — mean and 95th percentile. GPU samples that have not come back are left out of the GPU average.

`__forgeMemory().frame` includes `cpuMs`, `updateMs`, `cullMs`, `submitMs`, `gpuMs`, `objects`, `batches`, and `subDraws`. `__forgeMemory().profile` includes the rolling summary and the next-step line. `__forgeProfile()` returns the same capture the **Dump JSON** button copies to the clipboard and prints in the console:

```js
copy(JSON.stringify(__forgeProfile(), null, 2))
```

Closing the HUD removes the command-encoder hook and does not begin another timer query. Opening it allocates a small ring of timestamp buffers. A readback that is still in flight when you close the HUD is dropped.

The next-step line uses the rolling averages once 12 frames are in the window:

| Reading | Next step |
| --- | --- |
| GPU average is at least 1.5× the CPU average | Overdraw, alpha-tested foliage, far meshes, far proxies |
| Cull average is at least 5 ms and at least 35% of CPU | Spatial chunk batching |
| CPU average is at least 1.5× the GPU average, and objects + batches average at least 100 | Fewer render objects (texture arrays) |
| Otherwise | CPU and GPU are close. Compare cull with the object count |

This checkout has no game dump, so there is no new Field split recorded here. The last total on a capped Hyrule Field Child load is still **50.3 ms** and about **5,600 draws** (see the table below). Open that scene with Profile on, orbit, and dump. The HUD's last line is the recommended next step.

## Render bundles per cell

Static scenery can be recorded into a WebGPU render bundle, one bundle per 64 m cell. The toggle is **Bundles** on the viewport toolbar (`?bundles=1`, remembered in `ocarinaforge.renderBundles`). It is off until a Field run beats main. WebGL ignores it: `BundleGroup` is only used when the backend reports `beginBundle`. three stays at 0.187.0. `BundleGroup` is already in that build (`three.webgpu.js`). No upgrade.

This is not a geometry merge. Issue #5's chunk merge is still separate. The batch key gains `|cell:ix,iz` while the toggle is on, so each existing `BatchedMesh` is split by the actor pivot's world XZ. Water, terrain, ground, and helpers stay on the normal key. `keys` on the HUD is the material key, so it stays comparable to the 391-key Field. `batches` counts the split meshes.

three r187 records the group's draws into a `GPURenderBundle` when `BundleGroup.version` changes. Setting `needsUpdate` is what bumps that version. A later frame replays the bundle and does not encode those draws again. The group is **not** `static`. A static bundle returns from `NodeMaterialObserver.needsRefresh` before the matrices-texture check, so `setMatrixAt` never reaches the GPU. A dynamic bundle still replays; it uploads that same texture when its version changes. `group.visible` shows or hides the whole cell. That does not re-record. A cell is re-recorded when its loaded content changes (a model is added or removed, an array layer rebinds, or instance visibility changes the merged ranges) or an edit is committed (move, add, delete, undo). Distance LOD still calls `setVisibleAt`, and those ranges are draw arguments, so a flythrough that changes LOD re-records the cell. `bundles` on the HUD is that count. Replay does not help if it stays high.

The editor path stays outside the bundle. Selecting an actor rebuilds that actor onto a `|loose:` key, parented to the scene, so the GPU id pass, the gizmo, the selection tint, and a drag all hit a normal batched instance. The cell the actor left is re-recorded. Committing the drag, or undo, re-records again. Drag frames do not. Deselect puts the actor back in its cell.

### Field benchmark

**Field benchmark** on the Profile panel waits until queued, loading, background, and pending are all zero (or 90 seconds), then flies a fixed loop. The eye is at y=190 and looks at y=150. The XZ points are (0, 0), (160, 0), (160, 160), (0, 160), (−160, 160), (−160, 0), (−160, −160), (0, −160), (160, −160), (0, 0), at 90 m/s and a fixed 1/60 s step. It restores the camera at the end. The JSON (avg and p95) is copied and printed. `__forgeBenchmark()` returns the last result. Run it with **Bundles** off, then on, in the same session.

### Headless measurement (not the Field)

This machine has no game dump. `node tools/measure-render-bundles.mjs` serves `tools/render-bundle-bench.html` and opens it in headless Chrome with SwiftShader (`--use-webgpu-adapter=swiftshader`). The page is 36 cells × 30 `BatchedMesh` × 4 instances of one box, one shared `MeshBasicMaterial`. There is no LOD, so each cell is recorded once. 27 Nov 2026, three r187, adapter architecture `swiftshader`. Eight warmup frames, then 40. The camera orbits.

| | submit avg | submit p95 | drawCalls during the 40 | first frame drawCalls | first frame triangles | JS heap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bundles off | 5.0 ms | 8.3 ms | 1081 | 1081 | 51841 | 55.8 MB |
| Bundles on | 2.4 ms | 3.6 ms | 1 | 1081 | 51841 | 57.8 MB |

The first bundle frame recorded the same 1081 draws and 51841 triangles (16.4 ms). The next frames replayed: `BundleGroup.version` did not change, and `drawCalls` fell to 1 because `executeBundles` does not call `info.update`. That 1 is the pass that is not inside a bundle. Submit on the replay frames was 2.4 ms against 5.0 ms for the direct draws. An earlier run of the same page, before the first-frame probe, was 7.8 ms off and 2.3 ms on. SwiftShader moves. Both replays were faster than the direct path, and the heap grew by about 2 MB, not a second copy of the scene.

This does not say the Field's 29.7 ms submit is beaten. The grid has no streaming, no texture arrays, no LOD, and one material. A Field flythrough that re-records cells can lose the difference. The bar is still Bradyn's Windows/AMD/Chrome run below.

### Placement

Bundles off and on have to draw an instance in the same place. `tools/bundle-placement.html` renders one `BatchedMesh` (two geometries, so the second draw has `firstInstance > 0`) with the Forge batch setup: `matrixWorld` identity, `matrixWorldAutoUpdate` false, frustum and sort off. `node tools/measure-bundle-placement.mjs` reads the pixels in headless Chrome, SwiftShader, 27 Nov 2026.

| | red centroid | green centroid |
| --- | --- | --- |
| Direct, and the bundle record and replay | 63.5, 64 | 97.6, 63.9 |
| `setMatrixAt` moves green to (3, 2, 0), bundle still `static` | unchanged | unchanged (97.6, 63.9) |
| Same edit, `static = false` | 63.5, 64 | 98.7, 40.4, matching a direct render of the moved mesh |
| `mesh.visible = false` without re-recording | the boxes stay | the boxes stay |
| `needsUpdate` after that hide | empty | empty |

The record itself is the right place, including two geometries and a later camera move. The failure is a matrix written after the record. A static replay never uploads it. That is the on-only placement bug: off uploads on the next draw, on kept the recorded texture. Cells are now dynamic. A visibility change, or a new matrices/indirect/color texture object, still bumps the cell version, because a bundle ignores `mesh.visible` and a replaced texture is a new bind group. `setInstanceCount` replaces that texture. On this machine the old texture stayed bound for a direct redraw as well, so that path is not the on-only bug. `material.needsUpdate` after the replace drew nothing here, so the shader is not rebuilt that way.

Transparent materials are not cell-split. Their sort rewrites the indirect map every frame, and a replay would keep the old draw ranges. Terrain, water, ground, and helpers were already excluded. Terrain still samples its own matrix.

The scene preview sets `mesh.visible = false` on a batched mesh it replaces. Off honors that. On used to keep drawing the recorded mesh. The stamp re-records that cell.

### Bradyn's Field run (Windows, AMD, Chrome)

Same benchmark, bundles off then on. Avg / p95. Placement was correct off and wrong on. This is the run the placement fix is for. It is not a win.

| | cpu | update | cull | submit | gpu | gpuDraws | draws | bundleRecords | writeTex | heap avg (final) | geom | bc7 | capped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Off | 57.4 / 74.3 | 29.6 / 44.7 | 6.2 / 7.6 | 21.7 / 27.1 | 0.69 | 381 / 1185 | 401 | 0 | 43.9 / 47 | 1.35 GB (1.307) | 768 MB | 750 MB | 569 |
| On | 53.6 / 73.5 | 31.1 / 47.2 | 7.1 / 9.2 | 15.4 / 22.0 | 0.69 | 548 / 1622 | 1589 / 2539 | 0.41 / 2 (415 total) | 18.2 / 34 | 1.07 GB (1.05) | 749 MB | 683 MB | 596 |

`writeTex` near 44 with bundles off is the LOD path, not a per-frame rewrite of every batch. `scheduleMergedDraws` calls `_updateDrawParameters` only when visibility changed or the mesh sorts or frustum-culls. That call sets `indirectTexture.needsUpdate`, and WebGPU uploads it. The fly moves every frame, and the benchmark skips the cull early-out, so LOD pops land in this number. Showing a slot also called `setMatrixAt` for a matrix that was already written at append time, which uploaded the whole matrices texture again. That second write is gone. The summary now has `indirectFills` and `lodFlips` beside `writeTex`. On was lower (18) because a static bundle skipped the upload. That skip is the stale matrix. Dynamic bundles upload when the texture version changes, so on should move back toward off. `binds` on was 2.5 / 6 and off was about 0.

The residency cap is unchanged: soft **1.2 GB** of geometry CPU plus BC7, hard **1.5 GB** (`RESIDENT_BYTE_SOFT` / `RESIDENT_BYTE_HARD`). 768 + 750 MB is about 1.52 GB, past the hard cap. `capped` 569 and 596 is how many models the budget is holding as helper boxes, and those boxes add draws. The still view on main `e65af37` was heap 902 MB and not capped because that camera did not stream this loop. Off versus on is the same fly. Neither number is the still-view 29.7 ms / 902 MB / not-capped capture. Submit off was already 21.7 ms with `gpuDraws` 381, not 29.7 ms with about 4673 calls.

`update` near 30 ms and `cull` near 6–7 ms are on both sides, so they are not the bundle. `update` is everything in the tick except cull and submit: the camera step, `prioritizeLoad` (only after a 2 m move or a 30° turn), texture detail, the GPU-profile walk, and label render. Cull is `syncBatchVisibility`. It is forced every benchmark frame. A still view takes the `shouldRecull` early-out, which is why that capture showed cull 0. The summary now splits `preCullMs`, `flyMs`, `priorityMs`, `textureMs`, `labelsMs`, and `gpuSampleMs`. This machine has no dump, so those pieces are for the retest.

### Windows retest

1. `npm run build --prefix frontend`
2. Restart the editor server so it serves that build.
3. Hard-reload.
4. Open Hyrule Field Child, Static + Dynamic, with **Profile** on.
5. Leave **Bundles** off. Wait until streaming settles. Click **Field benchmark**.
6. Turn **Bundles** on. Wait until the view settles again. Click **Field benchmark**.
7. Paste both JSON summaries. Read `summary.submitMs` (avg and p95), `summary.gpuMs`, `summary.gpuDraws`, `summary.bundleRecords`, `summary.writeTex`, `summary.indirectFills`, `summary.lodFlips`, `summary.preCullMs`, `summary.flyMs`, `summary.priorityMs`, `summary.textureMs`, `summary.labelsMs`, `summary.gpuSampleMs`, `heapBytes`, `geomBytes`, `bc7Bytes`, and `capped`.
8. With bundles on, objects have to sit where they sit with bundles off. A tree or a building in the wrong place means the cell is still showing a stale matrix.

The success bar, against main `e65af37` on that machine (cpu 41.3, update 11.6, cull 0, submit 29.7 ms, gpu 5.5 ms, obj 4282, batches=keys=391, sub 699, draws 5372, about 4673 real GPU calls, heap 902 MB, nothing capped): submit well below 29.7 ms, heap at or under about 1 GB, nothing capped, and the picture the same as main. The fly streams past the 1.5 GB hard cap, so its heap and `capped` count are not that still view. `gpu draws` is the real GPU call count. `bundles` near 0 during the fly is the replay. Placement has to match before submit is a result. If submit does not fall, this spike does not beat main.

No game or file format changed. Longshot's modding API does not describe render bundles or scenery cells. `docs/formats.md` is unchanged.

## How to measure

1. Start `./build.sh` or `build.bat` so the editor is on 47231 and the API on 47232. The numbers below were taken with the editor on 47241 and the API on 47242, which left 47231/47232 free for another window.
2. Open the viewer and load Hyrule Field Child. The status label names the backend that actually started (`WebGPU`, or `WebGL (WebGPU unavailable: …)` with the reason) and ends with the short revision. A plain `WebGL` from a stale `frontend/dist` or a cached module is not a WebGPU fallback. The revision should match `/api/health`. See [troubleshooting.md](troubleshooting.md). Turn **Profile** on and orbit so frames keep coming. The HUD then shows the CPU and GPU split beside the draw, instance, and streaming counts.
3. In the devtools console, `copy(__forgeMemory())` after the background load finishes. The report includes texture format counts, geometry roles, unique models vs instances, draws, frame time, and the profile block. `copy(JSON.stringify(__forgeProfile(), null, 2))` is the short frame capture.
4. Chrome's task manager (or a headless script using `--enable-precise-memory-info`) is the working-set number. JS heap alone under-reports GPU textures.

`tools/measure-field-memory.mjs` is a headless Chrome run against the already-running 47241 server. It records heap, Chrome working set, the status label, `__forgeMemory()` when present, and a screenshot. It leaves 47231/47232 alone. Defaults: `FORGE_WASM_MAX_WS_MB=10240` (kill the Chrome tree at that working set), `FORGE_WASM_MIN_FREE_MB=4096` (stop if system free RAM falls this low), `FORGE_WASM_MIN_FREE_START_MB=10240` (refuse to launch below that floor).

To check the residency budget and stream-in hitches on a dump: `npm run build --prefix frontend`, then restart the editor server so it serves that build. Open Hyrule Field Child, orbit in place until streaming settles, and read `__forgeMemory().resident` (`evictions`, `reloads`, `assetBytes`, `peakHeapBytes`) and `__forgeMemory().stream.maxFrameMs`. Do the same on Kokiri Forest Child. `node tools/measure-streaming-policy.mjs` reprints the synthetic orbit table; it does not open a scene.

## Measured numbers

Dates and clock times are America/New_York, 25 Nov 2026, headless Chrome against `http://127.0.0.1:47241/` and the `ray-test` dump. Those runs read draw counts from the viewport overlay. It stayed `idle` until the camera moved, so frame time was not on the line during streaming. The under-4 GB working-set goal is met at the 240 unique-mesh resident cap; see the last section.

### Before (WebGPU, every packed map decoded to RGBA; extras already PNG)

| Checkpoint | Unique models | JS heap | Chrome working set | Private | Draws | Instances |
| --- | --- | --- | --- | --- | --- | --- |
| Mid-load (prior session) | 567 of 852 | 2.31 GB | 15.0 GB | ~29 GB | not recorded | not recorded |
| This session | 186 of 852 | 0.72 GB | 5.0 GB | 10.2 GB | 6936 | 6101 |
| This session | 304 of 852 | 1.30 GB | 8.5 GB | 17.4 GB | 5402 | 8247 |
| This session | 371 of 852 | (no heap snap) | (vite later died) | | 5030 | 9216 |

The 15 GB / 2.31 GB JS heap figure at 567 of 852 remains the best “late mid-load” baseline. A full 852 load was not reached: around 8–17 GB private the first baseline also took down the 47241 Vite server (and, from memory pressure, the 47231/47232 processes on this machine were no longer listening; they were not restarted).

### After caps + BC7 typo fix, but still PNG extras (same convert cache)

No `fell back to the bitmap` and no `bc7-rgba-srgb` errors. Packed albedos upload as BC7/BC1. Extras were still PNG, so process memory stayed in the same band.

| Checkpoint | Unique models | JS heap | Chrome working set | Private | Draws | Instances | Report BC7 / RGBA objects |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 174 of 852 | 174 | 0.81 GB | 4.8 GB | 9.5 GB | 7094 | 5979 | 118 / 870 |
| 292 of 852 | 292 | 1.23 GB | 7.6 GB | 15.5 GB | 5435 | 8178 | 279 / 1738 |
| 383 of 852 | 383 | 1.67 GB | 9.7 GB | 20.4 GB | 5046 | 9318 | 440 / 2581 |
| 546 of 852 | 546 | 2.19 GB | 13.5 GB | 25.0 GB | 3985 | 11248 | 593 / 3410 |
| 641 of 852 | 641 | 2.64 GB | 16.2 GB | 29.4 GB | 3938 | 12113 | 760 / 4163 |

The RGBA column over-counts clones; unique-source accounting was added after this run. The working set is the number that matters, and it still tracks the old 15 GB-at-567 path. The run was stopped at 16 GB to protect the dev servers. Draws fall as box actors are replaced by `BatchedMesh` instances (~14.5k draws at 0 models, ~3.9k at 641).

### After packing extras as BC7 (`shared-v3` / `models-53`)

25 Nov 2026, America/New_York. The convert cache was rebuilt on a restarted 47242, then a headless Chrome run with a 10 GB working-set cap and a 4 GB free-RAM floor launched against 47241. Packed extras did upload as BC7 (caps `webgpu`/`bptc` true; no `bc7-rgba-srgb` fallback). That was not enough to finish the Field: the run stopped at **388 of 852** unique models when free RAM dropped below 4 GB (Chrome working set 8.97 GB, private 14.41 GB). JS heap was **4.67 GB**, already over the 4.19 GB V8 limit, so a full 852-model resident set would OOM the tab.

| Checkpoint | Unique models | JS heap | Chrome working set | Private | Unique tex GPU (BC7 / RGBA / BC1) | Geo CPU | Draws | Frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Mid-load | 157 / 852 | 1.54 GB | 3.71 GB | 6.58 GB | 576 MB (601 / 167 / 17) | 239 MB | 7340 | — |
| Mid-load | 260 / 852 | 2.05 GB | 4.60 GB | 8.30 GB | 977 MB | 424 MB | 5638 | — |
| Mid-load | 310 / 852 | 3.53 GB | 7.31 GB | 11.85 GB | 1.43 GB | 605 MB | 5278 | — |
| Mid-load | 370 / 852 | 4.11 GB | 8.27 GB | 13.43 GB | 1.75 GB | 777 MB | 4995 | — |
| Capped (free RAM floor) | **388 / 852** | **4.67 GB** (limit 4.19) | **8.97 GB** | **14.41 GB** | **1.88 GB** (1226 / 646 / 9 MB; 2142 BC7 / 456 RGBA / 43 BC1) | **889 MB** | **5029** | **52.4 ms (~18 fps)** |

`__forgeMemory()` at the cap: unique textures 2641 with **1.87 GB of CPU-side copies** still held in JS; geometries 888.8 MB (part and template-part were the same 2087 geometries counted twice, plus 446.8 MB in `BatchedMesh`). Snap still sampled (island tree y=131.35319, field y=147.44118).

### After CPU texture release and a resident mesh budget

The previous unlimited `canFillMap` path is what would have OOM'd a full 852-unique-model load. This measurement stopped further loads at **240** unique meshes (or **2.2 GB** JS heap). A farther resident was evicted only when a closer model needed the slot. The hard cap then was **280** unique or **2.8 GB** heap. The working limit now is the 1.2 GB geometry-plus-BC7 budget; 4096 meshes is only the last resort. After the GPU queue is idle for three frames, `releaseTextureCpu` drops mip/ImageBitmap buffers in place so three does not re-upload emptied data.

Remeasured 25 Nov 2026, 10:13–10:23 AM America/New_York, same 10 GB working-set cap, 4 GB free-RAM floor, and 10 GB start-floor, only against 47241/47242. The headless wait stayed on the prepare banner until the 7-minute timeout because `foregroundSettled` still treated leftover deferred names as in-flight; the scene itself was idle at the cap after about 2.5 minutes (259 listed models, 241 unique meshes, 0 queued). Orbit, Snap, and a Field screenshot ran after that. A follow-up change marks the cap as settled so the "Scene ready" banner can hide.

| Checkpoint | Unique meshes (listed) | JS heap | Chrome working set | Private | Unique tex GPU (BC7 / RGBA / BC1) | Tex CPU | Geo CPU | Draws | Frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Mid-load | 147 (154 / 852, 7 boxes) | 0.61 GB | 2.77 GB | 5.52 GB | 380 MB (315 / 63 / 2) | 70 MB | 216 MB | 7372 | — |
| Resident cap, idle | **241** (259 / 852, 18 boxes) | **0.61 GB** | **3.18 GB** | 6.85 GB | **700 MB** (575 / 121 / 4) | **136 MB** | 426 MB | 5632 | — |
| After orbit (field-ready) | **241** (259 / 852) | **0.62 GB** | **3.23 GB** | 6.93 GB | **700 MB** (575 / 121 / 4) | **136 MB** | 426 MB | **5632** | **50.3 ms (~19 fps)** |

Compared with the models-53 cap at 388/852 (JS heap **4.67 GB**, working set **8.97 GB**, texture CPU **1.87 GB**): heap is about **7.5×** lower, working set is about **2.8×** lower and under 4 GB, and CPU texture copies fell from 1.87 GB to 136 MB. The tab no longer crosses the 4.19 GB V8 heap limit. Not every unique model stays resident; the other 593 stay queued as background and stream in when you get closer.

Snap still sampled: island tree y=131.35319 (err 0.00019, median ~15 µs), field y=147.44118 (err 0.00018, median ~5 µs).

Kokiri Forest Child switched and began loading (195 of 482 unique models when the 3-minute wait ended; the same "Scene ready" hang). Mid-load: JS heap 1.11 GB, working set 4.27 GB, private 8.01 GB, 2653 draws, 31.1 ms, texture CPU 89 MB / GPU 749 MB. A handful of `GLTFLoader: Couldn't load texture blob:` messages appeared at the scene switch; they did not crash the tab. Re-check the forest visually after the settled-banner fix.

`tools/measure-field-memory.mjs` also gained a 10 GB Chrome working-set cap, a 4 GB free-RAM floor, and a 10 GB start-floor so a failed run cannot again knock down other servers on the machine. It treats a resident-capped idle load (`0 queued`, `0 loading`, leftover background remaining) as finished.

### GPU picking release (synthetic)

Hyrule Field was not loaded for this change: this environment has no game dump and the 47241 viewer was not running. The 300–400 MB figure in issue #3 is the hoped-for size of the part clones inside the 426 MB Geo CPU line at the 241-mesh cap. That line also includes `BatchedMesh` and terrain, and those stay.

The release itself was measured with three.js `BatchedMesh.addGeometry` on 25 Nov 2026: 240 meshes, 20,000 vertices each, position + normal + uv + a `Uint32` index. The part arrays were **164.79 MB**. Releasing every mesh except one marked keep-cpu (the water case) freed **164.11 MB** of those arrays. The batch buffers stayed allocated. `__forgeMemory()` will show the same drop in the part role, and no drop in the batched role. Picking no longer builds a BVH on the part clone; those trees were extra heap on top of the attribute bytes and are not in the 164 MB.
