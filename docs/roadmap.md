# Roadmap

On November 20, 2026 the editor was going to move into Longshot's in-game developer UI (branch `editor-bridge`, `--with-editor`). On November 22 that direction was dropped. The in-game editor on branch `editor-mod` could write a placements patch and reload the area, and editing live memory does not feel like editing the game.

Forge is the editor again. The 3D view is not frozen. New work is a visual front end for a Longshot mod, and the running game is a preview. The design, the patch mapping, the ten editor milestones, and the Rust hot-path steps are [design/mod-editor.md](design/mod-editor.md). This page is the short list.

Parsers, decoding, the typed change format, validation, and export stay in this repo. They are the data the editor writes.

## Maintained

Parsers, decoding, the project format, validation, and export stay the job of this repo. The page is where new editing is built.

- BYML, zstd, BFRES, and BNTX. The container rules are [formats.md](formats.md). Textures and the Switch 2 GOB order are [textures.md](textures.md).
- Behavior and AI schedule decoding. A decoded graph keeps the dump's node, pin, and edge ids. A field edit is a `set field` op stored beside the base graph. See [project-format.md](project-format.md) and [longshot-mapping.md](longshot-mapping.md).
- Typed scene changes. An actor's identity is its banc `Hash`, also stored as `Phive.Placement.ID`. A hash this project mints sets bit 62 and is not rewritten when the actor moves.
- Validation (`ocarina_forge.validate.diagnose`, `python -m ocarina_forge problems`) and the exporters: the rebuilt romfs, and the Longshot package described in [longshot-mapping.md](longshot-mapping.md).
- **New level** stays on this side. It starts from an existing area's terrain, climate, and actor packs instead of an empty banc. It is not built.

### Import `<overlay>/edits.jsonl`

The November 20 note said Forge should import Longshot's `<overlay>/edits.jsonl` (one typed op per line: `banc`, actor `Hash`, `field`, `old`, `new`). That import is not the plan. A Forge save writes the mod's `patches/*.json` instead. The import is not built, and it is not a milestone.

## Editor

The page does what this build already shipped, and it gains features again along [design/mod-editor.md](design/mod-editor.md). Numbers for the view are in [performance.md](performance.md). Shortcuts are in [controls.md](controls.md).

The first milestone is the Kokiri sign, as one project and one mod: create the project, open `KokiriForest_Child`, select `Obj_Kokiri_Signboard_A_01` with `--id 0x4a13f8880eb01a43` (the class is placed more than once; without the hash the edit writes nothing and exits non-zero), move it, and save a Longshot mod into the test game's `mods/local/` folder. An unchanged project writes no patch files. The second milestone rotates that same sign, in radians, then scales it. Scale is `set` when the banc already has the key and `add` when it does not. An `add` waits for the next process start, and the inspector says so. Later milestones are renderer fidelity (materials and UVs, terrain tiling, the other scale cases already written down), the editor UX that is still missing (a grid and an axis indicator; the scale gizmo is the Scale button, and S stays fly-back), actor add, a read-only behavior graph, field edits that stay in the project, and one dialog line. Remove of a banc actor has no API op and is recorded as a gap.

The performance pass is in this build: redraw on change, instanced repeats, frustum culling, the game's LOD meshes where the file has them, a generated mesh otherwise, low-mip textures at distance, a glTF meshopt and KTX2 cache, nearest-first streaming, and prebaked terrain atlases. The terrain overlay split (main floor and `_sub` as separate layers) is in this build. The WebGPU trial is shelved; see Later.

BC7 mode 6, ASTC/BC/RGBA decode, deswizzle, the per-pixel loops, and BFRES vertex and index decode are in `native/`, called from Python. The Python functions remain when the extension is not built. PNG is zlib level 1. On `ba00e7f` the house was 6.3 s and the four Kokiri and castle scenes were 537 s of cold convert, 298 s of that in BFRES. That decode, the attribute min/max, and the glb float pack are in the crate. Skin indices, bones, and meshopt stay in Python. A scene open starts the misses on the process pool. The same crate builds for wasm32 without PyO3. That host is not scheduled. Numbers and the re-profile commands are in [performance.md](performance.md). The steps are [design/mod-editor.md](design/mod-editor.md).

Editing that already shipped stays: undo and redo, ground snap, Ctrl+K search, live validation, opt-in hot reload, docked panels, and field edits on existing behavior nodes. **Behavior** draws the selected actor's AINB graph, its schedule rows, and its event-flow hooks. Export writes a behavior file only after a round trip. A schedule that lives in another actor's pack is labeled shared.

## Later

These are not the next slice. Where one of them is a numbered milestone, or a Rust step, [design/mod-editor.md](design/mod-editor.md) says which. The rest wait until those are done.

### Rust hot paths, then maybe no Python server

R1 and the ports through BFRES vertex and index decode are in this build: BC7 mode 6, ASTC/BC/RGBA decode, deswizzle, the per-pixel loops, then attribute decode, triangle strips, and the glb float pack, in one crate called from Python. zstd and BYML stay in Python. The frontend stays JavaScript and three.js. The Python server stays.

Step two is still the same crate compiled to WebAssembly, with the dump picked through the File System Access API. It is not scheduled. The crate's hot functions stay bytes in and bytes out so that compile stays possible.

### Blueprint phase B

Connecting and adding nodes, and the palette and wire drag that edit them. The unfinished add, connect, and disconnect ops are on branch `glinesbdev/blueprint-nodes-a68c`. They are not on `ato-import`. This build still edits fields on existing nodes only. Milestone 9 keeps those field edits in the project and does not export a replaced graph.

### Baked lighting

`bake0` and `bake1`, and the mesh `bake` UV, are not sampled. [models.md](models.md) records that. A per-scene lightmap is later than the ten milestones. The heightmap's `bake_ao` grid is already applied to the ground and stays as it is.

### Terrain and occlusion

The shipped heightmap, the `_sub` overlay, frustum culling, and the `bake_ao` grid stay. Milestone 4 is the tiling check (the floor meets the actors, and `_sub` stays the upper layer). New occlusion culling is later than that.

### Asset browser

An asset library browser. Drag an actor from the library onto the scene the way Unity drops a prefab. Not started.

### Other viewer work

Snap to a grid is milestone 6, with the axis indicator. The scale gizmo is the Scale button. Custom behavior graphs, a quests browser, and live previews of schedules and event flows wait until milestone 9. One dialog line is milestone 10. A desktop package is not in the ten.

The WebGPU trial stays shelved on three r170. The opt-in remains `?webgpu=1` (or `?renderer=webgpu`), and WebGL stays the default. No adapter keeps that renderer on its WebGL2 backend. On a real Kokiri GPU the frame stayed 3–12× slower than WebGL. Revisit it only with a newer three, after the milestones that still run on WebGL. The measurements are in [performance.md](performance.md).
