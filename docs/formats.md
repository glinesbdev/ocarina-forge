# Formats this version reads and writes

The container rules below were reconciled with After the Ocarina's `docs/game-formats.md` and `docs/blockers.md` (branch `m8-navi-rumors`, commit `ad60e27`). Those notes were verified in game. An earlier Ocarina Forge write-up used the public MIT notes instead, and its resource-size formula was wrong. The checklist is [testing-on-real-dump.md](testing-on-real-dump.md). The equivalence command is [equivalence.md](equivalence.md).

`ocarina_forge/formats/LICENSE-oot-remake-modding.txt` is that MIT license. The code here is a fresh implementation of those layouts, not a copy of the third-party tree, so the editor’s library can stand on its own.

Nothing in this repository is extracted from a game dump.

Community format references used for cross-checking include [TSCB terrain research](https://github.com/TotkMods/Research/blob/main/Formats/TerrainSceneBinary/Readme.md), [AINB research](https://github.com/TotkMods/Research/blob/main/Formats/Ainb/Readme.md), and the [EventFlow format index](https://github.com/TotkMods/Research/blob/main/Readme.md). These are reverse-engineering notes, not an engine specification; check format versions and runtime behavior against the target game build.

## Where files live

Dump root (the folder Ocarina Forge stores in config):

```text
romfs/Banc/<Area>/<Scene>.bcett.byml.zs
romfs/Pack/Actor/<Gyaml>.pack.zs
romfs/Pack/ZsDic.pack.zs
romfs/System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs
mods/<name>/romfs/...
```

`GameBancParam` `.bgyml` files are per-area settings, not placement files, and are not listed as scenes.

A scene name looks like `KokiriForest_Child_Static` or `HyruleField_Adult_Dynamic`. Static and Dynamic are separate bancs. The editor opens one of them as the file it writes, and draws the other (same folder, same age, `_Static` swapped with `_Dynamic`) as a companion overlay. Kokiri houses live in the Dynamic banc; the NPCs in Static stand on them.

## BYML

Version 7, little endian, magic `YB`. Ocarina Forge reads and writes hash maps (`0x20` u32 keys, `0x21` u64 keys) as well as the usual arrays, dicts, strings, and numeric types. Identical containers are shared. Bool is not stored as an int.

`Hash32` and `Hash64` in Python are dict subclasses so a rebuilt banc can keep `AuxFlags` and anything else keyed by hash. The public `byml.py` writer did not emit hash maps; this one does, because dropping them would change the file.

## Zstd dictionaries

`Pack/ZsDic.pack.zs` is plain zstd (no dictionary) containing a SARC of `*.zsdic`. Dictionary ids used on 1.1.1:

| Id | Used for |
| --- | --- |
| 1 | everything else (`.zs`) |
| 2 | `.bkprb.zs` |
| 3 | `.bcett.byml.zs` (placement) |
| 4 | `.pack.zs` |

Export compresses a changed banc with the dictionary id the dump’s own frame used, falling back to 3 for `.bcett.byml.zs`. Checksums are off, content size is on. Export uses level 19. Tests use a lower level.

If the dictionary pack is missing or has the wrong id, export fails with a message that names the file. It does not write a plain-zstd banc and hope.

## SARC

Little endian only. Names are sorted by the usual hash (`h = h * 0x65 + byte`). Repacking with an original SARC as a template keeps each file’s alignment. The actor catalog does not unpack actor packs; it reads the filenames in `Pack/Actor`.

## Resource size table

`System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs`, plain zstd, RESTBL version 1. If that name is missing, the first `System/Resource/*.rsizetable.zs` is used. If there are scene diffs and no table, export stops and does not install anything.

The path key is the romfs path **without** `.zs`. CRC is `zlib.crc32`. A file that was not compressed keeps its suffix: logic modules and AI schedules are `.ainb` and `.bgyml` keys. Named entries exist for CRC collisions. The size is a memory budget, a multiple of 8, not the file size.

When a shipped file's bytes differ from the dump, the entry becomes:

```text
align8(old entry + max(0, new unpacked size - old unpacked size) + 1024)
```

The raise is not skipped because the new file still fits under the old entry. An entry is never shrunk. Saria's schedule is the measured case: entry 14448 for a 3316-byte file. A file that grows to 3384 bytes is `align8(14448 + 68 + 1024)` = 15544. Leaving 14448 made her stand with no dialogue, because the game loaded a default schedule. The logic module's dump entry 12984, for a file that goes 12598 → 13189, becomes `align8(12984 + 591 + 1024)` = 14600.

A path with no entry is not added, except when a patch asks for one. The tapestry step inserts two new hashed entries of 40152 (the CRC of the model path without `.zs`). The hashed list stays sorted. Named entries are copied unchanged. Longshot loads only the mod table that sorts last, so the shipped table has to be the one that wins.

Scene export uses this formula and refuses a banc that has no row. The After the Ocarina engine writes the table with `rewrite_hashed`, including those two new rows.

## Actor placement

Fields the editor treats as the actor header:

- `Gyaml` — actor name
- `Hash` — u64 instance id
- `SRTHash` — u32
- `Translate` — `[x, y, z]` metres, or `{X, Y, Z}`
- `Rotate` — radians, yaw in Y, optional
- `Scale` — optional
- `Name` — optional instance name

Everything else (`Dynamic`, `Links`, `Presence`, `Bakeable`, `Phive`, …) is a typed parameter. New actors get `Hash` and `Phive.Placement.ID` set to the same new u64. `make_actor` also sets `SRTHash` to the low 32 bits of that hash. A hash this project mints sets bit 62. That bit is not part of `SRTHash`.

`Links` is a list of dictionaries. The validator treats `Dst` as the other actor’s hash. An edited link may use `Hash` instead. `Src`, when present, is the hash of the actor that owns the link. A zero hash is an empty slot. The same check accepts a target that lives in another banc in the same area folder (the Static/Dynamic pair, or another layer in that folder).

These root keys are group lists, not actors: `AiGroups`, `FarDeleteGroups`, `SimultaneousGroups`, `PlgGdTable`, and `PhiveHeader.PlacementGroups`. Export copies them through from the source banc. It does not add a new hash to them. A removed actor whose u64 still appears in one of those lists, or in some other actor’s `Links`, is left in place and the export log warns.

Category colors use the pack-name prefix: `Npc_` / `Shop_`, `Enemy_`, `Animal_`, `Eft`, `Land_`, `Area_` / `Area`, `Obj_` / `FldObj_` / `Item_` / `Weapon_` / `Dm_`, and system names (`StartPos`, `Player`, `Sound`, `AirWall`, `Ground`, anything with `Anchor` or ending in `Actor`).

### Load layers

One banc file holds every actor. There is no decoration list, light list, or water list in the placement BYML. Forge splits the editor payload by `Gyaml`, and skips the separate files those layers decode:

| Layer | What it is |
| --- | --- |
| models | Actor BFRES for `Npc_`, `Enemy_`, `Animal_`, `Shop_`, and everything that is not one of the layers below. `Land_*` is an actor mesh, not the heightmap |
| terrain | The static-map TSCB (`TerrainArc`) and the walkable surface pack. Not a Gyaml |
| water | Actor BFRES whose name has a water token (`water`, `lake`, `waterfall`, `ocean`, `pond`, `sea`, `river`, `stream`, `moat`, `aqua`, `mizu`, `brook`), including a camel-case piece such as `WaterPlane` or `LakeHrl`. An `npc` token stays a model. The material reader splits only on underscores, so `WaterPlane` is one token there and is not how this layer is chosen. Surveyed TSCB indexes have no water type |
| collision | `AirWall`, `collision`, `collider`, or `navmesh` in the Gyaml. Drawn as a gizmo. Collision, navmesh, and occluder shapes inside a BFRES are already left out of the drawn glb |
| behavior | The per-class AINB graph, plus link gizmos. Not a slice of the banc |
| effects | Gyaml starts with `Eft` (`Eft_` and `EftLoc_`) |
| decorations | Dressing tokens: grass, flower, bush, weed, ivy, fern, mushroom, moss, vine, shrub, plant, clover, herb, petal. `Obj_Tree` stays a model |
| lights | `light`, `lamp`, `torch`, `candle`, `lantern`, `chandelier`. `bake0` / `bake1` are not this layer |

`textures` is not an actor layer and it is not a second glb. The editor always fetches one geometry file, `/api/models/<Gyaml>/model.glb?flat=1`. That file has the mesh and a plain material. It does not embed images, and its cache key does not include texture bytes. Images are `GET /api/models/<Gyaml>/textures`. The manifest is keyed by `partName:materialIndex`, which is the glTF node name with the `lodN:` prefix removed. Three.js strips `:` from `Object3D.name`, so `lod0:Body:0` becomes `lod0Body0` and does not match that key. The original node name stays on `userData.name`. The client looks the slot up with that name, not the sanitized object name. Albedo is sRGB. Normal, ORM, and emissive maps are linear. A decoded image is assigned only when it is a three.js texture. Each image is a content-addressed file in the shared texture store (`shared-v1`, `/api/model-textures/<sha256>.ktx2`, or `.png` / `.jpg`). The manifest itself is cached under `texman/` (`texman-1` plus the converter prefix and the bfres bytes). A second request reads that file and does not decode the BNTX again.

`Load textures` (on unless the preset is Layout or Logic, or the URL says `textures=0`) streams those manifests after the mesh, a few at a time, nearest visible actors first. One runs while model loads are still in flight, and four run once geometry is idle. The stage-bar Textures box only shows or hides the images. That swap writes the resident maps back onto the same material the batches already use. It does not fetch, decode, or dispose. Turning Load textures off stops the queue and leaves images that already arrived in memory. The status label shows `textures 120/450` while that queue is open. Turning models on while textures are loaded fetches the geometry file once and the manifest once. The geometry cache key is `models-56`.

`gpu_image` will not pack a BC level 0 whose width or height is not a multiple of 4, so that manifest entry is a JPEG or PNG (`/api/model-textures/<sha256>.jpg` or `.png`), not an OCFG. The stream loads every one of those URLs through the GPU texture loader. A file that is not KTX2 is the image itself and is decoded with `createImageBitmap`. An OCFG or raw KTX2 whose blocks the GPU cannot upload, and which has no JPEG or PNG trailer, does not reject: the loader resolves a 1×1 flat colour and warns once for that texture. The show/hide switch does not fetch, so it does not hit this path.

`Links` is described above. A load filter does not strip `Phive`, and `Phive.Placement.ID` still mirrors `Hash`. Save, Save Longshot mod, and Build mod read the source banc plus the project diff. They do not drop an actor because its layer was not fetched.

Actor meshes are Switch BFRES plus embedded BNTX. The reader, the cache, and what still falls back to a box are in [models.md](models.md). The pack, the model, and the texture are global files. The banc only stores the placement. What has to be copied when a placement moves to another banc is [research/asset-library.md](research/asset-library.md).

## Phive shapes

Actor-local collision that the terrain fitter reads is `Phive/Shape/Dcc/<Gyaml>__Physical.Nin_Ounce_NVN2.bphsh`, with or without `.zs`. `read_mesh_shape` returns triangles only for a file this parser accepts. Anything else, including a bad length, is an empty list. The synthetic file in `tests/test_phive.py` is what these offsets were checked against. A retail shape was not part of that test.

The wrapper is little endian. It starts with `Phive\0`. The u32 at `+12` is the start of the payload, and the u32 at `+28` is its size. The payload is big-endian Havok chunks (u32 length with the high bits masked, then a 4-byte kind). The first chunk is `TAG0`. Inside it, `SDKV` must be `20250200`, and the item index must name one `hknpMeshShape` at `(0, 1)`.

In the `DATA` chunk of that shape:

| Offset | Field |
| --- | --- |
| `+0x50` | three floats, inverse quantization scale, each finite and greater than 0 |
| `+0x88` | relative i32/u32 view of `hknpMeshShape::GeometrySection`, stride `0x40` |

Each section:

| Offset | Field |
| --- | --- |
| `+8` | relative view of `Primitive` records, 4 bytes each |
| `+16` | relative view of `hkUint8` vertex bytes |
| `+32` | three i32 origin values |

A primitive is four vertex indexes. The fourth index equal to the third is one triangle. Otherwise the record is a quad and becomes two triangles. Vertices are three u16s, dequantized as `(value + origin) * inverse_scale`. If the first origin word is `0x7fffffff`, the vertices are three floats instead. A non-finite float rejects the whole shape.

This reader does not open `Phive/StaticCompoundBody`. Those per-scene `.bphsc` bakes are the collision copy discussed in [#10](https://github.com/glinesbdev/ocarina-forge/issues/10) and in the asset-library note. No offset inside a `.bphsc` is implemented here.

## BFRES models (version 10)

Version 9 FMDL keeps an 8-byte reserved slot between the material array (`+0x38`) and the material dictionary (`+0x48`). Version 10 does not. On these files:

| Offset | Field |
| --- | --- |
| `+0x38` | material array |
| `+0x40` | material dictionary |
| `+0x48` | a different pointer. It is not the dictionary |
| `+0x68` | u16 vertex count, u16 shape count, u16 material count |

Reading `+0x48` as the dictionary returns one material, because that pointer is not the name list. The u16 at `+0x6C` is a second copy of the material count. When it is larger than the dictionary and that many `FMAT` blocks are present, the array length wins.

## BFRES materials (version 10)

Version 9 FMAT blocks are `0xA8` bytes and keep the Wexos field order (texture names at `+0x30`, sampler dictionary at `+0x48`, counts at `+0x9C` / `+0x9D`). Version 10, which is what the Switch 2 dump reports, is `0xB0` bytes:

| Offset | Field |
| --- | --- |
| `+0x08` | name pointer |
| `+0x20` | texture-name pointer array (one u64 string per texture) |
| `+0x38` | sampler-name dictionary (`_a0`, `_n0`, `_rgh0`, `bake0`, … in the same order as the textures) |
| `+0x40` | render-state string pointer array |
| `+0xA0` | u16, 0 or 1 on these files. Not the length of that array |
| `+0xA2` | u8 sampler count |
| `+0xA3` | u8 texture count |

The render-state array is 31 fixed slots of string pointers, not the version-9 `gsys_*` records. The walk stops at a null pointer or a pointer that is not a BFRES string (u16 length, bytes, NUL). An empty string is a real slot and does not stop the walk. Index 10 is cull (`front` or `both`), index 11 is the alpha mode (`opaque`, `mask`, `translucent`, `custom`), and index 15 is the alpha test (`true` on a mask). Albedo is `_a0`, then `_a1` or `_a2`. A material whose samplers are only `tc` / `ta` (runtime names `MaterialAlb` / `MaterialCmb`) has no albedo slot. `Mt_TerraMaterial_NNN` is handled apart from that rule: layer NNN of the `MaterialAlb` array is the colour. Version 10 has no render-info dictionary at the version-9 offsets, because `+0x18` and `+0x20` are the texture list. Other qwords in the header are checked for a BFRES string dictionary. When that dictionary is missing, `Mt_Sand_Kokiri_Cliff` uses layer 16 and the log says the index is a guess.

## FSHP flag 0x10

Public Switch 1 notes give FSHP `+0x04` two bits that matter here: `0x02` means the shape has a vertex buffer. Kokiri and Deku files also set `0x10`, so the flags word is `0x12` (`Obj_Kokiri_Door_A_*`, `Fence_A_03`, `RoofFence_A_01`, `StoneGate_A_01`, `Stone_A_06`, `Land_Deku_CaveProp_A_*`, `ProceduralIvy_*`, `House_Outside_C_00`, and the rest of that set: 69 of 526 models in those two areas). On those shapes the FVTX pointer lands in zeroed memory and the mesh-count byte is 0. Bytes after the GPU buffer base (`0x7000` on the files that were inspected) include the u32s `0x40` and `0x7c`, which is the sort of header a meshlet or mesh-codec block would have.

The [ResMeshlet header](https://epd.zeldamods.org/wiki/Bfres#ResMeshlet) points to the GPU buffer. Forge uses the layout documented by [NUP Explorer](https://github.com/lunixxx37/NupExplorer/blob/main/nupx/clusters.py) to read the packed vertices and triangle indices, together with normals, UV sets, and the first vertex colour. Rigid single-buffer BFRES 10.4 shapes supply authored geometry for the near and far LODs. Unsupported shapes still use their bounding record, and `model-debug` reports the reason. `.bfres.mc` is a separate stream (`MCPK` / `FMSH`). The TerrainArc heightmap below handles the field.

## TerrainArc

The ground under a scene is not `Model/<Area>_01`. That bfres is a stand-in. The heightmap is named by the scene’s game banc param and stored under `TerrainArc/`.

`Banc/GameBancParam/<Level>.game__banc__GameBancParam.bgyml` (or the `.zs` file) has `TerrainSceneName` and `TerrainScenePosition` (`X`, `Y`, `Z`). `<Level>` is the banc filename with `.bcett.byml.zs` and a trailing `_Static` or `_Dynamic` removed, then the area folder name. `$parent` is followed: a leading `?` and `Work/` are stripped, `.gyml` becomes `.bgyml`, and `.zs` is tried. The parent fills fields the child does not set. At most eight parents.

`TerrainArc/<scene>.tscb` (plain, or `.tscb.zs`) is little-endian:

| Offset | Field |
| --- | --- |
| `+0x00` | `TSCB` |
| `+0x10` | float, metres per terrain unit |
| `+0x14` | float, maximum mesh height (world metres of a 0..1 sample). Retail scenes use 800. |
| `+0x38` | u32 tile count |
| `+0x78` | relative u32 to a table of relative u32s, one per tile |

A relative u32 is `field + value`. A tile starts with three floats: centre x, centre z, edge length, in terrain units. `+0x60` is a relative offset to a NUL-terminated tile name. The quadtree names every tile; only some have a file.

The [community TSCB notes](https://github.com/TotkMods/Research/blob/main/Formats/TerrainSceneBinary/Readme.md) describe version `0x70C`, including grass and water types. The 25 indexes checked in the installed OoT build use version `0x10D`. Its file-type count is at `+0x30` and its file-type offset table at `+0x70`. Type descriptors carry the type code, name, extension and an optional resource class. Each area has a resource count at `+0x20` and a table of relative u32 offsets at `+0x68`. Each referenced record starts with the type code and contains two raw height floats at `+0x20` and `+0x24`. Entries can vary by tile. A record declares metadata and does not prove a payload exists on disk or define how the game uses it.

The installed OoT indexes declare `hght` (`0x0000`), `norm` (`0x0100`), `mate` (`0x2000`), `bake_ao.extm` (`0x5100`), `bake_gi.extm` (`0x5200`), and `custom.extm` (`0x5300`). The survey found no water type in those indexes and no `.water.extm` payload under the installed `TerrainArc`. The viewport draws water from actor BFRES models. The meaning of the `custom.extm` payload is still open.

`python -m ocarina_forge ground-debug Banc/HyruleField/HyruleField_Child_Dynamic.bcett.byml.zs --resources-only --at=-72,510 --json` lists the records for tiles that cover a point. Omit `--at` for all tiles and `--json` for a text summary. The command parses the indexes without decoding payloads. Normal terrain loading uses the existing tile reader and filename lookups. A full ground-debug <banc> --at x,z (without --resources-only) reports the composed world surface at that point: the finest height tile plus Land_* island, cliff, soil, and hill meshes, taking the highest hit. Water in height-diff uses the Front/Surface mesh, not the surface AABB, so a hole in the sheet (the Lake Hylia island) stays dry and a higher pool wins over a lower plane.

`TerrainArc/<scene>/<name>.hght` starts with `TARB`. The u16 at file offset 10 is the header size. The bytes after that header are a zstd frame (dictionary id from the frame, usually the shared zs dictionary). Unpacked, offsets are relative the same way:

| Offset | Field |
| --- | --- |
| `+0x2C` | relative u32 to the height block |
| `+0x30` | u32 size of that block |

The height block is 257×257 float32 samples, row-major, row along z and column along x, edge to edge. A finite non-zero sample is ground. The retail range is about 0..0.18, but an exact 0 left after the dashed-column repair is treated as uncovered (a hole), not a height at the terrain origin. A leftover whose absolute value is at most 1e-3 is treated the same way: at height scale 800 that is under a metre, and meshing it produced a flat grass sheet at the origin under hills and unbaked Land_* meshes. Those leftovers showed up as worldY=0 (or a few centimetres) under hills the game actually walks. Only a non-finite sample (NaN or infinity, including those uncovered zeros) is a hole. The header that is parsed has no hole bitmap, and an empty `.mate` texel is a missing material, not a hole. When the block size is `257×257×4` the floats are linear. Otherwise the block is the same block-linear layout as a texture: 4 bytes per sample, 16 GOBs per block, so block-height log2 4 (`surface_bytes` is 417792). Columns 0..255 fill those sixteen GOBs. Column 256 is a single texel, and its odd rows are stored 4 bytes after the even row. Reading the ordinary GOB offset there hits padding and returns exact 0, a dashed column down to the origin. That column is read from the packed slot, and a remaining one-sample zero copies the height beside it. The value is 0..1 and the world position is `unit * scale + origin`. World Y is `sample * heightScale + origin.Y`. Y is up. Surface triangles are wound so the normal points up. The mesh is front-side, and the skirts stay.

That height scale is not a fudge factor. A naive least-squares fit of Forge Y against `world.ray` ground hits can look like 0.58× (Kokiri) or 0.72× (Lake Hylia) because those scans also hit `Land_*` cliffs, rocks, and ground meshes that sit above the heightmap, and because some tiles used to decode almost flat. Against points that really are on the heightmap, the implied scale is 800.00 (Kokiri median `gameY / raw` matches the TSCB float at `+0x14` to a few parts in 10^5). The same 800 applies to Hyrule Field. Longshot's modding API has no heightmap query (`has()` / typings / spec: `content.model` serves models, and `world.ray` is the game's own collision).

A sparse `.hght` (many real zeros) can fool a "fewer zeros, smoother field" log2 guess into picking an almost-flat GOB layout whose decoded max is about 1/256, while the packed block and the hght resource `rawMax` at `+0x20`/`+0x24` still contain highs around 0.10–0.22 (80–180 m). Decode now keeps the default log2-4 layout when it recovers at least 90% of the packed-block height range, and otherwise chooses a log2 that does. Hyrule Field tile `3300000024` was the first example: default decode max 0.0039, packed/TSCB max 0.11–0.16.

The walkable sample uses the main scene first, finest tile first, and skips a tile with no usable height so a coarser tile can fill the hole. A finer tile that exists only as `.mate` or `bake_*.extm`, or whose resource `rawMin` and `rawMax` are both 0, is not ground at the origin. The `_sub` overlay is not the walkable floor and must not win when main still has a sample.

Some ray hits are not in the heightmap at all. Kokiri rocks and `Land_Kokiri_Ground_*` sit about 10–70 m above the local heightmap (the heightmap itself tops out around 0.184 × 800 ≈ 147 m in that scene). The 213 m hill at (187.6, 627.0) is `Land_HrlField_Middle_Cliff_A_04`. Snap and height-diff already take the higher of the height tile and those Land_* floors. The visible preview mesh stays on the lighter heightmap plus ground/floor/road cutouts, so a rock can still look like it floats until that actor model has loaded. A few west-of-lake samples around 175 m have near-zero height in every covering `.hght` under the default (and recovered) layout, and no `Land_*` origin within 60 m; those need a wider actor or collision check, not a different global scale. Many of those footprints have a finer TSCB tile that ships only bake-ao/gi, no `.hght`; the parent sample there is a near-zero leftover and is now a hole, so the preview does not draw a flat field at the origin. The game walks `Land_*` cliff and mountain meshes in some of those areas (`MiddleMountain_*_UnBake` is one name). Snap includes `mountain` in the composed surface list.

Entrance refinement uses a named `FieldAndDungeonEntranceEventRequestActor` and a same-name `StartPos` within 40 m. The Market start is about 21.5 m from its trigger, which a 20 m limit missed. The fitted box is the union of 15 m pads around the start and those triggers. The fit can raise too-low coarse ground up to the approach floor, as well as cut a coarse mesh that bridges a depression, then taper into the surrounding field.

A second scene is loaded when it exists. The name comes from a GameBancParam string whose key contains `sub` or whose value ends in `_sub`, from the ASCII `<scene>_sub` convention, or from a `<name>_sub` string inside the main TSCB. `TerrainArc/<scene>_sub.tscb` is an overlay, not a finer replacement of the walkable floor. On Kokiri its heights sit about 13–24 m above the actors. Each scene is meshed on its own: the finest tile in that scene wins, and a sub quad does not drop a main cell. A different scale is fitted onto the main scene so the overlay lands in the same place. `.hght` is read from that scene’s own directory.

The material grid is `TerrainArc/<scene>/<name>.mate` for a main tile and `TerrainArc/<scene>_sub/<name>.mate` for an overlay tile (same TARB and zstd wrapper). The other scene’s `.mate` is not a fallback. Unpacked, `+0x2C` is a relative offset to the image and `+0x30` is its size. Retail images are 835584 bytes: two block-linear surfaces of 257×257 four-byte texels, the same log2-4 layout as a heightmap (417792 bytes each). The second surface is often zeros. A texel is material A, material B, blend, extra. 255 and 127 are not layer indices. An empty or invalid texel copies the nearest written neighbour before the mesh is built, so a boundary is not magenta and is not material 0. 0x7f in a blend byte is a halfway mix of the two albedos (a guess). A blend byte other than 0 or 0x7f is that byte divided by 255. The diagnostic layer is still material A unless that raw weight is 128 or more. A tight linear grid still uses the older rule for the named layer (blend at or above 128 selects the second index) and stores the byte as a weight. A byte or u16 grid is accepted when the values can be MaterialAlb layers (0..89). Up to 48 distinct layers are kept; rarer ones stay the earth colour. The mesh does not interpolate layer ids across a triangle: each quad has one pair, and only the weight is interpolated. Those layers are packed into one atlas (16 most-used, plus an earth cell) so the field is one draw. `.hght` `+0x1C` is used only when no `.mate` decodes. If neither source yields a layer, the tile is a flat earth colour. The view samples that atlas with triplanar world-space UVs, blended by the surface normal, repeating every 8 metres. That repeat is still a guess.

`<name>.<child|adult>.bake_ao.extm` (the variant matching `Child` or `Adult` in the banc path) uses that same header. The image is a 1-byte or 4-byte block-linear grid; format byte 0x06 prefers 4 bytes, and the surface size decides. A 4-byte texel uses the first byte when the others are 0 or 0x7f. `<name>.custom.extm` is not decoded.

A coarse cell is dropped only when its centre lies inside a quad a finer tile of the same scene actually emitted. A sub quad does not remove the main floor under it. A finer height that never became a triangle leaves the coarse ground in place. A tile whose edge is 24 m or less stays on step 8, and one of 80 m or less stays on step 16, so a small tile does not collapse to a 5×5 sheet. Every remaining tile that meets the placed actors, expanded by 200 m, is meshed. There is no fixed tile cap. Samples are about 2 m apart within 70 m of the actor centre (3 m when more than 24 tiles sit in that radius), about 5 m out to 180 m, and about 10 m in the rest of the margin. The step is 8, 16, 32, or 64, never 4, on that path. Each tile’s outer edge grows a vertical skirt (at least 4 m, or 2% of the tile) so two LOD steps meet without a crack. The ground is front-side. **Sub terrain** starts off and shows a wireframe reference for the overlay, which on Kokiri sits above the actors. Actor LOD, the small-prop distance cull, and the InnerMesh / bake / fade / water-volume filters do not apply to this mesh.

For a Child or Adult banc, Forge tries `<name>.<child|adult>.mate` in the tile's terrain directory before `<name>.mate`. The generic grid also covers unreadable age-specific grids. In the surveyed build, Hyrule Field has 186 adult material tiles. The material choice keeps the same height samples.

The Lake Hylia fishing pond fit uses the placed `Obj_LakeHrl_WaterPlane_A_07` mesh to bound the underwater area. Where the coarse ground intersects that water sheet, the fit limits ground height to 0.12 m below the local surface. The fit runs in the existing terrain preparation worker. In the viewport, bass and loach spawn positions above the pond mesh have a separate submerged display pose. The saved placement and Inspector coordinates still identify the spawn point.

## Scene lighting, fog, and effects

Forge does not yet decode a scene environment file into sun, fog, or exposure. What follows is what this checkout can actually point at. Anything not measured on a dump here is marked unverified. No offsets were added for a layout this code does not read.

### What the placement files already contain

Effect actors are the `Eft` prefix (`Eft_Firefly` is in the synthetic fixture). `EftLoc_` and any gyaml with `Locator` are markers: a missing mesh is not an error. A gyaml with `Fog` in the name, including `VolumeFogArea`, is a volume outline. The viewport draws a small cross on those three groups. The cross is a gizmo. It is not a particle simulation.

`Dynamic` keys on those actors are listed by name (`lighting-survey`, `/api/lighting`). The values are not read as colours. This checkout has not seen a retail `VolumeFogArea` parameter list, so no fog colour, near, or far field is named from the banc.

### GameBancParam

`Banc/GameBancParam/<Level>.game__banc__GameBancParam.bgyml` is the per-area parameter file the terrain loader already opens. The keys Forge interprets are `TerrainSceneName`, `TerrainScenePosition`, and `$parent`. Other keys are reported by name and left alone. A test fixture once used a document shaped `{"Climate": {}}` as a stand-in for a file with no actors. That is not evidence the game has a `Climate` key.

### Baked light already in the terrain and materials

The TSCB index declares `bake_ao.extm` (`0x5100`) and `bake_gi.extm` (`0x5200`). `bake_ao.extm` is the ground occlusion grid described above. `bake_gi.extm` is only a declared extension. Its payload is not decoded. `custom.extm` is not decoded either.

BFRES `bake0` and `bake1` bind `CmnTex_BakeLightDefault` or a `Dummy*` texture. Those are shared placeholders, not a per-scene lightmap, and the viewer does not sample them. The `bake` vertex attribute is a second UV. It is not written into the glTF. The glTF writer stores occlusion as `occlusionTexture` on the ORM image and does not set `texCoord`, so the loader samples `TEXCOORD_0` (attribute `uv`). A channel of 1 samples `TEXCOORD_1` (`uv1`). A mesh that lacks that attribute is drawn without the sample.

### What Longshot has said, not re-checked on a dump here

These notes are from public Longshot write-ups. They are not file layouts, and this checkout did not open the game's files to confirm them.

- Volumetric Ground and Sky fog is edited in memory from "environment-palette" resources (Longshot PR #2423, unmerged). The PR names the menu fields (near/far density, noise, scattering, absorption, emission attenuation). It does not publish a romfs path or a struct. Placed mist and local fog volumes are separate from those two layers.
- Fog and exposure come from the game's own values. `RX_VK_OOT_FOG` and `RX_VK_OOT_EXPOSURE` do nothing (Longshot PR #159).
- The renderer description in Longshot PR #727 talks about screen-space AO, cascaded sun shadows, per-object baked light, and reflections from a fixed environment picture. That is a description of the frame, not a file format.

From the Longshot modding API (spec revision 4, 2026-11-19, and the world-layer commit described on PR #1958). These are the published names, not measurements from a dump:

- `has(name)` answers whether this game build has a feature. It returns false for an unknown name and does not throw. Importing a `longshot/...` module this API version does not have is refused at link time (section 9). Section 7.2 says a mod's modules are its static imports, and `import()` does not resolve. The same section says every API module's default export is its frozen namespace, and a function whose feature this build lacks is still exported and throws `NotAvailableError` when called. A static import fails when the module is absent from the API version. It does not fail merely because `has()` is false. The default capture entry does not static-import `longshot/gfx`. `lighting-mod --install --gfx-probe` does, and reads that default export's names without calling them.
- `game.state()` returns `frame`, `mode`, `map`, `age`, and `timeOfDay`. The published return does not include sun direction, sun colour, ambient, fog, sky, exposure, or a tonemap curve.
- `player.state()` returns `position`, `velocity`, `life`, `maxLife`, `rupees`, and `maxRupees`.
- `longshot/gfx` is the world-drawing module. Its permission and `has()` name are `gfx.world` (drawing) and `gfx.shader` (screen effects). Spec section 10.1 lists `world.line`, `world.circle`, `world.box`, `world.quad`, `world.model`, and a separate `model` import. Section 10.3 exports `effect`. PR #1958's world-layer commit describes lines, boxes, circles, and billboards, and `WorldShape` handles (`moveTo`, `setPoints`, `setStyle`, `setText`, follow an actor, `setVisible`, `release`). The spec says those shapes are lit by a simple model that uses the scene's sun direction. That sentence describes the renderer. It is not a function that returns the sun. Section 10.3: `effect({ fragment, stage, uniforms })` returns a screen-effect handle (`set`, `setEnabled`). The shader's output goes to the screen. Section 10.4 says nothing reads that output back into a script, and the API does not replace the game's shaders. No published getter returns sun, fog, ambient, exposure, or an environment palette.
- The same spec revision marks `longshot/gfx` and `longshot/world` as not built yet. `docs/modding/gfx.md` and `docs/modding/world.md` on the public `mod-api` branch were not readable from here (404). `longshot/world` (`world.ray`) is the collision query height-check uses once `has("world.ray")` is true. Lighting capture does not static-import it.

From a Longshot run on 2026-11-25 (the `modding-with-editor` build), not from the spec: `has("gfx.world")` was true, `has("gfx.shader")` was false, `has("game.pause")` was true, and `has("world.ray")` and `has("world.rayAll")` were false. The log file was `<game>\logs\mod-run.txt`. The store file was `<game>\dev\user\modstore\global\local~lighting-capture.json` (the slash in `local/lighting-capture` is a `~`). That file is the storage key map (`lighting` and `lighting:<map>`). It has no `mod` field. `game.state().map` for Child Kokiri was `KokiriForest_Child`, which is the same key `map_name_for_banc` makes from `KokiriForest_Child_Static` and `KokiriForest_Child_Dynamic`. A run of the dynamic import logged `TypeError` with the message `import("longshot/gfx") in /main.js: a mod's modules are its static imports; import() is not available`. That text is the runtime saying what section 7.2 says. The same run granted the permissions the manifest asked for, including `gfx.world`. The permission grant and the missing dynamic import are separate.

Observed on Bradyn's `modding-with-editor` build, 2026-11-25, from a static import that read names and types and did not call them. The log line was `FORGE_LIGHTING gfx` followed by:

```json
{"present":true,"staticImport":true,"moduleKeys":["world","effect"],"types":{"world":"object","effect":"function"},"worldType":"object","worldKeys":["line","box","circle","billboard"],"world":{"line":"function","box":"function","circle":"function","billboard":"function"}}
```

Compared with spec revision 4, this build's module exports `world` and `effect` only. There is no `model` export. `world` has `line`, `box`, `circle`, and `billboard`. It does not have `quad` or `model`, which section 10.1 names. The four values are functions. The `WorldShape` methods in the PR #1958 note are on a handle those functions return. This run did not call them, so those names are not in the log. `effect` is a function. Section 10.3 says that function builds a screen-effect handle from a fragment shader. It does not return sun, fog, ambient, exposure, or an environment palette. An earlier run the same day had `has("gfx.shader")` false. Section 7.2 says a function whose feature the build lacks is still exported and throws `NotAvailableError` when called. This run did not call `effect`.

What that means: `longshot/gfx` on this build is overlay drawing (`world.line`, `world.box`, `world.circle`, `world.billboard`) plus `effect`. It has no lighting getters. Capture leaves sun, fog, ambient, exposure, sky, and env null. Those names are stored under `gfx` and are not copied into the lights.

Spec section 7.5 warns when one mod's script exceeds 2 ms in a frame (8 ms is a fault). On this build `actors.list` was about 0.9 ms a call, and the capture tripped that warning. The mod now calls `actors.list` on a frame that does not also write storage, and at most once every 600 frames. A sample keeps the effect list from the last refresh.

From the spec, section 12.4: grants are kept in `mods.json`, in the mod entry's `"granted"` field, and a required permission goes with the mod's switch. Section 5.4 says `mods.json` is the one list of which mods are on, in the user folder, and the sample entry has `id`, `enabled`, and `version`. From Bradyn's Longshot log on the `modding-with-editor` build, not from the spec's sample path: that file is `<game>\dev\mods.json`, and a permission can be pre-granted by listing it in the mod entry's `"granted"` array. That is the game's grant record. It is relevant to setting permissions when Forge creates a mod (issue #30). It is not a field of `mod.json`.

`lighting-survey` stats a fixed list of directory names (`Env`, `Sky`, `Fog`, `Lighting`, and the rest in `ocarina_forge/lighting.py`). Those names are a probe. A hit is a path to look at. A miss is not a claim that no such file exists under another name.

### Capture reference (Forge's own JSON, not a game format)

`python -m ocarina_forge lighting-import <game-or-json>` writes one file per map under `~/.local/share/ocarina-forge/lighting/` (`OCARINA_FORGE_LIGHTING` overrides it). A game folder is searched at `dev/user/modstore/global/local~lighting-capture.json` first. The written file name is the map key (`KokiriForest_Child.json` for Child Kokiri). The file is numeric data from the player's own session. It is not committed. `gfx`, when present, is the live module dump from the run above. It is not applied as a light.

```json
{
  "schema": 1,
  "map": "KokiriForest_Child",
  "mod": "local/lighting-capture",
  "unavailable": ["sun", "ambient", "hemisphere", "fog", "sky", "exposure", "tonemap", "env"],
  "probes": { "world.ray": false },
  "samples": []
}
```

A sample's `x`, `y`, `z` are Link's position in metres, Y up. `timeOfDay` is whatever `game.state()` returned; the unit in the modding spec is hours of the in-game day. `effects` are live actors whose type starts with `Eft` or `Effect` or contains `Fog` (`type`, `key`, `x`, `y`, `z` only). `observed.game` and `observed.player` are extra fields that build's `state()` actually had, size-capped. They are not applied as lights.

Optional light fields, when a later capture or a hand-filled file sets them:

| Field | Meaning in Forge |
| --- | --- |
| `sun.color` | `#rrggbb` (sRGB) or `[r, g, b]` linear 0..1 |
| `sun.position` | Direction toward the sun, the directional light's position relative to the origin |
| `sun.intensity` | 0..20 |
| `hemisphere.sky`, `hemisphere.ground`, `hemisphere.intensity` | Fill light |
| `ambient.color`, `ambient.intensity` | A small extra ambient term |
| `exposure` | 0.05..8, multiplied in the tonemap |
| `tonemap` | `aces` (default), `reinhard`, `cineon`, `linear`, or `none` |
| `fog` | `{kind: "linear", color, near, far}` or `{kind: "exp2", color, density}` |
| `sky.color` or `env.color` | Background colour. `env` is not a cubemap |

`aces` is three.js ACES Filmic. It is a stand-in. It is not verified as the game's curve. Invalid numbers are dropped. A null field keeps the editor default, which is also not a measurement: sun `#fff2d6` at intensity 2.6 from `[18, 36, 14]`, hemisphere sky `#d5e0d4` / ground `#4a4034` at 0.45, ambient `#8ea090` at 0.08, exposure 1.05, no fog. Output is sRGB. Shadows default off (one 512² sun shadow on the ground mesh only). AO toggles glTF occlusion maps. The terrain `bake_ao` grid stays in the ground mesh.

The capture mod and the Windows steps are in [testing-on-real-dump.md](testing-on-real-dump.md).

## BNTX

16-byte elements (ASTC, BC2, BC3, BC5, BC6, BC7) are read in the Switch 2 GOB order: bit 5 is y1 and bit 6 is x0. BC1, BC4, and RGBA8 stay on the Tegra X1 order. The measurement, the BRTI flag bytes, and `tex-audit` are in [textures.md](textures.md).

BRTI `textureLayout` is the u32 at `+0x34`. Its low 3 bits are the block-height log2, and that field is the height the decoder uses. The next u32 (`+0x38`, `sizeRange` in some notes) begins with `0xC9` on these files and is not a log2. A mip-0 size that matches a different log2 does not replace the field. `tex-audit` lists every texture where the size's log2 is not the usual height for that image. Pitch is the block row rounded up to 64 bytes. ASTC 8×5 is 8 pixels wide and 5 tall, 6×6 is square, and 10×5 is 10 wide; the block row is padded up to the GOB height (`block_height * 8` block-rows), not to the ASTC block's pixel height. The surface is that pitch times those padded rows.

ASTC format ids `0x37`, `0x38`, `0x39`, and `0x3A` (UNORM `xx01` and sRGB `xx06`) are 10×8, 10×10, 12×10, and 12×12. BC1 blocks with color0 ≤ color1 use index 3 as alpha 0 when the whole block is index 3. A block that also uses another index stores the endpoint average there, opaque, which is the castle stone rather than a black dot. An ASTC block that is all zeros on a trailing GOB row (the height in blocks is not a multiple of 8) is filled from the block above it. A compressed block that is all zeros in the last partial GOB (the width or height in blocks is not a multiple of a GOB) is filled from the last full GOB block. A 455-wide BC1 image is 114 blocks, pitched to 120, so blocks 112 and 113 are that edge. `texture_layer` reads one array slice: the count is BRTI+0x30, the image byte size is BRTI+0x50, and layer N is mip 0 at `image_offset + N * (image_size / count)`. The retail `MaterialAlb` array is ASTC 8×8, 1365×1365, 90 layers, 657920 bytes each, mip 0 of 484352 bytes. That whole stack is not decoded as one image.

## Longshot mod permissions

The table below is the Longshot `Permission` type at upstream commit `563ca4e5` (`563ca4e55b1306dd93ad57377020c463dd524c7f` on `mod-api`). The source files are `assets/modding/docs/permissions.md` (generated from the API schema) and `assets/modding/packages/api/longshot.d.ts`. Forge keeps the same rows in `ocarina_forge/permissions.py`. `GET /api/permissions` serves them to the editor. Refresh that module when the upstream file changes.

A mod's `mod.json` has two lists. `permissions` is what the game must grant before the mod can be enabled. `optionalPermissions` is what the mod may ask for later with `longshot/permissions` `request()`. A name in `permissions` is not also listed in `optionalPermissions`. Unknown names and duplicates are refused. `mods.inspect`, `mods.control`, and `ipc` are system-mod only. A player mod (`local/...`) that names them is refused.

`net` entries are `"wss://host[:port]"`, `"https://host[:port]"`, or `"*"`. Each one is the permission `net:<origin>` (`net:*` when the entry is `*`). That permission is required unless it is in `optionalPermissions`. Hosts are lower case. `:443` and the same host without a port are one origin, and the game grants the string as written. The two lists together name at most 64 origins.

`content` is read only when `runtime` is set, and only when `permissions` includes `content.add`, or `content.patch` when that module is typed patches and nothing else. Putting either name only in `optionalPermissions` does not satisfy this.

`multiplayer` cannot be `"local"` when `state.write`, `items.grant`, `content.add`, `actors.spawn`, or `actors.remove` is in either list. Those change the shared game, so the manifest says `"all"` or `"host"`.

`romfs.read` and `memory.read` cannot be held, in either list, together with a way off this PC: a `net` origin, `net:*`, `net.lan`, `session`, `ipc`, or `discord.status`.

These calls need no permission: `longshot` `has()`, `longshot/log`, `longshot/settings` `get` / `set` / `changed`, `longshot/permissions` `has` / `request` / `changed`, `longshot/dev`, `longshot/time`, `longshot/files`, `longshot/game` `frame.on`, `longshot/lifecycle` `stopping`, and every handle's `release()`. `longshot/mods` needs nothing except `inspect` / `logs` (`mods.inspect`) and `restart` / `setEnabled` / `reload` (`mods.control`).

| Permission | Player is told | Risk | Needed by |
| --- | --- | --- | --- |
| `state.read` | Can see what happens in your game | low | `longshot/game` state, after, flags.get, flags.has, and the update, mapLoading, mapLoaded, saveWriting, flagChanged, paused events; `longshot/player` state; `longshot/camera` state; `longshot/world` ray, destinations, states, state |
| `state.write` | Can change your game and what gets saved | medium | `longshot/game` flags.set, textShown; `longshot/player` set, setPosition, setAge; `longshot/rules` remove and the rewards, skips, actorLife, prices, heals, falls, rupees, bottles, damageTaken, damageDealt adds; `longshot/world` travel, setState |
| `camera.write` | Can move the camera, pause the game and hide its HUD | low | `longshot/game` pause; `longshot/camera` override, follow, and CameraOverride methods |
| `storage` | Can keep its own data on this PC | low | `longshot/storage` (any call): save.slot, flags, saves, Store global, profile, save, save.loaded |
| `save.flags` | Can change what your game saves and how | high | A content module's saved flags. No script call asks for this yet |
| `actors.read` | Can see the characters and enemies around you | low | `longshot/actors` list, info, alive, attached, and the removed, spawned, died, damaged, lifted, thrown, dropped events; `longshot/behaviour` (any call) |
| `actors.write` | Can change the characters, enemies and things around you: their health, where they are, which way they face, how they move, whether they are seen, what they hold on to and what their behaviour is told | high | `longshot/actors` damage, setLife, setPosition, setRotation, setVelocity, setVisible, light, attach, detach, answer |
| `actors.spawn` | Can make characters, enemies and things appear in your game, and take away the ones it made | high | `longshot/actors` spawn, remove |
| `actors.remove` | Can take away characters, enemies and things that are in your game | high | `longshot/actors` despawn |
| `items.read` | Can see the items you find and the places you take them from | low | `longshot/items` held, checked, granted |
| `items.grant` | Can give you items, and keeps a list of what it gave | medium | `longshot/items` grant, pending, remove |
| `input.read` | Can see the buttons you press on your controller | low | `longshot/input` action, pad.state, pointer.capture |
| `input.write` | Can press buttons for you, and change what your controller does | medium | `longshot/input` press, rumble, pad.intercept |
| `content.add` | Can add new items, characters and places to your game | medium | `longshot/content` item, actor, model, modelOf, place, change, changeActor, text, flag, tree, messages, sound |
| `content.read` | Can read the names and data of the game's actors and texts | low | `longshot/content` read.row, read.text, read.actorName, read.mapName, read.component, read.placements |
| `content.patch` | Can change the game's data in checked ways | low | `longshot/content` patch |
| `audio` | Can play its own sounds | low | `longshot/audio` load, play, playAt, playOn, and Sound and Voice handles |
| `audio.game` | Can change how loud the game's own music, sounds and voices are | low | `longshot/audio` setGroupVolume, duck |
| `ui.hud` | Can draw on your screen over the game | low | `longshot/ui` Hud and World |
| `ui.window` | Can open its own windows | low | `longshot/ui` Window; `longshot/capture` thumbnail, load, saved, remove |
| `ui.settings` | Can show its own page in the Mods tab | low | `longshot/ui` SettingsPage and DebugPage |
| `gfx.world` | Can draw its own shapes and labels in the game's world | low | `longshot/gfx` model, world.line, world.box, world.circle, world.billboard, world.model, and WorldShape methods |
| `gfx.shader` | Can run its own graphics effects on your GPU | medium | `longshot/gfx` effect, and Effect.set, setEnabled |
| `gfx.textures` | Can change how the game looks with its own pictures | low | A textures folder. No script call asks for this |
| `net:<origin>` | Can talk to that origin | medium | `longshot/net` connect and fetch. One permission per `net` entry |
| `net:*` | Can talk to any server on the internet | high | `longshot/net` when `net` is `*` |
| `net.lan` | Can talk to devices on your home network | high | `longshot/net` to home-network addresses |
| `romfs.read` | Can read the game's own files on this PC (never with the network) | high | `longshot/romfs` read, size. Never together with a way off this PC |
| `romfs.provide` | Can make game files while the game runs | high | No script call asks for this yet |
| `memory.read` | Can read all of the running game's memory. Full access to the running game: only for mods you trust | unsafe | `longshot/memory` read, regions. Never together with a way off this PC |
| `memory.write` | Can change the running game's memory directly, which can run any code on your PC, as you, with full access to your files: only for mods whose source you trust | unsafe | `longshot/memory` write |
| `hooks.unsafe` | Hooks the game's own code where Longshot never named a place. Full access to the running game: only for mods you trust | unsafe | `longshot/hooks` listen, sites, setValue |
| `session` | Can send messages to the players in your room | medium | No script call asks for this yet |
| `discord.status` | Can change your Discord status, which your Discord friends see | medium | No script call asks for this |
| `exefs` | Changes the game's own code. Full access to the running game: only for mods you trust | unsafe | The mod's own exefs patches. No script call asks for this |
| `mods.inspect` | Can see how your other mods run: their time, memory, faults and log | medium, system mods only | `longshot/mods` inspect, logs |
| `mods.control` | Can restart your other mods and switch them on and off | high, system mods only | `longshot/mods` restart, setEnabled, reload |
| `ipc` | Can talk to other programs on this PC through the local sockets or pipes it names | unsafe, system mods only | `longshot/ipc` (any call) |

The editor's **Mod permissions** panel (the header, the project dialog, the build dialog, and the search command of the same name) is this table. Each row is off, required, or optional. System-only rows stay disabled for a player mod. The network field is the `net` list. Errors from the rules above are shown before the file is written. Saving writes `permissions`, `optionalPermissions`, `net`, and `multiplayer` (only when it is not `local`) into the project's `manifest.yaml`. Export copies those fields into `mod.json`. A patch-only project that never set them keeps an imported manifest's lists, and an empty saved list clears them.

The panel also scans the mod's `entry` and `content` scripts and the static relative imports they reach. A static `longshot/...` import's calls are mapped with the table above. A call with no matching row is a hint, not a required permission. An imported module with no mapped call is a hint naming the permissions that module's calls use. `import()` is flagged. Spec 7.2: dynamic import is not supported, and a mod's modules are its static imports. A missing permission has an Add button that puts it in `permissions`.

Player grants are not `mod.json`. Spec 12.4 keeps them on the mod entry's `"granted"` array in the game's mod list. A missing grant raises `PermissionError` and the message names `mods.json`. The list sits beside the settings file: `mods.json` next to `longshot_options.ini`, or `user/mods.json`. A profile file is `profile.json` and its switch is `"on"`, not `"enabled"`. On the modding-with-editor build used for local testing, `longshot mod allow` writes `<game>/dev/mods.json`. Forge's **Pre-grant in mods.json** writes the declared permissions (required and optional, plus each `net` origin) into that entry, only for the folder named in the panel. It copies the existing file to `<name>.bak` first. It does not create or edit a folder that has no `mods/`, `dev/`, or `longshot_options.ini`, and it does not follow a link. Changing `mod.json` permissions may need the mod turned off and on again, or the game restarted, before the new manifest is read. `longshot mod restart` starts the runtime again and does not re-read `mod.json`.

The lighting-capture mod's manifest is checked against this table when it is installed. Its permissions stay `state.read`, `actors.read`, `storage`, and `gfx.world`. The first three are the calls in `capture.js`. `gfx.world` is the permission the spec attaches to `longshot/gfx`, which the gfx-probe entry imports and does not call.
