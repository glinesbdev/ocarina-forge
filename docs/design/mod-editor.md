# Mod editor

November 22, 2026. Ocarina Forge is the editor again. The in-game editor on Origin `glinesbdev/longshot`, branch `editor-mod` (`docs/editor-mod.md`, `docs/editor-bridge.md`, `mods/longshot-editor`), worked: a Save wrote a placements patch, the area reloaded, and Link was put back. Editing live game memory still does not feel like editing the game. The camera, the gizmos, and the actor writes all live inside the running process.

The new plan is a standalone visual editor. Maps, actors, behaviors, and dialog are edited in Forge. Every edit is a visual front end for a Longshot mod, built with upstream's Modding API ([PR 1958](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1958), branch `mod-api`). The running game is only a live preview. Forge tells a game started with `longshot mod run` to reload the mod and to teleport Link. The picture Bradyn works in is Forge's own renderer (Three.js, BFRES materials), not the game's. A game-quality renderer is a large job, so it is planned one step at a time.

Nothing in this note is posted upstream. Gaps are recorded here so a later change can be proposed by a person, not by this repo.

## Architecture

```text
Forge editor (Three.js, this repo)
        |  save
        v
mod folder on disk
  mod.json
  romfs/                 empty directory, only when a patch was written
  patches/<name>.json
  src/*.ts                 only when a content declaration is required
        |  longshot mod check / pack / dev
        v
game started with `longshot mod run`
        ^
        |  control bridge: mod_reload, teleport, shot
        |
Forge preview
```

The editor does not use the in-game listener on `127.0.0.1:47321`. That port belongs to a `--with-editor` build. Preview uses the control bridge that `longshot mod run` already opens for tools. The commands are the ones in `docs/modding/tools.md` on `editor-mod`:

| Tool | Bridge | What Forge uses it for |
| --- | --- | --- |
| `longshot mod dev` | `mod_reload` after `check` passes | Watch the mod folder and ask the running game to load it again |
| `longshot mod restart` | `mod_restart` | Start that mod's runtime again. Does not re-read `mod.json` |
| `longshot mod restart --manifest` | `relaunch` | Start the game again so a new `mod.json`, or a larger banc, is read |
| `longshot mod teleport [DESTINATION]` | `teleport` | Put Link on the map under edit |
| `longshot mod shot [PATH]` | `shot` | A PNG of the next frame, so the preview can be checked without looking at Forge's renderer |

`longshot mod check` compiles and links `src/` with the game's compiler and typechecks when `tsc` 7 is present. `longshot mod pack` writes the `.lsmod`. `longshot mod dev --once` copies the folder into the game's `mods/<owner>/<name>/` before the first launch.

A manifest mod with no `romfs/` directory is skipped before `patches/` are indexed (`host/mods.c` on `editor-mod`: `has_romfs` is `dir_exists` of that directory, and the index continues when it is missing). An empty directory is enough. `docs/modding/files.md` says the directory may be empty. `romfs/.keep` is not required by that check, and `index_romfs` would count the file as an added RomFS file, so Forge does not write it. `longshot mod check` does not require a script for a file-only mod (`docs/modding/manifest.md`: leave `runtime` out). The skip of a manifest mod that has patches and no `romfs/` directory disagrees with the file watch a few lines above. That is an upstream note. It has not been posted, and this repo does not post it.

### What carries over

From `editor-mod` and from `mods/static-move`:

- The placements patch. One JSON file per banc. `target` is the romfs path. `placements` is a list of ops. A move of an actor that is already in the file is `op: "set"`, `hash` (`0x` and 16 hex digits), `path` (`Translate`, `Rotate`, or `Scale`), and `value` (three floats). `Rotate` is radians, the same unit the banc stores. `content.place`'s `rotation` is degrees; a placements patch must not copy that unit. The sign patch that Bradyn already confirmed is the shape:

```json
{
  "target": "Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs",
  "description": "KokiriForest_013 (Obj_Kokiri_Signboard_A_01) moved on X.",
  "placements": [
    { "op": "set", "hash": "0x4a13f8880eb01a43", "path": "Translate", "value": [-9.75, 111.6, 10.24] }
  ]
}
```

  `5337883246609832515` is that same hash. The actor is `Obj_Kokiri_Signboard_A_01` in `KokiriForest_Child_Static`. The pot from the same edit is Dynamic hash `0x473ed5ff7535e13e`.

