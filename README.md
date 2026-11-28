# Ocarina Forge

Ocarina Forge reads a dump you already own and writes a Godot 4.7 project: meshes, materials, textures, skeletons, skin, animation clips, and a Hyrule Field scene. It does not run the game, and this repository must not contain game files, models, textures, or Nintendo text.

The web editor, the Python library, and the Rust tooling are the tag `web-editor-final`. This branch does not contain them.

Godot is **4.7.2 stable .NET**, and the SDK is **.NET 8**. `./build.sh` on Linux, or `build.ps1` on Windows, restores, builds, and tests. Pass a dump to generate a project. The dump is the folder that contains `romfs/`, or the `romfs` folder itself.

```bash
./build.sh
./build.sh --godot /path/to/Godot_v4.7.2-stable_mono_linux.x86_64 --dump /path/to/dump --out /path/to/project
```

```powershell
.\build.ps1
.\build.ps1 -Godot C:\Godot\Godot_v4.7.2-stable_mono_win64.exe -Dump E:\path\romfs -Out C:\path\project
```

`GODOT_BIN` or `GODOT` names the editor when the flag is omitted. If the SDK or the .NET editor is missing, the script prints that and a download link and stops. Without a dump, a missing Godot still runs the restore, the build, and the tests that do not launch Godot, and it says the headless steps were skipped.

The GitHub workflow runs the importer tests, the project planner tests, the viewer build, the viewer self-test, and a headless generate on `ubuntu-latest`. That is the Linux run this repo records. It is not a Steam Deck run, and it is not a run on a Windows GPU.

Generate, reimport, and the field scene are in [docs/godot-project.md](docs/godot-project.md). The path from the dump to those files, and what is still unread, is [docs/asset-pipeline.md](docs/asset-pipeline.md). Format notes are [docs/formats.md](docs/formats.md).

After a generate, `skin-check` walks every skinned model and prints the ones whose posed bounds blow up or whose bone indices fall outside the skeleton:

```powershell
dotnet run --project godot\Forge.Cli -- skin-check D:\dump
dotnet run --project godot\Forge.Cli -- skin-check D:\dump --prefix Player
```

Do not commit the generated project, `.godot/`, `bin/`, `obj/`, or a log. Those stay on the machine that owns the dump.

Thanks to Hyblitze, Zirem, NupExplorer (lunixxx37), KaboWabo/Kabosune, and BfresLibrary (KillzXGaming/Syroot). Notices are in [godot/NOTICE.md](godot/NOTICE.md).
