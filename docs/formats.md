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

`textures` is not an actor layer and it is not a second glb. The editor always fetches one geometry file, `/api/models/<Gyaml>/model.glb?flat=1`. That file has the mesh and a plain material. It does not embed images, and its cache key does not include texture bytes. Images are `GET /api/models/<Gyaml>/textures`. The manifest is keyed by `partName:materialIndex`, which is the glTF node name with the `lodN:` prefix removed. Three.js strips `:` from `Object3D.name`, so `lod0:Body:0` becomes `lod0Body0` and does not match that key. The original node name stays on `userData.name`. The client looks the slot up with that name, not the sanitized object name. Albedo is sRGB. Normal, ORM, and emissive maps are linear. A decoded image is assigned only when it is a three.js texture. Each image is a content-addressed file in the shared texture store (`shared-v1`, `/api/model-textures/<sha256>.ktx2`, or `.png` / `.jpg`). The manifest itself is cached under `texman/` (`texman-2` plus the converter prefix and the bfres bytes). A second request reads that file and does not decode the BNTX again.

`Load textures` (on unless the preset is Layout or Logic, or the URL says `textures=0`) streams those manifests after the mesh, a few at a time, nearest visible actors first. One runs while model loads are still in flight, and four run once geometry is idle. When the filter is off, a banner says so and points at Load textures in the Actors panel. The stage-bar Textures box only shows or hides the images. That swap writes the resident maps back onto the same material the batches already use. It does not fetch, decode, or dispose. The first check, while Load textures is off, turns the filter on. Turning Load textures off stops the queue and leaves images that already arrived in memory. The status label shows `textures 120/450` while that queue is open. Turning models on while textures are loaded fetches the geometry file once and the manifest once. The geometry cache key is `models-68`.

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

The class `.bphsh` is not the collision the game uses for a baked static. That copy is the per-tile compound below. `read_mesh_shape` still does not open those files.

## Static compound bakes

Longshot's modding API was checked before this layout was written down. Spec revision 4 (2026-11-19) and the schema that generates `longshot.d.ts` (commit `563ca4e55b1306dd93ad57377020c463dd524c7f` on `mod-api`) have no `has()` name that writes Phive, a `.bphsc`, or a static-compound transform. `world.ray` and `world.rayAll` are collision queries against Havok `hknpWorld::castRay`. On the build already recorded in this file, both `has()` calls were false. A ray does not edit a bake. `actors.setPosition` and `actors.setRotation` write the live actor. They do not write the banc and they do not write `StaticCompoundBody`. A placement patch can set `Translate`, `Rotate`, `Scale`, and `SRTHash`. That is a banc edit.

Hyblitze traced the actor-pack path and the physics ray. Zirem wrote the spawn request. That ray is the query `world.ray` would call. It is not this file. Their notes are from Heros Legacy (`heros-legacy/v3.0.0` at `6ce46cc7`). Their code is not copied.

The byte layout below is the one published by reading the files, in oot-texture-tool `phive_compound.py` (GPL-3.0, ToxicToucan). This repo reimplements that description. It does not copy the program. No retail `.bphsc` was opened in this repo. The `SRTHash` offset and the no-body marker were confirmed in game on Kokiri. A file that does not match fails closed, and the export stops.

The file starts with `Phive`. A mesh file uses `Phive\0`. At `+0x10` there are five little-endian u32s: actor-table start, actor-table end, a word that reader does not use, body-table start, body-table end.