- Typed change ops. Forge's scene YAML stays the source ([project-format.md](../project-format.md)). The mod folder is export output, the way `build/` is today. An `edited` actor with `translate` / `rotate` / `scale` becomes one `set` per field that changed. The hash is not rewritten when the actor moves.

- Hot reload. A `set` of a `Translate` or `Rotate` the placement already has, written into a container that placement does not share, stays the same file size. The game log says the banc is served anew from its next load. Leaving the area and entering it again shows the pose. `longshot mod dev` rebuilds a folder mod after the files have been still for 0.3 s and then sends `mod_reload`. A change under `patches/` or `romfs/` does not reload the mod's script. The editor mod's own Save rebuilds immediately and, when the log says served anew, reloads the area and puts Link back. Forge can ask for `mod_reload` and then `teleport`. It cannot invent an area reload the bridge does not have. Re-entry is the load.

- Adds need a process start. `Scale` on a placement that has no `Scale` key is an `add` of that path. A new actor is an `add` of the whole placement. Either one grows the served file. The size table is read once at process start. A file larger than the entry the process allocated is not served until the next start. The Save hint on `editor-mod` says to restart. There is no live `setScale`. `content.change` is also not a hot path: the content module runs at game start and emits its patches into that start's layers. A `params` edit shows up on the next `longshot mod restart --manifest` (or `longshot mod stop` and `longshot mod run`), not on `mod_reload` alone.

- Size-table headroom. A shared BYML container must not be overwritten in place. Kokiri's sign is the example: `Actors/77/Translate` is a container other actors share, and writing into it would move all of them. The patch writer makes a private copy of that array. The copy is only a few dozen bytes, but any growth asks the size table for `entry + (now - start) * 8 + 4096`, then rounded up to 32. For that sign the editor measured `132360 + 30036 * 8 + 4096 = 376744`, which rounds to `376768`. The game's entry is `132360` over a `132068`-byte file, so the copy does not fit. Rewriting with no shared containers grew the file from `132068` to `162104` bytes. The editor mod, when it is enabled at process start, walks every `Banc/*_Static.bcett.byml.zs` and `*_Dynamic.bcett.byml.zs`, rewrites each the way a `set` will, and reserves that entry. The log line is `editor: banc headroom:`. A later start with the same files reuses the table. A new key or a new actor can still grow past the reserved entry, and that Save says to restart. Forge does not inherit that reservation by installing `local/longshot-editor`. A Forge mod that grows the Kokiri Static file will not appear until a process start whose size table covers the new file. Milestone 1 treats that restart as the expected first apply, and says so in the UI.

### What gets dropped

These belong to the in-game editor. Forge does not call them.

- The in-game camera. `camera.override`, `setPosition`, `setTarget`, `setFov`, and the F6 pull-back. Forge's own camera is the one in [controls.md](../controls.md).
- The in-game gizmos. `gfx.world` lines, boxes, circles, and billboards, and the W / E / R handles drawn in the game view. Forge draws its own gizmos on its own meshes.
- Memory actor hooks. `actors.write` (`setPosition`, `setRotation`, and the rest) writes the live actor by guest address and does not write the banc. `actors.spawn` / `actors.remove` only cover actors that mod spawned, and a co-op room has no spawner. The live registry, the ActorLink walk, and the physics ray stay in the game. Forge selects and moves the actor in its own scene, then writes a patch.

Placement and collision research used by that in-game editor is Hyblitze's and Zirem's, from Heros Legacy (`heros-legacy/v3.0.0` at `6ce46cc7`). Hyblitze traced the actor-pack path and the physics ray. Zirem wrote the spawn request block that follows those notes. `docs/editor-bridge.md` on `editor-mod` records the layouts. Forge's preview does not use them. The static-compound bake is a different file from that ray. [formats.md](../formats.md) names them again for the bake, and their code is not copied into this repo.

## Mapping

Forge's typed records stay the source. The exporter writes the mod. [longshot-mapping.md](../longshot-mapping.md) described an older revision of the spec, before placements were keyed by hash. The table below is the `mod-api` shape on `editor-mod`, checked against `mods/static-move`, `docs/modding/content.md`, and `docs/editor-mod.md`.

