"""Builder change detection, saved settings, and prerequisite checks."""

from pathlib import Path

from ocarina_forge.builder import (
    COMMITTED_WASM_STAMP,
    MSVC_COMPONENT,
    PYTHON_VENV_CODE,
    PYTHON_VERSION_CODE,
    RUSTUP_SH,
    frontend_is_current,
    frontend_source_digest,
    install_commands,
    load_settings,
    lock_package_version,
    mark_dump_prompted,
    prerequisite_report,
    remember_dump,
    requirements_current,
    run,
    save_settings,
    saved_dump,
    settings_path,
    wasm_bindgen_version,
    wasm_is_current,
    wasm_outputs_present,
    wasm_source_digest,
    write_stamp,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeProbe:
    def __init__(self, *, system="linux", release=None, programs=None, runs=None, files=None):
        self._system = system
        self._release = {"ID": "ubuntu", "ID_LIKE": "debian"} if release is None else release
        self.programs = {
            "python3": "/usr/bin/python3",
            "node": "/usr/bin/node",
            "npm": "/usr/bin/npm",
            "gcc": "/usr/bin/gcc",
        }
        self.runs = {
            ("/usr/bin/python3", "-c", PYTHON_VERSION_CODE): (0, "3.12.3\n", ""),
            ("/usr/bin/python3", "-c", PYTHON_VENV_CODE): (0, "", ""),
            ("/usr/bin/node", "--version"): (0, "v22.23.4\n", ""),
        }
        if programs:
            self.programs.update(programs)
        if runs:
            self.runs.update(runs)
        self.files = set(files or [])
        self.calls = []

    def which(self, name):
        return self.programs.get(name)

    def run(self, args):
        self.calls.append(tuple(args))
        return self.runs.get(tuple(args), (127, "", "not mocked"))

    def exists(self, path):
        return path in self.files

    def system(self):
        return self._system

    def os_release(self):
        return dict(self._release)


def _remove(probe, *names):
    for name in names:
        probe.programs.pop(name, None)
    return probe


def seed_current(tmp_path: Path) -> Path:
    native = tmp_path / "native" / "src"
    native.mkdir(parents=True)
    (native / "lib.rs").write_text("fn ready() {}\n", encoding="utf-8")
    (tmp_path / "native" / "Cargo.toml").write_text("[package]\nname = \"sample\"\n", encoding="utf-8")
    (tmp_path / "native" / "Cargo.lock").write_text("# lock\n", encoding="utf-8")
    (tmp_path / "rust-toolchain.toml").write_text("[toolchain]\nchannel = \"1.83.0\"\n", encoding="utf-8")
    wasm = tmp_path / "frontend" / "wasm"
    wasm.mkdir(parents=True)
    (wasm / "ocarina_forge_native.js").write_text("export {}\n", encoding="utf-8")
    (wasm / "ocarina_forge_native_bg.wasm").write_bytes(b"\0asm")
    write_stamp(tmp_path / COMMITTED_WASM_STAMP, wasm_source_digest(tmp_path))
    return tmp_path


def _clear_config_env(monkeypatch):
    for name in (
        "OCARINA_FORGE_CONFIG", "BANCWRIGHT_CONFIG",
        "OCARINA_FORGE_DUMP", "BANCWRIGHT_DUMP",
        "OCARINA_FORGE_PROJECT", "BANCWRIGHT_PROJECT",
        "OCARINA_FORGE_GAME", "BANCWRIGHT_GAME",
        "OCARINA_FORGE_HOST", "BANCWRIGHT_HOST",
        "OCARINA_FORGE_PORT", "BANCWRIGHT_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_committed_stamp_matches_native_sources():
    assert wasm_outputs_present(ROOT)
    assert read_matches(ROOT)
    assert wasm_is_current(ROOT)
    assert wasm_bindgen_version(ROOT) == "0.2.130"


def read_matches(root: Path) -> bool:
    return (root / COMMITTED_WASM_STAMP).read_text(encoding="utf-8").strip() == wasm_source_digest(root)


def test_lock_parser_ignores_the_macro_package():
    text = """
[[package]]
name = "wasm-bindgen-macro"
version = "0.2.1"

[[package]]
name = "wasm-bindgen"
version = "0.2.130"
"""
    assert lock_package_version(text, "wasm-bindgen") == "0.2.130"
    assert lock_package_version((ROOT / "native" / "Cargo.lock").read_text(encoding="utf-8"), "wasm-bindgen") == "0.2.130"


def test_source_change_invalidates_wasm_until_a_local_stamp_matches(tmp_path):
    root = seed_current(tmp_path)
    assert wasm_is_current(root)
    (root / "native" / "src" / "lib.rs").write_text("fn changed() {}\n", encoding="utf-8")
    assert not wasm_is_current(root)
    write_stamp(root / ".ocarina-forge" / "wasm.stamp", wasm_source_digest(root))
    assert wasm_is_current(root)
    assert not wasm_is_current(root, force=True)


def test_missing_wasm_output_is_not_current(tmp_path):
    root = seed_current(tmp_path)
    (root / "frontend" / "wasm" / "ocarina_forge_native_bg.wasm").unlink()
    assert not wasm_is_current(root)


def test_frontend_rebuilds_when_sources_or_dist_change(tmp_path):
    source = tmp_path / "frontend" / "src"
    source.mkdir(parents=True)
    (source / "main.js").write_text("export {}\n", encoding="utf-8")
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    assert not frontend_is_current(tmp_path)
    write_stamp(tmp_path / ".ocarina-forge" / "frontend.stamp", frontend_source_digest(tmp_path))
    assert frontend_is_current(tmp_path)
    (source / "main.js").write_text("export const n = 1\n", encoding="utf-8")
    assert not frontend_is_current(tmp_path)
    write_stamp(tmp_path / ".ocarina-forge" / "frontend.stamp", frontend_source_digest(tmp_path))
    (dist / "index.html").unlink()
    assert not frontend_is_current(tmp_path)


def test_requirements_stamp_needs_a_venv(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    assert not requirements_current(tmp_path)


def test_settings_round_trip_and_ignore_a_broken_file(tmp_path):
    save_settings(tmp_path, {"askedDump": False, "dump": "/games/dump"})
    assert load_settings(tmp_path)["dump"] == "/games/dump"
    mark_dump_prompted(tmp_path)
    assert load_settings(tmp_path)["askedDump"] is True
    assert load_settings(tmp_path)["dump"] == "/games/dump"
    settings_path(tmp_path).write_text("{", encoding="utf-8")
    assert load_settings(tmp_path) == {}


def test_remember_dump_updates_the_editor_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _clear_config_env(monkeypatch)
    monkeypatch.chdir(root)
    dump = str(tmp_path / "dump")
    remember_dump(root, dump)
    assert load_settings(root)["dump"] == dump
    assert load_settings(root)["askedDump"] is True
    assert saved_dump(root) == dump
    from ocarina_forge.config import load_config

    assert load_config()["dump"] == dump
    assert (root / "ocarina-forge.local.yaml").is_file()


def test_current_wasm_does_not_require_rust(tmp_path):
    root = seed_current(tmp_path)
    probe = _remove(FakeProbe(programs={"rustc": "/usr/bin/rustc", "cargo": "/usr/bin/cargo", "rustup": "/usr/bin/rustup"}), "gcc")
    report = prerequisite_report(root, probe)
    assert not report.wasm_needed
    assert report.ok
    rust = report.get("rust")
    assert rust is not None and not rust.required
    linker = report.get("linker")
    assert linker is not None and not linker.required
    assert not any(call and call[0].endswith(("rustc", "cargo", "rustup", "wasm-bindgen")) for call in probe.calls)


def test_stale_wasm_requires_rust_and_a_linker(tmp_path):
    root = seed_current(tmp_path)
    (root / "native" / "src" / "lib.rs").write_text("fn changed() {}\n", encoding="utf-8")
    report = prerequisite_report(root, _remove(FakeProbe(), "gcc"))
    assert report.wasm_needed
    assert not report.ok
    rust = report.get("rust")
    linker = report.get("linker")
    assert rust is not None and rust.required and not rust.ok
    assert rust.command == RUSTUP_SH
    assert linker is not None and linker.required and not linker.ok
    assert linker.command == "sudo apt-get install -y build-essential"


def test_rebuild_flag_requires_rust_even_when_the_module_matches(tmp_path):
    root = seed_current(tmp_path)
    report = prerequisite_report(root, FakeProbe(), rebuild_wasm=True)
    assert report.wasm_needed
    assert report.get("rust") is not None and report.get("rust").required


def test_old_python_and_node_are_reported(tmp_path):
    root = seed_current(tmp_path)
    probe = FakeProbe(runs={
        ("/usr/bin/python3", "-c", PYTHON_VERSION_CODE): (0, "3.9.2\n", ""),
        ("/usr/bin/node", "--version"): (0, "v18.19.0\n", ""),
    })
    report = prerequisite_report(root, probe)
    python = report.get("python")
    node = report.get("node")
    assert python is not None and not python.ok and "3.9.2" in python.detail
    assert node is not None and not node.ok and "v18.19.0" in node.detail


def test_missing_venv_node_and_distro_commands(tmp_path):
    root = seed_current(tmp_path)
    apt = prerequisite_report(root, _remove(FakeProbe(runs={
        ("/usr/bin/python3", "-c", PYTHON_VENV_CODE): (1, "", "No module named ensurepip"),
    }), "node", "npm"))
    assert not apt.get("venv").ok
    assert "python3-venv" in apt.get("venv").command
    assert apt.get("node").command == "sudo apt-get install -y nodejs npm"

    fedora = prerequisite_report(root, _remove(FakeProbe(release={"ID": "fedora", "ID_LIKE": ""}), "node"))
    assert fedora.get("node").command == "sudo dnf install -y nodejs npm"
    assert fedora.get("linker").command == "" or not fedora.wasm_needed

    arch = prerequisite_report(root, _remove(FakeProbe(release={"ID": "arch", "ID_LIKE": ""}), "npm"))
    assert arch.get("npm").command == "sudo pacman -S --needed npm"


def test_windows_build_tools_are_detected_with_vswhere(tmp_path):
    root = seed_current(tmp_path)
    (root / "native" / "src" / "lib.rs").write_text("fn changed() {}\n", encoding="utf-8")
    vswhere = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    found = FakeProbe(
        system="windows",
        programs={"vswhere": vswhere},
        runs={(
            vswhere, "-latest", "-products", "*", "-requires", MSVC_COMPONENT, "-property", "installationPath",
        ): (0, r"C:\BuildTools\n", "")},
    )
    report = prerequisite_report(root, found)
    linker = report.get("linker")
    assert linker is not None and linker.ok and linker.required

    missing = FakeProbe(system="windows")
    missing_report = prerequisite_report(root, missing)
    missing_linker = missing_report.get("linker")
    assert missing_linker is not None and not missing_linker.ok
    assert "vswhere" in missing_linker.detail
    assert "Microsoft.VisualStudio.2022.BuildTools" in missing_linker.command
    assert "VCTools" in missing_linker.command
    assert missing_report.get("rust").command == "https://win.rustup.rs/x86_64"


def test_wasm_bindgen_must_match_the_lockfile(tmp_path):
    root = seed_current(tmp_path)
    (root / "native" / "Cargo.lock").write_text(
        '[[package]]\nname = "wasm-bindgen"\nversion = "0.2.130"\n',
        encoding="utf-8",
    )
    (root / "native" / "src" / "lib.rs").write_text("fn changed() {}\n", encoding="utf-8")
    probe = FakeProbe(
        programs={
            "rustc": "/usr/bin/rustc",
            "cargo": "/usr/bin/cargo",
            "rustup": "/usr/bin/rustup",
            "wasm-bindgen": "/usr/bin/wasm-bindgen",
        },
        runs={
            ("/usr/bin/rustc", "--version"): (0, "rustc 1.83.0\n", ""),
            ("/usr/bin/rustup", "target", "list", "--installed"): (0, "x86_64-unknown-linux-gnu\n", ""),
            ("/usr/bin/wasm-bindgen", "--version"): (0, "wasm-bindgen 0.2.100\n", ""),
        },
    )
    report = prerequisite_report(root, probe)
    assert report.get("rust").ok
    assert not report.get("wasm-target").ok
    bindgen = report.get("wasm-bindgen")
    assert not bindgen.ok
    assert "0.2.130" in bindgen.detail
    assert "0.2.100" in bindgen.detail
    assert bindgen.command == "cargo install wasm-bindgen-cli --version 0.2.130 --force"


def test_check_prints_a_report_and_shell_status(tmp_path, capsys, monkeypatch):
    root = seed_current(tmp_path)
    code = run(["--check"], probe=_remove(FakeProbe(), "node", "npm"), root=root)
    text = capsys.readouterr().out
    assert code == 1
    assert "Rust is not required" in text
    assert "sudo apt-get install -y nodejs npm" in text

    monkeypatch.setenv("FORGE_BUILDER_STATUS", "shell")
    code = run(["--check"], probe=_remove(FakeProbe(), "node", "npm"), root=root)
    status = capsys.readouterr().out
    assert code == 0
    assert "ok=0\n" in status or "ok='0'\n" in status
    assert "wasm_needed=0\n" in status or "wasm_needed='0'\n" in status
    assert "node" in status.split("missing=", 1)[1].split("\n", 1)[0]


def test_install_commands_are_explicit():
    apt = install_commands("linux", "apt")
    assert apt["python"] == "sudo apt-get install -y python3 python3-venv"
    assert apt["venv"] == "sudo apt-get install -y python3-venv"
    assert apt["linker"] == "sudo apt-get install -y build-essential"
    assert apt["rust"] == RUSTUP_SH
    assert install_commands("linux", "dnf")["linker"] == "sudo dnf install -y gcc"
    assert install_commands("linux", "pacman")["linker"] == "sudo pacman -S --needed base-devel"
    windows = install_commands("windows", "")
    assert "Python.Python.3.12" in windows["python"]
    assert "OpenJS.NodeJS.LTS" in windows["node"]
    assert "--silent" not in windows["linker"]
    assert "--silent" not in windows["python"]
