# Performance

Baseline on Bradyn’s PC, Kokiri Forest child Static+Dynamic, before this pass: about 55–65 fps, 200–275 draws, 10553 model draws if each copy was its own draw, and 1.4–1.8M triangles. The viewport status label is the draw counter. **Profile** adds the frame rate, instance count, and cull count.

## Render on change

`renderer.render` runs only from the animation frame requested by `requestRender`. Nothing calls `setAnimationLoop`. A frame schedules the next one only while the camera is moving (orbit damping, fly keys, or right-button look), the gizmo is dragging, or a model or terrain load is still in flight. Water does not animate, so it does not keep the loop. When that stops, **Profile** reads `idle` and the last draw count stays on the status label.

Check: the status label stays on. It starts with the backend (`WebGPU`, or `WebGL (WebGPU unavailable: …)` when a requested WebGPU session fell back) and includes the short git revision and the draw count. See [troubleshooting.md](troubleshooting.md). Turn **Profile** on, orbit or fly, then let go. The HUD should switch to `idle` within a frame of the camera settling. Holding a fly key, dragging the gizmo, or opening a scene (while the progress chip is up) should show a frame rate instead, and the frame time in milliseconds once a run of frames has been measured.

## Instanced repeats

Copies of a model that share an albedo are one `BatchedMesh`: the geometry is uploaded once and each actor is an instance (matrix, colour, visibility). The batch key is the material, so two houses with the same roof share a draw even when they are different actors. Picking reads the batch id back to the actor. The selection tints that instance. The gizmo stays on the actor pivot and writes the instance matrix while it drags. Hide, lock, and the category toggles use one gate: a hidden category drops the instance and cannot be picked; a locked category stays drawn, clicks pass through, and the gizmo does not attach. `frontend/src/instance.test.js` covers those gates. **Profile** includes `inst`, the number of instances. On Kokiri that count should be in the thousands while draws stay in the low hundreds.

Before and after this confirmation: draw count stays near the 200–275 baseline. The sample scene in this repo is too small to reproduce that (about 30 draws and a handful of instances).

The batch object stays at the origin with its matrix frozen. Instance matrices are world matrices, so a moving batch parent would drag every copy. The gizmo writes `setMatrixAt` only for the selected actor’s slots. `BatchedMesh` has no `instanceMatrix`; the drag used to throw on that flag after the first slot.

Check: two copies of one model count as two `inst` and do not add a second draw. Hide a category and those copies disappear and a click misses them. Lock the category and they stay visible, a click misses them, and the gizmo does not attach. Select one grass-topped block and drag it. That block moves, and the other copies around the pond stay where they are. Undo puts that one actor back. The field stays textured for the whole drag and after you release. Releasing posts the edit and refreshes the actor list. It does not fetch the terrain again, and it does not rebuild the batches: the moved copy's matrix is written in place, and the trees, rocks, and houses stay on the meshes already on screen. Undo of a move does the same for each actor whose pose changed. Adding or removing an actor builds the replacement first and then swaps, so the forest does not blink out.

## Batch fill and per-frame cost

A staged model used to build a new exact-fit `BatchedMesh` for every batch key in that arrival, even when a mesh for the same key was already on screen. The next copy of that key allocated another mesh. A texture landing on one material called `queueRebuild()`, which rebuilt every batch, and the pick-id texture for every batch was uploaded again. While the camera was still, the cull walked every instance anyway.

The filler keeps one mesh per batch key. Geometry and instances append. Capacity starts at 8 instances and 64 vertices/indices and doubles when the next add would not fit (`frontend/src/batch-fill.js`). A second mesh for a key exists only when that grow throws. A texture that changes a key restages the actors that use those parts. The other meshes stay. Pick ids are written for the meshes that changed. A click or a hover uploads a batch only when its id list changed, and an orbit does not upload any: visibility marks the batch dirty and the upload waits for the pick. The cull returns before the instance loop when the camera world matrix, the projection matrix, the selection, and the entry serial are unchanged. Fly vectors, the selection tint, and the cull scratch lists are allocated once. The status label and the Profile HUD write text only when the string changes.

**Profile** adds `keys` and `pick` on the draws line. `keys` is the number of distinct batch keys (`batches` should sit on that number, plus a small overflow). `pick` is how many id textures were uploaded since the previous sample. The stream line adds `compile` for the longest wall-clock wait while the meshes from one fill were compiled. That wait is `compileAsync`, one mesh at a time, and it is not inside the submit timer. `__forgeMemory().frame` includes `batchKeys` and `pickUploads`.

### Before

27 Nov 2026, Bradyn’s Windows machine, Hyrule Field Child, Static + Dynamic, mid-stream. This is the measured capture. This environment has no game dump, so it is not re-measured here.

| | latest |
| --- | --- |
| cpu | 632.9 ms (avg 75.9, p95 104.9) |
| update | 36.4 ms |
| cull | 14.7 ms |
| submit | 581.9 ms |
| gpu | 4.0 ms |
| frame | 75.1 ms |
| obj / batches / sub / draws | 4634 / 2253 / 656 / 5920 |
| tris / meshes / tex / heap | 1.9M / 128 / 595 / 1532 MB |
| streaming | conv 31289 ms, dl 253151 ms, up 14245 ms |

The 632.9 ms CPU sample includes the 581.9 ms submit spike. The average CPU is 75.9 ms and the p95 is 104.9 ms. `frame` is the gap between animation callbacks, not the CPU work. GPU time is 4 ms, so the spike is on the CPU inside `renderer.render`.

### After

No Field was loaded here. The numbers below are from `frontend/src/batch-fill.test.js` on this machine (Node, three r187, no GPU) and from the cull/pick predicates.

| What | Before | After |
| --- | --- | --- |
| 400 arrivals, 2 keys each, 50 distinct keys | 800 `BatchedMesh` constructions | 50 |
| 80 box instances of one key | 80 meshes, 4.0 ms | 1 mesh, 0.5 ms, grown 8 → 128 slots |
| one more 40,000-vertex geometry on that mesh | a new mesh, or a full rebuild | same mesh, grown to 65,536 vertices (uint32 indices), no overflow |
| cull, camera and selection unchanged | the instance loop | returns before the loop |
| pick-id uploads while orbiting, no click | every dirty batch, every frame | 0 |

On the Field capture, `batches` should move from 2253 toward `keys` once streaming settles. How far it falls depends on how many of those 2253 meshes shared a key. `keys` is the number to compare, not a guess from the 595 textures: the batch key also includes the mesh family, so two different meshes with the same image stay apart. The next section removes that family suffix and, on WebGPU, packs same-size compressed images into one array.

### Submit spike

`submit` is the CPU time around `renderer.render`, including each batch’s `onBeforeRender` (`scheduleMergedDraws` fills r187’s merged draw ranges; the WebGPU path does not fill them itself). `compileAsync` runs while a mesh is built, before `scene.add`, and it is awaited one at a time. `PIPELINE_COMPILE_LIMIT` stays 1: r187 keeps a single `_precompilationState`, and a second compile in that gap throws. That wait shows up as `compile` on the stream line and as time in the streaming frame gap, not as `submit`.

The 581.9 ms spike lines up with thousands of batch objects and 5920 draws, against 4 ms of GPU time. Fewer meshes is the change that can move `submit`. A custom indirect buffer, or raising the compile limit, is out of scope. If `batches` is near `keys` and `submit` is still hundreds of milliseconds, the rest is per-object work inside three’s render, not another rebuild.

Check: rebuild the frontend, restart the editor, hard-reload, open the same Field view with **Profile** on. On a still camera, `cull` should read about 0. While orbiting with no click, `pick` should stay 0. After streaming settles, `batches` should be close to `keys`. Compare `submit` (latest, avg, p95) with the table above, and read `compile` on the stream line if a hitch lands while models are still arriving. Textures, GPU picking, the gizmo, and selection should behave as before.

## Fewer batch keys