| Forge record | Mod API | Notes |
| --- | --- | --- |
| `edited` + `translate` | placements `set` of `Translate`, three metres | Same-size when the array is not a shared container. The Kokiri sign's array is shared, so the first write grows the file. |
| `edited` + `rotate` | placements `set` of `Rotate`, three radians | Do not emit `content.place`'s degrees. |
| `edited` + `scale`, key already present | placements `set` of `Scale` | Same-size, hot reload. |
| `edited` + `scale`, key absent | placements `add` of `Scale` | Grows the file. Next process start. |
| `collisionFollow: srt` on a pose move | placements `set` of `SRTHash` (an `add` if the key is missing) | `Hash` and `Phive.Placement.ID` stay. No `.bphsh` and no `.bphsc`. This does not move the Kokiri sign's baked row. |
| `collisionFollow: bake` on a pose move | the pose ops, plus the patched `.bphsc` tiles in `romfs` | `SRTHash` stays. The instance transform and the compound SIMD-tree bounds change. The shape bytes, the per-mesh AABB tree, and the actor-table tail stay. |
| `set` of an existing `Dynamic.*` scalar (string, number, bool) | `content.change({ file, placement, params })` | `placement` in the API text is the id without `0x` (`00a1b2c3d4e5f607`). The patch `hash` keeps `0x`. Applied at the next game start, because the content module runs then. A missing key is a stage error and the mod's content is not added. |
| `gyaml` on an existing actor | `content.change({ file, placement, actor })` | Same start rule. This changes what is placed there. It does not move it. |
| `added` | `content.place({ id, map, age, actor, position, rotation, scale, params, layer })` or a placements `add` of the whole actor | `content.place` adds a second actor and leaves the original. `rotation` is degrees. `layer` is `"static"` or `"dynamic"` (dynamic when omitted). `map` is `KokiriForest_Child`, not a new map name. New maps are refused: terrain, baked collision, and lighting are not something a mod can make yet. The in-game editor's new-actor hash sets bit 63. Forge mints bit 62. This slice warns and does not write an add. |
| `hidden` / `removed` | not written yet | The project still stores the state. Export warns. `docs/modding/files.md` describes a placements `remove`. This slice does not emit it. |
| Links, group lists | no op | Unchanged from [longshot-mapping.md](../longshot-mapping.md). Export warns and leaves them. |
| Behavior `set field` | no merge into the game's AINB | `content.tree({ id, from, add, patch })` copies a tree for a new actor. The game's own graph is still last-layer-wins. The project keeps the op. Export does not write a replaced graph. |
| Text label | `content.text({ file, label, text })` | One string, or an object by language. Control tags (colours, icons, pauses) are not in this API yet. |
| New flag | `content.flag({ id, kind, default, values, reset })` | Not saved. A flag kept with the save is `storage.save.flags`, a different feature. |
| Events, schedules | no merge | Stored as typed ops in the project. Not a replaced flow file. |

`mod.json` for a generated preview mod carries no Nintendo name in the id or the title. A workable shape, copied from `mods/static-move/mod.json` and stripped to what a patch-only mod needs:

```json
{
  "id": "local/kokiri-sign",
  "name": "Kokiri sign",
  "version": "0.1.0",
  "author": "Bradyn"
}
```

`id`, `name`, and `version` are the fields the loader requires (`host/mod_manifest.c`). `author` and `license` are written only when the project sets them. `description` is not a `mod.json` field on `editor-mod`; it stays in `manifest.yaml`. `runtime` and `entry` are omitted, so no `src/main.ts` is written. A later milestone that emits `content.change` or `content.place` adds `runtime`, `entry`, `api`, and `content.add` at that point, and only then.

A patch-only export omits `permissions`. When the project has a runtime, or you set them in **Mod permissions**, `manifest.yaml` holds `permissions`, `optionalPermissions`, and `net`, and export writes those into `mod.json`. The player-facing table, the manifest rules, and the `mods.json` `"granted"` array are in [formats.md](../formats.md).

## Projects

One project is one mod. The source is the Forge folder: `manifest.yaml`, `scenes/`, and the other domain files in [project-format.md](../project-format.md). Selection, camera, panel layout, undo, and caches stay there. They are not copied into the mod.

Projects live outside the game install and outside the dump:

```text
~/.local/share/ocarina-forge/projects/<project-id>/
```

