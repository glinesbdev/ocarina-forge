# Running the editor in the browser

This is an exploration of issue #13. It does not change the default workflow.
`build.bat`, `./build.sh`, and the committed module under `frontend/wasm/`
are how a checkout runs. A later change should not start until this
recommendation is accepted.

**Recommendation: keep the local Python server, and treat the browser as a
viewer plus a few bytes-in/bytes-out kernels.** A fully in-browser editor is
the wrong default. The tab that already displays a field-sized scene is the
process that runs out of memory. Moving dump reads, cache builds, and model
converts into that same tab stacks a second heap on top of the one that does
not fit. A hybrid is worth doing in small pieces: more wasm exports when a
profile says a kernel is hot, and an optional origin-private cache of files
the server already built. Install, hot reload, launching the game, and a full
convert of a field-sized dump stay on the machine.

## What already runs in the page

`native/src/wasm.rs` exports three jobs, all bytes in and bytes out:

- `WasmGroundSurface` samples one walkable height. `frontend/src/ground-surface.js` fetches `/api/ground/surface` once per scene, decompresses the gzip in the page (`DecompressionStream`), and keeps one module on the main thread. Sampling one point is synchronous.
- `WasmMeshIndex` is the bulk sampler. Threading is a JavaScript worker pool with `SharedArrayBuffer`, not `wasm-bindgen-rayon`. The live Snap path does not use that pool. It is there for height-diff grids.
- `deswizzle`, `decodeLinear`, and `decodeSwizzle` turn a block-linear surface into RGBA.

The page also decodes meshopt geometry in a worker (`frontend/src/geometry-decoder.js`). That worker is JavaScript, not this crate.

Everything else below is still the server: opening the dump, building the surface pack, converting models and terrain, streaming textures, lighting references, project files, and every export that writes a mod.

## Measurements

Built 25 November 2026 on a 4-core Linux VM, rustc 1.83.0, wasm-bindgen 0.2.130:

```text
cargo build --release --target wasm32-unknown-unknown --no-default-features --features wasm --locked --manifest-path native/Cargo.toml
```

Cargo reported 14.5 s after the wasm32 standard library was installed. This checkout has no game dump, so a field-scene convert and a surface-pack build were not re-run. Those times below are the ones already recorded in [wasm.md](wasm.md) and [performance.md](performance.md).

### Module size

The browser loads the wasm-bindgen output, not the raw `cargo` artifact. The raw file is large because bindgen's describe stubs are still in the export section.

| Artifact | Bytes | gzip -9 |
| --- | ---: | ---: |
| `cargo` wasm, before wasm-bindgen | 669,314 | 157,866 |
| After wasm-bindgen 0.2.130, this build | 140,165 | 53,773 |
| Committed `frontend/wasm/ocarina_forge_native_bg.wasm` | 141,659 | 48,899 |

This rebuild was not run through `wasm-opt`, and it is not a replacement for the committed module. The two files differ by about 1.5 KB.

The linear memory starts at 17 pages (1,088 KiB) and has no maximum. A wasm32 module can grow to 4 GiB and no further. There is no shared-memory flag on this build. Each worker instantiates its own module.

String search of this wasm found `astc` and `bcdec` (decode uses them) and did not find `encode_bc7`, `bc7_mip`, `decode_triangles`, or `face_normal`. BC7 encode, mesh decode, and vertex packing compile in the crate for a Python extension. They are not in the shipped module. Exporting them later will grow this file. Measure that size before committing a fatter module.

### Decode and encode, this VM

Node 22.23.4 instantiated the bindgen module. The buffer is a synthetic 1024×1024 BC7: 1,048,576 block bytes, first byte of each 16-byte block set to `0x40` (mode 6). Median of seven calls after one warmup:

| Call | Median |
| --- | ---: |
| wasm `decodeLinear` | 12.10 ms |
| wasm `decodeSwizzle` (tile mode 0) | 14.10 ms |

The same crate, release, native, no Python, on a 1024×1024 RGBA buffer whose bytes vary:

| Call | Median |
| --- | ---: |
| `encode_bc7` | 70.70 ms |
| `decode_linear` of those blocks | 7.71 ms |
| `decode_linear` of the `0x40` pattern | 7.22 ms |

