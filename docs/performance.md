# Performance

Baseline on Bradyn’s PC, Kokiri Forest child Static+Dynamic, before this pass: about 55–65 fps, 200–275 draws, 10553 model draws if each copy was its own draw, and 1.4–1.8M triangles. **Stats** in the stage bar is the counter. Later slices add instance and cull counts to that line.

## Render on change

`renderer.render` runs only from the animation frame requested by `requestRender`. Nothing calls `setAnimationLoop`. A frame schedules the next one only while the camera is moving (orbit damping, fly keys, or right-button look), the gizmo is dragging, or a model or terrain load is still in flight. Water does not animate, so it does not keep the loop. When that stops, the overlay reads `idle` and the last draw and triangle counts stay put.

Check: turn **Stats** on, orbit, then let go. The line should switch to `idle` within a frame of the camera settling. Holding a fly key, dragging the gizmo, or opening a scene (while the progress chip is up) should show a frame rate instead. The line starts with the backend (`WebGL` or `WebGPU`) and, once a run of frames has been measured, the frame time in milliseconds.

## Instanced repeats

Copies of a model that share an albedo are one `BatchedMesh`: the geometry is uploaded once and each actor is an instance (matrix, colour, visibility). The batch key is the material, so two houses with the same roof share a draw even when they are different actors. Picking reads the batch id back to the actor. The selection tints that instance. The gizmo stays on the actor pivot and writes the instance matrix while it drags. Hide, lock, and the category toggles use one gate: a hidden category drops the instance and cannot be picked; a locked category stays drawn, clicks pass through, and the gizmo does not attach. `frontend/src/instance.test.js` covers those gates. **Stats** includes `inst`, the number of instances. On Kokiri that count should be in the thousands while draws stay in the low hundreds.

Before and after this confirmation: draw count stays near the 200–275 baseline. The sample scene in this repo is too small to reproduce that (about 30 draws and a handful of instances).

The batch object stays at the origin with its matrix frozen. Instance matrices are world matrices, so a moving batch parent would drag every copy. The gizmo writes `setMatrixAt` only for the selected actor’s slots. `BatchedMesh` has no `instanceMatrix`; the drag used to throw on that flag after the first slot.

Check: two copies of one model count as two `inst` and do not add a second draw. Hide a category and those copies disappear and a click misses them. Lock the category and they stay visible, a click misses them, and the gizmo does not attach. Select one grass-topped block and drag it. That block moves, and the other copies around the pond stay where they are. Undo puts that one actor back. The field stays textured for the whole drag and after you release. Releasing posts the edit and refreshes the actor list. It does not fetch the terrain again, and it does not rebuild the batches: the moved copy's matrix is written in place, and the trees, rocks, and houses stay on the meshes already on screen. Undo of a move does the same for each actor whose pose changed. Adding or removing an actor builds the replacement first and then swaps, so the forest does not blink out.

## Frustum and draw distance

Each instance has its own world bounding sphere: the mesh sphere transformed by that actor’s matrix, not a sphere at the world origin and not the distance from the camera to the origin. The gap used for **Draw** and the size rules is the distance to the sphere surface, so a large mesh is not culled while its bounds are still inside the cap. A sphere outside the frustum is hidden. **Draw** can also drop anything past 200, 400, or 800 m. **Auto** leaves the size rules (small props with no far mesh fade by 56 / 96 / 180 m). The selection is not distance-culled. The field is not in this loop and its mesh is not frustum-culled, so flying back cannot drop the terrain on a bad sphere. The camera far plane grows as you fly, from at least 8000 m. `culled` in **Stats** is the hidden instances. The batch mesh itself is not frustum-culled; a sphere on the untransformed geometry used to hide every copy once the camera looked away from the origin.

Occlusion culling is not in this slice. A depth prepass over the whole forest is not cheap enough to pay for here, so it stays a later item.

Check: **Draw** on Auto, fly well back from Kokiri. Trees and the field stay. Tris should fall as actors switch to the coarse mesh, and should not collapse to a couple of thousand with `inst` at 0. Set 200 m and distant small props drop while `culled` rises. The selected actor stays drawn if you fly away from it. The grid and helper boxes are not the only thing left.

## LODs

A shape that already has a coarser BFRES level keeps that mesh as `lod1`. Anything else with more than about 300 triangles gets a meshoptimizer simplification at import, with the border locked so the outline does not collapse. The old grid quantize remains only when Node cannot run the simplifier. The view switches on screen size: under about 64 pixels the coarse mesh is drawn, and the full mesh comes back when the actor is larger on screen. A selected actor stays on the full mesh. The 44 m switch remains only for the first frames before the canvas height is known. Past the small-prop distances, or under about three pixels, a model that has a far mesh stays on that coarse mesh. It does not hide both levels. A model with no far mesh still drops. If the coarse mesh failed to build, the full mesh stays instead of drawing nothing.

