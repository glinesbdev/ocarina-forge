# Performance

Baseline on Bradyn’s PC, Kokiri Forest child Static+Dynamic, before this pass: about 55–65 fps, 200–275 draws, 10553 model draws if each copy was its own draw, and 1.4–1.8M triangles. **Stats** in the stage bar is the counter. Later slices add instance and cull counts to that line.

## Render on change

`renderer.render` runs only from the animation frame requested by `requestRender`. Nothing calls `setAnimationLoop`. A frame schedules the next one only while the camera is moving (orbit damping, fly keys, or right-button look), the gizmo is dragging, or a model or terrain load is still in flight. Water does not animate, so it does not keep the loop. When that stops, the overlay reads `idle` and the last draw and triangle counts stay put.

Check: **Stats** starts on. Orbit or fly, then let go. The line should switch to `idle` within a frame of the camera settling. Holding a fly key, dragging the gizmo, or opening a scene (while the progress chip is up) should show a frame rate instead. The line starts with the backend (`WebGL` or `WebGPU`) and, once a run of frames has been measured, the frame time in milliseconds.

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

Cached glbs use `EXT_meshopt_compression` on the geometry. The compression does not quantize positions or texture coordinates, so a decoded vertex matches the one that was packed, including UVs outside 0..1. Textures stay JPEG or PNG. A KTX2 encode was tried and rejected: three r170’s Basis transcoder returns an empty image for that encoder’s files, so the field and the actors would draw black. The view still loads a meshopt glb, and it has a KTX2 loader pointed at `/basis/` for when an encoder round-trips.

Before: a 40×40 grid glb was 70,788 bytes of plain glTF. After: the same triangles are 14,040 bytes, about a fifth, and a decoded vertex still matches the one that was packed. Kokiri’s sample actors are already a few kilobytes, so the byte cut shows up on the dense meshes. Draw count is not what this slice moves. That compression used prefix `models-21`. The atlas cache below moves the prefix to `models-22`, so the first open of this build rebuilds the glbs again.

Check: the first open after this build rebuilds the cache. Models should look the same as before, not black and not shifted. A wide view’s triangle count should still drop when actors are small on screen.

## Terrain atlas

The field is one atlas: up to 16 MaterialAlb layers plus an earth cell, each resampled to 128 pixels and packed into one image. That PNG (or JPEG) is stored under `atlas/` in the model cache. The key is the cell bytes and `atlas-1`. It does not include the glb prefix, so a converter bump that rebuilds the terrain glb reads the packed image instead of packing it again.

Before: every terrain rebuild packed the cells. A 17-cell solid atlas took 16 ms. After: the same cells read back in 0.7 ms. The sample scene has no MaterialAlb, so this shows up on a real dump when the glb cache misses and the layers have not changed.

Check: open a scene with a heightmap, quit, pull this build, and open it again. The field should look the same. A second rebuild of that scene (for example after the `models-22` miss) should not repack the atlas; `atlas/` in the model cache gains one file per distinct layer set and then stays put. Drag an actor and release. The field should not go black while the edit saves. Opening a different scene keeps the old field up until the new one's shader and atlas are ready, then swaps.

## Shared texture cache

A decoded BNTX is stored under its Tex path and the decoder version, not only under a hash of that one model's copy of the bytes. Castle bricks that many actors name are decoded once. The next model reads `bntx/*.bwtx`. The in-memory cache is still per process, so another worker hits the file. An embedded texture with no path keeps the blob hash.

## Converter cores

The process pool defaults to one less than `os.cpu_count()`, and never below 1. One core stays in the server process and does not start a pool. **Settings** in the top bar is a slider and a number from 1 to the machine's core count. It is saved in the config file and applied between converts: a job already running finishes on the old pool, and the next one uses the new count. `OCARINA_FORGE_WORKERS` overrides the slider. While that variable is set, Settings says so and does not change the running count.

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

BC and ASTC decode, zstd, and pack reads run in the server process when a model or a scene is built. The browser receives PNG, JPEG, and meshopt glbs. A KTX2 client transcoder was already rejected (three r170 returns an empty image). Terrain atlases and LOD meshes are built at import and cached. There is no client-side copy of that work to move.

## Stream timings

Every model convert records two server times. `convertMs` is the whole conversion. `textureMs` is only the texture decode that missed the decoded-image cache, so a warm open stays near zero on that number. The glb meta keeps the cold timing. A later open replaces the timing it returns with `cache=hit` and does not add those milliseconds to the server totals.

The server prints one line per convert on the process log:

`model Npc_Kokiri_F_01 cache=miss convert=120.5ms texture=80.0ms`

