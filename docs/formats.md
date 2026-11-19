# Formats this version reads and writes

The After the Ocarina archive (`docs/game-formats.md` and the `after_ocarina` library) was not in the environment this project was built in. The rules below are the ones Bancwright implements. They follow the public MIT notes and tools in [Kabosune/oot-remake-modding](https://github.com/Kabosune/oot-remake-modding) (the tree After the Ocarina vendors as `third_party/oot-remake-modding`) and the resource-size behavior Longshot published. Compare them with `docs/game-formats.md` on your machine. The checklist is [testing-on-real-dump.md](testing-on-real-dump.md).

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

The path key is the romfs path **without** `.zs`. CRC is `zlib.crc32`. Named entries exist for CRC collisions. The size is a memory budget, a multiple of 8, not the file size.

When a placement file is rewritten:

- The entry is never lowered.
- It is at least `unpacked size + 512`, rounded up to 8. Rewritten BYML is a little larger than Nintendo’s for the same values, and shipping placement entries sit a few hundred bytes above the file.
- If the unpacked file grew and an entry already existed, it is also at least `old entry + 8 * bytes grown + 4096`, rounded up to 8. That is the growth Longshot measured: a BYML list element costs more on the heap than it costs in the file. A bump of a few hundred bytes was not enough and the game could sit on the load.

The new entry is inserted as a hashed row, or as a named row when that CRC already belongs to a different path. Other rows, including ones this mod does not touch, are copied through.

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

Category colors use the pack-name prefix: `Npc_` / `Shop_`, `Enemy_`, `Animal_`, `Eft`, `Land_`, `Obj_` / `FldObj_` / `Item_` / `Weapon_` / `Dm_`, and system names (`StartPos`, `Player`, `Sound`, `AirWall`, `Ground`, anything with `Anchor` or ending in `Actor`).