After streaming settled, Field Child was one mesh per key: `batches` and `keys` were both 1004. The 3086 batches figure was mid-stream, while arrivals were still appending, and it is not the steady state. `submit` was 44.5 ms against 4.7 ms of GPU time and 0 ms of cull. That submit time is the CPU cost of about a thousand render objects inside `renderer.render` (a node material, its bindings, and a pipeline). Per-object frustum culling and opaque sorting are already off, so the instance walk is not the 44.5 ms. A quiet opaque mesh now skips the redundant merged-draw fill; that does not by itself turn 44 ms into a few milliseconds. Fewer meshes does, because the cost scales with the object count.

The mesh family is no longer part of the batch key. `materialKey` stays on the part for the texture manifest. Two shapes that sample the same images, alpha, side, tint, and vertex layout share one mesh. The vertex layout includes each attribute's item size, so a geometry that cannot share a `BatchedMesh` stays on its own key instead of being dropped.

On WebGPU, a compressed image (BC1, BC3, BC7, ASTC) joins an array with every other image of the same width, height, mip count, format, color space, wrap, repeat, offset, and uv channel. The batch key names that group, not the image. Each instance writes its layer into a float texture, and the colour node samples `texture(array, uv).depth(layer)`. Normal and roughness maps that pack the same way use the green and blue channels of that texel. Occlusion that shares the roughness image is the red channel of that same sample. A group holds at most 256 layers; the next image keeps its own key. Growing the array allocates a new texture and points the batches at it, because the GPU layer count is fixed. Blend, terrain, water, and helpers stay on the per-image key. WebGL does not build arrays. Material tint stays in the key: `setColorAt` is the selection tint, and a shared white material painted models a flat cream once before.

The array is not assigned to `material.map`, `normalMap`, `roughnessMap`, `metalnessMap`, `aoMap`, `emissiveMap`, `alphaMap`, or the other stock slots. Those slots compile `textureSample(texture, sampler, uv)`. A `texture_2d_array` needs the layer as a fourth argument (`array_index`). `CompressedArrayTexture` is not `isArrayTexture`, so three does not insert that index on its own. One invalid fragment pipeline makes the command buffer invalid, and every later draw in the frame is dropped. That is why a Field view with the array left on `map` (and on `aoMap`, when the occlusion map was the same image) drew flat land and left the water on its unshaded colour. The colour, normal, roughness, occlusion, emissive, and alpha nodes sample `userData` with `.depth(layer)`. The id-buffer mask does the same when the cutout is an array. A clone, including the no-uv clone, clears those slots. WebGL never creates the packer, so its stock samplers only see 2D images.

### Before

27 Nov 2026, Bradyn’s Windows machine, Hyrule Field Child, Static + Dynamic, Profile on, after streaming settled. The 3086 batches number from the same session was mid-stream. This environment has no game dump, so the Field column is not re-measured here.

| | settled |
| --- | --- |
| cpu | 56.9 ms (avg 61.1, p95 70.4) |
| update | 12.4 ms |
| cull | 0.0 ms |
| submit | 44.5 ms |
| gpu | 4.7 ms |
| obj / batches / keys / draws | 9886 / 1004 / 1004 / 5872 |

### Measured here

`frontend/src/texture-array.test.js`, Node, three r187, no GPU:

| What | Before this change | After |
| --- | --- | --- |
| 8 mesh families, one shader key | 8 keys (the family was appended) | 1 key, 1 `BatchedMesh`, 8 instances |
| 40 distinct BC7 images, one size class (8×8, sRGB) | 40 keys | 1 array, 40 layers, 1 key |
| the same images split across 8×8 and 16×16, or sRGB and linear | 40 keys | 2 or 3 arrays, one key each |
| a group already at its layer cap | the next image joins the array | that image keeps its own key |

### Windows capture with the array on `map`

27 Nov 2026, same machine, WebGPU, AMD, three 0.187.0, Hyrule Field Child, Static + Dynamic, Profile on. The family merge worked (`keys` and `batches` both 148). The fragment shader did not: `textureSample` of a `texture_2d_array` had no `array_index`, the pipeline was invalid, and the command buffer was dropped. `submit` at 111.3 ms is that failure, not the cost of 148 batches and not the 7922 draw counter. `sub` was 403, which is lower than the settled 5872 draws from before. GPU time stayed near 5 ms, which is what a frame that never ran the draws looks like. `compile` 1710 ms is the streaming total, including the rejected shaders.

| | before (settled, per image) | this capture (array on `map`) |
| --- | --- | --- |
| cpu | 56.9 ms (avg 61.1, p95 70.4) | 148.2 ms (avg 153.7, p95 220.5) |
| update | 12.4 ms | 37.0 ms |
| submit | 44.5 ms | 111.3 ms |
| gpu | 4.7 ms | 5.7 ms (avg 5.2, p95 6.0) |
| frame | | 183.2 ms |
| obj / batches / keys / draws | 9886 / 1004 / 1004 / 5872 | 7271 / 148 / 148 / 7922 |
| sub | | 403 |

### Settled Field after the layer-index fix

27 Nov 2026, same machine, WebGPU, AMD, three 0.187.0, Hyrule Field Child, Static + Dynamic, Profile on, after streaming settled. Land has albedo. The console has no `texture_2d_array` sample error. `submit` 31.2 ms beats the per-image 44.5 ms. `keys` is 466, not the 148 from the broken capture: that run had only partly streamed (`dl` 85160 vs 285421, meshes 135 vs 275, tris 994.9k vs 2M). The layer-index fix did not change the batch key. What did cost memory is the packer keeping every source mip buffer and a spare-capacity copy of the array. `tex` 1692 counted both. Heap before this change was about 1075–1532 MB.

| | before #70 (settled) | this capture |
| --- | --- | --- |
| cpu | 56.9 ms (avg 61.1, p95 70.4) | 42.4 ms (avg 57.3, p95 87.2) |
| update | 12.4 ms | 11.2 ms |
| submit | 44.5 ms | 31.2 ms |
| gpu | 4.7 ms | 5.2 ms (avg 5.2, p95 5.8) |
| frame | | 46.2 ms (avg 48.9, p95 65.0) |
| obj / batches / keys / draws | 9886 / 1004 / 1004 / 5872 | 3970 / 466 / 466 / 5123 |
| sub | | 466 |
| tex / heap | 455–595 / 1075–1532 MB | 1692 / 3736.2 MB |
| streaming | | conv 7107.3, dl 285421.3, up 3692.5, compile 530 ms, first edit 19.7 MiB |

The packer now starts at one layer, frees the previous array's mip buffers when a class grows, and compacts back to the live layer count. Once those bytes are in the array, the per-image CPU buffer is released unless a drawn 2D material (blend, water, terrain, helper) still samples it. A full BC7 that replaced a low bitmap is rebatched onto that size class instead of leaving both copies resident. `tex` counts the textures the batches actually sample (the arrays, the layer lookups, and the images that stay 2D), not the packed-away sources.

`Material.clone` JSON-serializes `userData`. The array batch was cloned with `forgeLit` still holding the compressed maps, which is the `Unable to serialize Texture.` flood. The clone pulls those textures out first. Array, layer, and pick textures are stored on non-enumerable fields, so a later serialize does not walk them.

`keys` stays split by array class (size, mips, format, color space, wrap), and by helper, water, terrain, blend, tint, and images that cannot pack. Tint stays in the key. Dump JSON's `keyReasons` and the Profile detail line count those reasons (`array`, `image`, `blend`, `helper`, `water`, `terrain`, `tint`).

### Field after releasing the CPU copies (`137ef86`)

27 Nov 2026, same machine, WebGPU, AMD, three 0.187.0, Hyrule Field Child, Static + Dynamic, Profile on. Heap is back to 1180.8 MB and the serialize warnings are gone. The console instead reports 874 `writeTexture` validation errors, all the same kind: the layout size is 16, 64, 144, 256, 1024, 4096, 9216, 16384, 65536, or 262144 bytes and the buffer is 0 bytes, offset 0, one layer. The targets are BC7 arrays (512² at 2–26 layers, 768² at 2–6 layers, Unorm and sRGB) and plain 2D images (512, 768, 1024). A lot of land looks missing or flat while the field is still streaming. This capture is mid-stream (`textures` 85/116, two still loading, `dl` 146188 against the settled 285421).

