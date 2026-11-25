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

Turning textures on fetches that manifest and assigns the maps onto the materials already on the mesh. It does not request `model.glb` again and it does not rebuild the mesh. Turning textures off disposes those GPU and CPU copies and restores the plain material. Turning models on while textures are on fetches the geometry file once and the manifest once. The geometry cache key is `models-56`.

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
| `+0x14` | float, world height of a 0..1 sample |
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

The height block is 257×257 float32 samples, row-major, row along z and column along x, edge to edge. A finite non-zero sample is ground. The retail range is about 0..0.18, but an exact 0 left after the dashed-column repair is treated as uncovered (a hole), not a height at the terrain origin. Those leftover zeros showed up as worldY=0 under hills the game actually walks. Only a non-finite sample (NaN or infinity, including those uncovered zeros) is a hole. The header that is parsed has no hole bitmap, and an empty `.mate` texel is a missing material, not a hole. When the block size is `257×257×4` the floats are linear. Otherwise the block is the same block-linear layout as a texture: 4 bytes per sample, 16 GOBs per block, so block-height log2 4 (`surface_bytes` is 417792). Columns 0..255 fill those sixteen GOBs. Column 256 is a single texel, and its odd rows are stored 4 bytes after the even row. Reading the ordinary GOB offset there hits padding and returns exact 0, a dashed column down to the origin. That column is read from the packed slot, and a remaining one-sample zero copies the height beside it. The value is 0..1 and the world position is `unit * scale + origin`. Y is up. Surface triangles are wound so the normal points up. The mesh is front-side, and the skirts stay.

A second scene is loaded when it exists. The name comes from a GameBancParam string whose key contains `sub` or whose value ends in `_sub`, from the ASCII `<scene>_sub` convention, or from a `<name>_sub` string inside the main TSCB. `TerrainArc/<scene>_sub.tscb` is an overlay, not a finer replacement of the walkable floor. On Kokiri its heights sit about 13–24 m above the actors. Each scene is meshed on its own: the finest tile in that scene wins, and a sub quad does not drop a main cell. A different scale is fitted onto the main scene so the overlay lands in the same place. `.hght` is read from that scene’s own directory.

The material grid is `TerrainArc/<scene>/<name>.mate` for a main tile and `TerrainArc/<scene>_sub/<name>.mate` for an overlay tile (same TARB and zstd wrapper). The other scene’s `.mate` is not a fallback. Unpacked, `+0x2C` is a relative offset to the image and `+0x30` is its size. Retail images are 835584 bytes: two block-linear surfaces of 257×257 four-byte texels, the same log2-4 layout as a heightmap (417792 bytes each). The second surface is often zeros. A texel is material A, material B, blend, extra. 255 and 127 are not layer indices. An empty or invalid texel copies the nearest written neighbour before the mesh is built, so a boundary is not magenta and is not material 0. 0x7f in a blend byte is a halfway mix of the two albedos (a guess). A blend byte other than 0 or 0x7f is that byte divided by 255. The diagnostic layer is still material A unless that raw weight is 128 or more. A tight linear grid still uses the older rule for the named layer (blend at or above 128 selects the second index) and stores the byte as a weight. A byte or u16 grid is accepted when the values can be MaterialAlb layers (0..89). Up to 48 distinct layers are kept; rarer ones stay the earth colour. The mesh does not interpolate layer ids across a triangle: each quad has one pair, and only the weight is interpolated. Those layers are packed into one atlas (16 most-used, plus an earth cell) so the field is one draw. `.hght` `+0x1C` is used only when no `.mate` decodes. If neither source yields a layer, the tile is a flat earth colour. The view samples that atlas with triplanar world-space UVs, blended by the surface normal, repeating every 8 metres. That repeat is still a guess.

`<name>.<child|adult>.bake_ao.extm` (the variant matching `Child` or `Adult` in the banc path) uses that same header. The image is a 1-byte or 4-byte block-linear grid; format byte 0x06 prefers 4 bytes, and the surface size decides. A 4-byte texel uses the first byte when the others are 0 or 0x7f. `<name>.custom.extm` is not decoded.

