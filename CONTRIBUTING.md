# Contributing

Thanks for helping with Ocarina Forge. This is enough to run a checkout and open a pull request.

Upcoming work is [ROADMAP.md](ROADMAP.md). It is a plan, not a promise. Open issues labeled [`roadmap`](https://github.com/glinesbdev/ocarina-forge/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap) are the ones to pick up.

## Set up and run

You need Python 3.11 or newer and Node 20 or newer. Rust is needed only when `native/` no longer matches the wasm module committed under `frontend/wasm/`.

On Windows, double-click `build.bat`. On Linux:

```bash
./build.sh
```

Open http://127.0.0.1:47231. The API listens on 127.0.0.1:47232. Ctrl-C stops both. `./build.sh --check` (or `build.bat --check`) only reports prerequisites. `./build.sh --no-browser` skips opening a browser. `./build.sh --rebuild-wasm` rebuilds the wasm module. `./build.sh --bench` runs the wasm benchmark instead of the editor. Details are in [docs/builder.md](docs/builder.md).

The pieces, if you want to run them yourself:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install --prefix frontend
npm run build --prefix frontend
python -m ocarina_forge --host 127.0.0.1 --port 47232
```

In another terminal, from `frontend/`: `npm run preview`. That serves the built editor on port 47231 and proxies `/api`.

The optional native extension is separate from the wasm module. `maturin develop --release --manifest-path native/Cargo.toml` builds it into the active virtualenv. Python keeps working when that extension is absent. On Windows, if `libunwind.dll` is not on `PATH`, set `OCARINA_FORGE_NATIVE_DLL_DIR` to its directory before launching.

`rust-toolchain.toml` pins Rust to 1.83.0. Rebuild the wasm module with `./build.sh --rebuild-wasm` (or `build.bat --rebuild-wasm`) after changing `native/`. That asks before installing the `wasm32-unknown-unknown` target and `wasm-bindgen-cli` at the version in `native/Cargo.lock`.

The first screen asks for a dump folder you already own (the one that contains `romfs`). With no dump on the machine, choose **Use the bundled sample dump**. That sample is synthetic and contains no game files. The builder can save that folder so the next launch does not ask.

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
