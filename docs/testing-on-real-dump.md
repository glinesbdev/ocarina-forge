# Checking Ocarina Forge on a real dump

For live actor transforms and matched terrain and water captures, see [Validate a Forge scene against Longshot](runtime-scene-validation.md).

The copy of Ocarina Forge that was built here never saw a game dump. The resource-size rule now matches After the Ocarina's `docs/game-formats.md`, which was verified in game. Synthetic fixtures cover the containers. This list is what to confirm on your machine, with `~/Games/Longshot/rc/v3.0.0`. The file-by-file check against After the Ocarina's own build is [equivalence.md](equivalence.md).

Do this on a copy of the dump if you want a safety net. Ocarina Forge is written to only read the dump and to only write under `mods/<project name>/`. Watch the build log anyway.

## Setup

1. `./scripts/dev.sh`, open http://127.0.0.1:47231.
2. Set the dump to `~/Games/Longshot/rc/v3.0.0` (the folder that contains `romfs`, not `romfs` itself). Both forms are accepted.
3. The scene list should fill from `romfs/Banc/<Area>/*.bcett.byml.zs` and should not list `GameBancParam` files.
4. Create a project whose name does not start with `_` or `-`. Confirm a name like `_draft` is rejected.

## A scene you know

5. Open a small scene and a large one. Actors should sit on the ground plane, Y up, at roughly the positions you expect from the game. A town NPC should be gold, an enemy red, a field object teal.
6. Select an actor in the view. The Actors panel scrolls that row to the center when it is off-screen, opens its category if that group is collapsed, and shows a hint when the search hides the row. Clicking the row in the panel selects it and does not scroll. The instance id should match `Hash` if you open the same file with the community BYML tools. `Phive.Placement.ID` should be the same number.
7. Move it by a metre. The other models and the ground stay up while the file saves. Ctrl+Z (or Undo) puts it back immediately, without the forest reloading. Ctrl+Shift+Z or Ctrl+Y puts the move back. **Changes** lists “Move …” under **This session**, latest first, and a redo step after an undo. The project YAML loses that actor key when the move is undone all the way. Repeat for a property, Duplicate, Hide, Delete, and Areas → Lock. Add lands at the camera target; with **Snap** on (V toggles it) the new actor and a sideways drag sit on the heightmap or on a collision volume. A pure Y drag stays at the height you leave it, and the gizmo stays on the model. Ctrl+K finds that actor by name, by class (`Npc_Kokiri`), and by hash; Enter frames it. The same box opens another scene and runs a command such as “Toggle snap” or “Show problems”. **Problems** lists anything wrong with the scene, and the same lines sit under the actor in Properties. Open `KakarikoVillage_Child_Static`, Kokiri Forest, and one dungeon with no edits: the badge should show no errors. Vanilla links are `{Dst, Src, Gyaml: Reference}`; `Dst` is resolved against this banc and the other bancs in the same folder, and `Src` has to be the actor itself. An unedited dangling link is a warning. Flag names such as `HavePlayedEvent.Dm_Kakariko_0050` are checked as the murmur3 of the name against `Hash` fields in `GameData/`, not as strings in the string table, so a vanilla scene has no unknown flags. `EftLoc_` and other packs with no ModelInfo are not missing-model warnings. Copy, JSON, and Markdown share the list, and `python -m ocarina_forge problems Banc/KakarikoVillage/KakarikoVillage_Child_Static.bcett.byml.zs` prints it. A class that is not in `Pack/Actor`, a pack that names a model file the dump does not have, two actors sharing a hash, `Dynamic.IsEnable` stored as text, and a link you edited to a hash that is not in the scene each show up after the edit, without reloading the forest. Clicking a row frames that actor. A dump with no GameDataList shows one warning, not a flag error on every actor. **Project → Hot reload** stays off until you save a mod folder such as `~/Games/Longshot/mods/Ocarina Forge` (not a name starting with `_` or `-`). The next save writes the built files there, and the stage bar says how many. Leave the map and enter it again; the game reads placement on load, so the open map does not change under you. Undo back to an empty diff and re-enter: those files are gone and the dump's placement is used. A file you put in that folder yourself is left alone.
8. Hide a vanilla actor, build, and confirm that hash is absent from the exported Actors array and still present in the project file with `state: hidden`.
9. Add an actor by a real pack name. The new hash should have bit 62 set. Load the mod in Longshot only after the size-table check below.

