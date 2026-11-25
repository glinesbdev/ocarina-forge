# Roadmap

This is the contributor plan for Ocarina Forge. It is a plan, not a promise: order and scope can change as we learn more. To pick something up, start with the open issues labeled [`roadmap`](https://github.com/glinesbdev/ocarina-forge/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap).

The design note for how the editor maps onto a Longshot mod is [docs/design/mod-editor.md](docs/design/mod-editor.md). Measured viewer numbers are in [docs/rendering-performance.md](docs/rendering-performance.md).

## Done so far

- Forge is the editor. A save writes a Longshot mod folder (`mod.json`, `patches/`, and an empty `romfs/` when a patch was written) and can install it under the test game.
- You can browse banc scenes, move and edit actors, undo and redo, snap to the ground, search, and dock panels. Validation runs live, and hot reload is opt-in.
- The behavior panel draws the selected actor's AINB graph, its schedule rows, and its event-flow hooks. A field on an existing behavior node can be edited.
- BYML, zstd, BFRES, and BNTX parsing, typed scene changes, validation, and export stay in this repo.
- The viewer redraws when the scene changes, instances repeated meshes, frustum-culls, uses the file's LOD meshes when they exist, caches glTF as meshopt and KTX2, streams the nearest models first, and caps how many unique meshes stay resident. Terrain draws the main floor and the `_sub` overlay as separate layers.
- Slow decode and mesh work runs in the Rust crate (`native/`). The same crate, built to WebAssembly, samples walkable ground in the page. WebGPU is the default renderer. `?renderer=webgl` (or `?webgpu=0`) keeps WebGL.

## Performance

Hyrule Field is the scene this work is aimed at. After the resident-mesh cap, a headless load sat at about **50 ms/frame** (~19 fps) and about **5,600 draws** (50.3 ms and 5,632 draws at 241 unique meshes, with about 0.6 GB of JS heap and a 3.2 GB Chrome working set). The table is in [docs/rendering-performance.md](docs/rendering-performance.md).

The first step is the profiling HUD, so the rest of this list is driven by a split of CPU time and GPU time.

- [#2 Split frame time into CPU vs GPU (profiling HUD)](https://github.com/glinesbdev/ocarina-forge/issues/2) — show CPU time and GPU time on the frame before changing how the scene is drawn.
- [#3 Replace CPU picking with a GPU ID buffer](https://github.com/glinesbdev/ocarina-forge/issues/3) — pick actors with a GPU ID buffer instead of a CPU raycast.
- [#4 Use GPU instancing for repeated meshes](https://github.com/glinesbdev/ocarina-forge/issues/4) — draw repeated meshes with GPU instancing.
- [#5 Static chunk batching and dynamic batching](https://github.com/glinesbdev/ocarina-forge/issues/5) — batch static scenery into chunks, and batch what still moves.
- [#6 Indirect and multi-draw where WebGPU allows](https://github.com/glinesbdev/ocarina-forge/issues/6) — use indirect and multi-draw on the paths WebGPU allows.
- [#7 Use texture arrays to cut bind and material switches](https://github.com/glinesbdev/ocarina-forge/issues/7) — pack textures into arrays so the viewer binds and switches materials less often.
- [#8 Smarter streaming and residency budget](https://github.com/glinesbdev/ocarina-forge/issues/8) — stream models in and out with a tighter residency budget.
- [#9 Compile shader pipelines in the background](https://github.com/glinesbdev/ocarina-forge/issues/9) — compile shader pipelines in the background.

## Editing and collision

- [#10 Make an actor's collision follow it when moved](https://github.com/glinesbdev/ocarina-forge/issues/10) — research how actor collision is stored, then move that collision with the actor.
- [#11 Move decorations with their parent piece](https://github.com/glinesbdev/ocarina-forge/issues/11) — when a piece moves, the decorations that sit on it move with it.
- [#12 Snap bug: gizmo drops to the ground while the model stays in the air](https://github.com/glinesbdev/ocarina-forge/issues/12) — with Snap on, the gizmo falls to the ground while the model stays up. Fix this together with [#10](https://github.com/glinesbdev/ocarina-forge/issues/10) and [#11](https://github.com/glinesbdev/ocarina-forge/issues/11).

## Exploration

- [#13 Explore running the editor fully in the browser (WebAssembly)](https://github.com/glinesbdev/ocarina-forge/issues/13) — a long-term look at running the whole editor in the browser, including parsing and conversion.

Dialog editing, connecting and adding behavior nodes, baked lighting, an asset browser, and a desktop package are still later. They are written up in [docs/design/mod-editor.md](docs/design/mod-editor.md). What a drag from that browser can and cannot bring with it is [docs/research/asset-library.md](docs/research/asset-library.md) ([#16](https://github.com/glinesbdev/ocarina-forge/issues/16)).
