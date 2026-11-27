# Godot spike

A native Hyrule Field viewer, to see whether Ocarina Forge should leave the browser. The web editor is unchanged. This folder reads the dump from disk. It does not start a server, and it does not launch the game.

## What was built, and what was not run

Targeted engine: **Godot 4.7.2 stable .NET** (`4.7.2.stable.mono.official.ed1daf0bf`, 18 August 2026). Godot 4.8 was release-candidate 1 at the time, so it was not used. The C# projects target **net8.0**. This machine built them with the **.NET SDK 8.0.428**. `godot/global.json` asks for SDK 8.0.100 and rolls forward to any newer major, so SDK 8, 9, or 10 can build net8.0.

Verified here, on Linux, with no game dump and no GPU:

- `dotnet test godot/Forge.Import.Tests/Forge.Import.Tests.csproj` — 9 tests, synthetic BFRES / BNTX / BYML / SARC / terrain / flight / cache only.
- `dotnet build godot/viewer/OcarinaForge.Viewer.csproj`
- Godot 4.7.2 headless `-- --self-test`. That builds a synthetic dump, loads three `Obj_Tree` placements plus one flat height tile, and quits 0. The log was `field actors 3 instances 7 terrain 1345 bc7 64`.

Not verified, so this spike does not claim them:

- Windows, an AMD GPU, Arch Linux with a real GPU, or Steam Deck.
- A retail Hyrule Field dump. There is no dump in this environment, and none is committed.
- GPU frame time, draw calls, or VRAM on real hardware. Headless Godot uses a dummy renderer, so those counters are not a benchmark.
- The `field-loop-160m` JSON against the browser's ~58 ms / ~860 batches / ~640 MB geometry / ~745 MB BC7. The path and the JSON shape are implemented. The numbers are not.

## Importer

The importers are a class library, `godot/Forge.Import` (`OcarinaForge.Import`), with **no Godot reference**. The viewer turns `ImportedField` into nodes. A future editor can take the same objects and build its own scene.

The readers are a C# port of the Python in `ocarina_forge/formats/` and the scene lookup in `ocarina_forge/models.py`. They are not a new reverse-engineering pass. The layouts they follow are the ones already written in [formats.md](formats.md), and the Longshot notes already written in [longshot-mapping.md](longshot-mapping.md). Longshot's `has()` list includes `content.model` and does not include `gfx.textures` or sampler slot names. Those APIs describe the running game. This spike never starts the game, so it reads files instead.

**BfresLibrary is not linked.** Its published layout is Switch 1 / BFRES version 9. This dump's models are version 10: the FMDL has no reserved qword between the material array (`+0x38`) and the material dictionary (`+0x40`), and FMAT is `0xB0` rather than version 9's `0xA8`. The texture-name count is the byte at FMAT `+0xA2` (Longshot `host/bfres_edit.c`); files written by this repo's `sample_v10_bfres` store the sampler count at `+0xA2` and the texture count at `+0xA3`. The vertex format ids (the big-endian ushort at each attribute, such as `_F32_3` = `0x0518`) are the ones BfresLibrary documents, and the Python reader already credits that. The decoder itself is this port.

Switch 2 block-linear is the other reason not to hand the file to BfresLibrary. For 16-byte blocks (BC7, BC3, BC5, ASTC) bit 5 is y1 and bit 6 is x0. A Switch 1 deswizzle scrambles those blocks. BC1, BC4, and the 4-byte height samples stay on the Tegra X1 order.

**GDExtension in C++ is not clearly better for this spike.** The expensive part of a field view is the GPU, and the import is cached. One .NET runtime is already required by Godot 4.7's C# build. A C++ extension would duplicate the readers or sit behind the same `ImportedField` boundary without making the first viewer faster. The library boundary is there if a later editor wants a native reader.

BC7 is uploaded as compressed blocks (`Image.Format.BptcRgba`). The tests assert an 8×8 texture is 64 bytes, not 256 bytes of RGBA. Godot's `Image.CreateFromData` has no separate sRGB flag. Format `0x2006` (sRGB) and `0x2001` (UNORM) both go up as `BptcRgba`. The browser already treats that distinction as a real sampling difference; this spike does not claim to have solved it.

ASTC terrain `MaterialAlb` is not decoded and not uploaded. AMD desktop GPUs often have no ASTC sampler, and expanding the atlas to RGBA would blow the memory number the comparison cares about. Terrain is a flat colour. The note is on the field.

The first successful load writes a cache **outside the repo**:

- Windows: `%LOCALAPPDATA%\OcarinaForge\godot-cache`
- Linux: `$XDG_CACHE_HOME/ocarina-forge/godot` or `~/.cache/ocarina-forge/godot`
- Override: `--cache <dir>` or `OCARINA_FORGE_GODOT_CACHE`

The file is magic `OFM1`, not a Godot `.res`, so the tests can round-trip it without the engine. The key is a hash of the importer version plus the placement files. The second open skips the BFRES and BNTX parse. The viewer still builds the nodes from that cache.

## What the viewer does

Given the dump (the folder that contains `romfs/`, or `romfs` itself):

1. `--dump <path>` if it was passed.
2. Otherwise `user://ocarina-forge.cfg`, key `dump/path`.
3. Otherwise a folder picker. Headless with no path prints usage and quits 2.