## Export

10. Build with install and zip. The log should name dictionary **3** for each `.bcett.byml.zs`, and it should say the size table was written.
11. The installed folder is `mods/<name>/romfs`. `BUILD.txt` sits beside `romfs`, not inside it.
12. Diff the exported banc against the dump’s banc with your usual tools. Unchanged actors should match in value. `AuxFlags` and any other hash map should still be present. Translate that was `{X, Y, Z}` should stay a dictionary.
13. Compare the new RESTBL entry with `docs/game-formats.md`. A changed file is `align8(old entry + max(0, growth) + 1024)`. The entry is never lowered, and a missing entry is not invented. A too-small entry is the one that hangs on the load.
14. Boot Longshot with the mod enabled. The edited scene should load, the moved actor should be where you put it, and a hidden actor should be gone. Then quit and confirm the dump’s own `romfs/Banc` files are byte-for-byte unchanged.

## Importer

15. Point **Import** at After the Ocarina’s `src/scenes`. Each file should become `scenes/<Area>/<Scene>.yaml` in the project, with hashes preserved and `op: add` turned into `state: added`.
16. Open one imported scene in the view and confirm the moved actors sit on the vanilla ones you expect. Then build only if the size-table check above agreed with your notes.

## Models and picking

17. Open a scene you know. The progress chip should count models, then disappear. A real actor mesh is `Model/<ModelProjectName>.<FmdbName>.bfres.zs`, named by `Component/ModelInfo` inside the actor pack, with the base color in `Tex/<name>.bntx.zs`. Select one and read **Model** in the properties: a mesh names that source and a triangle count; a box names the paths it tried. `Obj_LiftRock_A_01` should cite `Model/Obj_Rock_A.Obj_LiftRock_A_01.bfres.zs`.
18. The sample dump (Use the bundled sample dump, then Kokiri Forest child static) is the known-good picture of that layout: Fado is a gold pyramid from `Model/Npc_Kokiri_F.Npc_Kokiri_F_01.bfres.zs` with `Tex/Npc_Kokiri_F_01_Alb.bntx.zs`, both trees are green cones from `Model/Obj_Tree.Obj_Tree.bfres.zs`, and a flat green quad is `Model/KokiriForest_01.KokiriForest_01.bfres.zs`. Deku Baba stays a box because its bfres is truncated. Mido, the heart, the sign, the firefly, and StartPos stay boxes because the sample has no model for them. The sample project hides the cucco; turn on Show hidden to see that box.
19. Click an NPC who stands inside a large Area or a `VolumeFogArea`. The NPC should be selected, not the volume, and the volume must not paint a fill over the trees. Volumes (Area, Volume, Fog, Trigger, Sensor, Collision, and any placeholder 8 metres or larger) are neutral edge outlines. The faint fill appears only while that volume is selected, and it does not write depth. Click an edge, or empty space inside the outline, to select it. Alt-click, or click the same spot again, cycles through whatever the ray hit. **Areas** and **System** in the stage bar are Show, Lock (visible, not pickable, no gizmo), or Hide. The outliner has a **Volumes** chip that hides every outline, and a chip per category (NPC, Object, Area, …). The heightmap is not pickable. On a real Kokiri dump **Ground** starts on and the field is the TerrainArc mesh, not the dark sheet under the trees. The sample dump has no TSCB, so its `KokiriForest_01` stand-in stays hidden and Ground draws a neutral grid instead of the green sheet. Actors stay clickable while the field is still loading.
20. Kokiri Forest child should not cover the actors with a green sheet. That sheet was `Model/KokiriForest_01.KokiriForest_01.bfres.zs`, the untextured terrain proxy (reason `terrain proxy`). It is used only when no TerrainArc TSCB exists. **Ground** off is the default for that proxy. Turn Ground on and the view should show a neutral grid on y = 0, not the green mesh and not a grid floating at the proxy’s median height. A real heightmap (reason `terrain heightmap`) is on by default and hides that grid, because the mesh is the ground. Turn Ground off to hide the heightmap. The atlas helpers live outside `main()`, so the terrain shader compiles and the field draws. A TSCB that fails to produce triangles does not fall back to the green mesh. A missing map and a missing TSCB are silent: actors stay where they are, with no extra box.
21. Converted glTF is under `~/.cache/ocarina-forge/models` (or `$OCARINA_FORGE_CACHE`). It must not appear in the repo. The first open of Kokiri child converts in several processes and streams the models nearest the camera first; the progress chip stays up and the view can be orbited while the rest load. Quit and reopen the same scene: the second open should be well under half a minute, from that cache, without decoding textures again. The first successful resolve is what creates that folder. Water should be one faint desaturated sheet (about 20% alpha, depth write off), not two stacked flowmaps and not a cream plane, a dark moiré patch, or a pale green slab. **Water** hides it. **Ground** starts on when the scene has a terrain heightmap, and stays off for the untextured `KokiriForest_01` stand-in. `Mt_TerraMaterial_012` and the other numbered terra materials should show a tiled albedo from that layer of `MaterialAlb`, opaque, not a cream sheet. `Mt_Sand_Kokiri_Cliff` says layer 16 is a guess. Water-volume, bake-shadow, fade-black, and inner-mesh shapes should be gone, not grey slabs.
22. From the repo, with the venv active: `.venv/bin/python scripts/check_models.py ~/Games/Longshot/rc/v3.0.0 Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs`. The summary should count meshes and `Obj_LiftRock_A_01` should name `Model/Obj_Rock_A.Obj_LiftRock_A_01.bfres.zs`. For the field, run `.venv/bin/python scripts/check_terrain.py ~/Games/Longshot/rc/v3.0.0 Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs`. It should name the TSCB, list tiles as drawn or skipped with a reason, and list ground-like flag `0x10` shapes separately. Paste that output if the ground is still missing.
23. Open the After the Ocarina project and Kokiri Forest child. `AtO_SpiritStone_Forest` (`Obj_TimeTemple_SpiritStone_A_01`) should stand near the Deku Tree, around (84, 120.3, -27), with a violet tint and the label “added by build, flag-gated”. Drag it. `scenes/m6-stones.yaml` should gain the new `translate` and keep its comments and the fire and water stones. Undo puts the stone back. The Changes tab lists the move and Revert restores that field. Hide or Delete asks, then removes that list entry. Saria is not a ghost: her file does not place an actor.
24. The dot on a map name has a tooltip with the counts (moved, hidden, logic, text, and the rest). Open **Changes**, next to Properties. **This session** is the undo stack for this sitting (Ctrl+Z). Under it, each project row is one saved edit, with the old value, the new value, and the YAML file. Click a project row to frame that actor. Revert puts back just that part of an actor diff. **All map changes** in the project panel lists every map. Advanced patches are not reverted from this list; they still build as before.
25. What this build does not do, so do not file those as regressions yet: skeletal animation (bind pose only), splat maps, and `bake0` / `bake1` (the bind is `CmnTex_BakeLightDefault`, a placeholder, and the `bake` vertex attribute is not read; a normal, roughness, metal, AO, and `_em0` / `_ec0` are bound, and `_a1` blends over `_a0`), `.bfres.mc` mesh-codec streams, FSHP flag `0x10` (doors, some fences, `House_Outside_C_00`, ivy — a box the size of the shape bounds, reason `geometry format not decoded (FSHP flag 0x10)`), and a BFRES whose GPU buffer is an external section past the end of the file. Those stay boxes, and the properties panel should say why. Upside-down textures are possible; the viewer does not flip V. 16-byte blocks use the Switch 2 GOB order ([textures.md](textures.md)). An 8-byte block that still looks scrambled is a different layout, and `tex-audit` marks it `disagree`. A missing or failed albedo is a flat colour, not magenta. Foliage cards should be cutouts, not solid quads, when the material asks for alpha test. A missing `Tex/` file borrows a one-letter sibling when that file exists, and otherwise uses a rock, vine, or cloth colour when the name says so.
26. Kokiri Forest child Static should also draw the Dynamic banc: houses under the NPCs, dimmed, with “with Dynamic” in the scene name. **Companion banc** turns that overlay off. **Pick companions** turns picking off without hiding them. Selecting a house and moving it writes `KokiriForest_Child_Dynamic`, not the Static file. The NPCs stay in Static. Static alone, with the overlay off, still looks like the characters are floating where a house is missing; the field under them should be the heightmap, not a gap onto the dark sheet. `Land_Kokiri_FieldProp_LostWoodGate_A_01` and every other undecoded or missing model is an outline with a faint fill, not a solid green box. Area volumes are outlines only until you select one. The status label prints the draw count. **Profile** prints fps. With the heightmap on, a wide view of Kokiri child Static+Dynamic should stay at or above 45 fps. The field is one atlas draw of the leaf tiles only (about 2 m between samples near the actors, coarser farther out), with triplanar UVs so cliffs are not smeared, and skirts so neighbouring tiles meet. A coarse tile must not still be drawn on top of a finer one. A magenta line down a trunk is a UV seam sampling a gutter, and that gutter is filled from the neighbouring bark. An `_Fx` or `_Msk` texture is not the bark colour. Ctrl+C on `./scripts/dev.sh` should stop without a KeyboardInterrupt from each worker. Repeated actors that share an albedo should collapse toward one draw per material. Orbit a moment and stop: the view should stop redrawing, and the fps line holds still. Move a house in the Dynamic banc and confirm the edit still lands in `KokiriForest_Child_Dynamic`, not Static.
27. The first launch renames `~/.cache/bancwright` to `~/.cache/ocarina-forge` when the new folder is not there yet. Leave that folder in place. The glb prefix is `models-56` (decoded images stay `bntx-6`), so Hyrule Field and Castle pieces rebuild on open. Mode 6 blocks match the previous encoder. PNG images are zlib level 1. Vertex floats match the Python reader. Brick albedos are 16-byte ASTC in the Switch 2 GOB order; see [textures.md](textures.md). A cliff or mountain that failed with `Cannot read properties of undefined (reading 'extensions')` was a meshopt glb whose moss, orm, and normal overlay maps were cited from extras after the texture slot had been dropped. Those slots are written back. The stone, wood, and slate from the earlier edge fix rebuild with them. Emission maps, the moss blend, and the roughness maps are in that same prefix. Leave the cache folder in place. **Blend mask** draws the overlay weight (white is moss or dirt, black is the base). A terrain mesh without the per-vertex layer attribute logs a console warning and **Tile colors** draws stripes instead of tile hues. **Tile colors** uses that attribute for tile hues, and **Sub terrain** uses it to extract the wireframe overlay. **Sub terrain** starts off. Run:

