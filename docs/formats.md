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

A scene name looks like `KokiriForest_Child_Static` or `HyruleField_Adult_Dynamic`. Static and Dynamic are separate bancs.

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
