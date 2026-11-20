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

- The lighting model is the node physical shader, not the WebGL standard chunks. The forest can read a little brighter or flatter.
- `info.render.calls` is how many times `render()` ran, not the draw count. **Stats** reads `drawCalls` on WebGPU, then adds each `BatchedMesh` draw, because r170 does not count those itself. `tris` still misses the forest. Do not compare `tris` across the two backends on this build.
- `separate` is lod-0 parts that never received a `BatchedMesh` slot. A healthy Kokiri view stays near 0.
- The WebGPU page is one three build. `boot.js` loads `main.js?three=webgpu`, and that graph resolves `three`, `three/webgpu`, `three/tsl`, the addons, and three-mesh-bvh to `three.webgpu.js`. The default page still uses `three.module.js`.
- WebGL packs every geometry that shares a material into one `WEBGL_multi_draw`. WebGPU has no multi-draw: one indexed draw can cover one index range. Copies of that geometry are one instanced draw, and the first instance is the slot in the batch's indirect texture, not always 0. A material used by two meshes is two draws, not one per copy and not one per material.
- Masked foliage keeps the map alpha in a colour node and discards below `alphaTest`. WebGL still uses `alphaToCoverage` and the GLSL alpha test.
- The terrain node samples the atlas with its own uv and then clears `material.map`. The mesh does not put that map back, which was a request for attribute `uv`. A geometry that really has no `uv` gets its own material clone with the map removed, and one console warning names that object.
- The first WebGPU frames compile pipelines. A later frame that is not loading a model should create none. `material.needsUpdate` increments `version` and does not by itself compile a pipeline. Orbit once and let it settle before reading fps. A second "Multiple instances of Three.js" warning means the graph split again. If the device is lost, a toast says so and unchecking **WebGPU** reloads the WebGL editor.
- Labels are DOM on both. Shadows stay off.

Kokiri on `4bf5287`, same spot, camera moving, before this pass:

| backend | line | while moving |
| --- | --- | --- |
| WebGPU | `WebGPU · idle · 66.3 ms · 315 draws · 10461 inst · 9436 culled · 0 separate · 99.7k tris` | about 6 fps |
| WebGL | `WebGL · idle · 20.4 ms · 192 draws · 10461 inst · 9222 culled · 0 separate · 1.2M tris` | the editor |

`idle` is the last frame after the camera stopped. The 66.3 ms is the moving frame. `separate` was already 0, so the copies were instances. `tris` on WebGPU still misses the forest.

Counted on a SwiftShader WebGPU device, not on Kokiri: the first frame creates the pipelines (8 for a small scene). The next frame creates 0. Setting `needsUpdate` moved `version` from 1 to 2 and still created 0 pipelines. Culling calls `setVisibleAt` only when the bit changes, and that flag does not rebuild the draw list on a quiet frame. The 66 ms was not a per-frame recompile.

Three things were wrong:

1. **Draws.** The WebGPU batch key appended `geometry.uuid`, so one albedo became one mesh per geometry (315 draws against WebGL's 192). That split existed because r170's batched draw restarts at instance 0 whenever a draw has more than one instance. Two geometries in one mesh would both read the first geometry's matrices. This build uses the WebGL material key, keeps copies of one geometry contiguous, folds each run, and passes that run's first slot. A two-geometry batch in the probe drew `[36 indices, 2 instances, first 0]` and `[6 indices, 1 instance, first 2]`, then the same list on the next frame with no new pipeline.
2. **Grass sheets.** The node standard material multiplies a vec3 colour by the map, and that product's alpha is 0. With the alpha test live, the whole card is discarded. With the test not covering the draw and the target opaque, the grass colour is written for every pixel of the card. `prepareMaterial` had also turned on `alphaToCoverage`, which this backend enables only when the pass is multisampled. Cutout materials now sample the map in a colour node, keep the texel alpha, and discard below the cutoff. A probe card is green on the opaque half `(17, 160, 30)` and the clear colour `(17, 34, 51)` on the punched half.
3. **`Vertex attribute "uv"`.** The grid, the gizmo, the helper boxes, and the terrain node do not ask for `uv`. The warning is some other geometry whose material still has a map. The walk clones that material, drops the map, and names the object once. In the probe that object was `sign-post`. Kokiri will name whatever mesh it actually is.

WebGL stays the default. The SwiftShader box probe from the previous pass (66 ms either way, because that adapter is not draw-call bound) is not a Kokiri number.

Check: open Kokiri on WebGL, turn **Stats** on, orbit the same spot, and note ms and draws (the last pass was about 20 ms and 192 draws). Check **WebGPU** and orbit that spot again. Grass and leaves should be cut out, not solid rectangles. The console should not say `Vertex attribute "uv"`; if a mesh had a map and no uv, one warning names it. `separate` stays 0. Draws should stay in the hundreds, not climb toward `inst`. After the models have loaded, a quiet frame should log `WebGPU profile: render calls 1, pipelines this frame 0, material versions stable`. Compare ms with the WebGL pass. Uncheck **WebGPU** and confirm the line says `WebGL` again. Without an adapter, the line should start with `WebGPU (WebGL2 fallback)`.
