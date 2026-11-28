# Asset pipeline

This is the path from a dump the user already owns to a Godot 4.7 project. Mod export is a later phase. This page is the map of what the generator writes today, what is fragile, and what a later importer of behaviours, collision, audio, or new assets has to add. Format layouts are in [formats.md](formats.md). The generate commands are in [godot-project.md](godot-project.md).

The dump root is the folder that contains `romfs/`, or `romfs` itself. Nothing from that dump is committed.

## What one generate writes

`ocarina-forge-godot new <dump> <project>` copies the addon, then Godot headless runs `--generate`. A second run rewrites an output only when the importer id or the raw source bytes change. The manifest hash is SHA-256 of the importer id plus those bytes. It is not a hash of the Godot file.

| Output | Source |
| --- | --- |
| `models/<Model>/<Model>.tscn` | One BFRES model: meshes, materials, skeleton, skin, animation player |
| `models/<Model>/animations.res` | FSKA clips bound to that skeleton |
| `models/<Model>/meshes/<part>_lodN.res` | LOD 0 and LOD 1 |
| `models/<Model>/materials/<Material>.tres` | One `StandardMaterial3D` per material |
| `textures/<name>.res` | A compressed image, shared by name |
| `maps/HyruleField.tscn` | Terrain plus one node per placed actor |
| `maps/HyruleField/layer_N.png` | One RGBA8 layer of the terrain albedo array |
| `.ocarina/manifest.json` | Each output, its romfs sources, and the hash |

The viewer in `godot/viewer` uses the same readers and draws a baked field. It does not write skin weights or play clips. The project does.

## Models

`ModelCatalog` walks loose `romfs/Model/**/*.bfres` (and `.zs`) and BFRES members of `Pack/Actor/*.pack`. It skips `.anim`, `.Skeleton`, and `.Tex`. `ModelLocator` is the placement path: a gyaml's model info names `Project.Fmdb`, then the same files.

`BfresReader` reads version-10 FMDL. Vertex positions, normals, UVs (V flipped), and skin weights become Godot arrays. Actor triangles are reversed so Godot's clockwise front face keeps the BFRES normals. A part with no UVs, a span of 8 m or more, and a thickness of at most `max(1 m, 4% of the span)` is a flat slab and is omitted. A model whose every part has no UVs and no texture name, and whose largest part spans 8 m or more, is the terrain proxy and is omitted. The omit line names the gyaml, the shape, and the material.

Packed meshlets (FSHP flag `0x10`) are decoded for skin count 0. Any other skin count on a meshlet is skipped. External GPU buffers and `.bfres.mc` are not read.

## Materials and textures

Each material becomes a `StandardMaterial3D` (opaque, alpha, or scissor). Water is one shared unshaded material. A missing albedo uses the same flat colour as the viewer. sRGB BC7 sets `albedo_texture_force_srgb`. The block is still uploaded as BPTC. `_a1`, roughness, metal, emission, and const-colour tints are not written.

`TextureLibrary` looks up a name in this order: an exact `Tex/<name>` file, a pack member, a `Model/*.Tex.bfres` companion, then a stem of at least 6 characters that sits inside the other name. At most eight files are opened for that last step. A gyaml ending in `_DropFixed_<digits>`, `_<digits>`, or a single trailing letter (the remainder at least 8 characters) is also looked up without that suffix.

BC7, BC1, BC3, BC4, BC5, and RGBA8 stay compressed. ASTC is decoded on the CPU and transcoded to BC7 (long edge 1024) because desktop AMD does not sample ASTC. `Tex/CmnTex_Rock_HrlField_UmeCliff_Near_A_Alb.bntx.zs` is a loose 1024×1024 BNTX `0x3106` (sRGB ASTC 6×6). The earlier miss count came from rejecting that format after the file had already been found. A Windows headless import after the decode wrote 639 texture resources and 3 missing slots. This checkout has no retail dump, so that 639 / 3 count was not remeasured here. `AstcSixBySixDecodesSrgbAlbedo` is the synthetic check that the decoder accepts the footprint. `materialsUnbound` counts materials whose albedo name is empty. It is not `texturesMissing`.

Terrain does not save a `Texture2DArray`. Godot 4.7 reloads a saved array with null images and draws the magenta checker. Each layer is a PNG, and a tool script on the terrain node builds the array when the scene enters the tree.

## Skeletons, skin, and clips

FSKL becomes a `Skeleton3D`: name, parent, local translation, quaternion, scale. Euler flag `0x1000` is converted first. Bone flag `0x800000` is segment-scale compensation (`T * inverse(parentScale) * R * S`). That composition is folded into the rest locals and into the clip keys, so Godot's rest and the pose use the same basis.

