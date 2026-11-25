# Rendering performance

This note is the Field-scale memory and draw path: what the viewer already does, what this pass fixed, and which game-engine techniques are still on the table. Measured numbers at the end are from a headless Chrome load of Hyrule Field Child on the `ray-test` dump through `scripts/dev-wasm.bat` (ports 47241/47242). `docs/performance.md` covers the earlier Kokiri-scale instancing and on-demand render work.

## What was blowing the tab

A previous measurement (25 Nov 2026, America/New_York) of Hyrule Field Child, after the Snap dispose fixes, reached **2.31 GB JS heap** (limit 4.19 GB) and a **15.0 GB Chrome working set** (~29 GB private) at **567 of 852** unique scene models. The tab could crash with Out of Memory once the Field finished streaming.

The largest cause on WebGPU was that packed KTX2 textures never actually uploaded as BC1/BC7. `textureSupport` read `renderer.getContext()`, which is a `GPUCanvasContext` with no `getExtension`. Every compressed cap came back false, so `chooseTexture` decoded the JPEG/PNG fallback into an RGBA `ImageBitmap`. Full-resolution RGBA for hundreds of unique maps is several times larger on the GPU than BC7 and also keeps a decoded CPU copy. A second, independent gap: three.js r170's WebGPU `getFormat` maps DXT, ETC2, and ASTC, but not `RGBA_BPTC_Format` (BC7, 36492). Even a correct `CompressedTexture` would have failed to upload.

## Fixes in this pass

- **WebGPU compressed textures.** `textureSupport(gl, renderer)` uses `renderer.backend.hasFeature("texture-compression-bc")` and `"texture-compression-astc"` after `WebGPURenderer.init()` (already awaited). `canUploadCompressed` does not call `compressedTexImage2D` on a context that has no WebGL probe; alignment is still checked. Packed maps upload as BC7/BC1 when the adapter supports `texture-compression-bc`.
- **BC7 format mapping.** A Vite plugin (`frontend/webgpu-bptc-plugin.js`) inserts the missing `RGBA_BPTC_Format` case into r170's `getFormat`, and corrects r170's typo `BC7RGBAUnormSRGB: 'bc7-rgba-srgb'` to the WebGPU name `bc7-rgba-unorm-srgb`. `patchWebgpuBc7Format` in `frontend/src/backend.js` also wraps `backend.textureUtils.getFormat` at init (and when `textureUtils` is assigned later).
- **Normals, ORM, and other data maps are packed too.** Albedos already went through `gpu_image`. Extra maps used `_encode_cached(..., gpu=False)` and stayed PNG, which is most of the unique Field textures (thousands of PNG files in the shared-v1 store vs ~1.3k packed KTX2). Those now pack as BC7 as well. `asDataMap` keeps them in linear color space and repairs the compressed sampler. Model caches are `shared-v3` / `models-53` so existing GLBs rebuild.
- **Template geometry.** After `partsFromTemplate` clones and bakes a mesh, the source template geometries are disposed. Actors keep their part geometries for picking; `BatchedMesh.addGeometry` still has its own copy. That drops one of the three CPU vertex copies per unique mesh.
- **Instrumentation.** In development, `window.__forgeMemory()` (and `viewport.getMemoryReport()`) returns a per-category breakdown: textures by format (unique sources, not clones), geometries by role, unique models vs instances, draw counts, and frame time.

CPU mip/ImageBitmap data for shared textures is not cleared after upload. Clones share the same source, and a later material would re-upload empty mips. Compressed mips are small once RGBA is gone.

## Techniques already in the viewer

### GPU instancing

Identical models already share one `BatchedMesh` per material (`batchKey`). Geometry is uploaded once; each actor is an instance with its own matrix, tint, and visibility. Picking reads the batch id back to the actor. The gizmo writes `setMatrixAt` for the selected actor only. There is no need to break an instance out of the batch to move or edit it. **Stats** shows `inst` for the live instance count.

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

Already implemented in `frontend/src/cull.js`. Off-screen instances are hidden; **Stats** shows `culled`.

## Further wins (researched, not all implemented)

| Technique | Estimated gain | Status |
| --- | --- | --- |
| Compressed GPU textures (BC7/BC1) | **4–8×** texture memory vs RGBA; often the difference between a Field tab surviving and OOM | Implemented this pass |
| Dispose leftover CPU geometry after extract | One of three vertex copies gone; tens to a few hundred MB on Field | Implemented this pass |
| Frustum culling | Already removing off-screen instance draws | Already present |
| Distance streaming / unload far unique models | Caps the working set to a budget; already has background fill, template prune, and first-mip then detail | Expand only if the compressed path is still over budget; screenshots must stay comparable |
| Occlusion culling (Hi-Z / GPU queries) | **10–30%** fewer shaded pixels and some draws in dense Field views | Not implemented |
| HLOD / impostors for far actors | Large GPU save at distance; visible change if the billboard is wrong | Not implemented |
| Texture arrays / atlases | Fewer binds, more meshes in one batch; needs authoring or a pack step | Not implemented |
| Meshlets / cluster culling | Large for huge unique meshes; mesh shaders are not how three r170 draws `BatchedMesh` | Research only |
| GPU-driven indirect draws | Pays off after GPU culling writes the indirect buffer | Not implemented |
| True static merge with breakout on edit | Modest draw reduction on top of instancing | Not implemented |

## How to measure

1. Start `scripts/dev-wasm.bat` so Vite is on 47241 and the API on 47242. Do not use `scripts/dev.bat` on this worktree (the `.venv` is a junction).
2. Open the viewer, turn **Stats** on (it is on by default), and load Hyrule Field Child.
3. In the devtools console, `copy(__forgeMemory())` after the background load finishes. The report includes texture format counts, geometry roles, unique models vs instances, draws, and frame time.
4. Chrome's task manager (or a headless script using `--enable-precise-memory-info`) is the working-set number. JS heap alone under-reports GPU textures.

`tools/measure-field-memory.mjs` is a headless Chrome run against the already-running 47241 server. It records heap, Chrome working set, the stats line, `__forgeMemory()` when present, and a screenshot. It leaves 47231/47232 alone.

## Measured numbers

Dates and clock times are America/New_York, 25 Nov 2026, headless Chrome against `http://127.0.0.1:47241/` and the `ray-test` dump. The stats overlay stays `idle` until the camera moves, so frame time was not on the line during streaming. Draw counts below are from that overlay. A later full-load remasure (after shared-v3 rebuilds extras as BC7) is still needed for the under-4 GB goal.

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

Not remasured in this session. First load after this change reconverts models into the new cache, then unique normals and ORM should upload as BC7 instead of decoded PNG. That is the remaining large memory win. Recheck with `tools/measure-field-memory.mjs` and `copy(__forgeMemory())` once convert finishes. Kokiri Forest Child and local Snap should still be checked after that rebuild.

Visuals: albedo should be unchanged. Data maps stay linear via `asDataMap`. Compare a Field screenshot from the same camera after the cache rebuild.
