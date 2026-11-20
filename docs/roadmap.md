# Roadmap

Approved next, in this order. The terrain overlay split (main floor and `_sub` as separate layers) is in the build these notes ship with. The performance pass is in this build. The WebGPU trial is shelved on three r170. Editing UX has undo, redo, ground snap, Ctrl+K search, and live validation in this build. Hot reload, grid snap, and the behavior editor are not started. Numbers for each slice are in [performance.md](performance.md).

## 1. Performance pass

Render on change, so an idle view does not redraw. Instance repeated models on the GPU. Frustum and occlusion culling. Real LODs: the game’s LOD meshes where the file has them, and a generated mesh otherwise, with low-mip textures at distance. A GPU-ready import cache (glTF with meshopt, and KTX2). Nearest-first streaming. Prebaked terrain and atlases per scene.

## 2. WebGPU trial (shelved)

The opt-in stays, labelled experimental, and WebGL stays the default. Three stays on r170. **WebGPU** in the stage bar, or `?gpu=1`, still reloads on `WebGPURenderer`. No adapter keeps that renderer on its WebGL2 backend, and the stats line says `WebGPU (WebGL2 fallback)`. On a real Kokiri GPU the frame stayed 3–12× slower than WebGL (the last pass was 247.9 ms and 338 draws, against WebGL under 30 ms). r170 has no multi-draw, so one shared material is still one draw per index range. The render-call loop (up to about 80 `render()` calls in a frame) and the remaining solid grass cards are unresolved. Revisit this on a newer three. The measurements are in [performance.md](performance.md).

## 3. Editing UX

In this build: instant undo and redo (Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y) for a move, a rotate, a property edit, add, delete, duplicate, hide, and the Areas / System lock. The stack is in memory and the project diff is written from the same shot, so a transform undo patches the instance matrix and does not reload the scene. **Changes** lists that session above the project diff. **Snap** (checkbox, or V) raycasts the terrain and collision volumes while a move or a new actor is dropped.

In this build: Ctrl+K (or the Search button) opens a palette. It fuzzy-matches actors in the open scene by name, class, and hash, plus scene files and editor commands. Enter frames the actor, opens the scene, or runs the command.

In this build: **Problems** (and the same lines under the selected actor in Properties) lists diagnostics from the open scene. Each one names a file, a JSON pointer or an actor hash, a code, a message, and a severity. The list is fetched again after an edit and after undo. It reports an unknown actor class, a class with no model file, a duplicate hash, a parameter whose kind does not match the scene file, a link to a hash that is not in the scene, and a flag name that is not in GameDataList. A dump without `GameData/GameDataList.Product.110.byml.zs` or without `Pack/Actor` gets one warning instead of a row per actor. Volumes and areas are not flagged for a missing model.

Still later: hot-reload of changed files into the Longshot mod folder, and snap to a grid.

## 4. Behavior editor

A Blueprint-style editor over AINB and event flows. A raw layer that exposes every node, and a curated friendly layer. Stages: a read-only panel, then editing nodes, then custom graphs, with a flags and quests picker. Live previews of AI schedules, event flows, and dialog.