Godot skins with `poseWorld * inverse(restWorld) * vertex`. A wrong bone index is invisible at the bind pose and explodes as soon as a clip moves the bone the index actually names. Vertex `_i0` is a slot. The FSKL matrix list is smooth entries first (u16 at `+0x3A`) and rigid entries after them (u16 at `+0x3C`). FSHP has its own skin-bone table. `SkinBinder` tries the combined palette, the raw index as a bone, the shape table, and the rigid half. It keeps the reading whose posed bounds stay near the rest bounds. Skin 0 and skin 1 store the vertex in one bone's space and are baked into model space when that bone has a rigid-matrix index. A rigid index of -1 means the vertex is already in model space, and baking it again is skipped. Smooth skin (count 2 or more) stays in model space. Eight weights are written when any vertex uses influences 4–7. An index outside the skeleton is clamped to bone 0 and recorded.

`ocarina-forge-godot skin-check <dump>` walks every catalog model, attaches external clips, and prints models whose posed extent grows past three times the rest extent, whose rest vertices sit more than 1.25 m from the bone they name, or whose indices fall outside the skeleton. The extent ratio ignores a pure translation, so a hat that slides without growing does not trip it, and a cutscene that moves `Skl_Root` by many metres does not look like an explosion. The distance check can flag a small accessory on a large skeleton. The command exits 0 either way. The tally is the report. It was not run on a retail catalog in this checkout. `Player_Adult`, `Player_Child`, Epona, and a cow were not opened here. The synthetic stand-ins are `ASmallPoseStaysNearRestAndAWrongIndexBlowsTheBounds`, `ShapeTableBeatsAPaletteThatScalesTheWrongBone`, and `RigidTailBakesABoneLocalVertexOntoThatBone`.

FSKA clips land on an `AnimationPlayer` at 30 frames per second. That rate was not measured on a retail file. Loop follows flags bit 2. Cubic keys are sampled as Hermite and Godot then plays those samples linearly. `AnimationLocator` also opens `Model/<project>.anim.bfres` from the actor's `AnimationRef`, from the model name, from the actor name, and from those names plus `_Animation`. Tracks bind by bone name. Child Epona drops retargeted adult projects and adds `Dm_MelodyGet_Epona_Animal_Horse_Epona_Child`. The retarget scale in the child component is recorded and not applied. `romfs/AS/*.root.asb.zs` is not parsed. `Model/<X>_Animation.Skeleton.bfres` is not opened. `FMAA`, `FSHU`, bone visibility, and shape curves are not read.

One file that contains two models shares the clip objects. The first model's binder rewrites those keys. A second model in the same file sees the keys already compensated.

## Actors and the field

`FieldLoader` reads the child static banc and the dynamic sibling, places each actor, and attaches the model. Helpers, bounds boxes, markers (`HrlField_Note_15`, a name containing `AppearPoint`, `MiniGame_Supporter`), and the omitted slabs are not instanced. LOD 0 ends at 72 m. Shadows are off. The editor light rig is the browser viewport's rig, not a measured in-game sun: directional `(1, 0.949, 0.839)` energy 2.6 from `(18, 36, 14)`, ambient `(0.557, 0.627, 0.565)` energy 0.55, background `(0.078, 0.106, 0.086)`, ACES exposure 1.05.

`models` generates the catalog without a banc. The map scene is still named `maps/HyruleField.tscn` even when `--banc` points at another area.

## Fragile

- The skin-index choice is a score, not a flag in the file. A reading that does not move the sampled bones can win while the mesh still sits on the wrong bone. `skin-check` is the list to read after a reimport. A ratio under 3 and a gap under 1.25 m is a pass of that check, not a look at the mesh.
- Importer ids are `godot-project-6` (manifest) and `godot-spike-7` (viewer cache). A skin or mesh change that forgets to bump the id leaves the previous Godot files in place.
- Playback at 30 fps, linear samples of cubic curves, and the dropped retarget scale will drift from the game on a long clip.
- The stem search opens at most eight files. A shared albedo whose name shares no 6-character stem still misses.
- Terrain size is capped at a long edge of 1024. The in-game textures are larger.

## What is not in this pipeline yet

Actor behaviour, AI, events, schedules, and the ASB clip graph need a reader for the actor-param components beyond `ModelInfo` and `AnimationRef`, and a decision about which of those become Godot nodes. Collision is Phive, and the terrain occluder is a render occluder, not gameplay collision. Audio is unread. A new asset type (a different BFRES version, a buffer that is not in the file, a texture the CPU decoder rejects) needs a format note in [formats.md](formats.md) before a decoder. Mod export, the manifest as a source list for a romfs patch, stays a later phase and is not written by this generate.