Wasm decode on this VM is the same order as the 11.17 ms `decodeSwizzle` in [wasm.md](wasm.md) (that one was headless Chrome on another machine, a swizzled synthetic surface). One 1024² encode is about 71 ms and is not in the wasm module. A scene is many images plus BFRES parse, glTF assembly, and PNG. [performance.md](performance.md) records a house cold convert at 5.5 s with the Rust extension (it was 233 s before that port) and four forest and castle scenes at one job totaling 262.9 s. Those numbers are from another machine. They are the cost a browser convert would still have to pay, per scene, inside the tab.

### Field-sized scenes, from the existing notes

[wasm.md](wasm.md) measured the large field scene on a machine that has a dump:

- Surface pack: 999,339 triangles, 525,737 vertices. First build 122 s. Later cache hit 7 ms. Gzip on disk 33.4 MB, raw 75.4 MB.
- The wasm index reports `memoryBytes` 125,185,776 (about 119 MB), on top of the 17-page start.
- After the pack is ready, one Snap sample is 10–25 µs. Until then, `GET /api/ground` is about a second per point once the server mesh cache is warm, and the first point was 116 s.
- At 567 of 852 unique models the tab held a 2.31 GB JS heap (limit 4.19 GB) and a 15.0 GB Chrome working set. The 119 MB index is small beside that.

[rendering-performance.md](rendering-performance.md) is the later cap: about 240 unique meshes or 2.2 GB of JS heap stops further loads, with a hard stop near 280 or 2.8 GB. A headless run with a 10 GB working-set cap stopped at 388 of 852 models with a 4.67 GB JS heap, already over the 4.19 GB limit. An earlier pass that baked island and hill meshes into the visible terrain glb ran the tab out of memory. The heightmap stays on screen, and the walkable meshes stay in the one wasm object that `WasmGroundSurface.free()` drops on a scene change.

The current glb cache prefix on main is `models-60`. Decoded images stay `bntx-6`. Texture manifests are `texman-1` under the cache, and shared images are `shared-v1`.

## Memory, workers, and the wasm32 ceiling

A browser port has four separate limits, and the field scene presses all of them:

1. **JS heap.** The measurements above hit a limit near 4 GB while models were still streaming. Flat glbs (no embedded images) and the texture stream from `/api/models/{gyaml}/textures` move image bytes off the geometry file. They do not give the heap a new ceiling.
2. **Wasm linear memory.** 32-bit, 4 GiB maximum, and this build sets no cap of its own. The field surface index is already 119 MB in that memory. A convert that also holds decompressed archives and RGBA scratch images grows the same memory.
3. **Worker copies.** The bench's eight workers each instantiate the module. That is the right shape for a short sample. It is the wrong shape for a 119 MB surface or a convert scratch buffer copied into every worker. One convert worker, or a pool that shares read-only pages, is the only arrangement that stays near the server's memory. `SharedArrayBuffer` still needs the COOP/COEP headers `frontend/vite.config.js` already sends (`credentialless`, not `require-corp`).
4. **GPU working set.** Chrome's process tree was 15 GB on that field load. Wasm cannot see that memory, and moving CPU convert into the tab does not shrink it.

The server's process pool is the isolation the tab does not have. Children are `spawn` processes. A warm cache hit stays in the parent. The default count is one core left free (`OCARINA_FORGE_WORKERS`, else the saved config, else `cpu_count - 1`). Interactive converts pause the warm queue. A browser worker can do the same scheduling. It cannot give the convert a heap the viewer is not sharing.

The decompressed-read cache in `dump.py` is capped at 512 MiB. A tab that is already near 4 GB of JS cannot keep that cache beside the scene.

## Inventory

Each row is a job the local server does today, then what a browser build would need. Paths are the ones in `ocarina_forge/server.py` on main, including load filters and lighting.

The browser tools named here:

- **FS Access** is `showDirectoryPicker` / `showOpenFilePicker`. Chromium only. See the support section.
- **Directory input** is `<input webkitdirectory>`. Read-only, and it copies the chosen files into the tab.
- **OPFS** is `navigator.storage.getDirectory()`. Private to the origin, quota-limited, invisible to the user and to the game.
- **IndexedDB** is for small JSON (config, project diffs), not for a multi-gigabyte glb cache.
- **Worker** is a dedicated worker with its own wasm instance.
- **Bytes-in/bytes-out** means a new `#[wasm_bindgen]` export that takes bytes and returns bytes, the same rule as `decodeSwizzle`. The Python material policy does not move with that export.