`OCARINA_FORGE_PROJECTS`, or `projects:` in the config file, moves that folder. Creating a project with a blank folder uses it. The mod id is `local/<project-id>`. The id is lower case, and each half follows the rules in `docs/modding/manifest.md` on `editor-mod`.

Save installs into the test game:

```text
~/Games/Longshot/mod-test/mods/local/<project-id>/
```

`OCARINA_FORGE_GAME`, `--game`, or `game:` in the config file changes the install. The default is Bradyn's test install.

After the Ocarina is one project of this kind: `examples/after-the-ocarina`, format 2, `engine: after-ocarina`. Opening it opens that folder. Its romfs build stays the Build romfs action. A Longshot-mod save of that project exports only placement ops that differ from the dump. It does not replace the pipeline.

## Importing local Longshot mods

The project chooser scans `<game>/mods/local/`. It opens an existing Forge project with the same `local/<slug>` id, or
imports a mod whose patch files contain placement `set` operations for `Translate`, `Rotate`, or `Scale` vectors. Other
formats are marked not editable. The imported source tree lives at `.longshot-source/` inside the Forge project, separate
from editor diffs. Export preserves its runtime files and original `mod.json` fields, then replaces its patch folder with
the current Forge output. Imported placement operations whose actors are not in the configured dump are retained when
their edits remain in the project; unsupported removals are warned about rather than silently dropped.

## Lean export

A mod never ships a file that does not change the game. Export writes:

- nothing, when every edit matches the dump or was moved back
- otherwise `mod.json`, `patches/<Scene>.json` for each banc that still has an op, and an empty `romfs/` directory

For a newly created patch-only project, no empty patch file, `romfs/.keep`, or `src/` is written. Imported local mods keep
their source files and runtime manifest fields. No project YAML, undo history, or cache is installed. Installing an empty
new project deletes `mods/local/<project-id>` so a previous move does not stay in the game.

`content.change`'s placement id (no `0x`) and the patch `hash` (`0x` plus 16 hex digits) are the same integer. Forge stores `0x` and 16 lower-case hex digits. A 16-digit hex string and the decimal form normalize to that.

Hide and remove stay in the project and in the view. Export warns and writes no op. `docs/modding/files.md` on `editor-mod` describes a placements `remove`. This slice does not emit it. Turning the warning into that op is the next export step, not this one. That note is not posted upstream.

## Milestones

Each one is shippable on its own and is something Bradyn can check on his machine. The renderer stays Forge's. A step is done when the check below passes, not when the picture matches the game in every way.

### 1. Move the Kokiri sign, and see it in the game

Create a project, open `KokiriForest_Child`, select `Obj_Kokiri_Signboard_A_01` (Static, `KokiriForest_013`, hash `0x4a13f8880eb01a43`), and move it. Save writes `mod.json`, an empty `romfs/`, and `patches/KokiriForest_Child_Static.json` with one `set` of `Translate`. `longshot mod check` is the game-side check. The class is placed more than once, so the edit command has to name the hash. Without `--id` it writes nothing and exits non-zero.

```bash
python -m ocarina_forge project create kokiri-sign --title "Kokiri sign" --author Bradyn
python -m ocarina_forge edit KokiriForest_Child \
  --gyaml Obj_Kokiri_Signboard_A_01 --id 0x4a13f8880eb01a43 \
  --translate -9.75,111.6,10.24 \
  --dump ~/Games/Longshot/rc/v3.0.0
python -m ocarina_forge mod-export --install --dump ~/Games/Longshot/rc/v3.0.0
```

Preview, on a game already started with `longshot mod run`, the mod turned on: Forge runs `longshot mod dev --once` if the folder is not installed yet, then tells him the first apply needs a process start, because this sign's `Translate` is a shared container and the file grows. He runs `longshot mod stop` and `longshot mod run` (or `restart --manifest`). Forge then runs `longshot mod teleport` to the child Kokiri destination and `longshot mod shot`. The sign in the PNG stands at the pose he set. The patch file on disk matches the `static-move` shape. A second move that does not grow the file again is the hot-reload check: `mod_reload`, leave the area, enter it, shot. If that second shot is still the old pose, the size table is still short and the UI says restart, which is the headroom caveat and not a failed move.

### 2. Rotate, then scale, the same sign

