# Formats this version reads and writes

The container rules below were reconciled with After the Ocarina's `docs/game-formats.md` and `docs/blockers.md` (branch `m8-navi-rumors`, commit `ad60e27`). Those notes were verified in game. An earlier Bancwright write-up used the public MIT notes instead, and its resource-size formula was wrong. The checklist is [testing-on-real-dump.md](testing-on-real-dump.md). The equivalence command is [equivalence.md](equivalence.md).

`bancwright/formats/LICENSE-oot-remake-modding.txt` is that MIT license. The code here is a fresh implementation of those layouts, not a copy of the third-party tree, so the editor’s library can stand on its own.

Nothing in this repository is extracted from a game dump.

## Where files live

Dump root (the folder Bancwright stores in config):

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

Version 7, little endian, magic `YB`. Bancwright reads and writes hash maps (`0x20` u32 keys, `0x21` u64 keys) as well as the usual arrays, dicts, strings, and numeric types. Identical containers are shared. Bool is not stored as an int.

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

Everything else (`Dynamic`, `Links`, `Presence`, `Bakeable`, `Phive`, …) is a typed parameter. New actors get `Hash` and `Phive.Placement.ID` set to the same new u64.

Category colors use the pack-name prefix: `Npc_` / `Shop_`, `Enemy_`, `Animal_`, `Eft`, `Land_`, `Area_` / `Area`, `Obj_` / `FldObj_` / `Item_` / `Weapon_` / `Dm_`, and system names (`StartPos`, `Player`, `Sound`, `AirWall`, `Ground`, anything with `Anchor` or ending in `Actor`).

Actor meshes are Switch BFRES plus embedded BNTX. The reader, the cache, and what still falls back to a box are in [models.md](models.md).

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

Wexos, BfresLibrary, and Switch Toolbox do not document that bit or a meshlet layout inside FSHP. `.bfres.mc` is a separate stream (`MCPK` / `FMSH`) and is still refused. There is not enough public structure to turn those bytes into indices, so this reader does not guess a decoder. The shape’s bounding record (center and extent, six floats at the pointer in FSHP `+0x38`) becomes the actor’s box, and the reason is `geometry format not decoded (FSHP flag 0x10)`. A file that also contains ordinary shapes still draws those. These shapes are not the missing field. The field is the TerrainArc heightmap below. `scripts/check_terrain.py` lists the ground-like actors that still have a 0x10 shape.

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

`TerrainArc/<scene>/<name>.hght` starts with `TARB`. The u16 at file offset 10 is the header size. The bytes after that header are a zstd frame (dictionary id from the frame, usually the shared zs dictionary). Unpacked, offsets are relative the same way:

| Offset | Field |
| --- | --- |
| `+0x2C` | relative u32 to the height block |
| `+0x30` | u32 size of that block |

The height block is 257×257 float32 samples, row-major, row along z and column along x, edge to edge. A sample of exactly 0 is a hole. When the block size is `257×257×4` the floats are linear. Otherwise the block is the same block-linear layout as a texture: 4 bytes per sample, 16 GOBs per block, so block-height log2 4 (`surface_bytes` is 417792). The value is 0..1 and the world position is `unit * scale + origin`. Y is up.

A second scene is loaded when it exists. The name comes from a GameBancParam string whose key contains `sub` or whose value ends in `_sub`, from the ASCII `<scene>_sub` convention, or from a `<name>_sub` string inside the main TSCB. `TerrainArc/<scene>_sub.tscb` is merged by tile name. A smaller edge replaces a larger one, and a different scale is fitted onto the main scene. Each tile’s `.hght` is taken from `TerrainArc/<scene>_sub/` or `TerrainArc/<scene>/`, whichever has the file. The fine folder is tried first.

The material grid is `TerrainArc/<scene or scene_sub>/<name>.mate` (same TARB and zstd wrapper). The file beside the heightmap is tried first, then the other folder. Unpacked, `+0x2C` is a relative offset to the image and `+0x30` is its size. Retail images are 835584 bytes: two block-linear surfaces of 257×257 four-byte texels, the same log2-4 layout as a heightmap (417792 bytes each). The second surface is often zeros. A texel is material A, material B, blend, extra. 255 and 127 are not layer indices. 0x7f in a blend byte is neutral, so material A is drawn and material B is only listed in the histogram. A blend byte other than 0 or 0x7f is treated as a 0..255 weight, and 128 or more selects material B. That reading is a guess. A tight linear grid still uses the older rule (blend at or above 128 selects the second index). A byte or u16 grid is accepted when the values can be MaterialAlb layers (0..89). Up to 48 distinct layers are bound; rarer ones stay the earth colour. `.hght` `+0x1C` is used only when no `.mate` decodes. If neither source yields a layer, the tile is a flat earth colour. The UV repeat, 8 metres, is still a guess.

`<name>.<child|adult>.bake_ao.extm` (the variant matching `Child` or `Adult` in the banc path) uses that same header. The image is a 1-byte or 4-byte block-linear grid; format byte 0x06 prefers 4 bytes, and the surface size decides. A 4-byte texel uses the first byte when the others are 0 or 0x7f. `<name>.custom.extm` is not decoded.

A coarser tile does not cover a cell whose centre lies in a finer tile. Every tile that meets the placed actors, expanded by 200 m, is meshed. There is no fixed tile cap. Samples are step 4 (65×65) within 80 m of the actor centre, step 8 out to 180 m, and step 16 in the rest of the margin. When more than 40 tiles sit inside that 80 m, the nearby step drops to 8 so the village stays dense without a huge triangle count. Actor LOD, the small-prop distance cull, and the InnerMesh / bake / fade / water-volume filters do not apply to this mesh.

## BNTX

BRTI layout byte 0 (offset `+0x34`), low 3 bits, is the block-height log2. Byte 4 is `0xC9` on these files and is not that field. When the stored height does not match the mip-0 size in the offset table, the size wins. Pitch is the block row rounded up to 64 bytes; the surface is that pitch times the block rows rounded up to `block_height * 8`.

ASTC format ids `0x37`, `0x38`, `0x39`, and `0x3A` (UNORM `xx01` and sRGB `xx06`) are 10×8, 10×10, 12×10, and 12×12. BC1 blocks with color0 ≤ color1 use index 3 as alpha 0. An ASTC block that is all zeros on a trailing GOB row (the height in blocks is not a multiple of 8) is filled from the block above it. `texture_layer` reads one array slice: the count is BRTI+0x30, the image byte size is BRTI+0x50, and layer N is mip 0 at `image_offset + N * (image_size / count)`. The retail `MaterialAlb` array is ASTC 8×8, 1365×1365, 90 layers, 657920 bytes each, mip 0 of 484352 bytes. That whole stack is not decoded as one image.
