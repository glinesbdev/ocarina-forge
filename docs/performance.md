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

A decoded BNTX is stored under its Tex path and the decoder version, not only under a hash of that one model's copy of the bytes. Castle bricks that many actors name are decoded once. The next model reads `bntx/*.bwtx`. The in-memory cache is still per process, so another worker hits the file. An embedded texture with no path keeps the blob hash. The file also keeps the linear blocks (cache `bntx-6`) so a later encode can upload BC1, BC3, or BC7 without decoding them again.

## GPU textures

An albedo in the glb is `image/ktx2` with `KHR_texture_basisu`. The bytes are a small container: a KTX2 of the blocks, then the JPEG or PNG. three r170's KTX2 loader raw-uploads only uncompressed formats, ASTC 6x6, and one float ASTC. BC1 and BC7 throw `Unsupported vkFormat`. A Basis file is vkFormat 0, and that is the transcoder path that came back as an empty image. This machine has no basisu or toktx binary, so UASTC and ETC1S were not encoded and decoded again. The files we write use a real vkFormat and are not Basis.

The view uploads the blocks with `CompressedTexture` when the GPU has the matching extension: `WEBGL_compressed_texture_s3tc` for BC1 and BC3, `EXT_texture_compression_bptc` for BC7, `WEBGL_compressed_texture_astc` for ASTC. Native BC1 and BC7 mip 0 are copied through. Lower mips are encoded in that same format. BC3 and ASTC are re-encoded to BC7 mode 6 for the whole chain, because a desktop GPU usually has BPTC and not ASTC. A swizzle pass that had to change the footprint encodes from the decoded pixels. Normals and ORM stay PNG, so a colour block format is not applied to data textures.

Without the extension, the trailing JPEG or PNG is decoded with `createImageBitmap`, off the main thread. A vkFormat of 0 still goes to three's Basis transcoder under `/basis/`. Meshopt still compresses the geometry and copies the image bytes through.

The glb prefix is `models-46` and decoded images are `bntx-6`, so the first open after this build misses the glb cache. Mode 6 blocks match the previous encoder. PNG images in the glb are zlib level 1. The file bytes change, and the pixels stay the same. The decoded BNTX files are reused. The first GPU build left castle walls near black and roofs, trees, and some ground flat white, with an empty Problems list. GLTFLoader calls `loader.load` on the KTX2 loader. The stand-in only implemented `parse`, so `load` threw, the loader swallowed the error, and the material kept no map. A one-level KTX that does upload still samples black: three then assigns LinearMipmapLinear from the sampler, and a compressed texture cannot invent the missing mips. `prepareMaterial` also used to turn `generateMipmaps` back on, which WebGL rejects for BC formats. Each albedo now stores the whole chain, the loader implements `load`, and a compressed map is forced to that chain's filter after the sampler write. A size that is not a multiple of 4 stays a JPEG or PNG, because WebGL rejects that level 0. A compressed upload the GPU refuses falls back to that bitmap and shows up in Problems. `?textures=bitmap` (or Settings → GPU textures off) uses the bitmap on purpose, for an A/B against the blocks. Headless Chrome with ANGLE SwiftShader, which exposes S3TC and BPTC, drew a 4-level BC1 quad and its JPEG through GLTFLoader as the same pixel. A one-level file stayed that color on SwiftShader and kept LinearMipmapLinear until the repair pass set Linear. A desktop GL that samples an incomplete compressed texture as black is the case that repair covers. The sample Kokiri walk on this machine, one core, is 16 models, 2 converted, 76.4 ms convert, and 0.4 ms texture the first time. The second run is 2 cache hits and 0 ms. That is server time. There is no display GPU here, so the browser upload is not in that number. Retail Kokiri and Hyrule Castle are not in this checkout. Time them with the warm command in Stream timings: the first run is cold, the second is warm, and Stats shows `conv`, `tex`, `dl`, and `up`.

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

BC and ASTC decode, zstd, and pack reads run in the server process when a model or a scene is built. Albedo arrives as a GPU block image (see GPU textures) with a JPEG or PNG behind it. Normals, ORM, and the terrain atlas stay PNG or JPEG. Meshopt glbs are unchanged. Terrain atlases and LOD meshes are built at import and cached.