### Process, config, and the sample

| Job | Today | In the browser |
| --- | --- | --- |
| `GET /api/health` | Version and build stamp. | A static string. |
| `GET /api/stream` | Convert count, cache hits, texture time, and the warm job, since the process started. The status label polls this. | Counters on the worker that converts. Useless until convert itself moves. |
| `GET /api/status`, `PUT /api/config` | Saved dump path, project path, worker count, cache limit. Config is `ocarina-forge.local.yaml` or `~/.config/ocarina-forge/config.yaml`. The dump value is an absolute path the process can reopen. | No arbitrary path. A Chromium handle can be stored in IndexedDB and must be re-authorized. Firefox and Safari have no handle. Worker count becomes `navigator.hardwareConcurrency`. The 20 GiB cache cap becomes a storage quota the page does not control. |
| `POST /api/setup/sample` | Builds the synthetic fixture and copies the sample project. No game data. | Ship the fixture with the page. This is the one open path that needs no dump. |

### Dump access

`Dump` is read-only. It never writes the romfs. `read_file` decompresses `.zs` with the dictionaries in `Pack/ZsDic.pack.zs` and remembers up to 512 MiB. `list_scenes` walks `romfs/Banc`. `actor_names` lists `Pack/Actor`.

| Job | Today | In the browser |
| --- | --- | --- |
| Point the editor at a dump | `PUT /api/config` with a directory that contains `romfs/`. | FS Access, read-only, Chromium. The directory input works in other browsers and cannot write back. Either way the bytes land in the tab. They must not be uploaded. See Legal. |
| `GET /api/scenes` | Lists bancs and marks project edits. | Directory listing plus the project diff. Listing a full romfs through the picker is a lot of round trips. The server does one `rglob`. |
| `GET /api/actors` | Actor pack names, filtered. | A directory listing of `Pack/Actor`. |
| `GET /api/scene`, companions, recipes | Decompress and parse one BYML, apply the project diff, attach the sibling banc and recipe actors. | BYML, zstd, and the dictionary SARC have to exist as bytes-in/bytes-out, or the scene JSON still comes from the server. `formats/byml.py` is the writer as well as the reader. |
| `GET /api/changes`, `GET /api/scene/changes`, `GET /api/scene/problems` | Diff the project against the dump. Problems also read the gamedata list. | Same BYML reader, plus the project files. `validate.py` is about 800 lines of policy on top of that reader. |
| `GET /api/flags` | Substring search of gamedata names once the query is two characters. | The name list can be a JSON the server (or a one-time local index) already extracted. The page does not need the whole archive if the names are cached. |

### Project files and load filters

Projects live under `~/.local/share/ocarina-forge/projects` (or `OCARINA_FORGE_PROJECTS`), outside the dump and outside the game. The source is YAML. Export output is `build/`.

| Job | Today | In the browser |
| --- | --- | --- |
| `GET /api/projects`, `POST /api/project`, `POST /api/project/open` | Create and open a project folder. | OPFS or a picked folder. IndexedDB is enough for the manifest index. |
| `POST /api/scene/edit`, `PUT /api/scene/diff`, `PUT /api/scene/recipes` | Write the scene diff or recipe list, then schedule hot reload. | The YAML write can move. The hot-reload side effect cannot. See below. |
| `GET /api/load-filters`, `PUT /api/load-filters` | Which layers this project fetches: models, terrain, water, collision, behavior, effects, decorations, lights, and whether textures stream. Stored on the project. | Small JSON. Easy to keep in IndexedDB. It only saves work if the convert it gates is still skipped. The page already sends the filter on model, ground, and surface requests. |
| `POST /api/import/ato` | Import a scenes folder from a path on disk. | A picker, or leave it on the server. The `after_ocarina` pipeline that builds those projects stays Python. |

### Models, textures, and the warm queue

