# Contributing

Thanks for helping with Ocarina Forge. This is enough to run a checkout and open a pull request.

The project is the Godot 4.7 generator. Build and test with `./build.sh` or `build.ps1`, as [README.md](README.md) describes. The web editor these older notes used to launch is the tag `web-editor-final`.

Upcoming work is [docs/asset-pipeline.md](docs/asset-pipeline.md) and [docs/godot-project.md](docs/godot-project.md). [ROADMAP.md](ROADMAP.md) describes the archived editor.

## Tests

`./build.sh` restores, builds, and runs the .NET tests. With Godot 4.x .NET on `GODOT_BIN` or `--godot`, it also runs the viewer self-test and the headless generate. The fixtures are synthetic and do not need a game dump.

## Pull requests

Open pull requests against `main`. Name the branch for the change (`godot-project-gen`), with no `username/` prefix.

## Game files

Contributions must never include game files, dumps, models, textures, or Nintendo text. Readers supply their own dump. This repository does not ship that data, and a pull request that adds it will not be merged. Tests stay on synthetic fixtures.

## Docs

When a change alters behavior, or what we know about a file format, update the matching note in [docs/](docs/). Format rules live in [docs/formats.md](docs/formats.md). The dump-to-scene path is [docs/asset-pipeline.md](docs/asset-pipeline.md).

## Credits

Thanks to Hyblitze, Zirem, NupExplorer (lunixxx37), KaboWabo/Kabosune, and BfresLibrary (KillzXGaming/Syroot).
