# One-click builder

`build.bat` and `build.sh` set Forge up, build what changed, and open the editor. They are the way to start a checkout. Rust is not part of that unless the wasm module committed under `frontend/wasm/` no longer matches `native/`.

Windows: double-click `build.bat`. It runs `scripts/builder.ps1` with `-ExecutionPolicy Bypass`.

Linux:

```bash
./build.sh
```

Both scripts install missing tools only after a yes. They then run `python -m ocarina_forge.builder`, which is the shared setup.

## What a run does

1. Check Python 3.11 or newer, the `venv` module, Node 20 or newer, and npm.
2. If `native/` differs from the committed wasm module, also check Rust, a C linker (or the Visual Studio C++ build tools on Windows), the `wasm32-unknown-unknown` target, and `wasm-bindgen-cli`.
3. Create `.venv` when it is missing, and install `requirements.txt` when that file changed.
4. `npm install` in `frontend/` when `node_modules` is missing or the package files changed.
5. Rebuild the wasm module only when it is out of date, or when `--rebuild-wasm` is passed.
6. `npm run build` in `frontend/` only when editor sources changed since the last bundle.
7. Ask once for a game dump if none is saved. Enter saves it. A blank answer leaves the choice to the editor.
8. Start the API on 127.0.0.1:47232 and the editor on http://127.0.0.1:47231, then open a browser. Ctrl+C stops both.

The editor is Vite's preview of `frontend/dist`. It proxies `/api` to the API, and it sends the same cross-origin headers the dev server uses, so the wasm worker pool can start. `OCARINA_FORGE_HOST` and `OCARINA_FORGE_PORT` override the API address. `BANCWRIGHT_HOST` and `BANCWRIGHT_PORT` are still read when the new names are unset.

A later run skips installs and builds that are already current, and it does not ask for the dump again.

## Flags

| Flag | Effect |
| --- | --- |
| `--check` | Print the prerequisite report and exit. Nothing is installed or started. |
| `--no-browser` | Start the editor without opening a browser. |
| `--rebuild-wasm` | Rebuild the wasm module even when the committed copy matches `native/`. |
| `--bench` | Run the wasm benchmark in `tools/wasm-bench/` instead of the editor. |

`--check` exits 0 when the tools you need are present. Rust counts as present when the wasm module is current, even if `rustc` is not installed.

`--bench` copies `frontend/wasm` into `tools/wasm-bench/pkg`. If `tools/wasm-bench/data/hyrule-field-child.bin` is missing, it exports fixtures from the saved dump (`tools/export_wasm_fixture.py --dump …`) and then runs `npm run bench`.

## Where settings and stamps live

The dump folder is written to the editor config: `./ocarina-forge.local.yaml`, or `~/.config/ocarina-forge/config.yaml` when that file is already the one in use. Both are outside git. `OCARINA_FORGE_DUMP` still overrides them. The builder also records that it asked, and a copy of the path, in `.ocarina-forge/builder.json` (the `.ocarina-forge/` directory is gitignored).

Build stamps in that same directory:

| File | Meaning |
| --- | --- |
| `requirements.stamp` | Hash of `requirements.txt` after a successful pip install. |
| `npm.stamp` | Hash of the frontend package files after `npm install`. |
| `frontend.stamp` | Hash of the editor sources after `npm run build`. |
| `wasm.stamp` | Hash of the `native/` sources after a local wasm rebuild. |

`frontend/wasm/source.sha256` is committed next to the wasm module. It is the hash of `native/Cargo.toml`, `native/Cargo.lock`, `native/src/`, and `rust-toolchain.toml` for that module. A checkout matches when that hash, or `.ocarina-forge/wasm.stamp`, equals the current sources and the wasm files are present. A local rebuild updates both stamps.

## Toolchain installs

Nothing is installed until you answer yes. `--check` only prints the command.

### Windows (`build.bat`)