`describe_actor` / `describe_terrain` in `models.py` (about 3,600 lines) plus `formats/bfres.py` (about 3,800), `formats/bntx.py`, `formats/terrain.py`, and `formats/gltf.py` are the convert. Cache keys hash the source bytes, the prefix (`models-60` for glbs, `bntx-6` for decoded images), and a stamp of the converter sources. Files land under `~/.cache/ocarina-forge`. A daemon thread drops old versions and evicts the least recently used files down to 90% of the cap. The default cap is 20 GiB. `0` means unlimited.

`formats/meshopt.py` keeps a Node process up (`frontend/tools/meshopt-worker.mjs`) and ships `EXT_meshopt_compression` in the glb. If Node is missing, the converter falls back to a grid quantize and skips a generated LOD. The game's own coarser level is left as it is.

`POST /api/warm` and `GET /api/warm` prepare one scene and its sibling. The page does not call them. It reads `warm` off `GET /api/stream`. What the page does call is `POST /api/models/batch`, which prefetches queued names on the pool when more than one worker is configured. The CLI `warm` command is what a game build with the editor enabled runs before opening a scene.

| Job | Today | In the browser |
| --- | --- | --- |
| `POST /api/models/batch`, `GET /api/models/{gyaml}` | Cache hit returns a glb URL. A miss converts on the pool, one flight per gyaml. Load filters can skip a whole layer. `flat=1` (the usual path now) writes geometry without embedded images. | A worker can run the Rust kernels that are already bytes. The policy that chooses materials, punches magenta, builds ORM, and decides water is still Python. A second copy of that policy in JavaScript will drift. The existing JS surface sampler is kept only so the bench can compare it. |
| `GET /api/models/{gyaml}/model.glb` | The cached glb, or the shared-texture variant when the query version matches. | OPFS can remember a glb the server produced. Building it in the tab is the field-sized job this note leaves on the server. |
| `GET /api/models/{gyaml}/textures` | A manifest of decoded images, keyed by the original glTF node name. The file is cached under `texman/`. A second request does not decode the BNTX again. | The manifest is small. The decode behind it is the 12 ms wasm kernel plus every format wasm does not cover (BC6, PNG wrapping, partial-gob policy). The server still does that decode. |
| `GET /api/model-textures/{name}` | Immutable KTX2, PNG, or JPEG from the shared store. | Same bytes from OPFS, still produced by the server. A content hash in the name is the cache key. |
| `POST /api/warm` | Bounded prefetch, paused while an interactive convert runs. | A queue in front of the same worker. It does not remove the need for the convert. |
| Scene preview `GET /api/scene/preview.glb` | `scene_preview.py` shells out to Node (180 s timeout) and writes a coarse glb. | No `child_process` in the page. The preview is a server cache file the page can fetch. |
| `GET /api/scene/mesh-audit` | Converts or reads the scene's models and reports them. | Stays with the convert. |

PNG encoding stays in Python on purpose. `native/src/image.rs` says a fast deflate that only run-length-encodes zeros made tiled maps several megabytes, so IDAT stays at zlib level 1 in Python. BC6 stays on `texture2ddecoder`. `native/src/decode.rs` does not claim to match its half-float rounding, and the wasm build does not include it.

### Terrain, ground, and the surface pack

| Job | Today | In the browser |
| --- | --- | --- |
| `GET /api/terrain`, `GET /api/terrain/model.glb` | Convert the TSCB height field, apply the material grid and `bake_ao.extm` as vertex shade, and optionally refit the mesh to water and roads (`prepare_scene_terrain`). A corrected glb is a second cache file. | The visible mesh is the piece that ran the tab out of memory when it included every island and hill. Keep building it on the server. The fit walks actor meshes. That is the same collect the surface pack does, and the first field collect is on the order of two minutes. |
| `GET /api/ground` | Python sample of height tiles plus floor meshes. Snap uses it until the pack arrives. The server keeps the mesh cache in the process (`query_ground_cached`). | Already the fallback. Once the pack is in wasm, the page samples locally. |
| `GET /api/ground/surface` | Build or load the gzipped OFGS pack (`surface_pack.py`, magic `OFGS`). Invalidated when the banc or the pack version changes. | The sample is wasm. The build still reads every floor mesh out of the dump and welds it in Python. Moving the build means moving that collect, not `WasmGroundSurface`. A field pack is 75 MB raw before it becomes a 119 MB index. |

