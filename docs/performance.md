# Performance

Baseline on Bradyn’s PC, Kokiri Forest child Static+Dynamic, before this pass: about 55–65 fps, 200–275 draws, 10553 model draws if each copy was its own draw, and 1.4–1.8M triangles. **Stats** in the stage bar is the counter. Later slices add instance and cull counts to that line.

## Render on change

`renderer.render` runs only from the animation frame requested by `requestRender`. Nothing calls `setAnimationLoop`. A frame schedules the next one only while the camera is moving (orbit damping, fly keys, or right-button look), the gizmo is dragging, or a model or terrain load is still in flight. Water does not animate, so it does not keep the loop. When that stops, the overlay reads `idle` and the last draw and triangle counts stay put.

Check: turn **Stats** on, orbit, then let go. The line should switch to `idle` within a frame of the camera settling. Holding a fly key, dragging the gizmo, or opening a scene (while the progress chip is up) should show a frame rate instead.

## Instanced repeats

Copies of a model that share an albedo are already one `BatchedMesh`: the geometry is uploaded once and each actor is an instance (matrix, colour, visibility). Picking, the selection tint, the gizmo, hide, lock, and the category toggles still go through that instance. **Stats** now includes `inst`, the number of those instances. On Kokiri that count should be in the thousands while draws stay in the low hundreds. This slice does not change the batching, so the draw count should stay near the 200–275 baseline.

## Frustum and draw distance

Each instance has a bounding sphere. While the camera is moving, a sphere outside the frustum is hidden, and **Draw** can also drop anything past 200, 400, or 800 m. **Auto** leaves the old size rules (small props fade by 56 / 96 / 180 m). The selection is not distance-culled. `culled` in **Stats** is the hidden instances. GPU per-object frustum culling stays on as well.

Occlusion culling is not in this slice. A depth prepass over the whole forest is not cheap enough to pay for here, so it stays a later item.

Check: **Draw** on Auto should look like the previous build. Set 200 m, orbit to the edge of the village, and distant props should drop while `culled` rises. The selected actor stays drawn if you fly away from it.