`scripts/builder.ps1` looks up Python with `py -3`, then `python`, then `python3`. It reads the machine and user `PATH` from the registry after an install, and it puts `%USERPROFILE%\.cargo\bin` in front when that folder exists, so the same window can see the new tools.

| Missing | What it offers |
| --- | --- |
| Python 3.11+ | `winget install --id Python.Python.3.12 -e` |
| Node 20+ or npm | `winget install --id OpenJS.NodeJS.LTS -e` |
| Rust, and the wasm module is out of date | Download `https://win.rustup.rs/x86_64` and run `rustup-init.exe`. If that fails, it offers `winget install --id Rustlang.Rustup -e`. |
| Visual Studio C++ build tools | `vswhere -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64`. When that finds nothing, `winget install --id Microsoft.VisualStudio.2022.BuildTools` with the `Microsoft.VisualStudio.Workload.VCTools` workload. The installer window is the Visual Studio one, not a silent install. A reboot may be required. |

`winget` is not passed `--silent`. Agreement flags are added only after the yes, so the install does not stop on a second license prompt. If `winget` is missing, the script prints https://www.python.org/downloads/, https://nodejs.org/, or https://visualstudio.microsoft.com/visual-cpp-build-tools/ and stops.

After Rust is on `PATH`, the Python builder asks before `rustup target add wasm32-unknown-unknown` and before `cargo install wasm-bindgen-cli --version <lockfile> --force`. The version is the `wasm-bindgen` package in `native/Cargo.lock` (not `wasm-bindgen-macro`). `rust-toolchain.toml` pins channel `1.83.0` and the minimal profile. The wasm target is not listed in that file, so entering the directory does not download it. `rustup-init` is started with `--default-toolchain 1.83.0` after the yes.

### Linux (`build.sh`)

The script sources `$HOME/.cargo/env` when that file exists, so an existing rustup install is visible.

| Missing | What it prints, then runs after a yes |
| --- | --- |
| Python 3.11+ | apt: `sudo apt-get install -y python3 python3-venv`. dnf: `sudo dnf install -y python3`. pacman: `sudo pacman -S --needed python`. |
| `venv` / `ensurepip` | apt: `sudo apt-get install -y python3-venv`. dnf and pacman use the Python package above. |
| Node 20+ | apt: `sudo apt-get install -y nodejs npm`. dnf: `sudo dnf install -y nodejs npm`. pacman: `sudo pacman -S --needed nodejs npm`. If the distro package is older than Node 20, install Node 20 from https://nodejs.org/. |
| npm alone | The matching `npm` package for that distro. |
| C linker, and the wasm module is out of date | apt: `sudo apt-get install -y build-essential`. dnf: `sudo dnf install -y gcc`. pacman: `sudo pacman -S --needed base-devel`. `gcc`, `cc`, or `clang` counts as found. |
| Rust, and the wasm module is out of date | `curl https://sh.rustup.rs -sSf \| sh -s -- -y --default-toolchain 1.83.0`, then `source $HOME/.cargo/env`. |

`sudo` appears only inside those commands, and only after the yes. The wasm target and `wasm-bindgen-cli` are the same second prompt as on Windows.

The distro is read from `/etc/os-release` (`ID` and `ID_LIKE`): Debian, Ubuntu, Mint, and Pop use apt; Fedora, RHEL, CentOS, Rocky, and Alma use dnf; Arch and Manjaro use pacman.

## Optional native extension

Server-side hot paths can use the PyO3 extension. The builder does not compile it. With the virtualenv active:

```bash
maturin develop --release --manifest-path native/Cargo.toml
```

Without that build, those paths stay in Python. On Windows the extension looks for `libunwind.dll` on `PATH`, or in `OCARINA_FORGE_NATIVE_DLL_DIR`.

## When something fails

The builder prints the failing step and the last lines of that command. A port already in use means another Forge window is still running; quit it and start again. A non-interactive run will not install tools and will not ask for a dump: use `--check` to see the commands, set `OCARINA_FORGE_DUMP` if you want a dump, and pass `--no-browser` when there is no desktop.