`bake_gi.extm` and `custom.extm` are declared on the TSCB index and are not decoded. `bake0` / `bake1` on a material bind a shared placeholder, and the viewer does not sample them. Those facts are already in [formats.md](formats.md). A browser port does not unlock a lightmap that this code does not read.

### Lighting

There is one lighting HTTP route. The sun, fog, and exposure are not in a file this server decodes.

| Job | Today | In the browser |
| --- | --- | --- |
| `GET /api/lighting` | `lighting_report`: effect, locator, and fog actors from the banc (parameter names, not colours), GameBancParam key names, a directory-name probe under romfs, and an imported JSON reference if one exists. | The banc half needs the BYML reader. The reference is a JSON file under `~/.local/share/ocarina-forge/lighting` (`OCARINA_FORGE_LIGHTING`). The page can apply that JSON. It cannot invent the sun. |
| `lighting-import` (CLI) | Reads `<game>/dev/user/modstore/global/local~lighting-capture.json` and writes one JSON per map. The `~` is the mod id with the slash replaced. | That store is inside the game folder. Reading it is FS Access in Chromium, or the server. The file is the player's session, not a committed asset. |
| `lighting-mod --install` (CLI) | Copies the capture mod into `<game>/mods/local/lighting-capture`. `--gfx-probe` writes the entry that static-imports `longshot/gfx`, and refuses to leave the other entry in place. The manifest asks for `state.read`, `actors.read`, `storage`, and `gfx.world`. | Writing the game folder and starting the game stay native. The modding API has no getter for sun, fog, or exposure. On the build recorded in [formats.md](formats.md), `longshot/gfx` exports overlay drawing (`world.line`, `world.box`, `world.circle`, `world.billboard`) and `effect`. A page cannot call `has()` or `storage.global`. |
| `lighting-survey` (CLI) | Same report as the HTTP route, plus declared TSCB extension types. | Stays a dump-side tool. |

The lights layer in the load filter is a Gyaml name test (`light`, `lamp`, `torch`, and the other tokens in `layers.py`). It is not a decoded environment. Skipping that layer skips those actors' models. It does not turn off the editor's directional light.

### Behavior

| Job | Today | In the browser |
| --- | --- | --- |
| `GET /api/behavior` | Load the actor graph from the pack (`behavior.py`, about 1,800 lines) and apply saved ops. Uses the `evfl` and `ainb` packages. | Those packages are Python. A bytes-in parser does not exist in `native/`. Leave the graph on the server until one does. |
| `POST /api/behavior/field`, `PUT /api/behavior/ops` | Edit ops in the project. | The YAML can move with the rest of the project. The graph they apply to is the dump. |
| `POST /api/behavior/export` | Compile saved ops into the build folder. The opaque file is what the game loads, because graphs do not merge. | The write is export output. Share-safe export must still refuse a copied base graph. See Legal. |

### Export, install, hot reload, and permissions

Two exporters, plus hot reload:

| Job | Today | In the browser |
| --- | --- | --- |
| `POST /api/export` | Rebuild changed bancs with the dump's zstd dictionary, raise the resource-size table in place, write `build/romfs`. `install` copies that tree to `<dump>/mods/<name>/romfs`. `zip` writes a zip beside it. An `after_ocarina` project is built by that package instead, and scene diffs are not merged in. | Re-encoding BYML and zstd with the dump dictionary is a bytes-in job that does not exist in wasm yet. Copying into the game or dump `mods/` folder is a write outside the origin. The `after_ocarina` tree stays Python. |
| `GET /api/export/download` | The latest zip in `build/`. | A Blob download of files the page created. The zip must not grow dump-derived bytes. |
| `POST /api/mod-export` | Write `<project>/build/mod` (`mod.json`, placement patches, an empty `romfs/` when the manifest needs one). `install` copies it to `<game>/mods/local/<slug>` (`OCARINA_FORGE_GAME`, default `~/Games/Longshot/mod-test`). Imported mods keep their original runtime files. | The JSON can be built in the page from the project diff. Installing it is the game-folder write. |
| `GET` / `PUT /api/hot-reload` | On save, build and copy romfs into a chosen mod folder with `os.replace`, and delete files this tool wrote last time that the new build dropped. The game shows the change when the map is loaded again. Refuses the dump and the project's own `build/`. A folder whose name starts with `_` or `-` is ignored by the game and is refused here. | The game and the editor are two processes. A Chromium picker can copy bytes into a folder the user selects. It does not replace a saved absolute path, an atomic replace the game is reading, or a Firefox user. Leave hot reload on the server. |
| `GET /api/local-mods`, `POST /api/local-mods/open` | List `<game>/mods/local` and import one into the project library. | Reading the game tree. Same picker limit. The import copies whatever the mod folder contains. A share-safe import still must not treat those files as something to upload. |

