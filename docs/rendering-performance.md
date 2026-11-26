# Rendering performance

This note is the Field-scale memory and draw path: what the viewer already does, what this pass fixed, and which game-engine techniques are still on the table. Measured numbers at the end are from a headless Chrome load of Hyrule Field Child on the `ray-test` dump with the editor on ports 47241/47242. `docs/performance.md` covers the earlier Kokiri-scale instancing and on-demand render work.

## What was blowing the tab

A previous measurement (25 Nov 2026, America/New_York) of Hyrule Field Child, after the Snap dispose fixes, reached **2.31 GB JS heap** (limit 4.19 GB) and a **15.0 GB Chrome working set** (~29 GB private) at **567 of 852** unique scene models. The tab could crash with Out of Memory once the Field finished streaming.

The largest cause on WebGPU was that packed KTX2 textures never actually uploaded as BC1/BC7. `textureSupport` read `renderer.getContext()`, which is a `GPUCanvasContext` with no `getExtension`. Every compressed cap came back false, so `chooseTexture` decoded the JPEG/PNG fallback into an RGBA `ImageBitmap`. Full-resolution RGBA for hundreds of unique maps is several times larger on the GPU than BC7 and also keeps a decoded CPU copy. A second, independent gap: three.js r170's WebGPU `getFormat` maps DXT, ETC2, and ASTC, but not `RGBA_BPTC_Format` (BC7, 36492). Even a correct `CompressedTexture` would have failed to upload.

## Fixes in this pass