| | settled before the memory fix | this capture (streaming) | after streaming settled |
| --- | --- | --- | --- |
| cpu | 42.4 ms (avg 57.3, p95 87.2) | 82.8 ms (avg 66.3, p95 89.5) | |
| update | 11.2 ms | 26.5 ms | |
| submit | 31.2 ms | 56.9 ms | 44.3 ms |
| gpu | 5.2 ms | 6.4 ms | |
| obj / batches / keys / draws | 3970 / 466 / 466 / 5123 | 7331 / 164 / 164 / 7823 | |
| sub / pick | 466 / 0 | 328 / 1 | |
| tex / heap | 1692 / 3736.2 MB | 428 / 1180.8 MB | heap stayed at 1180.8 MB |
| keys | | array 19, image 50, blend 86, helper 2, water 7 | |
| streaming | conv 7107.3, dl 285421.3, up 3692.5, compile 530 ms | conv 4079, dl 146188.3, up 2084.6, compile 722.5 ms | |

`submit` at 44.3 ms is no better than the per-image 44.5 ms, and worse than the 31.2 ms from the run where the array uploads were valid. The arrays stay on. That 31.2 ms run is the measurement that a valid upload beats 44.5. The 44.3 ms is this bug, not the cost of packing.

What the extra time is: `retireArray` emptied every mip (`Uint8Array(0)`) and did not clear `source.dataReady`, and `settlePackedArrays` released each packed source's CPU buffer before three had uploaded it. `updateTexture` then calls `writeTexture` with that empty buffer. The error is uncaptured (`Uncaptured WebGPU GPUValidationError`), so the call does not throw and three still stores the texture version. A settled, idle frame does not retry the same texture unless something sets `needsUpdate` again. Compact is not per frame. It runs when a batch is rebuilt, and it returns immediately once capacity equals the layer count. There is no per-frame array growth in the render loop. The 874 errors are one pass over every mip of every texture that was still bound with an empty buffer, and the rebuilds that were still running as the field finished (detail BC7, a class that grew) did it again inside `submit`. That is the gap from 31.2 ms to 44.3 ms.

The packer now refuses that write. A buffer is released only after the GPU texture exists (`{ uploaded: true }`), and `dataReady` is cleared before the bytes are dropped. A grow or compact whose CPU chain is already gone copies the live layers with `copyTextureToTexture` and uploads only the new layer, and only when that layer's mip buffers are non-empty. A texture with an empty mip never gets `needsUpdate`. The source bytes stay until the array is on the GPU, then they are dropped, so the heap should stay at or under about 1.5 GB (1180.8 MB on this run).

### Field after the empty-write fix (`a351efc`)

27 Nov 2026, same machine, WebGPU, AMD, three 0.187.0, Hyrule Field Child, Static + Dynamic, Profile on, after streaming settled. The 0-byte `writeTexture` errors, the serialize warnings, and the missing `array_index` errors are gone. The console has 1265 `copyTextureToTexture` errors instead. `copySize.width` is 6, 3, or 1, which is not a multiple of the BC 4×4 block. Those are the tail mips of a 768 image (768→6, →3, →1). A 512 chain fails the same way at 2 and 1. Each mip was its own command buffer, so one bad mip invalidated that buffer and the grow lost the layers already recorded in it.

| | valid uploads | `137ef86` settled | `a351efc` settled |
| --- | --- | --- | --- |
| cpu | 42.4 ms (avg 57.3, p95 87.2) | | 64.0 ms (avg 63.9, p95 77.7) |
| update | 11.2 ms | | 23.4 ms |
| submit | 31.2 ms | 44.3 ms | 41.6 ms |
| gpu | 5.2 ms | | 6.2 ms |
| obj / batches / keys / draws | 3970 / 466 / 466 / 5123 | | 6973 / 399 / 399 / 8058 |
| sub | 466 | | 686 |
| tris / meshes | 2M / 275 | | 3.4M / 285 |
| tex / heap | 1692 / 3736.2 MB | 428 / 1180.8 MB (mid-stream) | 1042 / 1927.1 MB |
| keys | | array 19, image 50, blend 86, helper 2, water 7 | array 45, image 163, blend 173, helper 6, water 12 |
| compile | 530 ms | 722.5 ms | 442.6 ms |

`submit` 41.6 ms is still above the 31.2 ms bar, and heap 1927.1 MB is above 1.5 GB. `image` (163) and `blend` (173) are most of the 399 keys.

The copy now uses the physical block size, `max(4, ceil(mip / 4) * 4)`, so 6 becomes 8 and 3 or 1 becomes 4. Every mip and every layer of one grow goes into one encoder, and only after those extents are computed. A failed grow disposes the destination it just allocated and leaves the previous array in place, so the two do not both stay live. The render loop does not grow again on its own. A refused image keeps its own key until the next batch rebuild tries once more. That retry is what turned later images into the 163 `image` keys: the class's CPU copy had already been released, the GPU copy threw on the tail mip, and the image was not recorded.

Blend stays per image. A blend sample reads the base map, the moss map, and the moss ORM on two UV sets with a vertex mask. Putting a0, a1, and c0 into arrays means a new blend shader, not a one-line eligibility change, so it is not in this fix. Those 173 keys are 43% of 399. They would fall by however many blend images share a size class. They would not fall to 1.

Check: `npm run build --prefix frontend` (dependencies are unchanged, three stays 0.187.0), restart the editor, hard-reload. Open the same Field view with **Profile** on. While it streams and after it settles, the console should have no `copyTextureToTexture` block-size error, no 0-byte `writeTexture`, no `Unable to serialize Texture.`, and no `textureSample` of `texture_2d_array` with no `array_index`. Land should show albedo during streaming and up close. Water should not stay on the flat teal stand-in. After streaming settles, `submit` should be in the 31 ms range, clearly under 44.5 ms, and heap at or under about 1.5 GB. `image` should drop as the 768, 384, and 96 classes join their arrays. `blend` stays. `keys` and `batches` stay equal. `cull` stays about 0. Textures, GPU picking, the gizmo, and selection should behave as before.

## Frustum and draw distance

Each instance has its own world bounding sphere: the mesh sphere transformed by that actor’s matrix, not a sphere at the world origin and not the distance from the camera to the origin. The gap used for **Draw** and the size rules is the distance to the sphere surface, so a large mesh is not culled while its bounds are still inside the cap. A sphere outside the frustum is hidden. **Draw** can also drop anything past 200, 400, or 800 m. **Auto** leaves the size rules (small props with no far mesh fade by 56 / 96 / 180 m). The selection is not distance-culled. The field is not in this loop and its mesh is not frustum-culled, so flying back cannot drop the terrain on a bad sphere. The camera far plane grows as you fly, from at least 8000 m. `culled` in **Profile** is the hidden instances. The batch mesh itself is not frustum-culled; a sphere on the untransformed geometry used to hide every copy once the camera looked away from the origin.

Occlusion culling is not in this slice. A depth prepass over the whole forest is not cheap enough to pay for here, so it stays a later item.

Check: **Draw** on Auto, fly well back from Kokiri. Trees and the field stay. Tris should fall as actors switch to the coarse mesh, and should not collapse to a couple of thousand with `inst` at 0. Set 200 m and distant small props drop while `culled` rises. The selected actor stays drawn if you fly away from it. The grid and helper boxes are not the only thing left.

## LODs

A shape that already has a coarser BFRES level keeps that mesh as `lod1`. Anything else with more than about 300 triangles gets a meshoptimizer simplification at import, with the border locked so the outline does not collapse. The old grid quantize remains only when Node cannot run the simplifier. The view switches on screen size: under about 64 pixels the coarse mesh is drawn, and the full mesh comes back when the actor is larger on screen. A selected actor stays on the full mesh. The 44 m switch remains only for the first frames before the canvas height is known. Past the small-prop distances, or under about three pixels, a model that has a far mesh stays on that coarse mesh. It does not hide both levels. A model with no far mesh still drops. If the coarse mesh failed to build, the full mesh stays instead of drawing nothing.

Before this slice the far mesh was a grid quantize and it came on past 44 m even when the actor still filled the screen. A later pass then hid both levels once a tree was past 180 m or smaller than about three pixels, so a wide view dropped to helper boxes. After this fix, a wide view should draw fewer triangles than the 1.4–1.8M full-detail baseline once the coarse meshes are on screen, and those coarse meshes stay on screen. The sample scene’s meshes are too small to grow a far level.