Before this slice the far mesh was a grid quantize and it came on past 44 m even when the actor still filled the screen. A later pass then hid both levels once a tree was past 180 m or smaller than about three pixels, so a wide view dropped to helper boxes. After this fix, a wide view should draw fewer triangles than the 1.4–1.8M full-detail baseline once the coarse meshes are on screen, and those coarse meshes stay on screen. The sample scene’s meshes are too small to grow a far level.

Check: open Kokiri, fly back until trees are small, and **Stats** tris should fall while the trees and the field are still there. Fly toward one tree and it should sharpen before it fills the screen. Select an actor and fly away: that one stays on the full mesh. The first open of the meshopt build still uses cache `models-22`; this cull change does not rebuild it.

## Import cache

Cached glbs use `EXT_meshopt_compression` on the geometry. The compression does not quantize positions, so a decoded vertex matches the one that was packed. Textures stay JPEG or PNG. A KTX2 encode was tried and rejected: three r170’s Basis transcoder returns an empty image for that encoder’s files, so the field and the actors would draw black. The view still loads a meshopt glb, and it has a KTX2 loader pointed at `/basis/` for when an encoder round-trips.

Before: a 40×40 grid glb was 70,788 bytes of plain glTF. After: the same triangles are 14,040 bytes, about a fifth, and a decoded vertex still matches the one that was packed. Kokiri’s sample actors are already a few kilobytes, so the byte cut shows up on the dense meshes. Draw count is not what this slice moves. That compression used prefix `models-21`. The atlas cache below moves the prefix to `models-22`, so the first open of this build rebuilds the glbs again.

Check: the first open after this build rebuilds the cache. Models should look the same as before, not black and not shifted. A wide view’s triangle count should still drop when actors are small on screen.

## Terrain atlas

The field is one atlas: up to 16 MaterialAlb layers plus an earth cell, each resampled to 128 pixels and packed into one image. That PNG (or JPEG) is stored under `atlas/` in the model cache. The key is the cell bytes and `atlas-1`. It does not include the glb prefix, so a converter bump that rebuilds the terrain glb reads the packed image instead of packing it again.

Before: every terrain rebuild packed the cells. A 17-cell solid atlas took 16 ms. After: the same cells read back in 0.7 ms. The sample scene has no MaterialAlb, so this shows up on a real dump when the glb cache misses and the layers have not changed.

Check: open a scene with a heightmap, quit, pull this build, and open it again. The field should look the same. A second rebuild of that scene (for example after the `models-22` miss) should not repack the atlas; `atlas/` in the model cache gains one file per distinct layer set and then stays put. Drag an actor and release. The field should not go black while the edit saves. Opening a different scene keeps the old field up until the new one's shader and atlas are ready, then swaps.

## Model loading

Opening a scene sends one `POST /api/models/batch` with every actor name. Cache hits come back with a glb URL that includes the cache digest (`?v=`), and the view downloads up to 8 of those files at once, nearest to the camera first. A name that is not converted yet is `queued`: one of those 8 slots calls `GET /api/models/<name>`, which converts it, and only a real mesh then downloads the glb. Moving the camera re-sorts the queue that has not started. The same actor is not converted twice, and the same glb URL is not fetched twice. Conversions run in the `BANCWRIGHT_WORKERS` process pool (4 unless you set it; `0` or `1` stays in the server process) and the handlers are async, so a convert does not freeze the API. A versioned glb response sends `Cache-Control: public, max-age=31536000, immutable` and an `ETag` of the digest, so a reload is a browser cache hit. HTTP/2 is not enabled; localhost uses those parallel connections instead.

**Stats** adds `queued`, `loading`, and `loaded`. While Kokiri is streaming, `loading` should sit near 8 and `queued` should fall. After it settles, `loading` is 0 and `loaded` is the number of distinct models.

## WebGPU trial

Three stays on **r170** (`0.170.0`). That release already has `WebGPURenderer`, TSL node materials, and a `BatchedMesh` draw path, so a bump was not required for the trial. A newer three would also move the WebGL editor (material compile, `TransformControls`, `BatchedMesh`, and three-mesh-bvh) under an unproven renderer. WebGL remains the default until a Kokiri pass shows WebGPU is faster and looks the same.

The stage bar has a **WebGPU** checkbox. Checking it reloads with `?gpu=1`. Unchecking reloads without that parameter. `?renderer=webgpu` does the same thing. `?gpu=0` or `?renderer=webgl` forces WebGL. If Chrome has the WebGPU API but no adapter, three keeps `WebGPURenderer` and runs its WebGL2 backend. **Stats** then starts with `WebGPU (WebGL2 fallback)`, not `WebGPU`. That backend still uses the TSL terrain node, because it does not run `onBeforeCompile`. On Linux Chrome, `chrome://gpu` often says "No available adapters" until WebGPU is switched on: open `chrome://flags` and enable Unsafe WebGPU and Vulkan, or launch with `--enable-unsafe-webgpu --enable-features=Vulkan`, then confirm WebGPU is hardware accelerated in `chrome://gpu`.