- **WebGPU compressed textures.** `textureSupport(gl, renderer)` uses `renderer.backend.hasFeature("texture-compression-bc")` and `"texture-compression-astc"` after `WebGPURenderer.init()` (already awaited). `canUploadCompressed` does not call `compressedTexImage2D` on a context that has no WebGL probe; alignment is still checked. Packed maps upload as BC7/BC1 when the adapter supports `texture-compression-bc`.
- **BC7 format mapping.** A Vite plugin (`frontend/webgpu-bptc-plugin.js`) inserts the missing `RGBA_BPTC_Format` case into r170's `getFormat`, and corrects r170's typo `BC7RGBAUnormSRGB: 'bc7-rgba-srgb'` to the WebGPU name `bc7-rgba-unorm-srgb`. `patchWebgpuBc7Format` in `frontend/src/backend.js` also wraps `backend.textureUtils.getFormat` at init (and when `textureUtils` is assigned later).
- **Normals, ORM, and other data maps are packed too.** Albedos already went through `gpu_image`. Extra maps used `_encode_cached(..., gpu=False)` and stayed PNG, which is most of the unique Field textures (thousands of PNG files in the shared-v1 store vs ~1.3k packed KTX2). Those now pack as BC7 as well. `asDataMap` keeps them in linear color space and repairs the compressed sampler. Model caches are `shared-v3` / `models-53` so existing GLBs rebuild.
- **Template geometry.** After `partsFromTemplate` clones and bakes a mesh, the source template geometries are disposed. `BatchedMesh.addGeometry` keeps the copy it uploaded. The part clone is dropped once that copy exists; see GPU picking below.
- **Instrumentation.** In development, `window.__forgeMemory()` (and `viewport.getMemoryReport()`) returns a per-category breakdown: textures by format (unique sources, not clones), geometries by role, unique models vs instances, draw counts, and frame time. The Profile HUD splits that frame into CPU and GPU. It stays closed until opened. See [Profiling HUD](#profiling-hud).

CPU mip and ImageBitmap copies are dropped after the GPU upload (`releaseTextureCpu`). The source is marked so clones do not re-upload emptied mips. `repairCompressedTexture` will not set `needsUpdate` on a released texture.

## Techniques already in the viewer

### GPU instancing

Identical models already share one `BatchedMesh` per material (`batchKey`). Geometry is uploaded once; each actor is an instance with its own matrix, tint, and visibility. Picking reads an actor id from an offscreen target, including the per-instance id for preview `InstancedMesh` draws. The gizmo writes `setMatrixAt` for the selected actor only. There is no need to break an instance out of the batch to move or edit it. **Profile** shows `inst` for the live instance count.

### Static batching (merge vs instances)

True static batching — merging never-moving scenery into one mesh per material and world chunk — is **not** enabled. The instance path already folds those draws while leaving each actor addressable, which is what picking and editing need. A future merge would:

1. Combine static, unselected scenery per material and chunk.
2. On select or move, break that entry out of the merged mesh into a regular or instanced draw.
3. Optionally re-merge when the actor is deselected and still static.

Estimated extra gain on top of `BatchedMesh`: about **10–30% fewer draws** in dense Field scenery, little memory win (vertices still exist, just concatenated). Risk is to picking and to edit/undo. Documented, not implemented.

### Dynamic batching

CPU-merging small, unique, moving meshes every frame is a poor fit here. Most Field cost is unique gyamls and textures; repeats are already instanced. Estimated gain: **low**. Not implemented.

### Indirect / multi-draw

WebGPU supports `drawIndirect` and `multiDrawIndirect`. three r170 has no public `drawIndirect` path for `BatchedMesh`. The viewer already folds same-geometry instances on WebGPU through `groupGeometryDraws` / `_multiDrawCount` (a CPU-side multi-draw, not a GPU-written indirect buffer). A custom indirect path would matter more once cull results are written on the GPU. Estimated gain on the current CPU cull: **small**. Documented, not implemented as a custom buffer.

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
| Texture arrays / atlases | Fewer binds, more meshes in one batch; needs authoring or a pack step | Not implemented |
| Meshlets / cluster culling | Large for huge unique meshes; mesh shaders are not how three r170 draws `BatchedMesh` | Research only |
| GPU-driven indirect draws | Pays off after GPU culling writes the indirect buffer | Not implemented |
| True static merge with breakout on edit | Modest draw reduction on top of instancing | Not implemented |
| Async pipeline compile before a batch is attached | Removes 50–300 ms stream-in hitches. No steady-state frame change | Implemented. WebGPU awaits `compileAsync` with at most 6 in flight, then attaches. See [Shader pipelines](#shader-pipelines) |

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

three r170 builds a render pipeline on the thread that is drawing when the cache misses. `Pipelines` publishes the cache entry before `createRenderPipelineAsync` assigns `pipelineData.pipeline`. A draw in that window calls `setPipeline(undefined)`. `warmWebGLPrograms` stays on WebGL and does not call `compileAsync` on the live WebGPU renderer.

A new `BatchedMesh` is compiled with `compileAsync(batch, camera, scene)` (r170's `targetScene` argument is the live scene, so its lights are included) and only then added. At most **6** of those compiles run at once. More than that stalls the browser's GPU process. Terrain uses the same wait inside `warmTerrain`, after the WebGL warm call, and is still added only once that returns. `guardWebgpuSetPipeline` wraps `backend.draw` and skips a render object whose native pipeline is still missing.

r170's `compileAsync` calls `_renderTransparents(list, camera, scene, lights)`. `render()` calls the same method as `(list, doublePassList, camera, scene, lights)`. On a transparent batch the lights node arrives as the scene, `overrideMaterial` is undefined, and `renderObject` throws `Cannot read properties of undefined (reading 'isShadowNodeMaterial')`. Opaque batches never take that path. `repairCompileAsyncTransparents` puts the empty double-pass list back in the missing slot, and if a compile still throws it puts `_handleObjectFunction` back to `_renderObjectDirect` so later frames keep drawing. The failure is logged once and the batch is attached anyway.

### three.js upgrade

Forge pins `three@0.170.0`. The current npm release checked for this note is **0.187.0** (26 Nov 2026). An upgrade is not in this change.

r170's draw path still calls synchronous `createRenderPipeline` on a cache miss. Later releases have not removed that. Open reports on the 0.18x line: `compileComputeAsync` can resolve with `pipeline` unset and a later `setPipeline` throws ([#35556](https://github.com/mrdoob/three.js/issues/35556), r187), and `compileAsync` can miss the pipeline a later draw actually uses when the color format differs ([#35659](https://github.com/mrdoob/three.js/issues/35659), checked on r186 and current dev). A background-compilation pull request ([#33766](https://github.com/mrdoob/three.js/pull/33766)) was closed without merging. The BC7 `getFormat` patch, the batched-draw fold, and the rest of the r170 WebGPU shims would all have to be re-checked across 17 minor versions. That is not a small upgrade. The compile-before-attach path and the draw guard cover the race on r170.

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
- **obj / batches / sub / draws** — `obj` is render objects other than batches (`drawCalls` minus the batch count on WebGPU; the draw count on WebGL). `batches` is the `BatchedMesh` count. `sub` is the sum of `_multiDrawCount` (the folded WebGPU draws). `draws` is the same total the status label prints.
- **fps, inst, culled, separate** — frame rate (`idle` when the view has settled), live instances, hidden instances, and the draw count those actors would cost without batching.
- **queued, loading, loaded, background** — the model queue. `conv`, `tex`, `dl`, and `up` are this scene's convert, texture, download, and upload times. `warm` is the server warm-up. `detail` is the wait for the first nearby detailed batch. The server convert summary is the last of those lines, and **Dump JSON** keeps the recent convert log with it.
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