**Permissions** are a server-side mod-config job, tracked as issue #30 on branch `mod-permissions`. This note does not change that work or those files. Today `mod.json` is written by `modexport.py`, which keeps imported manifest fields and does not offer a picker. Grants live in the game's `mods.json` (`<game>/dev/mods.json` on the build [formats.md](formats.md) records), in the mod entry's `granted` array, not in `mod.json`. Pre-granting is a write into the game folder. Asking the running build `has()` is a call inside the game. Both stay native. A browser permissions form can edit the project copy of the declaration. It cannot grant it.

### Same libraries, not the HTTP server

These commands share the dump and the convert, and a browser editor does not replace them: `problems`, `model-debug`, `mesh-audit`, `ground-debug`, `material-debug`, `material-audit`, `tex-debug`, `tex-audit`, `behavior`, `behavior-audit`, `behavior-export`, `warm`, `scene-preview`, `project`, `edit`, `mod-export`, `profile`, `height-diff`, `lighting-import`, `lighting-survey`, `lighting-mod`.

`tools/wgsl` is a separate native binary (Naga). The server does not run it. A translated stage is reference data for a draw comparison. It is not a browser material.

The server also gzip-compresses JSON responses, serves `frontend/dist` when that build exists, and prunes the cache on a daemon thread. Those are process jobs. A static page has no prune thread. OPFS eviction is the browser's, and it is not the 20 GiB LRU this cache implements.

## What stays native

- **Writing the game tree.** Install (`mods/` and `mods/local/`), hot reload, the lighting-capture copy, and a `mods.json` grant. A Chromium directory picker can copy files the user points at. It is not a saved path, it is not atomic with a process that has the folder open, and it does not exist in Firefox or Safari. The default remains the server.
- **Starting a process.** The game itself, the meshopt Node helper, the scene-preview Node helper, and the Naga translator. The page has workers, not `subprocess`.
- **A full convert of a field-sized dump.** First surface collect on the order of two minutes, hundreds of models, a 20 GiB disk cache, and a tab that already stops around 240 resident meshes. The Rust pixel kernels are the small part. `models.py`, `formats/bfres.py`, and `formats/terrain.py` are the convert.
- **Formats the wasm crate does not claim.** BC6 (`texture2ddecoder`), PNG IDAT, zstd-with-dictionary compress for the romfs exporter, and the `evfl` / `ainb` graph libraries.
- **The `after_ocarina` build.** Text, events, gamedata, logic, and schedule for those projects. Scene diffs are not merged into that build.

## Browser support

Checked against caniuse and the File System Access write-ups current as of late 2026.

| Capability | Chrome / Edge | Firefox | Safari |
| --- | --- | --- | --- |
| `showDirectoryPicker` (read/write a user folder) | Yes since 86 (desktop). Android Chrome since 132. | No (through 160). | No (through 27). |
| Persistent "allow on every visit" | Desktop Chromium since about 122. | No. | No. |
| `<input webkitdirectory>` | Read-only file list. | Read-only. | Read-only. |
| OPFS `navigator.storage.getDirectory()` | Yes. | Yes since 111. | Yes since 15.2. |
| IndexedDB | Yes. | Yes. | Yes. |
| Wasm, workers, `SharedArrayBuffer` with COOP/COEP | Yes. The editor already sends the headers. | Yes, with the same headers. | Yes, with the same headers. `wasm-bindgen-rayon` was considered and not used. |

A browser-only dump open is a Chromium feature. Firefox and Safari can be given a read-only file list, or they keep using the local server. OPFS can hold a private cache. It is not the game folder, and a quota is not a promise of 20 GiB. `navigator.storage.persist()` reduces eviction. It does not set the cap.

