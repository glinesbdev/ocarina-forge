# Roadmap

Approved next, in this order. The terrain overlay split (main floor and `_sub` as separate layers) is in the build these notes ship with. The performance pass is in this build. The WebGPU trial is shelved on three r170. Editing UX has undo, redo, ground snap, Ctrl+K search, live validation, and opt-in hot reload in this build. Grid snap is not started. The behavior editor's read-only panel is in this build. Numbers for each slice are in [performance.md](performance.md).

## 1. Performance pass

Render on change, so an idle view does not redraw. Instance repeated models on the GPU. Frustum and occlusion culling. Real LODs: the game’s LOD meshes where the file has them, and a generated mesh otherwise, with low-mip textures at distance. A GPU-ready import cache (glTF with meshopt, and KTX2). Nearest-first streaming. Prebaked terrain and atlases per scene.

## 2. WebGPU trial (shelved)

The opt-in stays behind `?webgpu=1` (or `?renderer=webgpu`), and WebGL stays the default. The stage-bar checkbox is gone, and a saved `?gpu=1` link loads WebGL. Three stays on r170. No adapter keeps that renderer on its WebGL2 backend, and the stats line says `WebGPU (WebGL2 fallback)`. On a real Kokiri GPU the frame stayed 3–12× slower than WebGL (the last pass was 247.9 ms and 338 draws, against WebGL under 30 ms). r170 has no multi-draw, so one shared material is still one draw per index range. The render-call loop (up to about 80 `render()` calls in a frame) and the remaining solid grass cards are unresolved. Revisit this on a newer three. The measurements are in [performance.md](performance.md).

## 3. Editing UX

In this build: instant undo and redo (Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y) for a move, a rotate, a property edit, add, delete, duplicate, hide, and the Areas / System lock. The stack is in memory and the project diff is written from the same shot, so a transform undo patches the instance matrix and does not reload the scene. **Changes** lists that session above the project diff. **Snap** (checkbox, or V) raycasts the terrain and collision volumes while a move or a new actor is dropped.

In this build: Ctrl+K (or the Search button) opens a palette. It fuzzy-matches actors in the open scene by name, class, and hash, plus scene files and editor commands. Enter frames the actor, opens the scene, or runs the command.

In this build: **Problems** (and the same lines under the selected actor in Properties) lists diagnostics from the open scene. Each one names a file, a JSON pointer or an actor hash, a code, a message, and a severity. The list is fetched again after an edit and after undo. Copy, JSON, and Markdown export the full list, and `python -m ocarina_forge problems <banc> [--json]` prints the same report. It reports an unknown actor class, a pack that names a model file the dump does not have, a duplicate hash, a parameter whose kind does not match the scene file, a link whose `Dst` (or `Hash`) is not in this scene or the other bancs in the same folder, and a flag name whose murmur3 is not a GameDataList `Hash`. Vanilla links use `{Dst, Src, Gyaml: Reference, Name: Reference}`. `Src` has to be the actor’s own hash. An unedited link or flag is a warning; editing it makes the same finding an error. Effect locators (`EftLoc_`, `Loc_`, `Tag_`) and packs with no ModelInfo are not missing models. A vanilla scene with no edits should show no errors. A dump without `GameData/GameDataList.Product.110.byml.zs` or without `Pack/Actor` gets one warning instead of a row per actor.

In this build: **Project → Hot reload** is off until a Longshot mod folder is saved. A scene save waits a moment, builds the mod, and writes each file into that folder with an atomic replace. Files this editor wrote last time, and that the new build no longer contains, are removed. Other files in the folder stay. The stage bar says how many files were written. Longshot reads placement when the map loads, so leave the map and enter it again to see the change. A folder whose name starts with `_` or `-` is refused, because Longshot ignores it. An After the Ocarina project still builds with that pipeline, which does not merge scene actor diffs.

Still later: snap to a grid.

## 4. Behavior editor

In this build: the **Behavior** tab (or B) draws the selected actor's AINB AI graph, its schedule rows, and its event-flow hooks. **Friendly** uses the curated names. **Raw** shows every node, including ones that did not decode. Node, pin, and edge ids come from the dump file and stay put when the graph is drawn again. The panel does not edit. A later edit is a typed op (`add node`, `connect`, or `set field`) stored beside the base graph, not a replaced file. The mapping to the Longshot package is [longshot-mapping.md](longshot-mapping.md).

Still later: editing those nodes, custom graphs, a flags and quests picker, and live previews of schedules, event flows, and dialog.

## 5. Asset library

An asset library browser. Drag an actor from the library onto the scene the way Unity drops a prefab. This stays on the roadmap. It is not in this build.

## 6. New level

**New level** starts from an existing area's setup (terrain, climate, and the actor packs that area already uses) instead of an empty banc. This stays on the roadmap. It is not in this build.