Rotate writes `Rotate` in radians (45 degrees is `0.785398`, six digits in the patch). Scale writes `set` when the banc already has `Scale`, and `add` when it does not. The inspector says an `add` grows the file and waits for the next process start. The export log says the same. A shot shows the new yaw. Scale is checked the same way after a restart.

The class is placed more than once, so both commands name `--id`. Use the same project as the move. A yaw that is already the banc value writes no rotate op.

```bash
python -m ocarina_forge edit KokiriForest_Child \
  --gyaml Obj_Kokiri_Signboard_A_01 --id 0x4a13f8880eb01a43 \
  --rotate 0,0.785398,0 \
  --dump ~/Games/Longshot/rc/v3.0.0
python -m ocarina_forge edit KokiriForest_Child \
  --gyaml Obj_Kokiri_Signboard_A_01 --id 0x4a13f8880eb01a43 \
  --scale 1.5,1.5,1.5 \
  --dump ~/Games/Longshot/rc/v3.0.0
python -m ocarina_forge mod-export --install --dump ~/Games/Longshot/rc/v3.0.0
```

The patch stays one file. Translate is included only if it still differs. `Scale` is `add` when the sign has no scale key, and the log says it waits for the next game start. In the editor, Scale is the toolbar button. S remains fly-back. The stage bar names the mode by which button is on.

### 3. Materials and UVs, one surface at a time

The sign, then one wooden prop, then one foliage card. Each step binds the albedo the material names (`_a0`, then `_a1` or `_a2`), on the UV the assign names (`_u0`, `_u1`, or `_u2`). A sampler index of `0xFF` stays off. Normals and the packed ORM map stay on the following step, not this one. `material-debug` is the dump-side check. A `shot` of the same actor is the game-side check. Mismatch on one surface is a failed step. [models.md](../models.md) is the assign layout.

### 4. Terrain tiling

Kokiri's walkable floor meets the actors' feet. The `_sub` scene stays the upper layer (about 13–24 m on Kokiri), not a replacement of the main floor. `check_terrain.py` is the numeric check: origin, world scale, height scale, and one line per tile. A shot from the same teleport is the visual check. Tile boundaries and the partial last column of a `.hght` are the known cases in [formats.md](../formats.md).

### 5. Scale, one known case at a time

The first scale case is the Kokiri Emerald, `Obj_TimeTemple_SpiritStone_A_01` (`AtO_SpiritStone_Forest` in the After the Ocarina project), if opening it shows a scale mismatch. If it does not, the next case is the shutter door. The cases already written down, each its own step:

- Bone flag `0x800000`, segment-scale compensation, on a mesh that uses it.
- Bone flag `0x1000`, Euler XYZ in radians, composed `Rz · Ry · Rx`. `Obj_HrlCastle_SwitchShutter_A_01` is the tilted door in [models.md](../models.md).
- A recipe actor's `y_offset`, so the gizmo Y is not applied twice.
- A land actor scaled to 8 m or more stays a volume outline and does not lose a real mesh when it has one.

### 6. Editor UX that is still missing

Fly, orbit, pan, dock, undo, and F to frame are already in [controls.md](../controls.md). This step adds what that list does not have, matching the in-game editor's feel without using its renderer:

- The scale gizmo is the Scale button, beside Move (G) and Rotate (R). The pressed button names the mode. S stays fly-back.
- A ground grid and a corner axis indicator (X red, Y green, Z blue). The grid is still missing.
- A ground grid and a corner axis indicator (X red, Y green, Z blue).
- Grid snap is separate from ground snap (V). Shift+V toggles it. Move steps are 0.25, 0.5, 1, 5, or 10 metres. Rotate steps are 5, 15, 45, or 90 degrees. Scale snap is optional. L toggles world and local for move and rotate; scale stays local. Grid lines draws the move increment. With both snaps on, the grid sets X and Z and ground snap sets the height.
- F frames the selection and sets the orbit pivot there.

Check: move, rotate, and scale the sign from the gizmos alone, with Grid lines on, then F. No typed numbers. The patch from milestone 1 still updates.

### 7. Add an actor. Record remove as a gap

Add a pot (or any `Pack/Actor` class the dump has) with `content.place` or a placements `add`. Position in the patch is metres. Rotation emitted through `content.place` is degrees. The new hash sets bit 62. After a process start and a teleport, the shot shows the new actor and the original sign is still there. `content.place` adds; it does not replace.

