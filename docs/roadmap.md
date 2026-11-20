# Roadmap

Approved next, in this order. The terrain overlay split (main floor and `_sub` as separate layers) is in the build these notes ship with. The performance pass and the WebGPU trial are in this build. Editing UX and the behavior editor are not started. Numbers for each slice are in [performance.md](performance.md).

## 1. Performance pass

Render on change, so an idle view does not redraw. Instance repeated models on the GPU. Frustum and occlusion culling. Real LODs: the game’s LOD meshes where the file has them, and a generated mesh otherwise, with low-mip textures at distance. A GPU-ready import cache (glTF with meshopt, and KTX2). Nearest-first streaming. Prebaked terrain and atlases per scene.

## 2. WebGPU trial

In this build, as an opt-in. Three stays on r170. **WebGPU** in the stage bar, or `?gpu=1`, reloads on `WebGPURenderer`. The default, and any failed start, is the existing `WebGLRenderer`. The terrain atlas has a TSL port; instancing, picking, the gizmo, LOD, culling, and render-on-change are the same scene code on both. Water and leaf alpha go through the node material when WebGPU is on. r170 does not count `BatchedMesh` draws in the WebGPU info counters, so compare fps, frame time, `inst`, and the picture, not `tris`. How to switch, and the gaps, are in [performance.md](performance.md). A Kokiri A/B is still the thing that decides whether WebGPU becomes the default.

## 3. Editing UX

Instant undo and redo on the diff model. Hot-reload of changed files into the Longshot mod folder. Cmd-K search across actors, flags, messages, and scenes. Snap to the ground, to a surface, and to a grid. Live validation for bad flag references, missing models, and unreachable events.

## 4. Behavior editor

A Blueprint-style editor over AINB and event flows. A raw layer that exposes every node, and a curated friendly layer. Stages: a read-only panel, then editing nodes, then custom graphs, with a flags and quests picker. Live previews of AI schedules, event flows, and dialog.