Check: open Kokiri, fly back until trees are small, and **Profile** tris should fall while the trees and the field are still there. Fly toward one tree and it should sharpen before it fills the screen. Select an actor and fly away: that one stays on the full mesh. The first open of the meshopt build still uses cache `models-22`; this cull change does not rebuild it.

## Import cache

Cached glbs use `EXT_meshopt_compression` on the geometry. The compression does not quantize positions or texture coordinates, so a decoded vertex matches the one that was packed, including UVs outside 0..1. Textures stay JPEG or PNG. A KTX2 encode was tried and rejected: three r170’s Basis transcoder returns an empty image for that encoder’s files, so the field and the actors would draw black. The view still loads a meshopt glb, and it has a KTX2 loader pointed at `/basis/` for when an encoder round-trips.

Before: a 40×40 grid glb was 70,788 bytes of plain glTF. After: the same triangles are 14,040 bytes, about a fifth, and a decoded vertex still matches the one that was packed. Kokiri’s sample actors are already a few kilobytes, so the byte cut shows up on the dense meshes. Draw count is not what this slice moves. That compression used prefix `models-21`. The atlas cache below moves the prefix to `models-22`, so the first open of this build rebuilds the glbs again.

Check: the first open after this build rebuilds the cache. Models should look the same as before, not black and not shifted. A wide view’s triangle count should still drop when actors are small on screen.

## Terrain atlas

The field is one atlas: up to 16 MaterialAlb layers plus an earth cell, each resampled to 128 pixels and packed into one image. That PNG (or JPEG) is stored under `atlas/` in the model cache. The key is the cell bytes and `atlas-1`. It does not include the glb prefix, so a converter bump that rebuilds the terrain glb reads the packed image instead of packing it again.

Before: every terrain rebuild packed the cells. A 17-cell solid atlas took 16 ms. After: the same cells read back in 0.7 ms. The sample scene has no MaterialAlb, so this shows up on a real dump when the glb cache misses and the layers have not changed.

Check: open a scene with a heightmap, quit, pull this build, and open it again. The field should look the same. A second rebuild of that scene (for example after the `models-22` miss) should not repack the atlas; `atlas/` in the model cache gains one file per distinct layer set and then stays put. Drag an actor and release. The field should not go black while the edit saves. Opening a different scene disposes the previous field. A refine of the field that is already loading can still reuse the two most recently assembled meshes for that load. Preview packs include the main terrain layer only.

## Unload on map change

`openScene` calls `viewport.unloadScene()` before the scene request, including a reload of the same banc. That runs before `setActors`. An actor edit or undo still patches in place and does not unload the map.

`unloadScene` bumps the model, terrain, and texture generations and replaces the scene `AbortController`. In-flight model, texture, terrain, ground, lighting, behavior, and surface fetches for the old map are aborted. A result whose generation does not match is dropped. The terrain index worker is terminated. Geometry decode jobs are cancelled. The behavior panel's graph hooks are cleared and its DOM is replaced. The graph view removes its document keydown listener when that stage is no longer connected, so the previous actor's graph is not kept by the listener closure.

Geometries, `BatchedMesh` objects (including their matrix, indirect, and color textures), materials, decoded and compressed textures, and per-map pick targets are disposed. The link from a template geometry back to its batch (`userData.forgeBatch`) is cleared before the geometry is disposed, so a disposed batch is not kept alive by the template. Shared viewport resources stay: the actor box, the edge geometry, the effect gizmo, and the sub-terrain material. Actor entries, parsed templates, the template cache, `metaCache`, the texture queue, ground samples, the selection, and the gizmo are cleared. The wasm ground pack is freed with `disposeGroundSurface`, including when the next map has no terrain.

The shared image cache stays capped at 128. URIs the unloaded map touched are evicted, so the next map does not keep the previous map's decoded images. The two-field terrain cache still applies while one field is loading and refining. A map change disposes that field instead of holding it for the next map.

The status label includes `gpu N geom / N tex · heap XMB` when `performance.memory` is available (Chrome, including with precise memory info). Otherwise the heap reads `n/a`. Each unload, and the first settled model load of a generation, logs `forge memory unload:` or `forge memory load:` with the same counts, plus the wasm pack size when a surface is still resident. In a dev build, `__forgeMemory().gpu` is the same `renderer.info.memory` pair.

Check: with the bundled sample dump, open Kokiri Forest, then Hyrule Field, then Kokiri again, and click Kokiri a few more times. After each switch the status line and the console log should sit near the single-map counts, not climb. On a real dump, do the same between two large maps and watch the browser process in Task Manager. `frontend/src/scene-unload.test.js` loads a stand-in map, unloads it, and checks that no per-map object is still referenced.

## Shared texture cache

A decoded BNTX is stored under its Tex path and the decoder version, not only under a hash of that one model's copy of the bytes. Castle bricks that many actors name are decoded once. The next model reads `bntx/*.bwtx`. The in-memory cache is still per process, so another worker hits the file. An embedded texture with no path keeps the blob hash. The file also keeps the linear blocks (cache `bntx-6`) so a later encode can upload BC1, BC3, or BC7 without decoding them again.

## GPU textures

An albedo in the glb is `image/ktx2` with `KHR_texture_basisu`. The bytes are a small container: a KTX2 of the blocks, then the JPEG or PNG. three r170's KTX2 loader raw-uploads only uncompressed formats, ASTC 6x6, and one float ASTC. BC1 and BC7 throw `Unsupported vkFormat`. A Basis file is vkFormat 0, and that is the transcoder path that came back as an empty image. This machine has no basisu or toktx binary, so UASTC and ETC1S were not encoded and decoded again. The files we write use a real vkFormat and are not Basis.

The view uploads the blocks with `CompressedTexture` when the GPU has the matching extension: `WEBGL_compressed_texture_s3tc` for BC1 and BC3, `EXT_texture_compression_bptc` for BC7, `WEBGL_compressed_texture_astc` for ASTC. Native BC1 and BC7 mip 0 are copied through. Lower mips are encoded in that same format. BC3 and ASTC are re-encoded to BC7 mode 6 for the whole chain, because a desktop GPU usually has BPTC and not ASTC. A swizzle pass that had to change the footprint encodes from the decoded pixels. Normals, ORM, and other extra maps are packed the same way. The viewer treats those as data maps. See [rendering-performance.md](rendering-performance.md).

Without the extension, the trailing JPEG or PNG is decoded with `createImageBitmap`, off the main thread. A vkFormat of 0 still goes to three's Basis transcoder under `/basis/`. Meshopt still compresses the geometry and copies the image bytes through.

The glb prefix is `models-67` and decoded images are `bntx-6`, so the first open after this build misses the glb cache. Mode 6 blocks match the previous encoder. PNG images in the glb are zlib level 1. The file bytes change, and the pixels stay the same. The decoded BNTX files are reused. The first GPU build left castle walls near black and roofs, trees, and some ground flat white, with an empty Problems list. GLTFLoader calls `loader.load` on the KTX2 loader. The stand-in only implemented `parse`, so `load` threw, the loader swallowed the error, and the material kept no map. A one-level KTX that does upload still samples black: three then assigns LinearMipmapLinear from the sampler, and a compressed texture cannot invent the missing mips. `prepareMaterial` also used to turn `generateMipmaps` back on, which WebGL rejects for BC formats. Each albedo now stores the whole chain, the loader implements `load`, and a compressed map is forced to that chain's filter after the sampler write. A size that is not a multiple of 4 stays a JPEG or PNG, because WebGL rejects that level 0. A compressed upload the GPU refuses falls back to that bitmap and shows up in Problems. `?textures=bitmap` (or Settings → GPU textures off) uses the bitmap on purpose, for an A/B against the blocks. Headless Chrome with ANGLE SwiftShader, which exposes S3TC and BPTC, drew a 4-level BC1 quad and its JPEG through GLTFLoader as the same pixel. A one-level file stayed that color on SwiftShader and kept LinearMipmapLinear until the repair pass set Linear. A desktop GL that samples an incomplete compressed texture as black is the case that repair covers. The sample Kokiri walk on this machine, one core, is 16 models, 2 converted, 76.4 ms convert, and 0.4 ms texture the first time. The second run is 2 cache hits and 0 ms. That is server time. There is no display GPU here, so the browser upload is not in that number. Retail Kokiri and Hyrule Castle are not in this checkout. Time them with the warm command in Stream timings: the first run is cold, the second is warm, and **Profile** shows `conv`, `tex`, `dl`, and `up`.

