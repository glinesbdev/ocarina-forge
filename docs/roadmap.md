# Roadmap

On November 20, 2026 the main editor moved into Longshot. It is the in-game editor in the developer UI, on branch `editor-bridge` of Origin `glinesbdev/longshot`, built with `--with-editor`. The game-side notes are `docs/editor-bridge.md` in that repo.

Ocarina Forge's 3D viewer and web UI are frozen. Bug fixes only. No new viewer features.

The data and export side stays maintained: the BYML, zstd, BFRES, and BNTX parsers; behavior and AI schedule decoding; the typed change format with stable ids; validation; and the exporters. That side should import Longshot's `<overlay>/edits.jsonl` into a project.

## Maintained

Parsers, decoding, the project format, validation, and export stay the job of this repo. New editing is built in Longshot, not in the page.

- BYML, zstd, BFRES, and BNTX. The container rules are [formats.md](formats.md). Textures and the Switch 2 GOB order are [textures.md](textures.md).
- Behavior and AI schedule decoding. A decoded graph keeps the dump's node, pin, and edge ids. A field edit is a `set field` op stored beside the base graph. See [project-format.md](project-format.md) and [longshot-mapping.md](longshot-mapping.md).
- Typed scene changes. An actor's identity is its banc `Hash`, also stored as `Phive.Placement.ID`. A hash this project mints sets bit 62 and is not rewritten when the actor moves.
- Validation (`ocarina_forge.validate.diagnose`, `python -m ocarina_forge problems`) and the exporters: the rebuilt romfs, and the Longshot package described in [longshot-mapping.md](longshot-mapping.md).
- **New level** stays on this side. It starts from an existing area's terrain, climate, and actor packs instead of an empty banc. It is not built.

### Import `<overlay>/edits.jsonl`

Longshot writes one typed op per line under the overlay. Each op names `banc`, the actor `Hash`, `field`, `old`, and `new`. Forge should read that file into a project, matched to the scene diff by banc path and actor hash. This import is not built yet.

## Frozen viewer

The page still does what this build already shipped. It does not gain features. Numbers for the viewer are in [performance.md](performance.md). Shortcuts are in [controls.md](controls.md).

The performance pass is in this build: redraw on change, instanced repeats, frustum culling, the game's LOD meshes where the file has them, a generated mesh otherwise, low-mip textures at distance, a glTF meshopt and KTX2 cache, nearest-first streaming, and prebaked terrain atlases. The terrain overlay split (main floor and `_sub` as separate layers) is in this build. The WebGPU trial is shelved; see Paused.

Editing that already shipped stays: undo and redo, ground snap, Ctrl+K search, live validation, opt-in hot reload, docked panels, and field edits on existing behavior nodes. **Behavior** draws the selected actor's AINB graph, its schedule rows, and its event-flow hooks. Export writes a behavior file only after a round trip. A schedule that lives in another actor's pack is labeled shared.

## Paused

These were viewer milestones, or the plan to drop the Python server. They are not next.

### Run without the Python server

Not started. This was the plan to leave the Python process behind. The frontend would have stayed JavaScript and three.js.

Step one ports the hot paths to a Rust crate and calls that crate from Python with PyO3 and maturin. The paths are swizzle and deswizzle, BNTX and ASTC decode, BFRES mesh conversion, and zstd and archive reading. The per-model convert and texture times in [performance.md](performance.md) were going to decide which of those moved first.

Step two compiles that same crate to WebAssembly and moves parsing and conversion into the browser. The dump folder would be picked with the File System Access API.

### Blueprint phase B

Connecting and adding nodes, and the palette and wire drag that edit them. The unfinished add, connect, and disconnect ops are on branch `glinesbdev/blueprint-nodes-a68c`. They are not on `ato-import`. The frozen viewer still edits fields on existing nodes only.

### Baked lighting

`bake0` and `bake1`, and the mesh `bake` UV, are not sampled. [models.md](models.md) records that. A per-scene lightmap in the viewer is paused. The heightmap's `bake_ao` grid is already applied to the ground and stays as it is.

### Terrain and occlusion

Further terrain and occlusion work in the viewer is paused. The shipped heightmap, the `_sub` overlay, frustum culling, and the `bake_ao` grid stay. New occlusion culling, and a new terrain presentation, are not viewer work.

### Asset browser

An asset library browser. Drag an actor from the library onto the scene the way Unity drops a prefab. Not started.

### Other viewer work

Snap to a grid. Custom behavior graphs, a quests browser, and live previews of schedules, event flows, and dialog. Dialog editing and a desktop package.

The WebGPU trial stays shelved on three r170. The opt-in remains `?webgpu=1` (or `?renderer=webgpu`), and WebGL stays the default. No adapter keeps that renderer on its WebGL2 backend. On a real Kokiri GPU the frame stayed 3–12× slower than WebGL. Revisit it only if the frozen viewer needs a bug fix on a newer three. The measurements are in [performance.md](performance.md).