```
.venv/bin/python scripts/check_models.py /path/to/Longshot Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs
.venv/bin/python scripts/check_terrain.py /path/to/Longshot Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs
.venv/bin/python -m ocarina_forge material-audit --dump /path/to/Longshot --out material-audit.txt
.venv/bin/python -m ocarina_forge tex-debug Land_HrlCastle_Wall_Brick_A_Lighter_Alb --png /tmp/lighter.png --dump /path/to/Longshot
.venv/bin/python -m ocarina_forge tex-debug Land_HrlCastle_Wall_Brick_Blend_A_Alb --png /tmp/blend.png --dump /path/to/Longshot
.venv/bin/python -m ocarina_forge tex-audit --dump /path/to/Longshot
```

`material-audit` prints one line per sampler-role signature, the largest first, then an `unhandled` list (unknown sampler, unused sampler, unparsed assign, missing texture) also largest first. A line that says `splits on attributes` is the same sampler roles divided by the vertex attributes (or by the exact sampler names, the render state, or the name tags). `CmnTex_BakeLightDefault` and other `Default` or `Dummy` textures are placeholders, not missing files. `--json` writes the full split; the default text is the short list. The first run parses every model header; the next run reads the cache.

`check_terrain.py` prints the terrain scene, any `_sub` scene, the origin, the world scale and height scale, then one line per layer (`layer main` and `layer sub`: tiles, drawn, triangles, zeros) and one line per tile: `main` or `sub`, drawn or skipped, the directory the `.hght` came from, the sample step, the MaterialAlb layers, the `.mate` path, and the decoded material histogram (`hist`, counts of byte 0 and byte 1). A skipped tile says whether it was outside the actors, had no `.hght` in its own folder, had only non-finite samples, or was covered by a finer emitted quad of the same layer. `holes` counts non-finite samples, including exact 0s left after the dashed-column repair (those are uncovered, not ground). `zeros` counts exact 0 samples still present after that uncover step and should be 0 on a retail tile. `dropped` counts cells removed because a finer tile of that same layer emitted a quad there. The sub layer must not drop main cells: actors such as Mido should sit on main ground, and sub ground near them is about 13–24 m higher. `--png out.png` writes a 1024² top-down picture of that mesh in unit space -1..1 (x right, z up). Red is main, blue is sub, purple is both, black is uncovered. Kokiri’s main tiles should cover the village floor. Sub tiles (names starting around `15` and `16`, from `KokiriForest_01_sub`) are the overlay, not a replacement, and should not stop at 36 tiles. **Sub terrain** starts unticked. Tick it to inspect the overlay as a wireframe. On Kokiri the separate heightmap is about 13–24 m above the actors. The field is front-side, and the tile skirts stay. A vertical magenta line on the Deku Tree trunk should be gone: the seam no longer samples a coarse mip of a magenta gutter, and an effect or mask texture is not used as the bark colour. The next block lists ground-like actors whose shapes are still FSHP flag `0x10` (not decoded; they are not the field). It also says the actor LOD and the InnerMesh filter do not drop the heightmap, and that `.custom.extm` is not decoded. Paste that block if the ground is still the dark sheet or a flat earth colour.

