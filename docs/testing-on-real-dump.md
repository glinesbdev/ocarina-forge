# Checking Bancwright on a real dump

The copy of Bancwright that was built here never saw a game dump. The resource-size rule now matches After the Ocarina's `docs/game-formats.md`, which was verified in game. Synthetic fixtures cover the containers. This list is what to confirm on your machine, with `~/Games/Longshot/rc/v3.0.0`. The file-by-file check against After the Ocarina's own build is [equivalence.md](equivalence.md).

Do this on a copy of the dump if you want a safety net. Bancwright is written to only read the dump and to only write under `mods/<project name>/`. Watch the build log anyway.

## Setup

1. `./scripts/dev.sh`, open http://127.0.0.1:47231.
2. Set the dump to `~/Games/Longshot/rc/v3.0.0` (the folder that contains `romfs`, not `romfs` itself). Both forms are accepted.
3. The scene list should fill from `romfs/Banc/<Area>/*.bcett.byml.zs` and should not list `GameBancParam` files.
4. Create a project whose name does not start with `_` or `-`. Confirm a name like `_draft` is rejected.

## A scene you know

5. Open a small scene and a large one. Actors should sit on the ground plane, Y up, at roughly the positions you expect from the game. A town NPC should be gold, an enemy red, a field object teal.
6. Select an actor. The instance id should match `Hash` if you open the same file with the community BYML tools. `Phive.Placement.ID` should be the same number.
7. Move it by a metre, undo, and confirm the project YAML lost that actor key when the move was undone all the way. Redo should put it back.
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
19. Click an NPC who stands inside a large Area or a `VolumeFogArea`. The NPC should be selected, not the volume, and the volume must not paint a fill over the trees. Volumes (Area, Volume, Fog, Trigger, Sensor, Collision, and any placeholder 8 metres or larger) are neutral edge outlines. The faint fill appears only while that volume is selected, and it does not write depth. Click an edge, or empty space inside the outline, to select it. Alt-click, or click the same spot again, cycles through whatever the ray hit. **Areas** and **System** in the stage bar are Show, Lock (visible, not pickable, no gizmo), or Hide. The outliner has a **Volumes** chip that hides every outline, and a chip per category (NPC, Object, Area, …). The static map is not pickable. On Kokiri the map file is the untextured `KokiriForest_01` stand-in: it stays hidden, and **Ground** draws a neutral grid at that mesh’s height instead of the green sheet. A scene whose map has an albedo shows that mesh with Ground on. Actors stay clickable while a textured map is still loading.
20. Kokiri Forest child should not cover the actors with a green sheet. That sheet was `Model/KokiriForest_01.KokiriForest_01.bfres.zs`, the untextured terrain proxy (reason `terrain proxy`). **Ground** off is the default for it. Turn Ground on and the view should show a neutral grid, not the green mesh. A missing map model is silent: actors stay where they are, with no extra box. A map that has an albedo still draws, with Ground on by default.
21. Converted glTF is under `~/.cache/bancwright/models` (or `$BANCWRIGHT_CACHE`). It must not appear in the repo. The first open of Kokiri child converts in several processes and streams the models nearest the camera first; the progress chip stays up and the view can be orbited while the rest load. Quit and reopen the same scene: the second open should be well under half a minute, from that cache, without decoding textures again. The first successful resolve is what creates that folder. Water should be a translucent teal sheet (or a real albedo, still translucent and not writing depth), not a cream plane, a dark moiré patch, or a pale green slab.
22. From the repo, with the venv active: `.venv/bin/python scripts/check_models.py ~/Games/Longshot/rc/v3.0.0 Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs`. The summary should count meshes, `Obj_LiftRock_A_01` should name `Model/Obj_Rock_A.Obj_LiftRock_A_01.bfres.zs`, and the terrain line should name `KokiriForest_01`. Paste that output if a scene is still boxes.
23. Open the After the Ocarina project and Kokiri Forest child. `AtO_SpiritStone_Forest` (`Obj_TimeTemple_SpiritStone_A_01`) should stand near the Deku Tree, around (84, 120.3, -27), with a violet tint and the label “added by build, flag-gated”. Drag it. `scenes/m6-stones.yaml` should gain the new `translate` and keep its comments and the fire and water stones. Undo puts the stone back. The Changes tab lists the move and Revert restores that field. Hide or Delete asks, then removes that list entry. Saria is not a ghost: her file does not place an actor.
24. The dot on a map name has a tooltip with the counts (moved, hidden, logic, text, and the rest). Open **Changes**, next to Properties. Each row is one edit, with the old value, the new value, and the YAML file. Click a row to frame that actor. Revert puts back just that part of an actor diff. **All map changes** in the project panel lists every map. Advanced patches are not reverted from this list; they still build as before.
25. What this build does not do, so do not file those as regressions yet: skeletal animation (bind pose only), anything but the base color (no splat blend, no normal map), `.bfres.mc` mesh-codec streams, FSHP flag `0x10` (doors, some fences, `House_Outside_C_00`, ivy — a box the size of the shape bounds, reason `geometry format not decoded (FSHP flag 0x10)`), and a BFRES whose GPU buffer is an external section past the end of the file. Those stay boxes, and the properties panel should say why. Upside-down or scrambled textures are possible; the viewer does not flip V, and a Switch 2 GOB whose byte order differs from the Tegra X1 layout will look scrambled rather than flat grey. A missing or failed albedo is a flat colour, not magenta. Foliage cards should be cutouts, not solid quads, when the material asks for alpha test. A missing `Tex/` file stays flat.
26. Kokiri Forest child Static should also draw the Dynamic banc: houses under the NPCs, dimmed, with “with Dynamic” in the scene name. **Companion banc** turns that overlay off. **Pick companions** turns picking off without hiding them. Selecting a house and moving it writes `KokiriForest_Child_Dynamic`, not the Static file. The NPCs stay in Static. Static alone, with the overlay off, still looks like the characters are floating; that is the two files, not a bad transform. `Land_Kokiri_FieldProp_LostWoodGate_A_01` and every other undecoded or missing model is an outline with a faint fill, not a solid green box. Area volumes are outlines only until you select one. **Stats** prints fps and draw calls. With Ground off, a wide view of Kokiri child Static+Dynamic should be well under the previous 3513 draws and 4.4 million triangles (the last measured frame was 32 fps). Repeated actors that share an albedo should collapse toward one draw per material. Orbit a moment and stop: the view should stop redrawing, and the fps line holds still. Move a house in the Dynamic banc and confirm the edit still lands in `KokiriForest_Child_Dynamic`, not Static.
27. After pulling this build, delete `~/.cache/bancwright/models` (or leave it; the cache key is `models-9`, so older glbs are not reused) and run:

```
.venv/bin/python scripts/check_models.py /path/to/Longshot Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs
```

The summary line counts `shapes textured` and `flat fallback`. A material line says `textured` or `flat`, then the alpha mode (`opaque`, `mask 0.50`, or `blend`) and `both` or `front`. A flat material names its shapes under `flat fallback`. A water stand-in says `water stand-in` and the reason (no albedo, a normal map, a pale sheet, or a swizzle that still looks noisy). The next lines list every sampler (`_a0=Leaf_Alb, _n0=Leaf_Nrm`), the albedo that was used, and `decoded` with a format and a size. A family fallback or a corrected block height is in the note. A part that stays flat says `not decoded` and the reason (no `Tex/` match, an unsupported format such as an unknown ASTC id, a magenta placeholder, or only non-color samplers). If it says `no Tex file matched`, the script lists the closest files in `Tex/`. Paste that block for a tree that is still grey. A Deku tree should list several materials, not one, and a foliage material should say `mask`. `Land_Kokiri_House_Outside_C_00` should be a box with `geometry format not decoded (FSHP flag 0x10)`, not the inside of the house.

## If it fails

- “No game dump” or “no Banc folder”: the path is not the Longshot folder that contains `romfs`.
- “no resource size table”: `System/Resource/*.rsizetable.zs` is missing. Export will refuse to install. Do not copy a table from somewhere else into the repo.
- “dictionary” errors: `Pack/ZsDic.pack.zs` is missing or the frame’s dict id is not in the pack. The log names the file.
- A scene that is in the project but missing from the dump is listed separately and will not open.
- A format 1 project stores dialog, event, and table YAML and does not export them. The log says that on purpose. An `engine: after-ocarina` project builds those files. See [equivalence.md](equivalence.md).