A coarse cell is dropped only when its centre lies inside a quad a finer tile of the same scene actually emitted. A sub quad does not remove the main floor under it. A finer height that never became a triangle leaves the coarse ground in place. A tile whose edge is 24 m or less stays on step 8, and one of 80 m or less stays on step 16, so a small tile does not collapse to a 5×5 sheet. Every remaining tile that meets the placed actors, expanded by 200 m, is meshed. There is no fixed tile cap. Samples are about 2 m apart within 70 m of the actor centre (3 m when more than 24 tiles sit in that radius), about 5 m out to 180 m, and about 10 m in the rest of the margin. The step is 8, 16, 32, or 64, never 4, on that path. Each tile’s outer edge grows a vertical skirt (at least 4 m, or 2% of the tile) so two LOD steps meet without a crack. The ground is front-side. **Sub terrain** starts off and shows a wireframe reference for the overlay, which on Kokiri sits above the actors. Actor LOD, the small-prop distance cull, and the InnerMesh / bake / fade / water-volume filters do not apply to this mesh.

For a Child or Adult banc, Forge tries `<name>.<child|adult>.mate` in the tile's terrain directory before `<name>.mate`. The generic grid also covers unreadable age-specific grids. In the surveyed build, Hyrule Field has 186 adult material tiles. The material choice keeps the same height samples.

The Lake Hylia fishing pond fit uses the placed `Obj_LakeHrl_WaterPlane_A_07` mesh to bound the underwater area. Where the coarse ground intersects that water sheet, the fit limits ground height to 0.12 m below the local surface. The fit runs in the existing terrain preparation worker. In the viewport, bass and loach spawn positions above the pond mesh have a separate submerged display pose. The saved placement and Inspector coordinates still identify the spawn point.

## BNTX

16-byte elements (ASTC, BC2, BC3, BC5, BC6, BC7) are read in the Switch 2 GOB order: bit 5 is y1 and bit 6 is x0. BC1, BC4, and RGBA8 stay on the Tegra X1 order. The measurement, the BRTI flag bytes, and `tex-audit` are in [textures.md](textures.md).

BRTI `textureLayout` is the u32 at `+0x34`. Its low 3 bits are the block-height log2, and that field is the height the decoder uses. The next u32 (`+0x38`, `sizeRange` in some notes) begins with `0xC9` on these files and is not a log2. A mip-0 size that matches a different log2 does not replace the field. `tex-audit` lists every texture where the size's log2 is not the usual height for that image. Pitch is the block row rounded up to 64 bytes. ASTC 8×5 is 8 pixels wide and 5 tall, 6×6 is square, and 10×5 is 10 wide; the block row is padded up to the GOB height (`block_height * 8` block-rows), not to the ASTC block's pixel height. The surface is that pitch times those padded rows.

ASTC format ids `0x37`, `0x38`, `0x39`, and `0x3A` (UNORM `xx01` and sRGB `xx06`) are 10×8, 10×10, 12×10, and 12×12. BC1 blocks with color0 ≤ color1 use index 3 as alpha 0 when the whole block is index 3. A block that also uses another index stores the endpoint average there, opaque, which is the castle stone rather than a black dot. An ASTC block that is all zeros on a trailing GOB row (the height in blocks is not a multiple of 8) is filled from the block above it. A compressed block that is all zeros in the last partial GOB (the width or height in blocks is not a multiple of a GOB) is filled from the last full GOB block. A 455-wide BC1 image is 114 blocks, pitched to 120, so blocks 112 and 113 are that edge. `texture_layer` reads one array slice: the count is BRTI+0x30, the image byte size is BRTI+0x50, and layer N is mip 0 at `image_offset + N * (image_size / count)`. The retail `MaterialAlb` array is ASTC 8×8, 1365×1365, 90 layers, 657920 bytes each, mip 0 of 484352 bytes. That whole stack is not decoded as one image.