Serving the page from a public origin would also fight the COOP/COEP setup, and it is the legal problem in the next section. The editor stays on localhost.

## Legal

The editor reads a dump the player already has. It does not ship game files, and this repository must not gain any. A browser build is held to the same rule, with one extra failure mode: once the bytes are in the tab, a request can send them away.

- Dump and cache bytes stay on the machine. No new endpoint, analytics call, or crash report may attach them. A local origin is the way to keep that true. A hosted page that can read the dump can upload it.
- FS Access permission is consent to read a folder the user picked. It is not a license to copy the game into a mod, a zip, or OPFS that later syncs.
- Share-safe export, as [longshot-mapping.md](longshot-mapping.md) records from the modding API spec (revision 4, §6.6), refuses a cloned model, a decoded texture, and a copied event or AI graph. `GET /api/export/download` and any Blob download have to apply that refusal. The glTF cache is not a mod file.
- The synthetic fixture and the sample project are the only dump a build may include. `POST /api/setup/sample` already uses that fixture.
- Lighting JSON under the user data directory is session data. It is not committed. `gfx` in that file is a name dump from the player's run, not a texture.

No format fact turned up in this pass that [formats.md](formats.md) does not already record. The scene-lighting section there covers `bake_gi.extm`, the capture store path, and the `longshot/gfx` names observed on a running build. Nothing in this note is a new layout, so there is no addition for the `after_ocarina` format notes either.

## Phased plan

**Phase 0, now.** Accept or reject this note. No code change to the server, the wasm exports, or the dev scripts.

**Phase 1, only if a profile says a kernel is still hot.** Export more of the existing Rust as bytes-in/bytes-out (`encode_bc7`, mesh decode, vertex pack). They already compile for wasm32 and are absent from today's module. The caller stays the Python server until a parity test matches the extension byte for byte. Do not grow `frontend/wasm` without measuring the gzipped module. Effort is the export list and the tests, not a new converter. The risk is a second production caller of the same bytes.

**Phase 2, optional cache mirror.** Write server-built glbs and shared textures into OPFS under their content hash, so a repeat open in the same profile skips the download. The server still builds them. Effort is frontend cache code and an eviction story that fails soft when the quota says no. This does not remove Python.

**Phase 3, Chromium-only, and only for small files.** A read-only directory handle for the project, and for opening one placement file, once BYML and zstd-with-dictionary exist as wasm exports. Firefox and Safari keep the server. Do not convert the field scene in the tab. Do not walk the whole romfs on the opening click. Effort is a second implementation of the container layer (`formats/byml.py`, `formats/zsio.py`, `formats/sarc.py`) plus every caller that today takes a `Dump`. That is the large piece, and it is still not the model converter.

**Leave in place.** Install, hot reload, `mods.json` grants (issue #30), the lighting-capture install, the resource-size export, behavior graphs, the `after_ocarina` pipeline, meshopt's Node helper, and field-sized converts.

## Risks

1. **The tab is already the memory limit.** A field scene has reached a 4 GB JS heap and a 15 GB working set, and an earlier terrain mesh ran the tab out of memory. Wasm adds a second heap with a hard 4 GiB ceiling. A worker pool that copies the 119 MB surface makes that worse. The server's child processes are what keep convert memory out of the viewer.
2. **A page that can read the dump can upload it.** Any browser mode has to stay on localhost, with no request that carries dump, cache, or decoded-texture bytes. A directory picker is not permission to ship the game. Share-safe export has to apply to Blob downloads the same way it applies to a mod folder.
3. **Two converters will drift.** The JS surface copy exists for the bench. A JS or wasm port of `models.py` would be a second material policy. Pixel kernels can move as bytes. The policy should stay in one place until a test locks every output byte.
4. **The filesystem API is Chromium-only.** Firefox and Safari cannot reopen a dump directory. OPFS is not the game folder and will not hold a 20 GiB LRU the way `~/.cache/ocarina-forge` does. A design that assumes `showDirectoryPicker` drops every non-Chromium user onto a broken open screen unless the local server remains the default.
5. **Game integration does not move.** Hot reload, install, the capture mod, and permission grants write directories the game reads, and the capture has to run inside the game. Issue #30 is the permissions UI, on branch `mod-permissions`. It should land once, in the server, and not grow a second writer in the page.