The summary line counts `shapes textured` and `flat fallback`. A material line says `textured` or `flat`, then the alpha mode (`opaque`, `mask 0.50`, or `blend`) and `both` or `front`. A flat material names its shapes under `flat fallback`. A water stand-in says `water stand-in` and the reason (no albedo, a normal map, a pale sheet, or a swizzle that still looks noisy). The next lines list every sampler (`_a0=Leaf_Alb, _n0=Leaf_Nrm`), the albedo that was used, and `decoded` with a format and a size. A family fallback or a corrected block height is in the note. A part that stays flat says `not decoded` and the reason (no `Tex/` match, an unsupported format such as an unknown ASTC id, a magenta placeholder, or only non-color samplers). If it says `no Tex file matched`, the script lists the closest files in `Tex/`. Paste that block for a tree that is still grey. A Deku tree should list several materials, not one, and a foliage material should say `mask`. `Land_Kokiri_House_Outside_C_00` should be a box with `geometry format not decoded (FSHP flag 0x10)`, not the inside of the house.

## If it fails

- “No game dump” or “no Banc folder”: the path is not the Longshot folder that contains `romfs`.
- “no resource size table”: `System/Resource/*.rsizetable.zs` is missing. Export will refuse to install. Do not copy a table from somewhere else into the repo.
- “dictionary” errors: `Pack/ZsDic.pack.zs` is missing or the frame’s dict id is not in the pack. The log names the file.
- A scene that is in the project but missing from the dump is listed separately and will not open.
- A format 1 project stores dialog, event, and table YAML and does not export them. The log says that on purpose. An `engine: after-ocarina` project builds those files. See [equivalence.md](equivalence.md).

