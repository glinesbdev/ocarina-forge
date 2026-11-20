# Performance

Baseline on Bradyn’s PC, Kokiri Forest child Static+Dynamic, before this pass: about 55–65 fps, 200–275 draws, 10553 model draws if each copy was its own draw, and 1.4–1.8M triangles. **Stats** in the stage bar is the counter. Later slices add instance and cull counts to that line.

## Render on change

`renderer.render` runs only from the animation frame requested by `requestRender`. Nothing calls `setAnimationLoop`. A frame schedules the next one only while the camera is moving (orbit damping, fly keys, or right-button look), the gizmo is dragging, or a model or terrain load is still in flight. Water does not animate, so it does not keep the loop. When that stops, the overlay reads `idle` and the last draw and triangle counts stay put.

Check: turn **Stats** on, orbit, then let go. The line should switch to `idle` within a frame of the camera settling. Holding a fly key, dragging the gizmo, or opening a scene (while the progress chip is up) should show a frame rate instead.

## Instanced repeats

Copies of a model that share an albedo are one `BatchedMesh`: the geometry is uploaded once and each actor is an instance (matrix, colour, visibility). The batch key is the material, so two houses with the same roof share a draw even when they are different actors. Picking reads the batch id back to the actor. The selection tints that instance. The gizmo stays on the actor pivot and writes the instance matrix while it drags. Hide, lock, and the category toggles use one gate: a hidden category drops the instance and cannot be picked; a locked category stays drawn, clicks pass through, and the gizmo does not attach. `frontend/src/instance.test.js` covers those gates. **Stats** includes `inst`, the number of instances. On Kokiri that count should be in the thousands while draws stay in the low hundreds.

Before and after this confirmation: draw count stays near the 200–275 baseline. The sample scene in this repo is too small to reproduce that (about 30 draws and a handful of instances).

Check: two copies of one model count as two `inst` and do not add a second draw. Hide a category and those copies disappear and a click misses them. Lock the category and they stay visible, a click misses them, and the gizmo does not attach. Select one copy and it tints gold; dragging the gizmo moves that copy only.

## Frustum and draw distance

Each instance has a bounding sphere. While the camera is moving, a sphere outside the frustum is hidden, and **Draw** can also drop anything past 200, 400, or 800 m. **Auto** leaves the old size rules (small props fade by 56 / 96 / 180 m). The selection is not distance-culled. `culled` in **Stats** is the hidden instances. GPU per-object frustum culling stays on as well.

Occlusion culling is not in this slice. A depth prepass over the whole forest is not cheap enough to pay for here, so it stays a later item.

Check: **Draw** on Auto should look like the previous build. Set 200 m, orbit to the edge of the village, and distant props should drop while `culled` rises. The selected actor stays drawn if you fly away from it.

## Model loading

Opening a scene sends one `POST /api/models/batch` with every actor name. Cache hits come back with a glb URL that includes the cache digest (`?v=`), and the view downloads up to 8 of those files at once, nearest to the camera first. A name that is not converted yet is `queued`: one of those 8 slots calls `GET /api/models/<name>`, which converts it, and only a real mesh then downloads the glb. Moving the camera re-sorts the queue that has not started. The same actor is not converted twice, and the same glb URL is not fetched twice. Conversions run in the `BANCWRIGHT_WORKERS` process pool (4 unless you set it; `0` or `1` stays in the server process) and the handlers are async, so a convert does not freeze the API. A versioned glb response sends `Cache-Control: public, max-age=31536000, immutable` and an `ETag` of the digest, so a reload is a browser cache hit. HTTP/2 is not enabled; localhost uses those parallel connections instead.

**Stats** adds `queued`, `loading`, and `loaded`. While Kokiri is streaming, `loading` should sit near 8 and `queued` should fall. After it settles, `loading` is 0 and `loaded` is the number of distinct models.