It loads `Banc/HyruleField/HyruleField_Child_Static.bcett.byml` (`.zs` or plain) and the sibling `_Dynamic` file. System, area, effect, and marker actors are not meshed. The rest are looked up in this order: actor pack `ModelInfo` (`ModelProjectName` + `FmdbName`), `Model/<Project>.<Fmdb>.bfres`, then `Model/<Gyaml>.<Gyaml>.bfres`. An exact file that exists is not replaced by another fmdb in the same project. Textures come from the embedded BNTX, then `Tex/<name>.bntx` (case-insensitive file name), then the actor pack. A full romfs walk is not done.

Repeated actors (two or more of the same model) are one `MultiMesh` per part. A unique actor is a `MeshInstance3D`. Occlusion culling is on in `project.godot`. The terrain also contributes an `ArrayOccluder3D` (at most about 20,000 triangles). Godot 4.7 has no runtime HLOD baker for a streamed dump, so distance uses visibility ranges on the game's own LODs:

- a shape with a far LOD: LOD 0 ends near 80 m, LOD 1 begins near 64 m and ends at the far plane
- a shape with no far mesh: under 1.2 m drops past 56 m, under 3 m past 96 m, under 8 m past 180 m, larger shapes stay to the far plane
- terrain is not distance-culled
- camera far is 8000 m
- shadows are off

The free-fly camera is right-mouse look, WASD, Q/E or Space for vertical, Shift for faster, Esc to release the mouse. The overlay shows frame time, Godot's measured render CPU, GPU time when the renderer reports it, draw calls, object count, instance count, VRAM, texture memory, geometry bytes, and BC7 bytes.

`--benchmark` flies `field-loop-160m` from `frontend/src/field-benchmark.js`: eye y = 190, look y = 150, 48 m ahead, 90 m/s, the 160 m loop back to the origin, stepping 1/60 s rather than the real frame delta. The JSON is version 1, scene `hyrule-field-child`, path `field-loop-160m`. `summary` has avg and p95 with the same percentile (`ceil(p/100*n)-1`). Browser-only fields (`submitMs`, `writeTex`, heap, and the rest) are left out. `batches` is the draw-call count, because Godot does not report three.js batch objects. `cpuMs` is measured render CPU plus frame setup. `gpuMs` is omitted when the renderer returns 0.

`--self-test` builds a synthetic dump under the temp directory, loads it, and quits 0. It does not need a dump or a GPU.

## Build and run

Install the .NET SDK 8 or newer, and the **.NET** build of Godot 4.7.2, not the standard build. The standard build cannot run this project.

The Linux zip used here is `Godot_v4.7.2-stable_mono_linux_x86_64.zip`. The binary inside is `Godot_v4.7.2-stable_mono_linux.x86_64`. The matching Windows asset is `Godot_v4.7.2-stable_mono_win64.zip`. The executable name follows that archive (`Godot_v4.7.2-stable_mono_win64.exe`); if the zip names it differently, use the exe next to the `GodotSharp` folder.

From the repo, with `dotnet` on `PATH`:

```bash
dotnet test godot/Forge.Import.Tests/Forge.Import.Tests.csproj
dotnet build godot/viewer/OcarinaForge.Viewer.csproj
```

Linux, after the build:

```bash
/path/to/Godot_v4.7.2-stable_mono_linux.x86_64 --headless --path godot/viewer -- --self-test
/path/to/Godot_v4.7.2-stable_mono_linux.x86_64 --path godot/viewer -- --dump "$HOME/dump" --benchmark --benchmark-out field-loop-160m.json
```

Windows, from the repo, in PowerShell:

```powershell
dotnet test godot\Forge.Import.Tests\Forge.Import.Tests.csproj
dotnet build godot\viewer\OcarinaForge.Viewer.csproj
& "C:\Godot\Godot_v4.7.2-stable_mono_win64.exe" --path godot\viewer -- --dump "D:\dump" --benchmark --benchmark-out field-loop-160m.json
```

`D:\dump` is the folder that contains `romfs\`, or the `romfs` folder itself. The first launch with no `--dump` and no saved path opens a folder picker and stores the choice. Later launches reuse it. `--cache <dir>` moves the import cache.

Opening the project from the Godot project manager also builds the C# assembly. A headless run before that build has nothing to instantiate. Build with `dotnet build` first, or open the project in the editor once.

The benchmark writes `field-loop-160m.json` in the current directory unless `--benchmark-out` names another file, and it prints the same JSON. Quit is automatic when the loop ends.

## Known gaps

- Retail field numbers are not measured here.
- Terrain albedo is a flat colour. `MaterialAlb` / `.mate` are not decoded.
- `_a1` is remembered on the part and is not bound. The viewer samples `_a0` only.
- Const-colour shader params are not applied.
- A shape that fails to decode records a note and stops the rest of that model. The cursor would no longer land on a shape header.
- Flag `0x08` (vertex buffer past the end of the file) fails that actor with a clear error. `.bfres.mc` streams are not read.
- Skinned meshlets (skin count other than 0) are skipped.
- The `_sub` terrain overlay is left off. A sub scene is not a finer copy of the walkable floor.
- Physics and Phive collision are not built. The spike is a viewer.
- The sun is one `DirectionalLight3D`. It is not the game's lighting.
- Texture lookup does not scan the whole romfs when `Tex/` misses.
- Godot 4.7 visibility ranges stand in for HLOD. There is no generated HLOD mesh.