Remove and hide stay in the project and in the view. Export writes no patch and says why: the API has no remove for a banc actor. That gap is not posted upstream.

### 8. Behavior graph, read-only

Select a Kokiri NPC. **Behavior** draws the AINB graph, the schedule rows, and the event-flow hooks, with the dump's node ids. Nothing is written. This is the graph the page already draws. The check is that opening it does not create a `patches/` file or a `content.tree` call.

### 9. Behavior field edits stay in the project

A field change is still a `set field` op in `behaviors/<gyaml>.yaml`, with the node's stable id. Undo uses the same history as a scene edit. Export does not write a replaced AINB. If the milestone can show the edited value in Forge's graph, that is the test. The game does not change until a merge op exists. `content.tree` is only for a tree the mod owns.

### 10. One dialog line, then events as data

Change one existing label through `content.text`. `check` passes. After a process start, the line is visible in the game (a shot is not enough if the line is inside a conversation; the check is then the game's text plus the mod log). Control tags are out of scope. Event flows and schedules stay typed ops in the project and are not exported as whole files. A quests browser and live dialog preview wait until that export is real.

The Rust port below is part of this direction. It is not one of the ten, and it is not waiting until they finish. Baked lighting (`bake0` / `bake1`), an asset browser, a new level, and blueprint phase B (add and connect, still on `glinesbdev/blueprint-nodes-a68c`) stay later than the ten. WebGPU is the default renderer on three r187. `?renderer=webgl` keeps WebGL. The earlier Kokiri comparison, when that WebGPU path was several times slower, is in [performance.md](../performance.md). It does not have retail Kokiri or Hyrule Castle load times. This checkout does not contain those dumps.

### R1. Profile a cold load before any port

`python -m ocarina_forge profile --dump <dump> --model <gyaml> --cprofile out.prof` times one actor, cold then warm, and writes a cProfile of the cold convert. The stage lines split file decompress, BFRES parse, vertex decode, index decode, meshopt or the Python lod fallback, JPEG/PNG/GPU encode, glTF pack, deswizzle, and block decode. A scene walk takes `--limit N` and `--jobs N`. The default is one model at a time and every model. It does not measure browser `dl` or GPU `up`. Those stay on **Profile**. The house profile that picked R2 is in [performance.md](../performance.md).

The command uses a private cache directory unless `--home-cache` is set. A cold `cache=miss` is that directory. The glb key is `models-66`. The private profile cache is `~/.cache/ocarina-forge/profile-tmp` and is deleted on exit unless `--keep-cache` is set. Profile does not change it by itself. [performance.md](../performance.md) has the command and the house numbers. Retail numbers are measured on Bradyn's machine, not in this note.

The candidates, in the order a profile is allowed to reorder them:

- Swizzle and deswizzle, including the Switch 2 order for 16-byte elements. That is the bit-5 / bit-6 swap in [textures.md](../textures.md). Castle brick albedos are the known case: `Land_HrlCastle_Wall_Brick_A_Lighter_Alb` (ASTC 8×5) and `Land_HrlCastle_Wall_Brick_Blend_A_Alb` (ASTC 6×6). 8-byte and 4-byte elements stay on the Tegra X1 address. The formula already in `formats/swizzle.py` is what a port matches. Do not copy a third-party swizzle or encoder.
- BNTX and ASTC decode.
- BFRES mesh conversion.
- zstd, archive, and BYML reading.

A path is a candidate only if its share of the cold wall time is large on at least one of those two scenes. If `dl` and `up` are the cold time, and the Python paths are a small share, this step stops and says so. No port is started to have a port.

### R2. Port the top hot spot, still called from Python

One crate, `native/`, called from Python with PyO3 and maturin. The hot functions take bytes and return bytes. No Python types inside them. That is what lets step two compile the same crate to WebAssembly later. The Python server stays. The frontend stays JavaScript and Three.js. If `ocarina_forge_native` does not import, the Python functions run.

The house profile named BC7 mode 6 first (121.7 s) and ASTC/BC decode plus deswizzle next (about 24 s). Both are in this crate. A full bc7enc or Intel ISPC search was the other encoder. Those kernels are not a wasm32 build, and they would change the blocks. The crate ports this mode 6 instead, so the blocks match. Decode uses `bcdec_rs` for BC7 and `astc-decode` for ASTC, both pure Rust. BC1, BC3, BC4, and BC5 are our own so the 565 expansion matches `texture2ddecoder`. Parity tests compare bytes. The BC7 test also keeps a 40 dB PSNR bar against the Python decode, which is the bar if a later encoder is allowed to differ. On `4afe3f7` the house convert was 32.6 s. What remained was per-pixel Python: the 1024 box limit, the near-magenta scan, the ORM blit, the noise score, BC1 blocks, and one-step downsampling. Those are in the same crate, and the Python functions remain the fallback. PNG IDAT is zlib level 1. On `ba00e7f` the house convert was 6.3 s, and the four Kokiri and castle scenes at `jobs=1` were 537 s of cold convert, 298 s of that in BFRES parse. Vertex and index decode, the attribute min/max, and the glb float pack are in the same crate. The Python loops remain the fallback. A scene open starts the misses on the process pool. On `dcaac45` the house was 5.5 s and the four scenes were 280 s at `jobs=1`. Face normals are in the crate. A process keeps decompressed Tex files so the next model does not inflate them again. On `51e000d` the four scenes were 262.9 s at `jobs=1`. `texread` was 0.5 s, and `other` was still about 100 s. Bind pose, UV seam splits, and bounds now run on packed f64 bytes in the same crate. The glb prefix at that landing was `models-49`, because those converter sources are in the cache stamp. The current prefix is `models-66`. Mode 6 blocks still match. PNG pixels stay the same. Vertex floats, face normals, and the bind pose match the Python loop. A pack that does not name a model is a box and does not log `No model`. Before and after numbers are in [performance.md](../performance.md). Milestone 3 (materials and UVs) waits on the next profile: at `51e000d`, Kokiri Dynamic `other` and Castle Static bfres were each over 20% of that scene.

Where it sits next to the other milestones:

- A texture or swizzle win lands before milestone 3, so the material check is not waiting on the Python deswizzle, and so milestone 3's cache rebuild is the one that records the new time.
- A mesh win lands before milestone 5, for the same reason. Milestones 3 and 4 can proceed on the Python converter.
- A banc-reading win can land with milestone 1. It must not change the patch JSON. Opening the scene and writing the mod are the timings.

### R3. Port the second hot spot, or stop

Decode and deswizzle shipped with R2. On the measured house (`4afe3f7`, 32.6 s) the load memo had already removed the repeated reads, and the time left was the per-pixel loops now in the same crate. BFRES is 1.0 s on that run and is not ported. A full Kokiri cold open is still being measured. Do not port the rest of the list until that profile says something else is still large.

### Step two, kept in mind and not scheduled

The same crate, compiled to WebAssembly, is how parsing and conversion could later move into the browser. The dump folder would be picked with the File System Access API, and the Python server could then go away. That host is not a milestone. The viewer already samples walkable ground from `frontend/wasm/` (`frontend/src/ground-surface.js`); the server still builds the pack and still converts models. R2's byte API is the part that keeps the rest of the option open. Revisit a full move only after a port has a measured gain on Kokiri and on the castle.

Later, not one of these steps: baked lighting, an asset browser, a new level, and blueprint phase B, as above.

## Decisions

November 22, answered by Bradyn:

1. The scene YAML and the other typed files are the source. The mod folder is export output.
2. The mod id is `local/<project-id>`. Save installs into the test game's `mods/local/` folder. The default game is `~/Games/Longshot/mod-test`. `OCARINA_FORGE_GAME` changes it.
3. A full process restart for the first save in an area is acceptable. Size-table headroom is later.
4. New-actor hashes keep Forge's bit 62.
5. The no-prefix placement id and the `0x` hash are the same id. The internal form is `0x` and 16 hex digits.
6. Hide and remove are shown in Forge and warned on export. `files.md` describes `remove`; this slice still does not emit it.
7. After terrain, the Kokiri Emerald scale is first, then the cases already listed.
8. Teleport, then walking out and back, is enough preview for milestone 1.
9. Patch-only saves do not write `src/main.ts`.
10. The After the Ocarina romfs build stays beside the mod exporter.
11. Stop the Rust port when download and GPU upload dominate the cold time. The Python profile does not measure those. Compare it with **Profile** `dl` and `up` on a real open.
