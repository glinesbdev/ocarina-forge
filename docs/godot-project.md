# Godot project generator

`ocarina-forge-godot` reads a dump and writes a Godot 4.7 project. Models, materials, textures, and Hyrule Field are native scenes and resources. The user edits those in Godot. A later exporter will diff that project against a fresh import and write the layout Longshot's modding API expects. This step does not export a mod, and it does not launch the game.

This builds on the viewer in [godot-spike.md](godot-spike.md) (PR #78). The viewer stays. The generated project is the one you edit. `scenes/benchmark.tscn` instances the same fly camera, and `--benchmark` writes `field-loop-160m.json` in the same shape as the viewer.

## What was run, and what was not

Verified on Linux, with the synthetic dump only (no retail assets, no GPU):

- `dotnet test godot/Forge.Import.Tests/Forge.Import.Tests.csproj` — the 20 importer tests.
- `dotnet test godot/Forge.Project.Tests/Forge.Project.Tests.csproj` — the planner, the manifest, and a headless generate when `GODOT` is set.
- Godot 4.7.2 .NET `--headless` wrote `models/Obj_Tree/Obj_Tree.tscn`, `maps/HyruleField.tscn`, a BC7 `textures/Bark_Alb.res`, and `.ocarina/manifest.json`. A second run printed `skipped` and did not rewrite the mesh. `--check` loaded the map and the model.

Not verified, so this does not claim them:

- Windows, an AMD GPU, or any retail dump. There is no dump in this environment.
- Thumbnails in the editor. The files are `PackedScene`, `ArrayMesh`, `StandardMaterial3D`, and `ImageTexture` saved by `ResourceSaver`. Godot's FileSystem dock builds previews when the editor scans them. That scan was not watched here.
- That the generated Hyrule Field matches the game, or the owner's last fly-through. He has to open the project.

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
models/<Model>/meshes/<part>_lodN.res
models/<Model>/materials/<Material>.tres
maps/HyruleField.tscn          terrain plus one instance per placed actor
maps/HyruleField/terrain.res
maps/HyruleField/terrain.tres
maps/HyruleField/terrain.gdshader
maps/HyruleField/occluder.res
scenes/benchmark.tscn          the fly camera, instancing main.tscn
```

An actor is an instance of its model scene, with the banc translate / rotate / scale. Helpers, bounds boxes, and the other omitted shapes are not written. LOD 0 ends at 72 m and LOD 1 begins there, the same ranges as the viewer. The viewer batches repeats with MultiMesh. The project keeps one node per actor so they can be moved.

`.ocarina/manifest.json` is version 1. `files` maps a `res://` path to `kind`, `sources` (romfs paths; a pack member is `archive#member`), and `hash`. The hash is SHA-256 of the importer id `godot-project-1` plus the raw bytes of those files. It is not a hash of the Godot file, so a hand edit of an unchanged output is left in place. A source change rewrites that output. A later exporter can generate a second, untouched project and diff the two. Orphan files from a removed actor are not deleted.

The same writer runs from the CLI and from the **Ocarina Forge** dock (Reimport from dump). Reimport refreshes the addon sources and DLLs, then generates. It does not delete `models/`, `textures/`, or `maps/`.

## Converted

- BFRES meshes, LODs 0 and 1, normals, UVs (V flipped to Godot), and the bind-pose bake the viewer already does.
- `Skeleton3D` from FSKL: name, parent, local translation, quaternion (Euler flag `0x1000` is converted first), scale. The synthetic tree has no FSKL, so its scene has an empty skeleton. A model that has one gets a bone per dictionary entry.
- `AnimationPlayer` with an empty `AnimationLibrary`, so the Animation panel has a player to attach to later.
- `StandardMaterial3D` for opaque, alpha, and scissor materials. Water is one shared unshaded material. Missing albedo uses the same flat colours as the viewer. sRGB BC7 sets `albedo_texture_force_srgb`.
- Textures as `ImageTexture` resources whose image is the compressed blocks (BC7, BC1, BC3, BC4, BC5, RGBA8). ASTC is transcoded to BC7 the same way as the viewer (long edge 1024) because desktop AMD does not sample ASTC. `Image.Compress` needs the Godot editor binary, including `--headless`.
- Terrain as a mesh plus the viewer's blend shader (chosen layer, other layer, blend weight). MaterialAlb layers are separate `.res` files and one `Texture2DArray`. No `.mate` means the flat earth colour, which is what the synthetic field does.
- The editor light rig from the browser viewport (not a measured in-game sun): directional colour `(1, 0.949, 0.839)` energy 2.6 from `(18, 36, 14)`, ambient `(0.557, 0.627, 0.565)` energy 0.55, background `(0.078, 0.106, 0.086)`, ACES exposure 1.05, shadows off.
- Texture search, helper hiding, and plant name suffixes from the viewer. Those readers are unchanged.

## Not converted

- **FSKA and every other animation curve.** No reader in this repo decodes them. Longshot `content.model` is a static model and `Tex/` textures. `has()` has no animation getter. The skeleton is the bind pose only. Left: bone tracks (and material tracks, if a file has them) into the `AnimationLibrary` on the player that is already in the scene.
- Skin weights are not bound. Vertices are baked into model space, the same as the viewer, so a `Skin` would move them a second time. Left: keep the unbaked positions and a `Skin` once curves exist.
- Actor behaviour, exported properties, AI, and events.
- Audio.
- Phive collision. The terrain occluder is a render occluder, not gameplay collision.
- `_a1` overlay, normals, roughness, metal, emission, const-colour tints.
- FSHP flag `0x10`, external GPU buffers, `.bfres.mc`.
- Mod export. The manifest is the source list a diff exporter can use. It does not write `mod.json` or a romfs patch.
- A hardware sRGB view. `albedo_texture_force_srgb` is a material flag. The block itself is uploaded as BPTC.

## Commands

Godot is **4.7.2 stable .NET**. The Windows editor is typically `Godot_v4.7.2-stable_mono_win64.exe`. Set `GODOT` or pass `--godot`. The first generate transcodes MaterialAlb and walks texture archives. The second generate skips outputs whose sources have not changed.

Linux, from the repo:

```bash
export PATH="$HOME/.dotnet:$PATH" DOTNET_ROOT="$HOME/.dotnet" DOTNET_CLI_TELEMETRY_OPTOUT=1
export GODOT=/path/to/Godot_v4.7.2-stable_mono_linux.x86_64
dotnet test godot/Forge.Import.Tests/Forge.Import.Tests.csproj
dotnet test godot/Forge.Project.Tests/Forge.Project.Tests.csproj
dotnet run --project godot/Forge.Cli -- new /path/to/dump /path/to/hyrule-field-godot --godot "$GODOT"
"$GODOT" --path /path/to/hyrule-field-godot -- --benchmark --benchmark-out field-loop-160m.json
```

Windows PowerShell, from the repo:

```powershell
$env:GODOT = "C:\Godot\Godot_v4.7.2-stable_mono_win64.exe"
dotnet test godot\Forge.Import.Tests\Forge.Import.Tests.csproj
dotnet test godot\Forge.Project.Tests\Forge.Project.Tests.csproj
dotnet run --project godot\Forge.Cli -- new D:\dump D:\hyrule-field-godot --godot $env:GODOT
& $env:GODOT --path D:\hyrule-field-godot -- --benchmark --benchmark-out field-loop-160m.json
```

Open `D:\hyrule-field-godot` in the Godot editor to fly the field, or open `scenes/benchmark.tscn`. The dock button **Reimport from dump** runs the same generate. `ocarina-forge-godot reimport <projectDir>` does that from the shell. `ocarina-forge-godot check <projectDir>` loads the map and the first model and prints bone count.

Another banc:

```powershell
dotnet run --project godot\Forge.Cli -- new D:\dump D:\kokiri-godot --banc Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs
```

The map scene is still written to `maps/HyruleField.tscn` in this step. The actors are whatever that banc places. A per-area scene name is a later phase.

Do not commit the generated project. Its `.gitignore` ignores `models/`, `textures/`, `materials/`, `maps/`, and `.ocarina/`. Those files are decoded from the dump.
