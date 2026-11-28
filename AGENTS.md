# Agent rules

These rules apply to every coding agent in this repository (Cursor, Codex, Claude Code, and others). Follow them.

## Project

Ocarina Forge reads a dump the user already owns and writes a Godot 4.7 project. It does not run the game. The web editor, the Python library, and the Rust tooling are the tag `web-editor-final`. Upstream Longshot is `MagicBeanDev/Longshot`.

### Layout

- `godot/Forge.Import` — dump readers. No Godot types.
- `godot/Forge.Project` — the plan that maps those readers onto `res://` paths.
- `godot/Forge.Cli` — `new`, `models`, `reimport`, `check`, and `skin-check`.
- `godot/Forge.Import.Tests` and `godot/Forge.Project.Tests` — synthetic fixtures. They do not need a dump.
- `godot/viewer` — the Godot 4.7 .NET viewer.
- `godot/template` — the addon the generator copies into a project.
- `docs/formats.md` — file formats. `docs/asset-pipeline.md` — dump to scene. `docs/godot-project.md` — generate and reimport.
- `build.sh` and `build.ps1` — restore, build, test, and optional generate.

### Tests

```bash
./build.sh
```

On Windows, `build.ps1`. Godot 4.7 .NET and the .NET 8 SDK are the prerequisites. The importer tests do not launch Godot. The headless generate does, and it needs `GODOT` or `GODOT_BIN`.

## Git and PRs

- Never push to `main`. Push only to your own PR branch. Never force-push or rewrite pushed history. The owner verifies and merges every PR himself.
- Branch names must not start with a username prefix like `name/`. Use a plain descriptive name (for example `godot-project-gen`).
- In the PR body, use `Closes #N` only when the PR fully resolves an issue. Use `Refs #N` when it only partly covers one.
- When picking up an issue, add the `in-progress` label. The GitHub update replaces the whole label set, so keep the existing labels. Remove `in-progress` when the PR merges or work stops.
- Never push to upstream Longshot (`MagicBeanDev/Longshot`).

## Research and docs

- Before reverse-engineering any game or engine behavior or file format (lighting, actors, formats, collision, behavior), consult Longshot's modding API and its docs first: the module list via `has()`, the type definitions, and the spec. Also check this repo's `docs/formats.md`.
- Document everything new learned about the game or its file formats in `docs/formats.md`. The same finding must also be mirrored into the after-the-ocarina repo's `docs/game-formats.md`. Nothing should need to be rediscovered.
- Keep docs up to date with every change: plans, how-to-run, and architecture docs.

## Legal and assets

- Never commit Nintendo trademarks or game assets. Never commit baked, imported, generated, or cached output. Everything is read from the user's own game dump at runtime.
- Keep the credits for Hyblitze, Zirem, NupExplorer (lunixxx37), KaboWabo/Kabosune, and BfresLibrary (KillzXGaming/Syroot). Add any new third-party library with its license. NuxsGit is a reference only, so never copy its code. Never use HookyMod code.

## Testing and claims

- Never launch the actual game. Do not kill or restart the owner's running server or game processes. Tell the owner to.
- Never claim compatibility (specific GPUs such as AMD, Linux, Steam Deck, and others) without verifying it. Say clearly what was run and what is untested. The GitHub workflow on `ubuntu-latest` is a Linux run of the tests and the headless generate. It is not a Steam Deck run and it is not the owner's GPU.
- Do not disturb a dirty working tree or uncommitted changes that are not yours.
- Do not post anything externally (issues, comments, messages) unless asked.