## Converter cores

The process pool defaults to one less than `os.cpu_count()`, and never below 1. One core stays in the server process and does not start a pool. **Settings** in the top bar is a slider and a number from 1 to the machine's core count. It is saved in the config file and applied between converts: a job already running finishes on the old pool, and the next one uses the new count. `OCARINA_FORGE_WORKERS` overrides the slider. While that variable is set, Settings says so and does not change the running count.

## Disk cache

The on-disk cache is `~/.cache/ocarina-forge` (on Windows, `C:\Users\<you>\.cache\ocarina-forge`). The server prunes it on a background thread at startup: folders from older cache versions go first, then the least recently used files until the folder is under about 90% of the cap. The cap defaults to 20 GiB. `cacheLimit` in the config file and `OCARINA_FORGE_CACHE_LIMIT` change it; `0` means unlimited. The environment variable wins. `GET /api/status` reports `cache.bytes` and `cache.limitBytes` once the walk has finished. Quit the editor and delete that folder to clear it by hand. Everything in it is rebuilt on the next open.

## Workers

The client was measured before moving work off the main thread. Nothing in that set was faster in a worker, so none of it moved.

Meshopt decode uses three’s `MeshoptDecoder` on the main thread. A version-0 vertex buffer, the same encoding the glbs use, decoded in Chrome headless:

| work | main thread | 4 workers |
| --- | --- | --- |
| 16 × 20k vertices (240 KB decoded each) | 1.7 ms | 7.9 ms |
| 8 × 120k vertices (1.4 MB decoded each) | 10.6 ms | 25.4 ms |

The worker path did yield the main thread, and the round trip was still slower than the decode. `useWorkers` stays off.

Behavior layout is a depth walk. 160 nodes (the cap the panel draws) took 0.2 ms. 1,778 nodes took 2.7 ms. 4,000 nodes took 4.4 ms. Copying the graph into a worker would cost more than the walk. The cap is there because the DOM for a full retail graph is the slow part, not the positions.

Search over 8,000 actors took 0.16 ms to list and 0.02 ms for a query. That stays on the keystroke.

BC and ASTC decode, zstd, and pack reads run in the server process when a model or a scene is built. Albedo arrives as a GPU block image (see GPU textures) with a JPEG or PNG behind it. Normals, ORM, and the terrain atlas stay PNG or JPEG. Meshopt glbs are unchanged. Terrain atlases and LOD meshes are built at import and cached.

## Prepared scene streaming

A prepared scene packs a low-detail mesh for every drawable actor type in the map. Copies share the same geometry and are drawn in spatial batches. The model queue serves the selection and nearby geometry while the preview loads. Once the preview is on screen and nearby work has drained, a web worker fetches one distant model at a time at low priority. Moving the camera or requesting nearby detail pauses that work. The browser can reuse the downloaded GLBs from its HTTP cache. Forge parses and uploads a detailed model when the camera approaches it or an actor using it is selected. The viewer assembles model batches over successive frames. Textures with a reduced tier load at up to 256 pixels on their longest edge. Visible nearby models and the selection request higher-resolution textures as needed.

**Profile** reports `detail` from viewport setup to the first nearby visible detailed batch, active streaming frames over 25 ms, and browser-reported model and texture transfer bytes before the first gizmo move. Browser-cache hits contribute zero transfer bytes. Use the same map, view, and cache state when comparing runs.

## Stream timings

Every model convert records two server times. `convertMs` is the whole conversion. `textureMs` is only the texture decode that missed the decoded-image cache, so a warm open stays near zero on that number. The glb meta keeps the cold timing. A later open replaces the timing it returns with `cache=hit` and does not add those milliseconds to the server totals.

The server prints one line per convert on the process log:

`model Npc_Kokiri_F_01 cache=miss convert=120.5ms texture=80.0ms`

`GET /api/stream` is the same list, plus how many misses and hits this process has done and the summed miss times. **Profile** adds the scene's own sums: `conv` and `tex` from those timings, `dl` for the glb download, and `up` for glTF parse plus GPU upload. The HUD also shows the server summary, and a profile dump keeps the recent convert lines.

The sample Kokiri scene on this machine (11 actors, two real meshes) was 103.6 ms cold and 1.3 ms warm before this instrumentation. Retail Kokiri Forest and Hyrule Castle are not in this checkout. On a dump, the first run is cold and the second is warm:

```bash
.venv/bin/python -m ocarina_forge warm Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs --scene-only --dump ~/Games/Longshot/rc/v3.0.0
.venv/bin/python -m ocarina_forge warm Banc/HyruleCastle/HyruleCastle_Dynamic.bcett.byml.zs --scene-only --dump ~/Games/Longshot/rc/v3.0.0
```

Those are the placement files. The Static files in the same folders have no Actors array; warm skips them. `--scene-only` times that scene and does not walk neighbors. Each model prints `name cache=… convert=…ms texture=…ms` as it finishes. The last line is `warm done` and includes the wall time. Run a command twice. A cold line says `cache=miss` and has a texture time. The next run of the same models says `cache=hit` and texture time 0. `warm --all` walks every scene.

## Background warm-up

Longshot's `--with-editor` build runs `scene-preview --all --prepare-missing --share-textures`. Forge prepares one coarse preview per map variant and converts each distinct actor type once per run. It reuses valid converted models, content-addressed shared textures, and preview packs with matching inputs. Large maps get up to 160 coarse model types. The browser keeps that scenery visible while it brings in full models near the camera or on selection. Without a preview, it streams the full scene at up to two simultaneous model loads.

### TSCB resource inspection

`ground-debug --resources-only` parses resource metadata on demand. It adds no work to editor boot, normal terrain loading, or `--with-editor` scene preparation. On the installed OoT build, the Hyrule Field resource parser took a median 3.6 ms across 80 runs on an in-memory index, compared with 0.85 ms for the existing tile-only reader. Parsing all 25 indexes and 9,304 tiles took a median 36.4 ms across 16 passes. These timings exclude disk reads, command startup, and report serialization.

### Longshot shader reference