`GET /api/stream` is the same list, plus how many misses and hits this process has done and the summed miss times. **Stats** adds the scene's own sums: `conv` and `tex` from those timings, `dl` for the glb download, and `up` for glTF parse plus GPU upload. The Stats panel repeats the server summary under the line.

The sample Kokiri scene on this machine (11 actors, two real meshes) was 103.6 ms cold and 1.3 ms warm before this instrumentation. Retail Kokiri Forest and Hyrule Castle are not in this checkout. On a dump, the first run is cold and the second is warm:

```bash
.venv/bin/python -m ocarina_forge warm Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs --dump ~/Games/Longshot/rc/v3.0.0
.venv/bin/python -m ocarina_forge warm Banc/HyruleCastle/HyruleCastle_Field_Static.bcett.byml.zs --dump ~/Games/Longshot/rc/v3.0.0
```

If the castle file has a different name, use the path from the scene list. `warm --all` walks every scene. A cold line says `cache=miss` and has a texture time. The next run of the same models says `cache=hit` and texture time 0.

## Background warm-up

Opening a scene queues the rest of that scene's models, nearest to the camera first, and then the other scenes in the same folder, then the rest of the dump. The queue runs one model at a time and waits while an interactive convert is in flight, so it does not take a worker from the view. **Stats** shows `warm 12/80`. The same queue is `POST /api/warm`. `ocarina-forge warm <scene|--all>` does the walk in the foreground and prints each timing.

## Model loading

Opening a scene sends one `POST /api/models/batch` with every actor name. Cache hits come back with a glb URL that includes the cache digest (`?v=`), and the view downloads up to 8 of those files at once, nearest to the camera first. A name that is not converted yet is `queued`: one of those 8 slots calls `GET /api/models/<name>`, which converts it, and only a real mesh then downloads the glb. Moving the camera re-sorts the queue that has not started. The same actor is not converted twice, and the same glb URL is not fetched twice. Conversions run in the process pool (one less than the core count, unless Settings or `OCARINA_FORGE_WORKERS` says otherwise; 1 stays in the server process) and the handlers are async, so a convert does not freeze the API. A versioned glb response sends `Cache-Control: public, max-age=31536000, immutable` and an `ETag` of the digest, so a reload is a browser cache hit. HTTP/2 is not enabled; localhost uses those parallel connections instead.

**Stats** adds `queued`, `loading`, and `loaded`. While Kokiri is streaming, `loading` should sit near 8 and `queued` should fall. After it settles, `loading` is 0 and `loaded` is the number of distinct models.

## WebGPU trial (shelved)

WebGL stays the default. The stage-bar checkbox is gone. `?webgpu=1` or `?renderer=webgpu` still reloads on `WebGPURenderer`. `?gpu=0` / `?renderer=webgl` force WebGL. A saved `?gpu=1` link, the old checkbox flag, is stripped on boot and loads WebGL. There is no further work on this path. It can be revisited when three is newer than r170 (`0.170.0`).

Bradyn's Kokiri pass on `515cd10`, real WebGPU, same spot the earlier passes used:

| backend | frame | draws | notes |
| --- | --- | --- | --- |
| WebGPU | 247.9 ms | 338 | console showed up to 80 `render()` calls in one frame; some grass cards were still solid |
| WebGL | under 30 ms | the editor's usual count (192 on the previous pass) | the one to keep using |

That is about 8× the WebGL frame, and the whole trial sat between 3× and 12× slower (32 ms, then 66.3 ms and 315 draws on `4bf5287`, then 247.9 ms and 338 draws). `separate` was already 0, so the copies were instances. The extra cost did not come from falling out of `BatchedMesh`.

What r170 cannot do, and what is still unresolved:

- **No multi-draw.** WebGL packs every geometry that shares a material into one `WEBGL_multi_draw`. r170's WebGPU backend draws one index range per call. Sharing a material and fixing the first instance did not bring the draw count down to WebGL's, and the frame got worse.
- **Render-call loop.** `info.render.calls` is how many times `render()` ran, not the draw count. A quiet editor frame is one call. The Kokiri console reached about 80 calls in a frame. That loop is not fixed.
- **Cutouts.** Some grass cards still drew as solid sheets after the colour-node discard. The mask path is not reliable on this three.

The `?webgpu=1` path remains so a later three can be compared without rebuilding the opt-in. On Linux Chrome, hardware WebGPU needs Unsafe WebGPU and Vulkan (`chrome://flags`, or `--enable-unsafe-webgpu --enable-features=Vulkan`). `chrome://gpu` should say WebGPU is hardware accelerated. No adapter keeps `WebGPURenderer` on its WebGL2 backend, and **Stats** then starts with `WebGPU (WebGL2 fallback)`. Do not use that line, or a WebGPU line, as a performance target until the trial is reopened.