## Height-diff against a Longshot scan

`python -m ocarina_forge height-diff <results.json> --dump <dump> [--out report.json]` compares a height-check storage JSON (from the Longshot `world.ray` mod) to Forge. Ground is the composed surface: height tiles (unwritten 0 is no height) plus the Land_* island, cliff, soil, and hill meshes that `prepare_scene_terrain` also classifies as floors, taking the highest hit under the point. Water is a point-in-surface-mesh test on each WaterPlane / WaterSurface Front or Surface part (Back and Box volumes are ignored) with the actor transform; the highest real surface at that XZ wins, so a 135.9 m pool is not hidden by a 130.78 m lake plane, and a hole in the sheet (the Lake Hylia island) is dry. The viewport draws those same actor meshes, so it does not place water from the oversized surface AABB. The visible terrain fit keeps the earlier ground, floor, and road cutouts and does not bake island or cliff meshes into the GLB (that mesh ran the browser out of memory on Hyrule Field). Viewer Snap queries `/api/ground`, which uses the same composed sampler. To inspect one point:

```
python -m ocarina_forge ground-debug Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs --at=-192.39,506.95 --dump <dump>
python -m ocarina_forge serve --dump <dump>
```

Open the Child Hyrule Field (or Kokiri) scene and toggle **Snap**. Drag the island tree; it should sit on the island mesh near 131.35 m, not on the lakebed heightmap, even while the visible ground is still the lighter heightmap.

## Lighting capture

The capture mod is `ocarina_forge/lighting_capture/` (`mod.json`, `main.js`, and `capture.js`). It follows height-check: a Longshot mod writes `storage.global`, and Forge imports that JSON. It does not copy game files. From the modding spec, `game.state()` returns time of day and not sun, fog, sky, or exposure, so those fields stay null. Extra fields a build actually returns are kept under `observed`.

On 2026-11-25 the static probe on the `modding-with-editor` build logged this `longshot/gfx` surface. Names and types only. Nothing was called:

```json
{"present":true,"staticImport":true,"moduleKeys":["world","effect"],"types":{"world":"object","effect":"function"},"worldType":"object","worldKeys":["line","box","circle","billboard"],"world":{"line":"function","box":"function","circle":"function","billboard":"function"}}
```

Spec revision 4, section 10.1, also names `world.quad`, `world.model`, and a `model` import. Those were not exported here. Section 10.3 describes `effect` as `effect({ fragment, stage, uniforms })`, a screen-effect handle (`set`, `setEnabled`) whose output is drawn and not read back (section 10.4). This build exports `effect` as a function. An earlier run the same day had `has("gfx.shader")` false, and section 7.2 says that function would throw `NotAvailableError` if called. It was not called. The module is overlay drawing plus that function. It has no lighting getters, so the capture does not fill sun, fog, ambient, exposure, sky, or env from it.

The capture sampler on that run tripped the spec's 2 ms/frame script warning (section 7.5). `actors.list` was about 0.9 ms a call. The mod now refreshes that list on a frame that does not write storage, at most once every 600 frames. Reinstall the mod to pick that up. The Forge server does not need a restart for it. The gfx surface above is already recorded, so another probe run is not required for these docs.

On Windows, from the repo, with the game folder you actually run:

Quit Longshot first. The spec's section 7.2 does not provide a dynamic import, and a run of that call logged `TypeError` with the message that `import()` is not available. `--gfx-probe` installs the entry that static-imports `longshot/gfx` (its default export is the frozen namespace). The command prints `main.js` byte size and `static import of longshot/gfx`. The dynamic entry is the 1176-byte file; the probe install must not leave that file in place. The manifest asks for `gfx.world` as well as `state.read`, `actors.read`, and `storage`. A build without the module should omit `--gfx-probe`; that install does not static-import it.

The game keeps grants in `<game>\dev\mods.json`, on the mod entry's `"granted"` array (spec section 12.4 names that field; this path is from the modding-with-editor build). Pre-granting avoids the in-game prompt:

```json
{ "id": "local/lighting-capture", "enabled": true, "granted": ["state.read", "actors.read", "storage", "gfx.world"] }
```

That array is the game's record of what the player allowed. It is not `mod.json`. Changing `mod.json` permissions still needs a restart so the game reads the manifest again.

```bat
.venv\Scripts\python.exe -m ocarina_forge lighting-mod --install --gfx-probe --game E:\sw2\oot\stable\modding-with-editor
```

`--game` overrides the default `%USERPROFILE%\Games\Longshot\mod-test`. The command copies the mod to `<game>\mods\local\lighting-capture\`. Start Longshot, re-enable `local/lighting-capture`, and walk the map. The log is `<game>\logs\mod-run.txt`. A line containing `FORGE_LIGHTING` counts samples. A line `FORGE_LIGHTING gfx` lists the live export names. Quit so storage flushes. A run on 2026-11-25 wrote:

```text
<game>\dev\user\modstore\global\local~lighting-capture.json
```

The file is the storage key map. It does not contain `"mod": "local/lighting-capture"`. Find it with:

```powershell
Get-ChildItem -Recurse -Filter local~lighting-capture.json E:\sw2\oot\stable\modding-with-editor
```

Import the game folder. The command prints the map key it wrote (`KokiriForest_Child` for the Child Kokiri bancs). The files stay in your data folder, not the repo:

```bat
.venv\Scripts\python.exe -m ocarina_forge lighting-import E:\sw2\oot\stable\modding-with-editor
```

That writes `%USERPROFILE%\.local\share\ocarina-forge\lighting\<Map>.json` unless `OCARINA_FORGE_LIGHTING` is set. Restart the Forge server after pulling the editor change, then reload the page. Another import after that does not need a server restart, because each lighting request reads the file again. Reload the page so the open scene fetches it. The line under the stage bar names the reference path, the sample count, and which fields are from the file. **Editor defaults** draws the editor lights for comparison. **Shadows** stays off. **AO** toggles glTF occlusion maps. **Effect gizmos** marks `Eft_`, `EftLoc_`, and fog actors.

To list parameter key names from the dump without decoding unknown payloads:

```bat
.venv\Scripts\python.exe -m ocarina_forge lighting-survey Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs --dump path\to\Longshot
```

The format of the reference file is in [formats.md](formats.md).
