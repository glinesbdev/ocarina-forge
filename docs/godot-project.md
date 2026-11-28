# Godot project generator

`ocarina-forge-godot` reads a dump and writes a Godot 4.7 project. Models, materials, textures, and Hyrule Field are native scenes and resources. The user edits those in Godot. A later exporter will diff that project against a fresh import and write the layout Longshot's modding API expects. This step does not export a mod, and it does not launch the game.

This builds on the viewer in [godot-spike.md](godot-spike.md) (PR #78). The viewer stays. The generated project is the one you edit. `scenes/benchmark.tscn` instances the same fly camera, and `--benchmark` writes `field-loop-160m.json` in the same shape as the viewer.

## What was run, and what was not

The original generator checks ran on Linux with the synthetic dump:

- `dotnet test godot/Forge.Import.Tests/Forge.Import.Tests.csproj` — the importer tests, including a hand-built linear FSKA clip, a `CmnTex` albedo stored under a shorter `.bntx` stem, a graveyard plant resolved from the project file, and a flat untextured slab.
- `dotnet test godot/Forge.Project.Tests/Forge.Project.Tests.csproj` — the planner, the manifest, and a headless generate when `GODOT` is set.
- Godot 4.7.2 .NET `--headless` wrote `models/Obj_Tree/Obj_Tree.tscn`, `maps/HyruleField.tscn`, a BC7 `textures/Bark_Alb.res`, and `.ocarina/manifest.json`. A second run printed `skipped` and did not rewrite the mesh. `--check` loaded the map and the model. The synthetic tree has no FSKA, so the check still prints `check curves not imported`.
- A second headless process saved an 8×8 RGBA8 `Texture2DArray` and the same pixels as a PNG, then loaded both. The array came back with a null image (`probe array` / `image null`). The PNG reloaded (`probe png image ok`) and `CreateFromImages` rebuilt an array from it (`probe bind ok`, 8×8, one layer). That is the editor error `img.is_null()` and `_create_from_images != OK`. Reading the layer back with `GetLayerData` is empty under the headless dummy renderer, so the probe checks the array's width and layer count.

Not verified, so this does not claim them:

- GPU rendering and animation playback in the Windows editor.
- Thumbnails in the editor. The files are `PackedScene`, `ArrayMesh`, `StandardMaterial3D`, and `ImageTexture` saved by `ResourceSaver`. Godot's FileSystem dock builds previews when the editor scans them. That scan was not watched here. The terrain array is rebuilt by the tool script when the scene enters the tree. The headless probe showed a saved `Texture2DArray` reloads empty and a PNG does not. The editor viewport on his machine was not opened.
- That the generated Hyrule Field matches the game, or the owner's last fly-through. He has to open the project.

## Hyrule Field follow-up on Windows

On Windows with Godot 4.7.2 .NET, a fresh headless import wrote 639 texture resources and reported 3 missing texture slots, down from 1,710 in the previous generated project. The loose cliff albedo, `Tex/CmnTex_Rock_HrlField_UmeCliff_Near_A_Alb.bntx.zs`, decoded as sRGB ASTC 6×6 and was linked from the generated cliff material.

The generated map passed `--check` with 4,516 actors. Godot reopened 294 skeletal clips for child Epona, including its model-pose clip, and 16 for Cow. The track paths resolved against the names in each skeleton. The generator exited successfully and logged Godot .NET handle errors while saving the retail map and at shutdown. A separate process reopened the map successfully. Visual playback and a GPU benchmark remain open.

The generator loads standalone FSKA by following `Components.AnimationRef` in the actor pack to `AnimationResources`. It also checks exact model and actor filenames for companion clips. It binds tracks by exact bone name and records the source files in the manifest. The curves are saved in a compressed Godot `AnimationLibrary` resource referenced by the model scene. The generator does not evaluate ASB state machines or apply the game's retarget scale.

Godot decodes ASTC 4×4 and 8×8. Other ASTC footprints use the managed AssetRipper decoder and feed into the same BC7 transcode path.

## Cow and Epona playback

All four Cow mesh resources and all 46 bone definitions matched between the previous project and the first textured import. Both exports lacked mesh-skin bindings for the Cow. The generator now reads the unsigned palette indices and attaches the weighted meshes to their skeletons. Actor triangle winding is converted for Godot while preserving the BFRES normals. Child Epona's body uses the eight-weight surface format.

On Windows, a focused Godot 4.7.2 headless run reopened the regenerated Cow and child Epona scenes, checked their winding and rest-pose vertices, and verified vertex motion after seeking `Def_Normal_Talk_A` and `Appear`. Those clips use the compact FSKA curve layout. The full Field and editor viewport were outside this playback check.

## Separate Epona libraries

Child Epona exports its own model-pose clip and the three child-rig clips from the Melody Get sequence. The adult file named with `IsRetarget: true` in the child's animation component is excluded from this scene. Adult Epona retains its own library. Reimport with importer version 5 replaces the earlier child library.

## Model catalog

The `models` command writes an editable Godot library from a dump, independently of map placements. It discovers loose BFRES containers under `romfs/Model` and BFRES members of actor packs, then reads the mesh entries inside each container. Standalone animation, skeleton and texture companion files are excluded from the model list. When actor ModelInfo names the model, the importer can follow that actor's animation component. The catalog uses the same mesh, material, skin and animation writers as the map generator.

```powershell
dotnet run --project godot/Forge.Cli -- models D:\dump D:\ocarina-models --godot $env:GODOT
```

Omit `--prefix` to scan the whole dump. Add `--prefix Animal_` for a smaller catalog. Open `project.godot` and choose a scene under `models/` in Godot's FileSystem dock. `reimport` retains the catalog's source and filter. It checks the same source manifest and skips current resources. A catalog does not need a banc or a generated map.

The generator writes `.ocarina/model-catalog.json` with the status and source of each model. It records unsupported containers, models without a drawable mesh, missing texture references, and write failures. See the format limitations below for external GPU buffers and unsupported shapes.

Godot 4.7.2 .NET can report `Handle is not initialized` while retrieving cached resources during large imports. Forge now loads each model's texture dependencies outside the resource cache and keeps those references alive while saving the model. If an older catalog process stops, run `reimport` after it exits to pick up the updated addon and reuse the saved checkpoints. [Godot issue #112067](https://github.com/godotengine/godot/issues/112067)

The long line visible in child Epona's cutscene preview connects `Root` to `Skl_Root`. Godot draws it as a skeleton link. The child cutscene moves `Skl_Root` away from the local origin by up to about 13 m while the limbs keep their proportions. The catalog preserves that authored movement.

## Layout

Stable paths, so a later phase can add files without moving these:

```text
project.godot
addons/ocarina_forge/          plugin, writer, and the Forge DLLs
.ocarina/project.json          dump path and banc
.ocarina/manifest.json         each output, its romfs sources, and a source hash
textures/<name>.res            shared compressed textures
materials/water.tres
models/<Model>/<Model>.tscn    Skeleton3D, AnimationPlayer, one mesh node per part
models/<Model>/animations.res   linked AnimationLibrary when the model has clips
models/<Model>/meshes/<part>_lodN.res
models/<Model>/materials/<Material>.tres
maps/HyruleField.tscn          terrain plus one instance per placed actor
maps/HyruleField/terrain.res
maps/HyruleField/terrain.tres
maps/HyruleField/terrain.gdshader
maps/HyruleField/layer_N.png     one RGBA8 layer of the terrain array
maps/HyruleField/occluder.res
scenes/benchmark.tscn          the fly camera, instancing main.tscn
```

An actor is an instance of its model scene, with the banc translate / rotate / scale. Helpers, bounds boxes, and the other omitted shapes are not written. LOD 0 ends at 72 m and LOD 1 begins there, the same ranges as the viewer. The viewer batches repeats with MultiMesh. The project keeps one node per actor so they can be moved.

`.ocarina/manifest.json` is version 1. `files` maps a `res://` path to `kind`, `sources` (romfs paths; a pack member is `archive#member`), and `hash`. The hash is SHA-256 of the importer id `godot-project-6` plus the raw bytes of those files. It is not a hash of the Godot file, so a hand edit of an unchanged output is left in place. A source change, or a project whose manifest has an earlier importer version, rewrites that output. A later exporter can generate a second, untouched project and diff the two. Orphan files from a removed actor are not deleted. The viewer's import cache is a separate id, `godot-spike-7`.

The same writer runs from the CLI and from the **Ocarina Forge** dock (Reimport from dump). Reimport refreshes the addon sources and DLLs, then generates. It does not delete `models/`, `textures/`, or `maps/`.

## Converted

- BFRES meshes, LODs 0 and 1, normals, UVs (V flipped to Godot), and the bind-pose bake the viewer already does.
- `Skeleton3D` from FSKL: name, parent, local translation, quaternion (Euler flag `0x1000` is converted first), scale. The synthetic tree has no FSKL, so its scene has an empty skeleton. A model that has one gets a bone per dictionary entry.
- FSKA clips on that skeleton's `AnimationPlayer`. Each clip is an animation named from the file: `Skeleton:<bone>` tracks for position, rotation, and scale, at 30 frames per second. Loop follows flags bit 2. Euler clips are converted to a quaternion before the key is written. A model with skin weights gets a `Skin` from the rest pose, and the weighted meshes are children of the skeleton. Smooth vertices stay in model space. Skin 0 and skin 1 are bone-local and are baked into model space when the bone has a rigid-matrix index. The index space (palette, shape table, or the rigid half of the palette) is the one whose posed bounds stay near the rest bounds. Bone flag `0x800000` is folded into the rest locals and the clip keys. The synthetic tree has no FSKA, so its library stays empty and the check still says `check curves not imported`. A file that has clips prints `check clips N`, and the scene metadata says the 30 fps rate was not measured.
- `StandardMaterial3D` for opaque, alpha, and scissor materials. Water is one shared unshaded material. Missing albedo uses the same flat colours as the viewer. sRGB BC7 sets `albedo_texture_force_srgb`.
- Textures as `ImageTexture` resources whose image is the compressed blocks (BC7, BC1, BC3, BC4, BC5, RGBA8). ASTC is transcoded to BC7 the same way as the viewer (long edge 1024) because desktop AMD does not sample ASTC. `Image.Compress` needs the Godot editor binary, including `--headless`.
- Terrain as a mesh plus the viewer's blend shader (chosen layer, other layer, blend weight). Each MaterialAlb layer is decompressed to RGBA8, resized to one shared size (long edge at most 1024), and written as `maps/HyruleField/layer_N.png`. The terrain node is a tool script that builds the `Texture2DArray` from those PNGs when the scene enters the tree, in the editor and at play. `ResourceSaver` does not keep the array's images: a saved array reloads with `img.is_null()`, and the editor draws the magenta checker. No `.mate` means the flat earth colour, which is what the synthetic field does.
- The editor light rig from the browser viewport (not a measured in-game sun): directional colour `(1, 0.949, 0.839)` energy 2.6 from `(18, 36, 14)`, ambient `(0.557, 0.627, 0.565)` energy 0.55, background `(0.078, 0.106, 0.086)`, ACES exposure 1.05, shadows off.
- Texture search, helper hiding, and plant name suffixes from the viewer, plus the shorter-stem bundle search, the letter suffix, and the flat-slab hide described in [formats.md](formats.md). The load summary adds `load omitted` and the benchmark JSON adds `shapesOmitted` (and, on the viewer, `omitted` with the shape names).

## Not converted

- **Material, visibility, and shape animation.** FSKA bone curves are read. `FMAA` / `FSHU`, bone visibility, and shape curves are not. The 30 fps playback rate is a guess. Cubic curves are sampled, then Godot interpolates those samples linearly. The viewer still bakes poses and does not play the clips.
- A retail confirmation of the skin-index choice. The binder is checked on a synthetic skeleton (`Player_Adult` calm, `Player_Child` exploding, a shape table, and a rigid-half index). `skin-check` is what walks the dump. It was not run on a retail catalog here.
- Actor behaviour, exported properties, AI, and events.
- Audio.
- Phive collision. The terrain occluder is a render occluder, not gameplay collision.
- `_a1` overlay, normals, roughness, metal, emission, const-colour tints.
- FSHP flag `0x10`, external GPU buffers, `.bfres.mc`.
- Mod export. The manifest is the source list a diff exporter can use. It does not write `mod.json` or a romfs patch.
- A hardware sRGB view. `albedo_texture_force_srgb` is a material flag. The block itself is uploaded as BPTC.

## Commands

Godot is **4.7.2 stable .NET**. The Windows editor is typically `Godot_v4.7.2-stable_mono_win64.exe`. Set `GODOT` or pass `--godot`. The first generate transcodes ASTC albedos and MaterialAlb and walks texture archives. The second generate skips outputs whose sources have not changed.

Linux, from the repo:

```bash
export PATH="$HOME/.dotnet:$PATH" DOTNET_ROOT="$HOME/.dotnet" DOTNET_CLI_TELEMETRY_OPTOUT=1
export GODOT=/path/to/Godot_v4.7.2-stable_mono_linux.x86_64
dotnet test godot/Forge.Import.Tests/Forge.Import.Tests.csproj
dotnet test godot/Forge.Project.Tests/Forge.Project.Tests.csproj
dotnet run --project godot/Forge.Cli -- new /path/to/dump /path/to/hyrule-field-godot --godot "$GODOT"
"$GODOT" --path /path/to/hyrule-field-godot -- --benchmark --benchmark-out field-loop-160m.json
```

Windows PowerShell, from the repo. `reimport` writes into the project folder that already exists. The importer id `godot-project-6` makes the first reimport rewrite every generated output. Use `new` with a fresh directory to keep an edited project. The viewer ignores caches from earlier importer versions. `skin-check` reads the dump and does not write the project. It exits 0 even when a model is flagged, so the report is the output.

```powershell
$env:GODOT = "C:\Godot\Godot_v4.7.2-stable_mono_win64.exe"
dotnet test godot\Forge.Import.Tests\Forge.Import.Tests.csproj
dotnet test godot\Forge.Project.Tests\Forge.Project.Tests.csproj
dotnet run --project godot\Forge.Cli -- skin-check D:\dump
dotnet run --project godot\Forge.Cli -- skin-check D:\dump --prefix Player
dotnet run --project godot\Forge.Cli -- reimport D:\hyrule-field-godot --godot $env:GODOT
& $env:GODOT --path D:\hyrule-field-godot -- --benchmark --benchmark-out field-loop-160m.json
dotnet build godot\viewer\OcarinaForge.Viewer.csproj
& $env:GODOT --path godot\viewer -- --dump "D:\dump" --benchmark --benchmark-out field-loop-160m.json
```

Open `D:\hyrule-field-godot` in the Godot editor to fly the field, or open `scenes/benchmark.tscn`. The terrain node rebuilds the texture array when the scene opens. An animal scene's Animation panel lists the FSKA clips on its `AnimationPlayer`. The dock button **Reimport from dump** runs the same generate. `ocarina-forge-godot reimport <projectDir>` does that from the shell. `ocarina-forge-godot check <projectDir>` loads the map and the first model and prints bone count. A model with clips prints `check clips N`. The viewer's load block adds `shapesOmitted` and `omitted` (the gyaml, shape, and material of each hidden slab or proxy). `texturesMissing` is the count still unresolved after the shorter-stem search.

Another banc:

```powershell
dotnet run --project godot\Forge.Cli -- new D:\dump D:\kokiri-godot --banc Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs
```

The map scene is still written to `maps/HyruleField.tscn` in this step. The actors are whatever that banc places. A per-area scene name is a later phase.

Do not commit the generated project. Its `.gitignore` ignores `models/`, `textures/`, `materials/`, `maps/`, and `.ocarina/`. Those files are decoded from the dump.