An actor record is `0x20` bytes. That is the per-actor record issue [#10](https://github.com/glinesbdev/ocarina-forge/issues/10) calls section 1.

| Offset | Field |
| --- | --- |
| `+0x00` | u32 first body index |
| `+0x04` | u32 last body index, inclusive |
| `+0x08` | u64 layout `Hash` |
| `+0x10` | u32 `SRTHash`, the start of the 16-byte tail |
| `+0x14` | 12 bytes still unread |

`first == last == 0xFFFFFFFF` means the row has no body. `Land_Kokiri_Zenmai_A_02` (a fern) is that case. A hash in the actor table is not the same thing as baked collision. The patcher still does not write the tail. `bake` leaves the banc `SRTHash` alone, so the row's `SRTHash` has to stay the value the lookup uses.

Breath of the Wild's `ksys::phys::ActorInfo` is a different struct: 16 bytes, `HashId`, `SRTHash`, shape start, shape end, and `getActorIdx` matches hash and `SRTHash`. That SDK is not this file. Those offsets are not used.

A body record is `0x14` bytes. The u16 at `+0x0A` is the compound instance index. The record does not name which tagfile. One compound with that index and a non-zero shape pointer is the instance. Several compounds, and exactly one of those instances within `0.01` of the actor position, is also accepted. Anything else fails closed. A hash that appears twice fails closed.

One or more big-endian Havok `TAG0` tagfiles follow. The patcher uses a tagfile whose `SDKV` is `20250200` and whose second item record is `hknpCompoundShape`. Another SDK is skipped. A gap of more than 7 bytes inside a chunk fails closed.

Inside that compound, `+0x38` is a u32 offset from that field to the instance array, and `+0x40` is the count. Each instance is `0x40` bytes. Positions are world space.

| Offset | Field |
| --- | --- |
| `+0x00` | quaternion, xyzw, four floats |
| `+0x10` | position, three floats |
| `+0x1C` | padding, not written |
| `+0x20` | scale, three floats |
| `+0x2C` | padding, not written |
| `+0x30` | u64 shape offset from this field |

`patch_compound_transform` writes the quaternion, the position, and the scale. If the instance already matches the actor pose (position within `1e-3`, scale within `1e-4`, quaternion dot above `0.9999`), the new pose is written directly. Otherwise the actor's delta is applied, `new * inverse(old) * instance`, so an offset piece follows. Rotate is extrinsic XYZ, the same composition as a placement `Rotate`. A non-positive scale fails closed. `Ry(+90°)` takes local `+X` to world `-Z`. The shape pointer and the shape bytes stay.

The compound broadphase is an `hkcdSimdTreeNamespace::Node` array. On `KokiriForest_Child_X1_Z2` it is 222 nodes of 128 bytes, immediately after the 555-instance array. The ITEM view with that type name is the one that starts at the end of the instance array (a gap of at most 16 bytes still binds). Per-mesh `hknpAabb8TreeNode` arrays are a different type and are not this tree.

One node is four children, structure of arrays, then 32 bytes that a hex dump shows as eight u32s:

| Offset | Field |
| --- | --- |
| `+0x00` | `lx`, four floats, one per child |
| `+0x10` | `hx`, four floats |
| `+0x20` | `ly`, four floats |
| `+0x30` | `hy`, four floats |
| `+0x40` | `lz`, four floats |
| `+0x50` | `hz`, four floats |
| `+0x60` | u32 `data[4]`. On a leaf node this is the instance index. On an internal node it is the child index, or that index plus one when `m_parent` says so |
| `+0x70` | four filter-mask bytes, then a flags byte, depth, axis, and a rebalance count. `NODE_IS_LEAF` is bit 0 of the flags byte. `NODE_IS_FREE` is bit 1 |
| `+0x78` | u32 `m_parent` |
| `+0x7C` | u32 `m_numLeaves` |

The same tagfile holds other `hkcdSimdTreeNamespace::Node` arrays of two to four nodes, one per shape. Their child indices start over at 0. The refit uses only the array that begins at the compound's instance array, and a count that would run into the next array stops at that boundary.

An empty slot is the inverted box `+FLT_MAX` / `-FLT_MAX` (`min > max`). An internal lane whose `data` word is `0` or `0xFFFFFFFF` is also empty, even when the box is finite. Those sentinels are not pointers at node 0. A leaf lane uses `0xFFFFFFFF` for empty and any other word as the instance index, including 0. When every word at `+0x70` is 0 or 1, that word is a per-lane leaf mask instead of the node tail, and a non-zero word marks the lane as a leaf. The patcher does not write these 32 bytes, so the payload, the flags, and the child index stay. Two internal lanes that name the same real child still fail the export.

Moving the instance without this tree leaves the leaf on the old world box. A query only reaches the moved shape where that stale box still overlaps it. On the house facade that overlap was about 0.49 m, so only a strip collided. After the instance write, the patcher refits. The leaf is the slot whose payload is the instance index, or, when no payload matches, the unique tightest leaf whose box contains the old instance position. The new box is the min and max of the old leaf's eight corners through `new_instance * inverse(old_instance)`. On the cooked house leaf that matched to about 0.2 mm. Every parent slot that names that node is then set to the union of its child node's four slots, skipping empty ones, and that walk continues to the root. Several moved instances in one tile are applied to their leaves first, then the parents, so a parent sees every sibling that this call already moved. A second call, from another banc that shares the tile, sees those updated boxes and refits only its own instances.

A tagfile with no `hkcdSimdTreeNamespace::Node` array is an instance-only edit. A tagfile that has the array, and a moved instance with no leaf, fails the export. The same is true when the array is present and does not start at the instance array. The file length stays the same. Every changed byte has to be an instance quaternion, position, or scale, or one of those six SoA floats.

The scene directory is not a fixed grid. Kokiri's 4×4 per age and Hyrule Field's 256 tiles per age are the issue's counts. They were not re-counted. Under `Phive/StaticCompoundBody/`, Forge looks for the banc stem (`KokiriForest_Child_Static`), that stem without `_Static` or `_Dynamic` (`KokiriForest_Child`), and the area folder (`KokiriForest`). Every directory that exists is searched. Only `*.bphsc` and `*.bphsc.zs` are read. Symlinks are skipped. Child and Adult are separate bancs. Only the banc being edited is patched.

### What `collisionFollow` writes

The manifest key is `collisionFollow` (`collision_follow` is accepted). The CLI flag is `--collision-follow`. `POST /api/export` and `POST /api/mod-export` take `collisionFollow` in the body. A typo is an error. The default is `off`.

`srt` is variant B. A pose move keeps `Hash` and `Phive.Placement.ID`. Export mints a new `SRTHash`: the CRC-32 of `ocarina-forge-collision-follow-b:` plus the 16-digit actor hash, skipping 0, bumped until no other actor in the banc has it. Save Longshot mod adds a placements `set` of `SRTHash` (an `add` only when the key was missing). Build mod writes that same u32 into the banc. No `.bphsh` and no `.bphsc` is shipped. The shape is unchanged. Re-minting the hash is a new value in the data structure, not a new shape. It does not move the baked row.

`bake` leaves `SRTHash` alone, so a lookup by hash and `SRTHash` can still hit the same row. Each tile that contains the actor is copied, the instance transform is moved, and that compound's SIMD tree is refit. Static and Dynamic bancs that share a tile are applied to that tile together. One starts from the dump, and the next does not overwrite it with a fresh copy. A tile that does not mention the actor is left out. A tile that mentions the actor and cannot be patched fails the export, so a half-updated world is not shipped. The unpacked length stays the same. The bytes differ, so the size-table entry still grows by 1024 (`align8(old + 1024)`), and the whole table is shipped. A missing row is an error. It is not invented. The frame is recompressed with the dictionary id from the source frame. `.bphsc.zs` is not in the suffix table, so the id is not assumed to be 1. A scene with no `StaticCompoundBody` directory warns and does not fail. An actor that is in no tile is logged. The class `.bphsh` is not rewritten in either mode.

### Kokiri sign, in game

`Obj_Kokiri_Signboard_A_01` (`KokiriForest_013`, `0x4a13f8880eb01a43`) has both of these:

- An actor-side ControllerSet. It follows `Translate`. That is why `off` still has collision at the new spot.
- A baked compound row in `KokiriForest_Child_X1_Z2.bphsc`.

`off` moves the mesh and the ControllerSet. The vanilla bake row stays where it was, and that is an invisible box at the old spot. `bake` moves that row. The old spot is clear. Bake mode is required for this sign. The sign still felt solid at the new pose because the ControllerSet follows `Translate`. That did not show that the broadphase had moved.

`Land_Kokiri_House_Outside_B_02` (`0xd471946eaeb8e44f`) is only the front facade, and its collision is bake-only. In `KokiriForest_Child_X1_Z2` the actor is one compound row, `first = last = 322`, instance 290. The compound's node array is 222 nodes at file offset `0x243f0`, immediately after the 555 instances. Instance 290's leaf is node 45, slot 1. Its world box was `[-12.327, 111.089, 10.580]`–`[-7.840, 115.976, 13.766]`, which overlaps the moved facade by about 0.49 m. That was the strip that collided. A refit that treated every `0` in `m_data` as a child index stopped the export with "SIMD tree node has two parents", because empty lanes and the other shapes' trees repeat that sentinel. With those lanes ignored, the same +4 X move writes the leaf `[-8.3268, 111.0895, 10.5798]`–`[-3.8402, 115.9762, 13.7664]`. The shape bytes were not rewritten.

`examples/collision-follow-test` generates `scenes/` from the dump on each run and deletes the previous scene yaml first, so an earlier `--id` is not exported again. That folder is gitignored. Do not commit it. The steps, including the house-facade command, are in that example's README.

## What Snap can stand on

Longshot's modding API, on the `main` tree checked for this note, does not export a collision ray. `docs/dev` covers the host, the control bridge, and mods. It does not name Phive, a shape query, or a ground sample. Forge cannot ask a running game for the surface under an actor. The files below are the collision data this repo can read.

| Source | What it is | Snap |
| --- | --- | --- |
| Heightmap plus `Land_*` floors | The walkable pack (`OFGS`) and `GET /api/ground` | Used when that sample is finite. A height of exactly 0 from a height tile is the sentinel in `compose_height` and is not a floor |
| Actor-local `.bphsh` | `Phive/Shape/Dcc/<Gyaml>__Physical.Nin_Ounce_NVN2.bphsh`. Triangles in actor space. The placement transform puts them in the world | Not instanced into the viewport. The drawn collision stand-in is the volume gizmo for a Gyaml containing `AirWall`, `collision`, `collider`, or `navmesh` |
| `Phive/StaticCompoundBody` | Per-scene baked compound, world space of that scene | Not parsed. Snap does not read it |
| Visible map and room meshes | The static map glb, plus each `BatchedMesh` or preview `InstancedMesh` instance | Used when the walkable sample and the collision volumes both miss. The actor being dragged, and a mesh marked as its proxy, are left out of the ray. The ray is per instance, not one merged bounds box |

A room such as Boss Rush Internal Room has no TSCB and often no `Land_*` floor. The surface is the room model. Snap raycasts that geometry from the drag's X and Z and writes only Y. The nearest hit at or under the pivot wins. A hit above the pivot counts only when it is within a couple of metres, so a ceiling does not pull the actor up. Every mesh of the actor being moved is left out of the ray: submeshes, batched instances, proxies, and a companion banc copy of the same hash. The translate plane stays at the height the handle assigned. Sampling it at the snapped height would take X and Z from the view ray and pin the actor to one point. If the ray misses, the actor keeps its current height. It does not fall to y = 0.

The same paragraph belongs in After the Ocarina `docs/game-formats.md` under the actor-local collision note. Longshot's API was checked first and has no collision query to add there.

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
| `+0xA2` | u8 texture-name count. Longshot `host/bfres_edit.c` `tex_array` reads this byte (`*count = file[material + 0xA2]`). Files written by `sample_v10_bfres` store the sampler count here unless `counts` is `longshot` |
| `+0xA3` | u8 texture count on those samples. `0` when the count lives at `+0xA2`. A `1` here with a longer array at `+0xA2` is only the first texture |

The render-state array is 31 fixed slots of string pointers, not the version-9 `gsys_*` records. The walk stops at a null pointer or a pointer that is not a BFRES string (u16 length, bytes, NUL). An empty string is a real slot and does not stop the walk. Index 10 is cull (`front` or `both`), index 11 is the alpha mode (`opaque`, `mask`, `translucent`, `custom`), and index 15 is the alpha test (`true` on a mask). Albedo is `_a0`, then `_a1` or `_a2`. A material whose samplers are only `tc` / `ta` (runtime names `MaterialAlb` / `MaterialCmb`) has no albedo slot. `Mt_TerraMaterial_NNN` is handled apart from that rule: layer NNN of the `MaterialAlb` array is the colour. Version 10 has no render-info dictionary at the version-9 offsets, because `+0x18` and `+0x20` are the texture list. Other qwords in the header are checked for a BFRES string dictionary. When that dictionary is missing, `Mt_Sand_Kokiri_Cliff` uses layer 16 and the log says the index is a guess.

### Sampler usage and where the texture file lives

This was checked against Longshot before the matcher changed. The sources are `editor-mod` commit `73641e623ea7ec33826e954ea1be055f703fd30f`:

- `modapi/packages/api/longshot.d.ts` exports `has(feature: Feature): boolean`. `Feature` includes `content.model`. It does not include `gfx.textures` or any sampler-slot name. `has` returns false for an unknown name. `content.model` describes a `ContentModel` as `{ id, file, textures?: (string | ContentModelTexture)[] }`. Those textures are served as `Tex/<file name>` (a `.bntx.zs`, or a PNG made into one). The generated comment says every texture the materials name has to be the mod's or the game's. The schema hash in that file's header is `fed56c17d27826ec14733e83957372c778c12c344a51292c0052cc7b6adbeba97`.
- Modding API Spec revision 4 (2026-11-19), sections 6.3 and 6.4. Texture packs ask for permission `gfx.textures`. A script mod cannot ask for that permission yet, and it is not a `has()` feature in the type list above.
- `docs/modding/textures.md` and `docs/dev/reference/content.md`. One texture per file. Names match without regard to case. The short form is used when only one file has that name; otherwise the long form is `<file>/<name>`. `_Alb` is colour, `_Nrm` is the normal, `_Rgh` is roughness, `_AO` is ambient occlusion. Item icons are `UI/Tex/Icon/<name>.bntx.zs`.
- `nx2/texture_index.py` (`bntx_files`) walks every `**/*.bntx.zs` under the romfs. `host/mod_content.c` writes `romfs/Tex/<name>.bntx.zs`. `docs/dev/reference/actor-components.md` puts shared component files in `Pack/ResidentCommon.pack.zs`.

The API does not name BFRES sampler slots. `_a0` is the FMAT sampler dictionary, in the same order as the texture-name array at `+0x20`. The `+0x10` block's sampler-index list (the bytes after the attribute names, or `samplerIndices` when the count bytes at `+0x44` / `+0x45` match) says whether this variation samples that slot. `0xFF` means the sampler is not used. That number is not a texture remap. The dictionary entry stays paired with the texture at the same index.

Forge looks up the texture name the material stored, in this order:

1. An image embedded in the bfres, under that name.
2. A loose `.bntx` or `.bntx.zs` anywhere under the romfs. The match is case-insensitive, including the suffix (`.BNTX.ZS` is the same stem). When several files share a stem, `Tex/<name>.bntx.zs` wins, because that is the path the game opens. A nested `Tex/<folder>/`, `UI/Tex/`, `Effect/`, or `Banc/` file is used when `Tex/` has no file with that stem.
3. The actor pack `Pack/Actor/<Gyaml>.pack.zs` for the model being opened, if it contains a `.bntx` member with that stem.
4. Shared packs that sit directly in `Pack/`: `ResidentCommon`, then `Bootup`, then the other packs. `ZsDic` is not a texture archive.

A banc is a placement BYML. It does not contain textures. Step 2 still finds a `.bntx.zs` that was stored under `Banc/`.

`_a0`, then `_a1` and `_a2`, are the albedo slots. A normal, mask, roughness, or bake bound there is skipped. When that list is empty, a bound texture whose name is albedo, or whose slot name says albedo, diffuse, or base colour, is still tried. A sampler name that is not in that table and is not a known non-color slot (`_n0`, `s0`, `_b0`, and the rest) is still the colour: `content.model` does not publish slot names, and a character texture is often `Mt_Skin` with no `_Alb` suffix. `bake0`, `bake1`, `_b*`, and a texture named `CmnTex_Bake*` are not that fallback. They are lightmap placeholders. Runtime `tc` / `ta` and the names `MaterialAlb` / `MaterialCmb` are not that fallback either. `Mt_TerraMaterial_NNN` still reads layer NNN of `MaterialAlb`. Known non-color slots are not a fallback either. A BC4 image (`0x1D**`) is one channel and a BC5 image (`0x1E**`, including `0x1E01`) is two channels. Neither is an albedo or a blend layer, and a normal slot (`_n*`) is not one either. JPEG and PNG fallbacks of an ASTC albedo stay colour.

A hit is "via sampler" when the file stem is the name the winning slot bound. It is "via Tex fallback" when the file is the same stem plus or minus `_Alb` / `_Albedo`, or a sibling borrowed because the bound file is missing. The audit script is `scripts/audit_textures.py`. It counts those two and the flat-colour rows, and groups the flat rows by cause. The report names paths and sampler binds. It does not contain texels.

A character shape can store vertex colours and still name an albedo. The colours are a tint. They are a rock mask only on the material whose texture name is the rock. `Model/Npc_Goron_GOU_Biggoron.Npc_Goron_GOU_Biggoron.bfres.zs` has six materials: `Mt_Hair` (it also stores vertex colours), `Mt_Skin`, `Mt_Eye_Inner`, `Mt_Face`, `Mt_Rock`, and `Mt_Teeth`. Each one names its own texture. There is no retail bfres in this workspace, so those names are the ones the inspector showed, not a dump of the file's texture strings. The skin file `CmnTex_Chara_Mt_Skin_Npc_Goron_GON_001_Alb` and the rock file `CmnTex_Chara_Mt_Rock_Npc_Goron_GON_001_Alb` are different images. This checkout has no romfs, so neither file was decoded to PNG here. A reference of the character shows a smooth yellow body, with grey rock only on the back and shoulder.

Longshot's `content.model` does not list sampler slots, mask channels, or tint uniforms. `has()` includes `content.model` and does not include `gfx.textures`. What the host does publish, in `host/bfres_edit.h` and `tex_array` in `host/bfres_edit.c` on `editor-mod`, is that a version-10 FMAT names its textures with the string-pointer array at `+0x20` and the count in the byte at `+0xA2`. `docs/modding/assets.md` says those strings are the files under `Tex/` (`Mt_Body_Animal_Cucco_Alb`, and `CmnTex_...` for a shared one). Forge used to read the texture count from `+0xA3` and the sampler count from `+0xA2`. When `+0xA3` was a short count, the only name kept was the first pointer. On the eye that first pointer is the normal, so the albedo never entered the bind list. A sampler outside the `_a0` table, with a texture name that does not end in `_Alb`, was then dropped as well. The string pool still held every Goron albedo, and a token from the model name (`goron`) matched all of them, so the first file in that pool was applied to every material.

The geometry file the inspector shows is `?flat=1`. It used to clear the binds before writing the note, so every one of those six materials produced `No albedo sampler was usable and no Tex file matched` even when the name was in the FMAT. It still does not decode BNTX. It does stat the named file. The note says `found <path>` when `Tex/<name>.bntx.zs` or a pack member is on disk, and `file not found` plus the paths that were tried when it is not. A sentence that the material names no albedo is used only when the material, the rest of the file, the shader options, and the companion archives have no texture name. `GET /api/models/<Gyaml>/textures` is what decodes. That response used to search with only the gyaml, so `Npc_Goron_Biggoron_Adult` never opened `Pack/Actor/Npc_Goron_GOU.pack.zs` or `Model/Npc_Goron_GOU.Tex.bfres.zs`, and a shared `CmnTex_...` that lives in one of those archives was never painted. The request now passes the model path as well. A `Pack/Actor/` archive whose file name is contained in the texture name is opened too. The manifest keeps a material that failed: `file not found` or `decode error`. The page adds `not applied` when the mesh key misses or the image request fails, and `applied <path>` when the map is on the material.

When the shape index misses, Forge still uses the materials in the file. One material is that material. Several materials match only a shape whose name shares a token with the material. A missed index used to give every unmatched shape the first material whose image loaded. If the sampler list has no albedo, names are taken from shader-option strings, the shader-assign map, and `_Alb` strings in the file, and a name has to share a token with that material when the file has more than one material. Shader parameters that decode as floats or a texture SRT are not texture names. A float param whose name is a const colour, albedo colour, or base colour is a tint, and version 10 stores that on the material.

A rock material can still be two layers. Longshot does not describe that mix. It applies only when the albedo name or the material name contains `rock`: one albedo, a const-colour uniform (`const_color`, `albedo_color`, `base_color`, and the same family) or vertex rgb when the mask is alpha, and a `_c0` channel that swings by at least 0.4. A shader option that says `vertex`, `mask`, or `blend` does not put the skin albedo on that mix. Hair, skin, eyes, the face, and teeth keep the texture the material named. A small vertex colour is AO and is not a mask. A second albedo (`_a1`) stays on the moss path. The viewer mixes with `mix(skin, rock, smoothstep(0.35, 0.65, mask))` on WebGL and WebGPU. The details line lists the layers, for example `layers: Mt_Body shader Npc_Character/Goron_Skin; skin tint (0.95, 0.78, 0.16); rock _a0 Goron_Rock_Alb; mask _c0.a`.

`CmnTex_Chara_Mt_Skin_Npc_Goron_GON_001_Alb` is already a yellow-tan albedo (mean RGB about 164, 119, 52). `CmnTex_Chara_Mt_Rock_Npc_Goron_GON_001_Alb` is grey-brown (about 84, 74, 59). Every slot colour on that model is white, so no tint is applied. The flat geometry file (`?flat=1`) used to keep two materials: hair stayed separate because it is double-sided, and Body, Face, Eyes, Rock, and Teeth collapsed onto one material because they share a white colour and have no image. The writer deduped by image, colour, and sidedness. The page then wrote each texture onto that shared material, and the last write (the rock) painted the whole body. A flat mesh now keeps one glTF material per shape key (`Body_low:5`, `RockSmall_Body:4`). A mesh that already carries an image still shares a material with the same pixels. The page also clones a material object when one object is shared by two shape keys, so an older cached glb cannot paint them all with the last image. The geometry cache prefix is `models-67`.

Longshot `docs/modding/assets.md` says the names the materials give (`Mt_Body_Animal_Cucco_Alb`, and `CmnTex_...` for a shared texture) are the files under `Tex/`, with `_Alb` for colour. It does not describe a `.Tex.bfres` companion. Retail actors still share a texture archive. Forge also opens `Model/<prefix>.Tex.bfres` (and `_Tex`) and `Pack/Actor/<prefix>.pack.zs` for a prefix of the model project. `Npc_Goron_GOU_Biggoron` therefore searches `Model/Npc_Goron_GOU.Tex.bfres.zs` and `Pack/Actor/Npc_Goron_GOU.pack.zs`. The actor gyaml `Npc_Goron_Biggoron_Adult` does not contain that project, so the texture request has to be given the model path. Images in the companion bfres are used under the name the material stored. A loose `Tex/<name>.bntx.zs` still wins when it exists. `CmnTex_...` is that same name, not a different root.

The flat-colour sentence names the material, the sampler and texture names that were seen, and the paths that were searched. `scripts/audit_textures.py` counts a shape whose index misses. Companion `.Tex.bfres` files are searched and are not counted as models. Vertex colours are a flat cause only when no texture name was seen.

These stay a flat colour on purpose: a terrain proxy (no textures and no UVs), a shape that has vertex colours and no texture name anywhere, a runtime `tc` / `ta` material, a material whose samplers are only normals or masks, a magenta placeholder, a block format that is not a colour, a name that is in none of the places above, and a rejected water albedo, which stays the faint translucent sheet rather than a generated ripple.

The albedo slot rule is unchanged: `_a0`, then `_a1` or `_a2`. Longshot's `has()` list still has `content.model` and does not name sampler slots. A white mesh with black blotches was not reproduced here (this checkout has no `Land_LakeHrl_Tree_A_01` bfres). Two drawing bugs were in the viewer, not in the slot picker. The opaque colour node returned the texel alpha, so a low alpha on an otherwise opaque albedo punched a hole and the dark scene showed through. Opaque materials now keep alpha 1. A mask still uses the cutout node, and a blend material still uses the texel alpha. The geometry file (`?flat=1`) tags a water material (`banc=water`). Longshot's editor-mod `docs/modding/assets.md` names `_Alb` as the colour texture and does not describe a water-plane shader, a blend equation, or water uniforms. `docs/dev/reference/world.md` treats water volumes as not the scenery a ray stands on. The last correct sheet was `a1e7181`: unlit, blend, depth write off, factor `(0.35, 0.55, 0.55, 0.2)`. `e0783a1` replaced a missing albedo with the generated ripple `ocarina-forge-water` at opacity `0.62`. `a20b4fd` multiplied that ripple by white and retiling a 0..1 chart to 4 m. On `render-known-good` that is the opaque cyan grid on `Obj_LakeHrl_WaterPlane`, with dark holes at the island cutouts, because the sheet is a solid lid over those mesh holes. A rejected albedo is the faint sheet again. A usable albedo is still kept and multiplied by the same factor. The ripple texture is not bound. The geometry cache key is `models-68`.

`?texdebug=1` prints `forge texdebug actor` for the selected actor (console, and the Details panel). Each part names the albedo sampler, the Tex stem, the decoded format and size, the colour space, the map URL, whether the colour node is sampling, a helper tag when the shape is a fade or a board, and the uv attributes with their span. The material-debug role line is under that. Paste that block when a mesh is the wrong colour: the bound stem, the format (`bc7 srgb` or `bc7 linear`), and the uv span are what decide a mask, a bad format, or the wrong channel. The publish of a content-addressed `.ktx2` does not fail the texture request when Windows returns WinError 32 or Errno 13 on the rename. The bytes are served from the file when it already matches, or from memory when the rename has not landed. The texture route reads those bytes and closes the file.

Black boards, dark planes, shadow boards, and black fades are helper meshes, not the tree's albedo. The names in the Lake Hyrule retest are `BlackBoard` / `Mt_BlackBorad_Room` (the file spells it Borad), `pPlane1` / `Mt_DarkPlane`, `ShadowBoard` / `Mt_ShadowBoard`, and `VFX_BlackFade` / `Mt_Etc_LakeHrl_VFXBlackFade`. Longshot's `has()` list still has `content.model` and does not name these materials, a blend equation, or a multiply slot. The render-state strings this file already reads are index 10 (cull), index 11 (opaque / mask / translucent / custom), and index 15 (alpha test). No other slot has been identified as the blend equation, so these planes are not given a guessed multiply. They stay in the glTF (`extras.helper` is `fade`, `board`, `dark`, or `shadow`) and the view hides them until **Helpers** is on. The old omit looked for `fadeblack`. That is not a substring of `BlackFade`, so the planes were drawn. They have no `TEXCOORD`, the batch drops the map, and the flat material is an opaque sheet. That sheet is the black blotch over the tree and the ground next to it. `Land_LakeHrl_Tree_A_01` is not in this checkout, so its albedo was not opened. The slot rule is unchanged.

A two-layer material (`_a1` over `_a0`, masked by `_c0`) is not drawn by the albedo colour node. The editor loads `?flat=1`, which has no texels. That file used to write `extras.blend` and `COLOR_0` only when the overlay image was packed into the same glb. On the flat path the image is absent, so the mask and the blend extras were dropped. The texture manifest still named the overlay, and the page stored it on `blendMap`, but `attachBlend` requires `userData.blend` and the vertex mask. Neither was on the mesh. `attachAlbedo` then sampled `material.map` alone. The flat file now keeps `extras.blend` and `COLOR_0` without the overlay image. The manifest still brings both images. The albedo colour node does not replace a material that has a blend plan.

`Land_LakeHrl_Island_E_05` is the ground patch under the tree. A retest of that actor (`forge texdebug actor`, 21 parts) showed the white sheet with black blotches is the bake lightmap, not a missing blend mask. `lod0Cliff1__Mt_Land_LakeHrl_Island_C_Seal_B3`, `lod0Cliff__Mt_Lan_LakeHrl_Island_E_Grass2`, and `soil_Lb09__Mt_Land_LakeHrl_Island_E_Soil7` bound `sampler=bake0` `texture=CmnTex_BakeDefault` `format=bc5 linear 0x1e01`. `0x1E01` is BC5 UNORM: two channels, R and G. Sampled as albedo it is white where both channels are high and black where they are not. The Seal and Grass parts also used `CmnTex_BakeLightDefault` as the blend layer. Those materials are ordinary FMAT names. They are not `Mt_TerraMaterial_NNN`, and nothing in the sampler list or the shader params selects a `CmnTex_Map_*` layer from a vertex material index. The working cliff on the same actor is the pattern that is already implemented: `lod0Cliff__..._Cliff5` uses `_a0` `UmeCliff_B_Near_Alb` and blends `GerudoValley_gr_soil_A_Alb` (`blendNode=1`). Grass, soil, and seal on this island simply have no `_a*`. The viewer uses an albedo name from the shader options, the assign map, or the texture list when that name is a colour and shares a token with the material. A float param that is a const colour, albedo colour, or base colour is the flat colour. Otherwise the part is the neutral tan `(0.62, 0.58, 0.50)`. It does not sample `bake0`. A far LOD that shares that material name reuses the near LOD's resolved albedo, including when the blend mask has cloned the three.js material so the two LODs are separate objects.

BC7 colour space follows the NVN channel nibble, the same rule already used for BC1 (`0x1A01` UNORM, `0x1A06` sRGB) and ASTC (`xx01` / `xx06`). `0x2001` is BC7 UNORM and is written as vkFormat 145. `0x2006` is BC7 sRGB and is written as vkFormat 146. The block bits are the same either way. three picks `bc7-rgba-unorm` or `bc7-rgba-unorm-srgb` from the texture colour space, not from the vkFormat alone. Albedo used to force sRGB for every BC7 file, so a UNORM block was sRGB-decoded: a stored 0.5 becomes about 0.21 linear, midtones crush toward black, and a texel that is already 1 stays white. A 146 file is sampled as sRGB. A 145 file is sampled as linear. JPEG and PNG fallbacks stay sRGB. Normal and ORM maps stay linear.

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

## Scene lighting, fog, and effects

Forge does not yet decode a scene environment file into sun, fog, or exposure. What follows is what this checkout can actually point at. Anything not measured on a dump here is marked unverified. No offsets were added for a layout this code does not read.

### What the placement files already contain

Effect actors are the `Eft` prefix (`Eft_Firefly` is in the synthetic fixture). `EftLoc_` and any gyaml with `Locator` are markers: a missing mesh is not an error. A gyaml with `Fog` in the name, including `VolumeFogArea`, is a volume outline. The viewport draws a small cross on those three groups. The cross is a gizmo. It is not a particle simulation.

`Dynamic` keys on those actors are listed by name (`lighting-survey`, `/api/lighting`). The values are not read as colours. This checkout has not seen a retail `VolumeFogArea` parameter list, so no fog colour, near, or far field is named from the banc.

### GameBancParam

`Banc/GameBancParam/<Level>.game__banc__GameBancParam.bgyml` is the per-area parameter file the terrain loader already opens. The keys Forge interprets are `TerrainSceneName`, `TerrainScenePosition`, and `$parent`. Other keys are reported by name and left alone. A test fixture once used a document shaped `{"Climate": {}}` as a stand-in for a file with no actors. That is not evidence the game has a `Climate` key.

### Baked light already in the terrain and materials

The TSCB index declares `bake_ao.extm` (`0x5100`) and `bake_gi.extm` (`0x5200`). `bake_ao.extm` is the ground occlusion grid described above. `bake_gi.extm` is only a declared extension. Its payload is not decoded. `custom.extm` is not decoded either.

BFRES `bake0` and `bake1` bind `CmnTex_BakeDefault` or `CmnTex_BakeLightDefault`, or a `Dummy*` texture. Those are shared placeholders, not a per-scene lightmap, and the viewer does not sample them. On `Land_LakeHrl_Island_E_05` the grass, soil, and seal materials have no `_a*` slot, and the old fallback treated `bake0` as an unknown colour sampler because the texture stem is one token (`bakedefault`) and `bake0` was not in the non-colour sampler list. The bound file is BC5 (`0x1E01`). That is what drew the white and black ground. A material with no `_a*` now uses a named albedo from its shader options or texture list, or the shader's const colour, or the neutral tan. It does not fall back to the bake. Grass and soil here are not a terrain material-index blend. The `bake` vertex attribute is a second UV. It is not written into the glTF. The glTF writer stores occlusion as `occlusionTexture` on the ORM image and does not set `texCoord`, so the loader samples `TEXCOORD_0` (attribute `uv`). A channel of 1 samples `TEXCOORD_1` (`uv1`). A mesh that lacks that attribute is drawn without the sample.

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

A compressed mip chain keeps going to 1×1. A level whose width and height both fit in one block is still one block, and the next level is another block. Godot 4.7 `Image.CreateFromData` sizes that chain as `floor(log2(max(width, height)))` extra levels and rejects a short buffer. Measured from its error text: a 341×341 BC7 chain with 8 extra levels is 158416 bytes (stopping on the 2×2 level is 158400); a 512×512 DXT1 chain with 9 extra levels is 174776 bytes (stopping on the 4×4 level is 174760, the 2×2 and 1×1 blocks); a 171×171 DXT1 chain is 20040 bytes. The same shortfall showed up on power-of-two sizes, so it is not a non-power-of-two rounding rule. Whether a retail BNTX table stores those tail levels was not counted here (no dump in this environment). The viewer pads a short chain with a copy of the last block so the upload length matches. `_i0` is a palette slot: U8, U8_2, U8_4, and S8_4 read the first byte, not a colour divided by 255.

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

## Godot spike

The native viewer in `godot/` does not add a format. It ports the readers already described above (BYML, SARC, zstd dictionaries, BFRES 10 / FMAT `0xB0`, meshlets, BNTX block-linear, TSCB / `.hght`). Longshot was checked again before the texture search changed: `content.model` is in `has()`, `gfx.textures` is not, and both are in-game APIs. `docs/modding/assets.md` still says the material's texture name is a file under `Tex/`. The viewer also opens `Model/<prefix>.Tex.bfres`, which that page does not describe and which the browser already searches. The spike never launches the game. See [godot-spike.md](godot-spike.md).

The project generator in [godot-project.md](godot-project.md) uses those same readers. It writes the FSKL bind pose (bone name, parent, local translation, quaternion or the Euler flag `0x1000`, scale) into a Godot `Skeleton3D`, and it reads FSKA into that skeleton's `AnimationPlayer`. Longshot was checked first: `content.model` is a static model plus `Tex/` textures, `has()` has no animation getter, and `gfx.world` / `gfx.shader` are in-game. Nothing published there returns curve keys. The FSKA layout below is the public Switch header used by BfresLibrary for major 9 and 10 (file index at `0x58` / `0x60`, an 80-byte `FSKA` header, a 56-byte bone track, a 48-byte curve). Version 10 keeps those outer headers. Retail v10.4 also uses compact curve keys, described below. The original checks used a hand-built linear translation. Material (`FMAA` / `FSHU`), bone-visibility, and shape curves are not read. Playback is stored at 30 frames per second, which was not measured on a retail file. Cubic keys are sampled as Hermite (value, out-slope, in-slope) and Godot then plays those samples linearly.

A texture name that misses an exact `Tex/<name>` file is also opened when a shorter file stem (at least 6 characters) sits inside the name, or the name's stem sits inside the file. That covers a loose `.bntx`, a pack member, and a `Model/*.Tex.bfres` companion. The first image whose name matches, or whose name contains both the stem and `Alb`, is the one used. At most eight files are opened. `CmnTex_Rock_HrlField_UmeCliff_Near_A_Alb` can live in `Tex/CmnTex_Rock.bntx`. That rule matches the browser's bundle search. It was not counted in a retail `Tex/` listing.

BC4 (`0x1D01`, 8 bytes a block) and BC5 (`0x1E01`, 16 bytes a block) upload as RGTC. They are still not albedo. A `.mate` blend byte other than 0 or 0x7f is that byte over 255, and 0x7f alone is halfway; the chosen index stays on material A unless the weight is 128 or more. A gyaml ending in `_DropFixed_<digits>`, `_<digits>`, or a single trailing letter (the remainder at least 8 characters) is also looked up without that suffix, and `Pack/Actor/<stem>.pack` is opened for each of those stems. `Land_Graveyard_Plants_A_08` therefore tries `Land_Graveyard_Plants_A` and `Land_Graveyard_Plants`. That suffix rule was not counted in a retail `Model/` directory. `HrlField_Note_15`, a name containing `AppearPoint`, and `MiniGame_Supporter` are markers and are not drawn. A `Land_` plant is not a marker.

A model whose every part has no UVs and no texture name, and whose largest part spans 8 m or more, is the terrain proxy the browser hides (`KokiriForest_01`). A single part with no UVs, a span of 8 m or more, and a thickness of at most `max(1 m, 4% of the span)` is a flat untextured slab (the field sheets the 24-vertex bounds box missed). The omit line names the gyaml, the shape, and the material. A part that has UVs stays, so a cliff that is only missing `CmnTex_*` is still drawn.

One behaviour is worth stating next to the version-10 material notes. `sample_v10_bfres` stores the sampler count at FMAT `+0xA2` and the texture count at `+0xA3`. Longshot `host/bfres_edit.c` reads the texture-name count from `+0xA2`. The C# reader, like the Python reader, takes the longest real name list among `+0xA2`, `+0xA3`, and the sampler dictionary. That is the rule the synthetic v10 fixture passes. It is not a new retail measurement.

### Copy into after-the-ocarina `docs/game-formats.md`

No new retail layout was measured for the Godot spike or the Godot project generator. The FSKA section below is the public Switch v9+ header, checked with a hand-built clip, not with a retail file. The native reader uses the layouts already written up. The part a game-formats page needs, if it does not already have it:

BFRES here is little-endian Switch, version word major 10 (`0x000Axxxx`). Version 10's FMDL has the material array and then the material dictionary, with no reserved qword between them. `+0x48` is a different pointer. FMAT is `0xB0` bytes. Name is `+0x08`, texture-name pointer array `+0x20`, sampler dictionary `+0x38`, render-state string array `+0x40`. The byte at `+0xA2` is the texture-name count in Longshot's editor (`host/bfres_edit.c`). Files written as samples store the sampler count at `+0xA2` and the texture count at `+0xA3`. Render-state slot 10 is cull (`front` / `both`), slot 11 is the alpha mode.

Vertex attributes carry a big-endian ushort format id at `+8` (`_F32_3` is `0x0518`, `_F32_2` is `0x0517`, `_SN10` is `0x020E`, `_UN10` is `0x000B`). Snorm8 divides by 127. SN10's sign bit is `0x200` and the scale is 511. UN10 divides by 1023. `_i0` is a palette slot. U8, U8_2, U8_4, and S8_4 use the first byte as that slot, not a 0–1 colour.

Godot 4.7 sizes a compressed mip chain through 1×1, and each level is at least one block, including 2×2 and 1×1. It counts `floor(log2(max(width, height)))` extra levels: 341×341 BC7 is 158416 bytes, 512×512 DXT1 is 174776, 171×171 DXT1 is 20040. A chain that stops on the first level that fits in one block is 8 or 16 bytes short of that. Those lengths come from Godot's error text, not from counting offsets in a retail BNTX.

Packed meshlets are BFRES `0x000A0400` with FSHP flag `0x10`. The bit layout is the one NupExplorer published and `meshlet.py` implements. Skin count other than 0 is not decoded.

FSKA, for BFRES major 9 and 10, is the public Switch layout. The file's skeletal-animation values are the u64 at `0x58` and the dictionary is the u64 at `0x60` (absolute). Each clip is 80 bytes and starts with `FSKA`. Flags are the u32 at `+0x04`. Then seven u64s: name, path, bind skeleton, bind-index array, bone-anim array, user-data values, user-data dictionary. `+0x40` is the frame count (i32), `+0x44` the curve count, `+0x48` the baked size, `+0x4C` the bone-anim count (u16). Loop is flags bit 2. `(flags >> 12) & 7 == 1` means Euler XYZ; otherwise the rotation is a quaternion. A bone track is 56 bytes: name u64, curve array u64, base data u64, 16 reserved bytes, flags u32, then the begin-rotate / begin-translate / curve-count / begin-base-translate bytes and the curve start i32. A curve is 48 bytes: frame offset, key offset, flags u16, key count u16, anim-data offset u32, start / end / scale / offset / delta floats. Frame type is flags bits 0–1 (float, signed Q10.5, byte). Key type is bits 2–3 (float, int16, sbyte). Curve type is bits 4–6: 0 is cubic (four elements: value, out-slope, in-slope, delta), 1 is linear (two), anything else is a step (one). The value is `offset + key[0] * scale`, and a scale of 0 is treated as 1. Cubic slopes are multiplied by the frame span and evaluated as Hermite. Anim-data offsets: scale `0x04` / `0x08` / `0x0C`, translate `0x10` / `0x14` / `0x18`, rotate `0x20` / `0x24` / `0x28` / `0x2C`. Base data (flags bit 3 scale, bit 4 rotate, bit 5 translate) fills channels that have no curve. Bind indices are one u16 per bone track. This was not measured on a retail FSKA. `FMAA`, visibility, and shape curves are not read.

Skin weights are stored beside the baked positions. Skin 0 puts weight 1 on the shape bone (`0xFFFF` means none). Skin 1 and above read `_i0` through the palette and normalize `_w0` (a U8_4 weight is the byte over 255). Skin counts 5-8 also read `_i1` and `_w1`. The Godot project writes `ARRAY_BONES` and `ARRAY_WEIGHTS` and a `Skin` from the rest pose. The vertices stay in model space, so the rest pose does not move them a second time. The viewer still draws the baked mesh and does not play the curves.

BNTX BC7 is `0x2001` (UNORM) or `0x2006` (sRGB). The blocks are the same; the sampler's colour space is not. 16-byte blocks on Switch 2 use a different GOB order from Tegra X1: bit 5 is y1 and bit 6 is x0. BC1, BC4, and 4-byte height samples stay on Tegra X1. A desktop upload should keep BC7 compressed. BC4 is `0x1D01` (8 bytes a block, one channel) and BC5 is `0x1E01` (16 bytes, two channels). Neither is an albedo.

TSCB: world scale is the float at `+0x10`, height scale at `+0x14`, tile count at `+0x38`, and `+0x78` is a relative u32 to the tile table. A tile stores centre x, centre z, and edge, and the name at `+0x60` (relative). `.hght` starts with `TARB` when wrapped; the u16 at file `+10` is the header size and the rest is zstd. Unpacked, `+0x2C` is a relative offset to the height block and `+0x30` is its size. The grid is 257×257 float32, row along z. A block whose size is exactly `257*257*4` is linear. Otherwise it is block-linear, log2 4. Column 256's odd rows are 4 bytes after the even row of the previous GOB; the ordinary address reads padding 0. An exact 0 that survives that repair is a hole, not ground at the origin.

A banc actor is one `Actors` entry: `Gyaml`, `Hash` (u64), `Translate` and `Rotate` and `Scale` as `[x,y,z]` or `{X,Y,Z}`. Rotate is radians, extrinsic XYZ (`Rz·Ry·Rx`). `Ry` of +90° takes local +X to world −Z. Placement for the field is `Banc/HyruleField/HyruleField_Child_Static.bcett.byml` plus the `_Dynamic` sibling. The terrain scene name and origin are `TerrainSceneName` and `TerrainScenePosition` on `Banc/GameBancParam/<Level>.game__banc__GameBancParam.bgyml`, following `$parent` (strip a leading `?` and `Work/`, `.gyml` to `.bgyml`, try `.zs`, at most 8 deep).

### Standalone actor animation and Field cliff albedo

The local dump confirms that `Actor/<actor>.engine__actor__ActorParam.bgyml` inside the actor pack can point from `Components.AnimationRef` to `Component/AnimationParam/*.bgyml`. Each `AnimationResources` row names a `ModelProjectName`, which resolves to `Model/<project>.anim.bfres.zs`. The child Epona component points to `Animal_Horse_Epona_Adult_Animation` with `IsRetarget: true`. Its model-specific `Animal_Horse_Epona_Child.anim.bfres.zs` contains one model-pose clip. Cow points to `Animal_Cow_Animation`. Forge binds FSKA tracks to the model by name. The ASB plays clips by name, as described in [Longshot's animation asset docs](https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/mod-api/docs/modding/assets.md). Forge does not evaluate the ASB or apply the game's retarget scale.

`Tex/CmnTex_Rock_HrlField_UmeCliff_Near_A_Alb.bntx.zs` is a loose 1024×1024 image in BNTX format `0x3106` (sRGB ASTC 6×6). The earlier C# lookup found the file but discarded it when decoding rejected that format. Python's BNTX reader already supported the footprint. The Godot generator and viewer now decode these blocks on the CPU before the BC7 transcode.

### Actor mesh winding, weights, and compact FSKA curves

Godot treats clockwise triangles as front-facing. The Cow's vertex normals agreed with the original BFRES counterclockwise triangle orientation on all four surfaces. The native project writer swaps two indices per actor triangle to preserve exterior faces in Godot without negating the normals.

FVTX `_i0` formats `0x0302`, `0x0309`, and `0x030B` store one, two, and four unsigned byte palette indices. Cow uses the one- and four-byte forms. Child Epona also uses the two-byte form and has surfaces with a second four-influence bank in `_i1` and `_w1`. The importer normalizes both banks together and uses Godot's eight-weight surface format when any vertex uses the second bank.

In the local v10.4 Cow and Epona animation files, curve flags include `0x4000` for the compact key layout. A `0x0400` curve uses the frame multiplier at `+0x20`. Quantized value keys are unsigned and expand as `value = raw * float(+0x24) + float(+0x28)`. Quantized cubic data starts with two floats for slope scale and offset, followed by one interleaved value and slope per key. The same slope applies on both sides of a key for Hermite interpolation. This agrees with the public Switch 2 Toolbox curve reader. Godot stores the sampled result as linear keys.

### Child and adult Epona animation sources

The child actor pack's AnimationParam marks `Animal_Horse_Epona_Adult_Animation` as a retarget resource. Its `RetargetScaleRef` points to `Component/RetargetScale/Animal_Epona_Child.engine__component__RetargetScale.bgyml`, which lists a separate three-axis scale per clip. For example, `Wait` has `[0.62, 0.62, 0.58]`. This is an observed source parameter. The exact game retarget algorithm has not been reproduced.

Forge's child Epona scene uses the child model's pose clip from `Animal_Horse_Epona_Child.anim.bfres.zs` and three child-specific clips from `Dm_MelodyGet_Epona_Animal_Horse_Epona_Child.anim.bfres.zs`. Adult Epona uses its own animation resources. The two Godot libraries are generated from distinct files.

In the child cutscene clips, `Root` stays at the local origin while `Skl_Root` carries translation. In Godot, the longest measured `Root`–`Skl_Root` link was 13.06 m in `C04-Animal_Horse_Epona_Child-A-0-St` compared with a rest length of 0.95 m. The link reflects authored movement of the body hierarchy.
