# Project format

An Ocarina Forge project is a folder of small text files. One project is one mod. Format versions **1** and **2** are read. Format 1 is a scene-editor project. Format 2 is the same folder plus an `engine` key, so a build can run a scripted pipeline. The folder is meant to be committed. The game dump is not.

New projects are created in `~/.local/share/ocarina-forge/projects/<project-id>/` (or `OCARINA_FORGE_PROJECTS`). That folder is outside the dump and outside the game. The mod id is `local/<project-id>`. **Save Longshot mod** writes `build/mod/` and, when asked, copies it to `<game>/mods/local/<project-id>/`. The default game is `~/Games/Longshot/mod-test` (`OCARINA_FORGE_GAME`). `build/mod/` is export output. Editor state stays in the project. The export rules are [design/mod-editor.md](design/mod-editor.md).

```text
my-mod/
  manifest.yaml
  scenes/<Area>/<Scene>.yaml
  text/          # reserved
  events/        # reserved
  behaviors/     # set-field ops for one actor graph
  load-filters.yaml
  tables/        # reserved
  build/         # export output, not part of the source
```

`build/` is produced by export. Do not treat it as source.

## Importing local Longshot mods

The project chooser also scans `<game>/mods/local/` (the `OCARINA_FORGE_GAME` game root). It can open a matching Forge
project or import a mod whose `patches/*.json` files contain placement `set` operations on `Translate`, `Rotate`, or
`Scale`. Other local mods are shown as **Not editable**. Import creates a project in the Forge library and keeps the
original mod tree, including its runtime entry and manifest fields, in `.longshot-source/`; export and install preserve
those files while regenerating the placement patches. The game’s Ocarina Forge page can open a local mod directly.

## Load filters

The Actors panel has a load preset and a checkbox per layer (Models, Terrain, Water, Collision, Behavior, Effects, Decorations, Lights) plus Load textures. A checked layer is fetched. Load textures downloads images after the mesh. Layout, Logic, and `textures=0` leave it off. The eye hides a layer that is already loaded. The Ground, Water, and Textures checkboxes on the stage bar show or hide those things. Textures there does not dispose images and does not fetch them again. `?load=terrain,models` or `?load=layout` sets the filter for the session and saves it on the open project. Full clears the query.

`load-filters.yaml` sits next to `manifest.yaml`. It is editor state. Format 1. Save, Save Longshot mod, and Build mod do not read it.

```yaml
format: 1
preset: layout
textures: false
layers:
  models: true
  terrain: true
  water: true
  collision: true
  behavior: false
  effects: false
  decorations: true
  lights: true
visible:
  models: true
  terrain: false
```

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

`permissions`, `optionalPermissions`, and `net` are optional. The Mod permissions panel writes them. Export copies a non-empty list into `mod.json`. An empty list that was saved clears the same field on an imported manifest. A project that never set the key leaves an imported value as it was. `multiplayer` is written only when it is `all` or `host`. The rules for those fields are in [formats.md](formats.md).

`target.version` is the game the diffs were written against. Export warns when it is not `1.1.1`, because the size-table rule was checked against that version.

`collisionFollow` is optional: `off` (the default), `srt`, or `bake`. `off` moves the mesh and leaves the bake. `srt` mints a new `SRTHash` on a pose move and ships no shape file. `bake` patches the static-compound instance transform and keeps the shape bytes. The scene diff does not store the mode. [formats.md](formats.md) has the layout and what each mode writes.

`domains` lists the kinds of patch the project may contain. A format 1 project exports `scenes` and stores `text`, `events`, and `tables` for a later editor. See `ocarina_forge/domains.py`. `behaviors/` is the behavior editor's op list. It is not one of those reserved domains.

`engine` defaults to `scenes`. `after-ocarina` builds the project with the vendored pipeline instead of the scene rewriter. That project root is the pipeline's `src` directory:

```text
after-the-ocarina/
  manifest.yaml          # format: 2, engine: after-ocarina
  mod.toml
  text/*.yaml            # MSBT edits
  events/*.yaml          # flow patches, the boot hook, the rumor builder
  scenes/*.yaml          # logic, schedule, stone, and castle recipes
  rsdb/*.yaml            # GameAnalyzedEventInfo, EventSetting, challenge rows
  gamedata/*.yaml        # GameDataList reset type and BoolExp appends
  ui/*.yaml              # tapestry recipe; the bfres is cloned from the dump
```

`tables/` is still the name a visual RSDB editor would write. The pipeline reads `rsdb/` when that folder exists. Scene files in an engine project are recipes, not actor-hash diffs, unless they actually contain an `actors` map. The viewport highlights a recipe's banc with an open dot. Actor diffs, when a project has them, stay the filled dot and are not merged into the After the Ocarina build.

Some of those recipes have no visual editor yet. They are advanced patches: the YAML is the source, and the build produces the romfs. That includes event flows, the rumor builder, the boot hook, GameDataList, RSDB rows, AINB logic, AIScheduleParam, the stone clones, and the tapestry. The scene editor still owns ordinary actor diffs for a format 1 project.

## Behavior edits

One file per actor class:

```text
behaviors/Npc_Kokiri_Saria_Child.yaml
```

The dump's AINB, event flow, and schedule stay the base. The file stores `set field` ops. Each op names the node's stable id, the field, its type, and the new value. Setting the field back to the dump value removes the op. Undo replaces the whole list. Export writes the rebuilt file under `build/romfs` only after that format round-trips with no ops applied. A format that cannot round-trip keeps the ops and does not write the file.

```yaml
format: 1
gyaml: Npc_Kokiri_Saria_Child
ops:
  - op: set field
    id: "event:Event/EventFlow/Npc_Kokiri_Saria_Child.bfevfl.zs#0:Talk"
    field: action
    type: string
    value: Demo_Talk
```

Connecting nodes and adding nodes are not written yet.

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

Header fields are `Gyaml`, `Hash`, `SRTHash`, `Translate`, `Rotate`, `Scale`, `Name`. `Hash`, `SRTHash`, and `Phive.Placement.ID` cannot be edited in the inspector. The id is how the project finds the actor. Export can mint a new `SRTHash` when `collisionFollow` is `srt`. That mint does not change `Hash` or `Phive.Placement.ID`.

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

On a format 1 project, `text/**/*.yaml`, `events/**/*.yaml`, and `tables/**/*.yaml` are stored and the scene build says they were not written. On `engine: after-ocarina` they are the sources the pipeline applies, together with `rsdb/`, `gamedata/`, and `ui/`. The project panel lists the labels, flows, and quests. It does not edit them.

## After the Ocarina YAML

The importer (`ocarina_forge/importers/ato.py`) reads the scene patches from After the Ocarina, which are a different on-disk shape, and writes format 1 diffs.

Accepted inputs:

- One mapping with `banc` (or `file` or `path`) and `actors` (or `edits` or `ops`).
- A list of those mappings.
- A mapping whose keys are banc paths.
- A directory of `*.yaml` files. `manifest.yaml` is skipped.

An actor entry with `op: add`, or a `gyaml` and no hash, becomes `added` and gets a new hash. `hide: true` or `op: hide` hides. `op: remove` (or `delete`) removes. `translate`, `rotate`, and `scale` copy across. `params` is flattened into `set` paths. A `Name` parameter becomes the instance name.

How these files line up with the Longshot mod package, and what an exporter still has to translate, is [longshot-mapping.md](longshot-mapping.md). The YAML stays the source.

`examples/ato-scenes/hyrule_field.yaml` is a fixture in that shape. Point the importer at a real `src/scenes` folder when you have the After the Ocarina tree.