## Stream timings

Every model convert records two server times. `convertMs` is the whole conversion. `textureMs` is only the texture decode that missed the decoded-image cache, so a warm open stays near zero on that number. The glb meta keeps the cold timing. A later open replaces the timing it returns with `cache=hit` and does not add those milliseconds to the server totals.

The server prints one line per convert on the process log:

`model Npc_Kokiri_F_01 cache=miss convert=120.5ms texture=80.0ms`

`GET /api/stream` is the same list, plus how many misses and hits this process has done and the summed miss times. **Stats** adds the scene's own sums: `conv` and `tex` from those timings, `dl` for the glb download, and `up` for glTF parse plus GPU upload. The Stats panel repeats the server summary under the line.

The sample Kokiri scene on this machine (11 actors, two real meshes) was 103.6 ms cold and 1.3 ms warm before this instrumentation. Retail Kokiri Forest and Hyrule Castle are not in this checkout. On a dump, the first run is cold and the second is warm:

```bash
.venv/bin/python -m ocarina_forge warm Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs --scene-only --dump ~/Games/Longshot/rc/v3.0.0
.venv/bin/python -m ocarina_forge warm Banc/HyruleCastle/HyruleCastle_Dynamic.bcett.byml.zs --scene-only --dump ~/Games/Longshot/rc/v3.0.0
```

Those are the placement files. The Static files in the same folders have no Actors array; warm skips them. `--scene-only` times that scene and does not walk neighbors. Each model prints `name cache=… convert=…ms texture=…ms` as it finishes. The last line is `warm done` and includes the wall time. Run a command twice. A cold line says `cache=miss` and has a texture time. The next run of the same models says `cache=hit` and texture time 0. `warm --all` walks every scene.

## Background warm-up

Opening a scene queues that scene's models, nearest to the camera first. Neighboring scenes are markers until those models have started, and a file with no Actors array is skipped. The queue runs one model at a time and waits while an interactive convert is in flight, so it does not take a worker from the view. **Stats** shows `warm 12/80`. The same queue is `POST /api/warm`. `ocarina-forge warm <scene|--all>` does the walk in the foreground and prints each model as it finishes. `warm --scene-only <scene>` does not walk neighbors.

## Model loading

Opening a scene sends one `POST /api/models/batch` with every actor name. Cache hits come back with a glb URL that includes the cache digest (`?v=`), and the view downloads up to 8 of those files at once, nearest to the camera first. A name that is not converted yet is `queued`: one of those 8 slots calls `GET /api/models/<name>`, which converts it, and only a real mesh then downloads the glb. Moving the camera re-sorts the queue that has not started. The same actor is not converted twice, and the same glb URL is not fetched twice. Conversions run in the process pool (one less than the core count, unless Settings or `OCARINA_FORGE_WORKERS` says otherwise; 1 stays in the server process) and the handlers are async, so a convert does not freeze the API. A versioned glb response sends `Cache-Control: public, max-age=31536000, immutable` and an `ETag` of the digest, so a reload is a browser cache hit. HTTP/2 is not enabled; localhost uses those parallel connections instead.

**Stats** adds `queued`, `loading`, and `loaded`. While Kokiri is streaming, `loading` should sit near 8 and `queued` should fall. After it settles, `loading` is 0 and `loaded` is the number of distinct models.

## R1 profile

`profile` times the Python convert. Browser `dl` and GPU `up` are not in the process. If those dominate **Stats** on a cold open, the Rust port stops.

The default cache is a fresh directory under the temp folder. It does not read `~/.cache/ocarina-forge/models`, so a cold `cache=miss` is expected and is not a changed cache key. The key is `models-46`. `--home-cache` uses the normal directory instead. The stage timers wrap the functions the converter already calls, so this command does not rebuild a normal editor cache. `zstd` includes the model-file read before the convert clock and is not subtracted from `convert`. Vertex and index time are inside BFRES parse. Deswizzle and block decode are inside the texture clock. `other` is what is left. The log line `native bc7/decode: rust` means the extension loaded. `native pixels: rust` means this build's per-pixel functions are in that extension. `python` on either line means that path is the fallback. An older extension can say `bc7: rust` and `pixels: python` until `maturin develop` is run again.

One model:

```bash
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0 \
  --model Land_Kokiri_House_Inside_E_04 \
  --cprofile /tmp/house-inside-e04.prof
```

A scene walk is one model at a time unless `--jobs` is set. `--limit N` stops each Kokiri child banc and each Hyrule Castle Static or Dynamic banc after N models. Kokiri child on a 24-core Linux box, `mod-editor-m1` at `bbfd77c`, was killed at 600 seconds after 89 models: banc read 41 ms, cold convert about 599 s, texture about 130 s, every line `cache=miss`. That miss was the private cache. The synthetic fixture below is not that split.

```bash
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0 --limit 12 --jobs 8
```

On the synthetic Kokiri child Static fixture (11 actor classes, two real meshes) a cold wall was 82.6 ms, of which convert was 80.5 ms and texture 0.4 ms. The five candidate phases together were about 1.1 ms (zstd 0.15, BYML 0.12, BFRES parse 0.53, swizzle 0.31, decode 0). The warm wall was 1.5 ms with both meshes cached. A cProfile of `Npc_Kokiri_F_01` put most of that cold convert in the meshopt helper's first poll (about 55 ms) and in JPEG encode, not in swizzle or ASTC. Decode was 0 because the fixture is not castle ASTC. Do not treat this as the retail split.

## House profile and the native crate

`Land_Kokiri_House_Inside_E_04` on Bradyn's PC, `mod-editor-m1` at `a176bd6`, cold cache. This checkout does not have that model, so these before numbers are his, not a re-run:

| stage | time |
| --- | --- |
| cold convert | 233.4 s |
| encode | 156.1 s |
| `gpu_image` / mip chain | 134 s |
| `encode_bc7` | 121.7 s over 86 calls |
| `_mode6` | 116 s over 568k calls |
| `_index` tottime | 76.2 s over 8.67M calls |
| texture clock | 49.9 s (decode 18.0 s, swizzle 5.8 s) |
| `_choose_material` / `_load_named` | 73.7 s, 70 `_load_named` calls |
| bfres | 2.7 s |
| glb | 0.3 s |
| meshopt | 0.07 s |
| other | 24.3 s |
| warm | 1.5 ms |

`_load_named` includes the texture clock. The 70 calls are not 70 decodes. `_decode_bntx_cached` already returns the image when the path is cached, and it does not start the texture clock on that hit. Each call still decompressed the `.bntx.zs` and `_accept` still walked the pixels. `external` is filled after a successful load and was not checked first. A convert now remembers a load by dump, model, name, hints, alpha, cutoff, layer, and the data flags, and remembers the decompressed bytes by path. A second call with the same key does not read the file. A different name in the same file still skips the decompress. That is the `other` bucket. It does not remove the first decode.

The encoder is this mode 6, not bc7enc and not the Intel ISPC texture compressor. Those search partitions and modes. ISPC and AVX2 ports are not a `wasm32-unknown-unknown` build, and they would change the blocks. The profile's time is the Python `_index` loop, 16 weights by 4 channels, 8.67 million calls. The crate is that loop. `cargo build --manifest-path native/Cargo.toml --no-default-features --target wasm32-unknown-unknown --release` compiles it here. PyO3 stays out of that build.

