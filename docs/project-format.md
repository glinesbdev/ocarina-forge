# Project format

A Bancwright project is a folder of small text files. Format version is **1**. The folder is meant to be committed. The game dump is not.

```text
my-mod/
  manifest.yaml
  scenes/<Area>/<Scene>.yaml
  text/          # reserved
  events/        # reserved
  tables/        # reserved
  build/         # export output, not part of the source
```

`build/` is produced by export. Do not treat it as source.

## manifest.yaml

```yaml
format: 1
name: after-the-ocarina
title: After the Ocarina
version: 0.1.0
author: Bradyn
target:
  game: oot-switch2
  version: "1.1.1"
  host: longshot
domains:
  - scenes
  - text
  - events
  - tables
```

`name` is the folder Longshot loads: `mods/<name>/romfs`. It must match `^[A-Za-z0-9][A-Za-z0-9._-]*$` and must not start with `_` or `-`. Those prefixes are how Longshot disables a mod.

`target.version` is the game the diffs were written against. Export warns when it is not `1.1.1`, because the size-table rule was checked against that version.

`domains` lists the kinds of patch the project may contain. This version only exports `scenes`. The other three are stored so a later editor can fill them without a format break. See `bancwright/domains.py`. A new exporter should be called from `bancwright.export.build` beside the scene loop.

## Scene diffs

One file per placement banc:

```text
scenes/KokiriForest/KokiriForest_Child_Static.yaml
```

The path is the romfs path with `Banc/` and `.bcett.byml.zs` removed. The file itself records the full banc path, so a renamed file cannot be applied to the wrong scene.

```yaml
format: 1
banc: Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs
actors:
  "0x1000000000000001":
    state: edited
    translate: [6.0, 0.0, 3.0]
    rotate: [0.0, 1.2, 0.0]
  "0x1000000000000009":
    state: hidden
    gyaml: Animal_Cucco
  "0x40000000000000aa":
    state: added
    actor: Npc_Zora_01
    name: River
    translate: [1.0, 0.0, 5.0]
    rotate: [0.0, 0.0, 0.0]
    scale: [1.0, 1.0, 1.0]
    params:
      Dynamic:
        IsEnable: true
```

Actor keys are the instance hash, `Hash` in the banc, also stored as `Phive.Placement.ID`. Keys are strings (`"0x..."` with 16 hex digits). A bare `0x` token would be an integer under YAML 1.1, so the writer quotes them.

Hashes the project mints set bit 62 (`secrets.randbits(64) | (1 << 62)`), matching the usual “this id is new” convention. The hash is the identity of the edit. It is not rewritten when the actor moves.

### States

| State | Meaning on export |
| --- | --- |
| `edited` | The vanilla actor is kept. Only the fields present on the record change. |
| `added` | A new actor. `actor` is the Gyaml (the `Pack/Actor/<name>.pack.zs` name). |
| `hidden` | Omitted from the exported Actors array. The record stays, so the editor can show it again. Edits on that record are kept and come back on unhide. |
| `removed` | Same omission as hidden, but the editor lists it under Removed. Restore deletes the record. |

An empty `actors` map deletes the file. A diff that round-trips to the vanilla actor is dropped, so the file stays small.

### Edited fields

Only changed fields are stored.

| Field | Effect |
| --- | --- |
| `translate`, `rotate`, `scale` | Three numbers. Metres, radians, unit scale. If the vanilla file used an `X`/`Y`/`Z` dictionary, export keeps that shape. |
| `gyaml` | Replaces `Gyaml`. |
| `name` | Replaces `Name`. `null` clears it. |
| `set` | Dotted paths into the actor, for anything that is not a header field. `Dynamic.TalkCount`, `Links[0].Ref`. |

Header fields are `Gyaml`, `Hash`, `SRTHash`, `Translate`, `Rotate`, `Scale`, `Name`. `Hash`, `SRTHash`, and `Phive.Placement.ID` cannot be edited. The id is how the project finds the actor.

`set` values use plain YAML scalars, or a one-key tag when the BYML type is not the obvious one:

```yaml
set:
  Dynamic.TalkCount: 2
  Dynamic.DefaultDungeonRoomIndex: {"$u32": 1}
  Dynamic.SomeId: {"$u64": 12}
```

Tags: `$u32`, `$s64`, `$u64`, `$f64`, `$bin` (hex), `$h32`, `$h64`.

Added actors put the same extra fields under `params` instead of `set`. `params` is a tree, not a flat path map.

### What a scene diff does not do

Group lists (`AiGroups`, `FarDeleteGroups`, `SimultaneousGroups`, `PlgGdTable`, `PhiveHeader.PlacementGroups`) and `Links` are not rewritten. If a hidden or removed hash is still mentioned, export warns and leaves the reference. Check that scene in Longshot.

Actors with no `Hash` are shown as locked and copied through unchanged.

## Text, events, tables

Any `text/**/*.yaml`, `events/**/*.yaml`, or `tables/**/*.yaml` is part of the project and is left alone by this version’s exporter. The build log says so. The shapes those files will take (MSBT control codes, evfl graphs, RSDB rows) belong to later stages. Do not invent a second scene format beside `scenes/`.

## After the Ocarina YAML

The importer (`bancwright/importers/ato.py`) reads the scene patches from After the Ocarina, which are a different on-disk shape, and writes format 1 diffs.

Accepted inputs:

- One mapping with `banc` (or `file` or `path`) and `actors` (or `edits` or `ops`).
- A list of those mappings.
- A mapping whose keys are banc paths.
- A directory of `*.yaml` files. `manifest.yaml` is skipped.

An actor entry with `op: add`, or a `gyaml` and no hash, becomes `added` and gets a new hash. `hide: true` or `op: hide` hides. `op: remove` (or `delete`) removes. `translate`, `rotate`, and `scale` copy across. `params` is flattened into `set` paths. A `Name` parameter becomes the instance name.

`examples/ato-scenes/hyrule_field.yaml` is a fixture in that shape. Point the importer at a real `src/scenes` folder when you have the After the Ocarina tree.
