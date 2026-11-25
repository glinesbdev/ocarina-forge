# Contributing

Thanks for helping with Ocarina Forge. This is enough to run a checkout and open a pull request.

Upcoming work is [ROADMAP.md](ROADMAP.md). It is a plan, not a promise. Open issues labeled [`roadmap`](https://github.com/glinesbdev/ocarina-forge/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap) are the ones to pick up.

## Set up and run

You need Python 3.11 or newer, Node 20 or newer, and a Rust toolchain with Cargo.

On Linux, the dev script creates the virtualenv, installs Python and frontend dependencies, builds the native extension, and starts the editor:

```bash
./scripts/dev.sh
```

Open http://127.0.0.1:47231. The API listens on 127.0.0.1:47232. Ctrl-C stops both.

On Windows:

```bat
scripts\dev.bat
```

That script does the same setup. It looks for `libunwind.dll` on `PATH` so the native extension can load. If the DLL sits somewhere else, set `OCARINA_FORGE_NATIVE_DLL_DIR` before you launch.

The pieces, if you want to run them yourself:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install --prefix frontend
pip install "maturin>=1.7,<2"
maturin develop --release --manifest-path native/Cargo.toml
```

`maturin develop` builds `native/` into the active virtualenv. The dev scripts do that before the server starts. Then start the API with `python -m ocarina_forge` and the editor with `npm run dev` inside `frontend/` (Vite on port 47231).

The WebAssembly build of the same crate is separate. On Windows, `scripts\build-wasm.bat` compiles `native/` for `wasm32-unknown-unknown` (`--no-default-features --features wasm`) and runs `wasm-bindgen` into `frontend/wasm`. The same two commands work on other systems once the wasm target and `wasm-bindgen` are installed. `scripts\dev-wasm.bat` starts the editor on ports 47241 and 47242 without rebuilding the virtualenv. A wasm module is already committed under `frontend/wasm/`, so rebuild it only when the Rust changes.

The first screen asks for a dump folder you already own (the one that contains `romfs`). With no dump on the machine, choose **Use the bundled sample dump**. That sample is synthetic and has no Nintendo data.

## Tests

With the virtualenv active:

```bash
python3 -m pytest
```

Tests build synthetic fixtures and do not need a game dump. A checklist for a real dump is in [docs/testing-on-real-dump.md](docs/testing-on-real-dump.md).

Native unit tests:

```bash
cargo test --manifest-path native/Cargo.toml
```

Frontend tests use Node's test runner:

```bash
node --test frontend
```

## Pull requests

Open pull requests against `main`. Name the branch for the change (`fix-terrain-cutout`, `docs-bntx-notes`), with no `username/` prefix.

## Game files

Contributions must never include game files, dumps, models, textures, or Nintendo text. Readers supply their own dump. This repository does not ship that data, and a pull request that adds it will not be merged. Tests and the sample project stay on synthetic fixtures.

## Docs

When a change alters behavior, or what we know about a file format, update the matching note in [docs/](docs/). Format rules live in [docs/formats.md](docs/formats.md), textures in [docs/textures.md](docs/textures.md), models in [docs/models.md](docs/models.md), and the editor plan in [docs/design/mod-editor.md](docs/design/mod-editor.md). The contributor roadmap is [ROADMAP.md](ROADMAP.md).

## Credits

Thanks to Hyblitze and Zirem for the OoT remake research and tooling this project builds on.