Decode uses `bcdec_rs` for BC7 and `astc-decode` for ASTC LDR. Both are pure Rust. BC1, BC3, BC4, and BC5 are written here so the 565 expansion matches `texture2ddecoder` (bcdec's scale is off by one on some endpoints). BC2 on this `texture2ddecoder` has no entry point and is decoded as BC3, and the crate does the same. BC6 stays on `texture2ddecoder`.

Measured on this machine, release, one thread. Not the house:

| work | Python | Rust |
| --- | --- | --- |
| BC7 mode 6, 512² | 2320.3 ms | 16.8 ms |
| BC7 mode 6, 1024² | | 67.2 ms |
| BC7 mip chain, 1024² | | 94.7 ms |
| RGBA deswizzle, 1024², log2 4 | 692.2 ms | 6.9 ms |
| ASTC 8×5 decode, 1024×1020, one repeated block | 177.5 ms | 5.0 ms |
| BC7 decode, 512² | 48.7 ms | 1.8 ms |
| channel reorder, 1024² | 429.4 ms | 3.2 ms |

512² is 16,384 blocks. His 568k `_mode6` calls are about 35 of those images. 2320.3 / 16.8 is 138× on this CPU. Applied to his 121.7 s of `encode_bc7`, that is about 0.9 s for the same blocks. That is a ratio, not a new house convert. A 1024² Python encode was not run. Four times the 512² Python time is about 9.3 s, against 67.2 ms of Rust.

He measured that build (`4afe3f7`) on the same house. Cold convert 32.6 s. The 233.4 s line is the baseline above. This checkout still does not have the model.

| stage | time |
| --- | --- |
| cold convert | 32.6 s |
| encode | 16.55 s |
| other | 11.7 s |
| texture clock | 3.1 s (decode 0.93 s, swizzle 0.03 s) |
| bfres | 1.0 s |
| glb | 0.16 s |

cProfile of that run is 55.9 s under the profiler, so those tottimes are not wall time. The functions, by tottime: `limit_rgba` 14.2 s (52 calls), `_has_near_magenta` 6.9 s (42 calls, 37.9M inner calls), `zlib.compress` 6.4 s (75 calls), surface `_write` 5.9 s, `_noise` 3.6 s, `pack_layer` 9.7 s cumulative, `_bc1_block` 1.9 s cumulative (21.8k calls), `_downsample` 1.3 s. `encode_albedo` is 21.4 s cumulative and contains `limit_rgba`. BFRES at 1.0 s is not ported.

The same crate now does those pixel loops. Calls stay inside `limit_rgba`, `_has_near_magenta`, `_write`, `_noise`, `encode_bc1`, and `_downsample`, so the next cProfile still names them. BC1 mip chains encode in the crate the way BC7 chains already did. The ORM fill is one repeated pixel, which is a byte string times the pixel count, and each channel blit is the crate.

Measured here, release, one thread. Not the house:

| work | Python | Rust |
| --- | --- | --- |
| `limit_rgba` 2048×1024 down to 1024 | 783.3 ms | 11.4 ms |
| near-magenta scan, 1024², no hit | 95.4 ms | 0.7 ms |
| `_noise`, 1024² | 39.2 ms | 0.2 ms |
| `_downsample`, 1024² | 613.6 ms | 2.3 ms |
| BC1 encode, 256² | 147.7 ms | 0.4 ms |
| one ORM channel blit, 1024² | 279.0 ms | 2.0 ms |

`zlib.compress` is the PNG IDAT and the decoded-image cache. The cache file was already level 1 and stayed there. PNG was level 6. On a 1024² image with a gradient plus small noise, level 1 took 66.8 ms and 2.76 MB against level 6 at 114.7 ms and 2.67 MB. A 16-pixel tiled image was 6.0 ms and 0.07 MB at level 1, against 11.2 ms and 0.03 MB at level 6. PNG is now level 1. Cutouts, normals, and ORM still need a lossless image. Opaque albedos are already JPEG, and `_encode_cached` already skips a repeat inside one process. A cold profile uses an empty directory, so that cache does not remove the first 75 calls. A deflate that only run-length-encodes zeros was 5.1 ms and 4.79 MB on the detailed image, and 4.48 MB on the tiled one. That is not used. Level 0 would store the RGBA and is only faster when the image does not compress.

There is no house time for this build. The 32.6 s line is the before. The table above is the after on this machine. A full `KokiriForest_Child` cold open is still running on his PC and is not in this note. At `bbfd77c` that walk was killed at 600 s after 89 models. The command below prints one house, then a cold total for each Kokiri child banc and the castle. The log must say `native bc7/decode: rust` and `native pixels: rust`.

```bash
pip install maturin
maturin develop --release --manifest-path native/Cargo.toml
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0 \
  --model Land_Kokiri_House_Inside_E_04 \
  --cprofile /tmp/house-inside-e04.prof
python -m ocarina_forge profile --dump ~/Games/Longshot/rc/v3.0.0
```

Without the extension, the same commands run the Python fallback and the house stays near 233 s. An extension from `4afe3f7` prints `native pixels: python` and stays near 32.6 s. The first open after this build misses `models-46` once. Decoded `bntx-6` files are reused. Mode 6 blocks match. PNG bytes in the glb changed with the zlib level. The pixels did not.

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
