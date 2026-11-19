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

17. Open a scene you know. The progress chip should count models, then disappear. Actors whose pack contains a bfres, or whose pack names `Model/<name>.bfres.zs`, should become meshes with a base color. The rest stay labeled boxes. Select one and read **Model** in the properties: a mesh names its source and triangle count; a box names the reason.
18. The sample dump (Use the bundled sample dump, then Kokiri Forest child static) is the known-good picture: Fado is a gold pyramid from the actor pack, both trees are green cones from `Model/Obj_Tree.bfres.zs`, and a flat green quad is the static map. Deku Baba stays a box because its bfres is truncated. Mido, the heart, the sign, the firefly, and StartPos stay boxes because the sample has no model for them. The sample project hides the cucco; turn on Show hidden to see that box. On a real dump those actors should become meshes when the pack or `Model/` file is a readable BFRES.
19. Click an NPC who stands inside a large Area. The NPC should be selected, not the Area. The Area is a faint box. Click its edge, or click empty space inside it, to select it. Alt-click, or click the same spot again, cycles through whatever the ray hit. **Areas** and **System** in the stage bar are Show, Lock (visible, not pickable, no gizmo), or Hide. A land actor scaled to 8 metres or more is also a volume, so a ground slab does not swallow clicks; the toggles do not hide Land.
20. If `Model/<Scene>.bfres.zs` (or `Model/<Area>.bfres.zs`, `Terrain/`, or `Map/`) exists, it is drawn as static context and is not pickable. A missing map model is silent: actors stay where they are, with no extra box.
21. Converted glTF is under `~/.cache/bancwright/models` (or `$BANCWRIGHT_CACHE`). It must not appear in the repo. Quit and reopen the same scene: the second open should be immediate, from that cache.
22. What this build does not do, so do not file those as regressions yet: skeletal animation (bind pose only), anything but the base color, `.bfres.mc` mesh-codec streams, and a BFRES whose GPU buffer is an external section past the end of the file. Those stay boxes, and the properties panel should say why. Upside-down textures are possible; the viewer does not flip V.

## If it fails

- “No game dump” or “no Banc folder”: the path is not the Longshot folder that contains `romfs`.
- “no resource size table”: `System/Resource/*.rsizetable.zs` is missing. Export will refuse to install. Do not copy a table from somewhere else into the repo.
- “dictionary” errors: `Pack/ZsDic.pack.zs` is missing or the frame’s dict id is not in the pack. The log names the file.
- A scene that is in the project but missing from the dump is listed separately and will not open.
- A format 1 project stores dialog, event, and table YAML and does not export them. The log says that on purpose. An `engine: after-ocarina` project builds those files. See [equivalence.md](equivalence.md).
