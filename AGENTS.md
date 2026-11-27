# Agent rules

These rules apply to every coding agent in this repository (Cursor, Codex, Claude Code, and others). Follow them.

## Project

Ocarina Forge is a local scene editor for Longshot banc mods of the 2026 Nintendo Switch 2 remake of *The Legend of Zelda: Ocarina of Time* (version 1.1.1). It reads a dump the user already owns and writes a Longshot mod (`patches/<banc>.json`) that can be installed. The editor picture is Forge's Three.js renderer. Forge does not run the game. Upstream Longshot is `MagicBeanDev/Longshot`.

### Layout

- `ocarina_forge/` — Python library and FastAPI server: parsers, validation, and exporters.
- `ocarina_forge/formats/` — BYML, SARC, zstd dictionaries, RESTBL, BFRES, and BNTX.
- `native/` — Rust decode. The browser uses the committed wasm build under `frontend/wasm/`. Python falls back when the optional native extension is absent.
- `frontend/` — Vite + Three.js editor.
- `docs/` — formats, design, and how to run. Format notes live in `docs/formats.md`. Plans are `ROADMAP.md` and `docs/design/mod-editor.md`.
- `vendor/after_ocarina/` — the After the Ocarina build library.
- `examples/` — sample projects. Synthetic only. No game data.
- `tests/` — pytest. Fixtures are synthetic and do not need a dump.

### Tests

With the virtualenv active (`source .venv/bin/activate`):

```bash
python3 -m pytest
cargo test --manifest-path native/Cargo.toml
node --test frontend
```

`./build.sh` on Linux, or `build.bat` on Windows, installs dependencies and opens the editor at http://127.0.0.1:47231 (API on 127.0.0.1:47232). Details are in `docs/builder.md` and `CONTRIBUTING.md`.

## Git and PRs

- Never push to `main`. Push only to your own PR branch. Never force-push or rewrite pushed history. The owner verifies and merges every PR himself.
- Branch names must not start with a username prefix like `name/`. Use a plain descriptive name (for example `tiles-phase1-field-bake`).
- In the PR body, use `Closes #N` only when the PR fully resolves an issue. Use `Refs #N` when it only partly covers one.
- When picking up an issue, add the `in-progress` label. The GitHub update replaces the whole label set, so keep the existing labels. Remove `in-progress` when the PR merges or work stops.
- Never push to upstream Longshot (`MagicBeanDev/Longshot`).

## Research and docs

- Before reverse-engineering any game or engine behavior or file format (lighting, actors, formats, collision, behavior), consult Longshot's modding API and its docs first: the module list via `has()`, the type definitions, and the spec. Also check this repo's `docs/formats.md`.
- Document everything new learned about the game or its file formats (for example using actors from one scene's banc in another, actor dependencies, collision, behavior) in `docs/formats.md`. Note that the same finding must also be mirrored into the after-the-ocarina repo's `docs/game-formats.md`. Nothing should need to be rediscovered.
- Keep docs up to date with every change: plans, how-to-run, and architecture docs.

## Legal and assets

- Never commit Nintendo trademarks or game assets. Never commit baked, imported, generated, or cached output. Everything is read from the user's own game dump at runtime.
- Keep the credits for Hyblitze, Zirem, NupExplorer (lunixxx37), KaboWabo/Kabosune, and BfresLibrary (KillzXGaming/Syroot). Add any new third-party library with its license. NuxsGit is a reference only, so never copy its code. Never use HookyMod code.

## Testing and claims

- Never launch the actual game. Do not kill or restart the owner's running server or game processes. Tell the owner to.
- Never claim compatibility (WebGPU, specific GPUs such as AMD, Linux, Steam Deck, and others) without verifying it. Say clearly what was run and what is untested.
- Do not disturb a dirty working tree or uncommitted changes that are not yours.
- Do not post anything externally (issues, comments, messages) unless asked.