What is shared, so both backends should match:

- Actor batches. The same `BatchedMesh` stores the instance matrix, colour, and visibility. Picking is a CPU raycast on that mesh. The gizmo writes one actor's matrix. LOD and frustum culling set `setVisibleAt` from the same screen-size rules. Render-on-change is the same loop: no `setAnimationLoop`, another frame only while the camera, the gizmo, or a load is active.
- The field. WebGL still injects the GLSL atlas (`terrainCell`, triplanar blend, sub-sheet discard, tile colors). WebGPU ignores `onBeforeCompile`, so the same uniforms feed a TSL `colorNode` that does that work. Batch tint, selection colour, hide, and lock are applied after that colour on both paths.

What differs on r170's WebGPU backend:

- The lighting model is the node physical shader, not the WebGL standard chunks. The forest can read a little brighter or flatter. Water and alpha-tested leaves go through the node material's alpha test instead of the GLSL chunk.
- `info.render.calls` is how many times `render()` ran, not the draw count. **Stats** reads `drawCalls` on WebGPU, then adds each `BatchedMesh` draw, because r170 does not count those itself. `tris` still misses the forest. Do not compare `tris` across the two backends on this build.
- `separate` is lod-0 parts that never received a `BatchedMesh` slot. A healthy Kokiri view stays near 0. It used to add every lod-0 part, so the line read `10461 separate` next to `10461 inst` even when those copies were already instances.
- The WebGPU page is one three build. `boot.js` loads `main.js?three=webgpu`, and that graph resolves `three`, `three/webgpu`, `three/tsl`, the addons, and three-mesh-bvh to `three.webgpu.js`. Lights from the WebGL build are a different constructor, so `LightsNode` skipped them and the standard materials drew black. The default page still uses `three.module.js` and does not load the WebGPU build.
- r170's WebGPU backend draws one call per `BatchedMesh` instance. Copies of a single geometry are folded into one instanced draw before that loop. WebGL keeps material batches and `WEBGL_multi_draw`.
- The terrain node samples the atlas with its own uv. It does not read attribute `uv`, and geometries without `uv1`/`uv2`/`uv3` get a flat colour instead of a missing-attribute warning every frame.
- The first WebGPU frames compile pipelines. Orbit once and let it settle before reading fps. A second "Multiple instances of Three.js" warning means the graph split again. If the device is lost, a toast says so and unchecking **WebGPU** reloads the WebGL editor.
- Labels are DOM on both. Shadows stay off.

Kokiri on `7f9fdf2` with a real adapter, before this fix: `WebGPU · 32.1 ms · 129 draws · 10461 inst · 9424 culled · 10461 separate · 99.9k tris`. The view was black except labels, the gizmo, and a few speckles. The console repeated `Light node not found` for the hemisphere, directional, and ambient lights, `Vertex attribute "uv" not found`, and the two-three warning.

A stand-in on this machine (headless Chrome 157, SwiftShader Dawn, `--enable-unsafe-webgpu --use-vulkan=swiftshader`), 4000 lit box copies in one batch, after the fix:

| path | draws inside the batch | frame | notes |
| --- | --- | --- | --- |
| WebGPU, folded | 1 | 66 ms | waited on the GPU queue. `HemisphereLightNode` resolved, `separate` 0, no light/uv/duplicate warnings, a center pixel read (4, 19, 4) |
| WebGPU, one draw per copy | 4000 | 66 ms | same picture. This software adapter is not draw-call bound |
| WebGL, same boxes | one multi-draw | 49 ms | one `three.module.js`, no duplicate warning. Each frame was flushed with `readPixels` |

That is not Kokiri, and SwiftShader is not Bradyn's Vulkan GPU. WebGL stays the default. The fold is there so a real GPU is not asked for ten thousand draws.

Check: open Kokiri on WebGL, turn **Stats** on, orbit, and note fps, ms, and `inst`. Then check **WebGPU** (the page reloads). With an adapter, the line should start with `WebGPU`, `separate` should sit near 0, and the console should not repeat the light-node or missing-`uv` warnings. The field and the models should be lit, not black and not the warning stripes, unless **Tile colors** is on. Drag one copy of a repeated model: only that copy moves, during the drag and after release. Hide a category and those instances drop. Fly back: trees stay, and `inst` falls only by culling, not to zero. Uncheck **WebGPU** and confirm the line says `WebGL` again. Compare ms with the WebGL pass on the same orbit. Without an adapter, the line should start with `WebGPU (WebGL2 fallback)` and a toast says so.