Build the local converter with `cargo build --locked --release --manifest-path tools/wgsl/Cargo.toml`. Set `OCARINA_FORGE_SHADER_SELECTION` to a JSON file containing `translator` (the executable's absolute path), `cache` (a local output directory), and `stages` (32-digit hexadecimal stage hashes, first Longshot hash word followed by the second). Set `sample` to a positive number for an evenly spaced audit of the index. An optional `store` overrides the Longshot shader store directory. Use `node frontend/tools/prepare-longshot-shaders.mjs path/to/selection.json` for per-stage results.

Longshot already calls Forge's frontend build after preparing its Vulkan shaders. The Forge build reads only the configured selection, verifies each stage against Longshot's content hash, translates it with Naga, and caches WGSL that passes core validation. Keep the selection and output outside the checkout. Translated stages are reference data for a future draw comparison. Connecting them to an editor material still requires matching vertex inputs, resource bindings, fixed render state, and vertex and fragment interfaces.

Longshot's retired WebGPU renderer emitted WGSL from shader IR using Suyu's `EmitWGSL` and the draw's state. In the renderer before `df1d607d`, `RX_DUMP_WGSL` writes stage files into the specified directory. The Suyu emitter is still in the checkout. Vulkan-specific execution modes and draw parameters can prevent translation from the existing SPIR-V store.

Add a `wgsl` array of explicit dump paths to the selection file to import direct output through the same `--with-editor` build. Paths are relative to the selection file or absolute. Forge caches each validated source alongside its entry points, used resource bindings, vertex inputs, stage outputs, and any NVN constant-bank annotations in the dump. `forge-wgsl --inspect shader.wgsl` reports the WGSL interface by itself. Keep dumps and inspection output local. The old NVN stage keys name captured shader code, and a renderer can specialize the same code more than once for different draw state.

For a matched-view investigation, select the actor in Forge and run **Problems → Audit meshes → Mesh audit JSON** from the camera pose under investigation. The snapshot records the camera matrices, selected preview and detail surfaces, vertex attributes, index counts and LODs. In a separate Longshot capture of the same view, `RX_VK_DUMP` and `RX_VK_DUMP_ARGS=1` record the Vulkan draw arguments. Run `node frontend/tools/compare-longshot-draws.mjs <mesh-audit.json> <Longshot-log> [program-key]` to shortlist indexed triangle-list draws with matching index counts. Confirm candidates against the frame and the actual vertex and material data. An unmatched count can reflect a topology or LOD conversion; the command does not compare meshlet or indirect draws.

Forge compiles a new WebGPU batch with `compileAsync` before the mesh is added, one compile at a time. r187's `_precompilationState` is a single field, so overlapping `compileAsync` calls throw `useFrameBufferTarget of null`. The id-buffer pack is a native function. On Windows a `?three=webgpu` import of `three/tsl` was resolved to `three.webgpu.js`, which does not export `reference`, so the production build failed. That import now stays on one `three.tsl.js` id. A remaining split logs once and clicks use the CPU raycast. A draw whose native pipeline is not ready is skipped, so r170 does not call `setPipeline(undefined)` on that cache entry. The WebGL warm path is unchanged. Terrain texture preparation still runs before the terrain swap. A map assigned after that first compile is sampled by an albedo colour node, and a mesh still holding the flat material is pointed at the part that has the image before the serial rebuild finishes. **Problems → Audit meshes → Mesh audit JSON** includes WebGPU compiler errors recorded in the viewport session.

Opening a scene starts the viewer's model requests and terrain preparation together. Model cache misses use the interactive conversion pool. The previous actor batches remain visible until the new batches are assembled. A progress card leaves the camera controls available and clears when terrain is ready. Browser download, glTF parsing, and GPU upload remain separate from server conversion and are measured by `dl` and `up` in **Profile**.

Automatic scene opens no longer walk neighboring scenes or the full dump. The foreground command remains available for intentional broader warming: `python -m ocarina_forge warm <scene>` prints each model as it finishes, `warm --all` walks every scene, and `warm --scene-only <scene>` skips neighbors.

## Model loading

Opening a scene sends one `POST /api/models/batch` with every actor name. Cache hits come back with a glb URL that includes the cache digest (`?v=`). When the process pool is on (the default is one less than the core count), that batch starts every miss on the pool. The page then pulls up to 16 glbs at once, and never more than the worker count, nearest to the camera first. `GLTFLoader.parse` stays on the main thread, which is why the page does not ask for every model at once. Moving the camera re-sorts the queue that has not started. The same actor is not converted twice, and the same glb URL is not fetched twice. Settings or `OCARINA_FORGE_WORKERS` still sets the pool size, and 1 stays in the server process. The handlers are async, so a convert does not freeze the API. A versioned glb response sends `Cache-Control: public, max-age=31536000, immutable` and an `ETag` of the digest, so a reload is a browser cache hit. HTTP/2 is not enabled; localhost uses those parallel connections instead.

The status label shows `queued`, `loading`, and `background` while those counts are above zero. **Profile** also lists `loaded`. While a scene is streaming, `loading` sits near that slot count and `queued` falls. After it settles, `loading` is 0 and `loaded` is the number of distinct models.

## R1 profile

`profile` times the Python convert. Browser `dl` and GPU `up` are not in the process. If those dominate **Profile** on a cold open, the Rust port stops.

The default cache is a fresh directory under `~/.cache/ocarina-forge/profile-tmp`, on disk. It does not read `~/.cache/ocarina-forge/models`, so a cold `cache=miss` is expected and is not a changed cache key. The directory is deleted when the command exits. `--keep-cache` leaves it. `--home-cache` uses the normal model directory instead. The key is `models-67`. The stage timers wrap the functions the converter already calls, so this command does not rebuild a normal editor cache. `zstd` includes the model-file read before the convert clock and is not subtracted from `convert`. Vertex and index time are inside BFRES parse. Deswizzle and block decode are inside the texture clock. `other` is what is left. The log line `native bc7/decode: rust` means the extension loaded. `native pixels: rust` means the per-pixel functions are in that extension. `native mesh: rust` means vertex, index, and glb float packing are in it. `python` on any line means that path is the fallback. An older extension can say `bc7: rust` and `pixels: python`, or `pixels: rust` and `mesh: python`, until `maturin develop` is run again.

One model:

```bash
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0 \
  --model Land_Kokiri_House_Inside_E_04 \
  --cprofile /tmp/house-inside-e04.prof
```

A scene walk is one model at a time unless `--jobs` is set. `--limit N` stops each Kokiri child banc and each Hyrule Castle Static or Dynamic banc after N models. Kokiri child on a 24-core Linux box, `mod-editor-m1` at `bbfd77c`, was killed at 600 seconds after 89 models: banc read 41 ms, cold convert about 599 s, texture about 130 s, every line `cache=miss`. That miss was the private cache. The synthetic fixture below is not that split.

```bash
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0 --limit 12 --jobs 8
```

On the synthetic Kokiri child Static fixture (11 actor classes, two real meshes) a cold wall was 82.6 ms, of which convert was 80.5 ms and texture 0.4 ms. The five candidate phases together were about 1.1 ms (zstd 0.15, BYML 0.12, BFRES parse 0.53, swizzle 0.31, decode 0). The warm wall was 1.5 ms with both meshes cached. A cProfile of `Npc_Kokiri_F_01` put most of that cold convert in the meshopt helper's first poll (about 55 ms) and in JPEG encode, not in swizzle or ASTC. Decode was 0 because the fixture is not castle ASTC. Do not treat this as the retail split.

## House profile and the native crate

`Land_Kokiri_House_Inside_E_04` on Bradyn's PC, `mod-editor-m1` at `a176bd6`, cold cache. This checkout does not have that model, so these before numbers are his, not a re-run:

| stage | time |
| --- | --- |
| cold convert | 233.4 s |
| encode | 156.1 s |
| `gpu_image` / mip chain | 134 s |
| `encode_bc7` | 121.7 s over 86 calls |
| `_mode6` | 116 s over 568k calls |
| `_index` tottime | 76.2 s over 8.67M calls |
| texture clock | 49.9 s (decode 18.0 s, swizzle 5.8 s) |
| `_choose_material` / `_load_named` | 73.7 s, 70 `_load_named` calls |
| bfres | 2.7 s |
| glb | 0.3 s |
| meshopt | 0.07 s |
| other | 24.3 s |
| warm | 1.5 ms |

`_load_named` includes the texture clock. The 70 calls are not 70 decodes. `_decode_bntx_cached` already returns the image when the path is cached, and it does not start the texture clock on that hit. Each call still decompressed the `.bntx.zs` and `_accept` still walked the pixels. `external` is filled after a successful load and was not checked first. A convert now remembers a load by dump, model, name, hints, alpha, cutoff, layer, and the data flags, and remembers the decompressed bytes by path. A second call with the same key does not read the file. A different name in the same file still skips the decompress. That is the `other` bucket. It does not remove the first decode.

The encoder is this mode 6, not bc7enc and not the Intel ISPC texture compressor. Those search partitions and modes. ISPC and AVX2 ports are not a `wasm32-unknown-unknown` build, and they would change the blocks. The profile's time is the Python `_index` loop, 16 weights by 4 channels, 8.67 million calls. The crate is that loop. `cargo build --manifest-path native/Cargo.toml --no-default-features --target wasm32-unknown-unknown --release` compiles it here. PyO3 stays out of that build.

Decode uses `bcdec_rs` for BC7 and `astc-decode` for ASTC LDR. Both are pure Rust. BC1, BC3, BC4, and BC5 are written here so the 565 expansion matches `texture2ddecoder` (bcdec's scale is off by one on some endpoints). BC2 on this `texture2ddecoder` has no entry point and is decoded as BC3, and the crate does the same. BC6 stays on `texture2ddecoder`.

Measured on this machine, release, one thread. Not the house:

| work | Python | Rust |
| --- | --- | --- |
| BC7 mode 6, 512² | 2320.3 ms | 16.8 ms |
| BC7 mode 6, 1024² | | 67.2 ms |
| BC7 mip chain, 1024² | | 94.7 ms |
| RGBA deswizzle, 1024², log2 4 | 692.2 ms | 6.9 ms |
| ASTC 8×5 decode, 1024×1020, one repeated block | 177.5 ms | 5.0 ms |
| BC7 decode, 512² | 48.7 ms | 1.8 ms |
| channel reorder, 1024² | 429.4 ms | 3.2 ms |

512² is 16,384 blocks. His 568k `_mode6` calls are about 35 of those images. 2320.3 / 16.8 is 138× on this CPU. Applied to his 121.7 s of `encode_bc7`, that is about 0.9 s for the same blocks. That is a ratio, not a new house convert. A 1024² Python encode was not run. Four times the 512² Python time is about 9.3 s, against 67.2 ms of Rust.

He measured that build (`4afe3f7`) on the same house. Cold convert 32.6 s. The 233.4 s line is the baseline above. This checkout still does not have the model.

| stage | time |
| --- | --- |
| cold convert | 32.6 s |
| encode | 16.55 s |
| other | 11.7 s |
| texture clock | 3.1 s (decode 0.93 s, swizzle 0.03 s) |
| bfres | 1.0 s |
| glb | 0.16 s |

cProfile of that run is 55.9 s under the profiler, so those tottimes are not wall time. The functions, by tottime: `limit_rgba` 14.2 s (52 calls), `_has_near_magenta` 6.9 s (42 calls, 37.9M inner calls), `zlib.compress` 6.4 s (75 calls), surface `_write` 5.9 s, `_noise` 3.6 s, `pack_layer` 9.7 s cumulative, `_bc1_block` 1.9 s cumulative (21.8k calls), `_downsample` 1.3 s. `encode_albedo` is 21.4 s cumulative and contains `limit_rgba`. BFRES at 1.0 s is not ported.

The same crate now does those pixel loops. Calls stay inside `limit_rgba`, `_has_near_magenta`, `_write`, `_noise`, `encode_bc1`, and `_downsample`, so the next cProfile still names them. BC1 mip chains encode in the crate the way BC7 chains already did. The ORM fill is one repeated pixel, which is a byte string times the pixel count, and each channel blit is the crate.

Measured here, release, one thread. Not the house:

| work | Python | Rust |
| --- | --- | --- |
| `limit_rgba` 2048×1024 down to 1024 | 783.3 ms | 11.4 ms |
| near-magenta scan, 1024², no hit | 95.4 ms | 0.7 ms |
| `_noise`, 1024² | 39.2 ms | 0.2 ms |
| `_downsample`, 1024² | 613.6 ms | 2.3 ms |
| BC1 encode, 256² | 147.7 ms | 0.4 ms |
| one ORM channel blit, 1024² | 279.0 ms | 2.0 ms |

`zlib.compress` is the PNG IDAT and the decoded-image cache. The cache file was already level 1 and stayed there. PNG was level 6. On a 1024² image with a gradient plus small noise, level 1 took 66.8 ms and 2.76 MB against level 6 at 114.7 ms and 2.67 MB. A 16-pixel tiled image was 6.0 ms and 0.07 MB at level 1, against 11.2 ms and 0.03 MB at level 6. PNG is now level 1. Cutouts, normals, and ORM still need a lossless image. Opaque albedos are already JPEG, and `_encode_cached` already skips a repeat inside one process. A cold profile uses an empty directory, so that cache does not remove the first 75 calls. A deflate that only run-length-encodes zeros was 5.1 ms and 4.79 MB on the detailed image, and 4.48 MB on the tiled one. That is not used. Level 0 would store the RGBA and is only faster when the image does not compress.

He measured that build (`ba00e7f`) on the same house. Cold convert 6.3 s. Encode 1.7 s, other 2.3 s, texture 1.1 s. The 32.6 s line is the before for that build, and 233.4 s is the original. This checkout still does not have the model.

The same profile, `jobs=1`, now finishes the four scenes. Cold convert, then bfres / other / encode / glb / texture, in seconds:

| scene | models | convert | bfres | other | encode | glb | texture |
| --- | --- | --- | --- | --- | --- | --- | --- |
| KokiriForest_Child_Dynamic | 378 | 180.8 | 79.4 | 54.9 | 20.7 | 11.9 | 13.6 |
| KokiriForest_Child_Static | 119 | 85.3 | 58.6 | 13.5 | 5.6 | 5.0 | 2.7 |
| HyruleCastle_Dynamic | 148 | 76.0 | 31.5 | 22.1 | 10.7 | 5.0 | 6.7 |
| HyruleCastle_Static | 135 | 194.8 | 128.9 | 32.7 | 10.7 | 16.3 | 5.9 |

That is 537 s of convert. BFRES parse is 298 s of it. `other` is 123 s. glb pack is 38 s. The slowest models were `Land_HrlCastle_GardenInside_A` 37.5 s, `HyruleCastle_B` 11.3 s, `Land_HidingHole_Base_I_01` 8.0 s, and `DekuTree_A_01` 6.5 s. Meshopt is the small remainder, about 0.3 s a scene, and is not ported. The GardenInside cProfile he named is on his PC. It is not in this checkout, so the `other` note below is the code path, not a measured line from that file.

Vertex and index decode, and the min/max scan, now run in the crate from inside `_vertices`, `_colors`, `_extra_uvs`, `_lod`, and `_attr_ranges`. A short buffer or an unknown format returns nothing and Python keeps its loop, including the error. Skin indices stay in Python: a `u8` slot is the raw byte, not a colour. Floats match `struct`, including half-floats and snorm8 `-128`. Triangle strips keep the same winding flip. glb positions, normals, uvs, and colours are packed in the crate. Index buffers use `array.array`. Meshopt's Node pack is unchanged. The decoded-image memory cache holds 512 textures instead of 32. A scene reuses the same bricks across hundreds of models. The 32-slot list threw them out, and the next model zlib-decompressed the cache file again. That read sits outside the texture clock, so it landed in `other`. 512 is a scene-sized cap, not a measurement that the 123 s was all that decompress. The next profile is what shows whether `other` dropped.

Measured here, release, one thread. Not a scene:

| work | Python | Rust |
| --- | --- | --- |
| f32×3 vertex list, 100k | 71.6 ms | 12.8 ms |
| f16×2 vertex list, 100k | 68.2 ms | 11.0 ms |
| snorm8×4 vertex list, 100k | 157.6 ms | 15.9 ms |
| snorm10 vertex list, 100k | 119.8 ms | 12.7 ms |
| triangle strip, 300k indices | 28.9 ms | 1.5 ms |
| glb f32 pack, 100k positions | 14.4 ms | 5.8 ms |
| glb index pack, 100k indices | 8.8 ms | 0.5 ms |

The f32×3 decode by itself is 2.1 ms. Most of the 12.8 ms is the Python list of tuples that bone transforms and the glb pack still walk. The min/max scan does not build that list. 100k f32×3 bounds were 1.0 ms.

He measured that build (`dcaac45`) on the same machine, 24 cores, with all three native lines saying rust. The private cache had been left in `/tmp`, which is a 16 GB tmpfs there. One scene walk was about 7 GB, and the next run died with `Disk quota exceeded`. He deleted those directories and pointed `TMPDIR` at a disk. Cold house convert was 5.5 s: bfres 0.22 s, encode 1.7 s, other 2.3 s, texture 1.1 s. The 6.3 s line is the before for that build.

The four scenes at `jobs=1` were 280 s of cold convert, down from 537 s. Kokiri Dynamic 111.2 s (bfres 18.4, other 49.1, encode 20.6, glb 9.1, texture 13.7). Kokiri Static 34.6 s. Castle Dynamic 48.9 s. Castle Static 85.4 s (bfres 29.3, other 25.1, glb 14.0, encode 10.8). `Land_HrlCastle_GardenInside_A` was 18.0 s, down from 37.5 s. `--jobs 23` for all four scenes, cold and warm together, was 80 s of wall time. `other` is about 105 s and is the largest bucket. glb pack is still 9 to 14 s on the big scenes. The GardenInside and `Land_Kokiri_House_Inside_F_01` cProfiles are on his PC and are not in this checkout.

That `other` time is the texture file. Each model zstd-decompresses every Tex file it uses, and the decompressed bytes were remembered only for that one model. The decoded-image cache skipped the second decode, not the second read. A process now keeps up to 512 MB of those decompressed files, keyed by path, size, and mtime, so the next model does not inflate the same brick again. The first read of a file is still work. On a one-model house that is most of the old 2.3 s, and the profile now prints it as `texread` instead of leaving it inside `other`. A scene pays it once per worker, not once per model. `zstd` still includes those reads and is not subtracted. `texread` is.

The remaining glb time is `face_normals`, which runs inside `to_glb`. The float pack was already in the crate. Normals are in the crate now, same summation and the same `(0, 1, 0)` for a degenerate vertex, and `to_glb` takes the packed f32 bytes so it does not build a Python tuple per vertex. Meshopt's Node pack is unchanged. On this machine, 100k vertices: the Python normal loop plus the f32 pack was 61.2 ms, and the crate's packed normals were 9.7 ms. That is the loop, not a scene.

A box is a marker when the actor pack does not name a model. Profile prints `No model` only when the pack (or a `Model/<name>.bfres` file that is actually there) references a mesh and that file is missing. `Area`, `AreaCulling`, `AnchorAction`, `AgonyStoneReactArea`, and `AtHitPerceiveAreaActor` are logic actors. They stay boxes and do not each log a line. An actor whose pack names a bfres that is not in the dump still says `No model`.

He measured `51e000d` on the same machine. All four native lines said rust, and the profile cache was removed on exit. The four scenes at `jobs=1` were 262.9 s, down from 280 s. Kokiri Dynamic 106.3 s (bfres 18.2, other 47.8, encode 20.5, glb 5.5, texture 13.6, texread 0.5). Kokiri Static 33.0 s. Castle Dynamic 46.8 s. Castle Static 76.8 s (bfres 29.4, other 21.9, glb 8.3). `Land_HrlCastle_GardenInside_A` was 15.6 s, down from 18.0 s. `texread` was 0.5 s on Kokiri Dynamic, so it was not the `other` bucket. `other` is still about 100 s across the four scenes. On Kokiri Dynamic that is 45% of the scene. On Castle Static, bfres is 38% and other is 29%. Both are over 20% of that scene. The last house number is still 5.5 s, from `dcaac45`. This checkout does not have his `garden.prof`. The lines below are the ones he named from that file, not a profile opened here.

GardenInside tottime: `zlib.compress` 5.08 s over 155 calls; `decode_linear` 1.9 s; `_apply` 1.8 s over 4.35M calls; `_bulk_taken` 3.1 s cumulative; `gltf.py` alpha scan 1.14 s over 16.4M iterations; `min` and `max` about 2 s each cumulative; `round` 0.83 s over 3.6M calls; `encode_albedo` 4.1 s cumulative; UV seam close 1.0 s; bind pose 2.4 s cumulative. The 155 zlib calls are one PNG fallback per distinct image inside that model (cutouts, normals, and ORM stay lossless; opaque albedos are JPEG and do not call zlib). They are not 155 copies of one image. The encoded-image memory cache held 64 of them, so a later model encoded the same pixels again. It now holds 1024, which is the reuse across models in one process. The disk cache already reused an image after the first process. PNG stays zlib level 1. A deflate that only run-length-encodes zeros was measured earlier and is not used.

Bind pose, the UV seam split, shape-key rounding, and the position bounds now run in the crate on packed f64 bytes. `_vertices` no longer builds a Python tuple per vertex on the formats the crate decodes. `to_glb` packs those bytes and takes bounds and face normals from them. Skin slots are still chosen with the same rigid rule: a slot whose bone has no rigid index is left where it is, and a `u8` skin index is still the raw byte, read in Python. A short buffer or an unknown format stays on the Python loop. Floats match that loop.

There is no new house or scene wall time for this build. The table above is the before (`51e000d`). The times below are one thread on this machine, 100k vertices, not a scene:

| work | Python tuples | packed crate |
| --- | --- | --- |
| bind pose, 100k vertices | 27.9 ms | 3.6 ms |
| position bounds, 100k vertices | 12.1 ms | 0.8 ms |

Kokiri Dynamic `other` (45%) and Castle Static bfres (38%) were each over 20% of that scene at `51e000d`. This round is aimed at those loops. The next profile is what shows whether any bucket is still over 20% of a scene. Milestone 3, materials and UVs, is the next branch after that profile. It is not started here. The log must say `native bc7/decode: rust`, `native pixels: rust`, `native mesh: rust`, `native normals: rust`, and `native skin: rust`. An extension from `51e000d` prints `native skin: python` and stays on the Python vertex loop. The private cache is removed at the end. Pass `--keep-cache` to inspect it. The command below prints one house, then a cold total for each Kokiri child banc and the castle.

```bash
pip install maturin
maturin develop --release --manifest-path native/Cargo.toml
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0 \
  --model Land_Kokiri_House_Inside_E_04 \
  --cprofile /tmp/house-inside-e04.prof
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0
```

Without the extension, the same commands run the Python fallback and the house stays near 233 s. An extension from `4afe3f7` prints `native pixels: python` and stays near 32.6 s. An extension from `ba00e7f` prints `native mesh: python` and stays on the Python vertex loops. The current glb prefix is `models-67`. Decoded `bntx-6` files are reused. Mode 6 blocks match. PNG pixels stay the same. Vertex floats match the Python reader. Face normals match the Python loop, so a rebuilt mesh is the same geometry. The prefix still moves because `gltf.py` and `models.py` are in the cache stamp.

## WebGPU

WebGPU is the default renderer. Forge tries it before opening the workspace. If no usable adapter starts, Forge opens its WebGL2 renderer. `?renderer=webgl` (or `?webgpu=0`) selects WebGL2 directly. The measurements below are the earlier Kokiri comparison on Three.js r170 (`0.170.0`), from when this path was still an opt-in trial. Field-scale numbers for the current default are in [rendering-performance.md](rendering-performance.md).

Bradyn's Kokiri pass on `515cd10`, real WebGPU, same spot the earlier passes used:

| backend | frame | draws | notes |
| --- | --- | --- | --- |
| WebGPU | 247.9 ms | 338 | console showed up to 80 `render()` calls in one frame; some grass cards were still solid |
| WebGL | under 30 ms | the editor's usual count (192 on the previous pass) | faster on this Kokiri pass |

That is about 8× the WebGL frame, and the whole trial sat between 3× and 12× slower (32 ms, then 66.3 ms and 315 draws on `4bf5287`, then 247.9 ms and 338 draws). `separate` was already 0, so the copies were instances. The extra cost did not come from falling out of `BatchedMesh`.

What that r170 trial could not do, and what is still unresolved. The renderer is now three r187. See [rendering-performance.md](rendering-performance.md) for the checks against that source.

- **Merged draws, not multi-draw.** WebGL packs every geometry that shares a material into one `WEBGL_multi_draw`. r187's WebGPU backend draws one merged index range per consecutive run (`_mergedDraw*`). Forge fills those ranges with `scheduleMergedDraws`. That is still not one indirect multi-draw for the whole mesh. On the r170 trial, sharing a material and fixing the first instance did not bring the draw count down to WebGL's, and the frame got worse.
- **Render-call loop.** `info.render.calls` is how many times `render()` ran, not the draw count. A quiet editor frame is one call. The Kokiri console reached about 80 calls in a frame. That loop is not fixed.
- **Cutouts.** Some grass cards still drew as solid sheets after the colour-node discard. The mask path is not reliable on this three.

Use the viewport status label to confirm the backend. A WebGPU run starts with `WebGPU`. A fallback names the reason, for example `WebGL (WebGPU unavailable: adapter is null)`. Plain `WebGL` means this session asked for WebGL, or the page is a build from before that label. The short revision on the label is the page; `/api/health` is the server. When they disagree, restart and hard-reload before comparing frames. See [troubleshooting.md](troubleshooting.md). For renderer comparisons, keep the scene, camera, and texture settings the same across both runs.

## Field-scale memory and further GPU techniques

Compressed WebGPU textures, draw/frame measurements, static-batching notes, and the remaining technique list live in `docs/rendering-performance.md`.
