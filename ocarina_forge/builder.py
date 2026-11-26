"""Set up, build, and launch Ocarina Forge.

``build.bat`` and ``build.sh`` install OS toolchains, then run this module.
Rust is required only when ``native/`` no longer matches the committed wasm
module. Settings such as the dump folder live in the gitignored editor
config and in ``.ocarina-forge/builder.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ocarina_forge.locate import setting

EDITOR_PORT = 47231
API_PORT_DEFAULT = 47232
MIN_PYTHON = (3, 11)
MIN_NODE = (20, 0)
WASM_STAMP_NAME = "wasm.stamp"
FRONTEND_STAMP_NAME = "frontend.stamp"
REQUIREMENTS_STAMP_NAME = "requirements.stamp"
NPM_STAMP_NAME = "npm.stamp"
COMMITTED_WASM_STAMP = Path("frontend") / "wasm" / "source.sha256"
WASM_OUTPUTS = (
    Path("frontend") / "wasm" / "ocarina_forge_native.js",
    Path("frontend") / "wasm" / "ocarina_forge_native_bg.wasm",
)
WASM_SOURCES = (
    "native/Cargo.toml",
    "native/Cargo.lock",
    "native/src",
    "rust-toolchain.toml",
)
FRONTEND_SOURCES = (
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/vite.config.js",
    "frontend/one-three.js",
    "frontend/webgpu-bptc-plugin.js",
    "frontend/webgpu-bptc-plugin.test.js",
    "frontend/src",
    "frontend/public",
    "frontend/tools",
    "frontend/wasm",
)
SKIP_DIRS = {"node_modules", "dist", ".vite", "target", "__pycache__"}
SHELL_TOOLS = ("python", "venv", "node", "npm", "rust", "linker")
RUST_PIECES = ("wasm-target", "wasm-bindgen")
PYTHON_VERSION_CODE = "import sys; print('%d.%d.%d' % sys.version_info[:3])"
PYTHON_VENV_CODE = "import venv, ensurepip"
MSVC_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
WINGET_AGREEMENTS = "--accept-package-agreements --accept-source-agreements"
RUSTUP_SH = "curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain 1.83.0"


class BuilderError(Exception):
    """A problem the person running the builder can act on."""


class Probe(Protocol):
    def which(self, name: str) -> str | None: ...

    def run(self, args: list[str]) -> tuple[int, str, str]: ...

    def exists(self, path: str) -> bool: ...

    def system(self) -> str: ...

    def os_release(self) -> dict[str, str]: ...


@dataclass
class Tool:
    name: str
    label: str
    ok: bool
    required: bool
    detail: str
    command: str = ""
    version: str = ""


@dataclass
class Report:
    tools: list[Tool] = field(default_factory=list)
    wasm_needed: bool = False
    family: str = ""

    @property
    def ok(self) -> bool:
        return all(tool.ok or not tool.required for tool in self.tools)

    def get(self, name: str) -> Tool | None:
        return next((tool for tool in self.tools if tool.name == name), None)


class SystemProbe:
    """The tools on this machine."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, args: list[str]) -> tuple[int, str, str]:
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, "", str(exc)
        return done.returncode, done.stdout or "", done.stderr or ""

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def system(self) -> str:
        return sys.platform if sys.platform != "win32" else "windows"

    def os_release(self) -> dict[str, str]:
        return read_os_release()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def say(message: str = "") -> None:
    print(message, flush=True)


def confirm(question: str) -> bool:
    """Ask once. A non-interactive run does not install anything."""
    if not sys.stdin.isatty():
        return False
    say(f"{question} [y/N] ")
    try:
        answer = input().strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def tree_digest(root: Path, relatives: tuple[str, ...]) -> str:
    """Hash file paths and bytes. Missing inputs are skipped."""
    files: list[Path] = []
    for relative in relatives:
        path = root / relative
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            for file in path.rglob("*"):
                if not file.is_file():
                    continue
                if any(part in SKIP_DIRS for part in file.relative_to(root).parts):
                    continue
                files.append(file)
    digest = hashlib.sha256()
    for file in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = file.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes() if path.is_file() else b"")
    return digest.hexdigest()


def read_stamp(path: Path) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text.splitlines()[0].strip() if text else ""


def write_stamp(path: Path, digest: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest + "\n", encoding="utf-8")


def wasm_source_digest(root: Path) -> str:
    return tree_digest(root, WASM_SOURCES)


def frontend_source_digest(root: Path) -> str:
    return tree_digest(root, FRONTEND_SOURCES)


def wasm_outputs_present(root: Path) -> bool:
    return all((root / relative).is_file() for relative in WASM_OUTPUTS)


def wasm_is_current(root: Path, *, force: bool = False) -> bool:
    """True when the committed module, or a local rebuild, matches ``native/``."""
    if force or not wasm_outputs_present(root):
        return False
    digest = wasm_source_digest(root)
    if read_stamp(root / ".ocarina-forge" / WASM_STAMP_NAME) == digest:
        return True
    return read_stamp(root / COMMITTED_WASM_STAMP) == digest


def frontend_is_current(root: Path) -> bool:
    if not (root / "frontend" / "dist" / "index.html").is_file():
        return False
    return read_stamp(root / ".ocarina-forge" / FRONTEND_STAMP_NAME) == frontend_source_digest(root)


def settings_path(root: Path) -> Path:
    return root / ".ocarina-forge" / "builder.json"


def load_settings(root: Path) -> dict:
    path = settings_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(root: Path, data: dict) -> None:
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def mark_dump_prompted(root: Path) -> None:
    data = load_settings(root)
    data["askedDump"] = True
    save_settings(root, data)


def remember_dump(root: Path, dump: str) -> None:
    """Remember the dump for the next one-click run and for the editor."""
    data = load_settings(root)
    data["dump"] = dump
    data["askedDump"] = True
    save_settings(root, data)
    previous = Path.cwd()
    os.chdir(root)
    try:
        from ocarina_forge.config import save_config

        save_config(dump=dump)
    finally:
        os.chdir(previous)


def saved_dump(root: Path) -> str:
    """Env, then the editor config, then the builder's own copy."""
    chosen = setting("DUMP")
    if chosen:
        return chosen
    previous = Path.cwd()
    os.chdir(root)
    try:
        from ocarina_forge.config import load_config

        configured = str(load_config().get("dump") or "")
    finally:
        os.chdir(previous)
    if configured:
        return configured
    return str(load_settings(root).get("dump") or "")


def venv_python(root: Path) -> Path:
    folder = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    return root / ".venv" / folder / name


def in_project_venv(root: Path) -> bool:
    expected = venv_python(root)
    try:
        return expected.is_file() and Path(sys.executable).resolve() == expected.resolve()
    except OSError:
        return False


def requirements_current(root: Path) -> bool:
    if not venv_python(root).is_file():
        return False
    return read_stamp(root / ".ocarina-forge" / REQUIREMENTS_STAMP_NAME) == file_digest(root / "requirements.txt")


def npm_current(root: Path) -> bool:
    if not (root / "frontend" / "node_modules").is_dir():
        return False
    packages = tree_digest(root, ("frontend/package.json", "frontend/package-lock.json"))
    return read_stamp(root / ".ocarina-forge" / NPM_STAMP_NAME) == packages


def lock_package_version(text: str, package: str) -> str | None:
    """The version of one Cargo package. ``wasm-bindgen-macro`` is not ``wasm-bindgen``."""
    current = ""
    version = ""
    for line in text.splitlines():
        if line.startswith("[[package]]"):
            if current == package and version:
                return version
            current = ""
            version = ""
            continue
        if line.startswith("name = "):
            current = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("version = ") and current == package and not version:
            version = line.split("=", 1)[1].strip().strip('"')
    if current == package and version:
        return version
    return None


def wasm_bindgen_version(root: Path) -> str | None:
    lock = root / "native" / "Cargo.lock"
    if not lock.is_file():
        return None
    return lock_package_version(lock.read_text(encoding="utf-8"), "wasm-bindgen")


def read_os_release(path: Path | None = None) -> dict[str, str]:
    file = path or Path("/etc/os-release")
    if not file.is_file():
        return {}
    data: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def linux_family(release: dict[str, str]) -> str:
    blob = f"{release.get('ID', '')} {release.get('ID_LIKE', '')}".lower()
    if any(name in blob for name in ("debian", "ubuntu", "mint", "pop")):
        return "apt"
    if any(name in blob for name in ("fedora", "rhel", "centos", "rocky", "alma")):
        return "dnf"
    if any(name in blob for name in ("arch", "manjaro")):
        return "pacman"
    return ""


def install_commands(system: str, family: str) -> dict[str, str]:
    """Exact install lines. The shell scripts run these only after a yes."""
    if system == "windows":
        python = f"winget install --id Python.Python.3.12 -e {WINGET_AGREEMENTS}"
        node = f"winget install --id OpenJS.NodeJS.LTS -e {WINGET_AGREEMENTS}"
        linker = (
            "winget install --id Microsoft.VisualStudio.2022.BuildTools -e "
            f"{WINGET_AGREEMENTS} --override "
            '"--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"'
        )
        return {
            "python": python,
            "venv": python,
            "node": node,
            "npm": node,
            "rust": "https://win.rustup.rs/x86_64",
            "linker": linker,
        }
    packages = {
        "apt": {
            "python": "sudo apt-get install -y python3 python3-venv",
            "venv": "sudo apt-get install -y python3-venv",
            "node": "sudo apt-get install -y nodejs npm",
            "npm": "sudo apt-get install -y npm",
            "linker": "sudo apt-get install -y build-essential",
        },
        "dnf": {
            "python": "sudo dnf install -y python3",
            "venv": "sudo dnf install -y python3",
            "node": "sudo dnf install -y nodejs npm",
            "npm": "sudo dnf install -y npm",
            "linker": "sudo dnf install -y gcc",
        },
        "pacman": {
            "python": "sudo pacman -S --needed python",
            "venv": "sudo pacman -S --needed python",
            "node": "sudo pacman -S --needed nodejs npm",
            "npm": "sudo pacman -S --needed npm",
            "linker": "sudo pacman -S --needed base-devel",
        },
    }
    chosen = dict(packages.get(family, {}))
    chosen.setdefault("python", "")
    chosen.setdefault("venv", "")
    chosen.setdefault("node", "")
    chosen.setdefault("npm", "")
    chosen.setdefault("linker", "")
    chosen["rust"] = RUSTUP_SH
    return chosen


def parse_version(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part is not None)


def version_at_least(found: tuple[int, ...], required: tuple[int, ...]) -> bool:
    width = max(len(found), len(required))
    padded_found = found + (0,) * (width - len(found))
    padded_required = required + (0,) * (width - len(required))
    return padded_found >= padded_required


def vswhere_candidates() -> list[str]:
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return [
        str(Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"),
        str(Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"),
    ]


def find_vswhere(probe: Probe) -> str | None:
    found = probe.which("vswhere")
    if found:
        return found
    for candidate in vswhere_candidates():
        if probe.exists(candidate):
            return candidate
    return None


def msvc_installed(probe: Probe) -> bool:
    vswhere = find_vswhere(probe)
    if not vswhere:
        return False
    code, out, _err = probe.run([
        vswhere, "-latest", "-products", "*", "-requires", MSVC_COMPONENT, "-property", "installationPath",
    ])
    return code == 0 and bool(out.strip())


def linker_present(probe: Probe) -> bool:
    if probe.system() == "windows":
        return msvc_installed(probe)
    return any(probe.which(name) for name in ("gcc", "cc", "clang"))


def _python_candidates(probe: Probe) -> list[list[str]]:
    found: list[list[str]] = []
    for name in ("python3", "python", "py"):
        path = probe.which(name)
        if not path:
            continue
        prefix = [path, "-3"] if name == "py" or path.lower().endswith("py.exe") else [path]
        if prefix not in found:
            found.append(prefix)
    return found


def _tool(name: str, label: str, *, ok: bool, required: bool, detail: str, command: str = "", version: str = "") -> Tool:
    return Tool(name=name, label=label, ok=ok, required=required, detail=detail, command=command, version=version)


def prerequisite_report(root: Path, probe: Probe, *, rebuild_wasm: bool = False) -> Report:
    """What this machine still needs. Rust is omitted when the wasm module is current."""
    system = "windows" if probe.system() == "windows" else "linux" if probe.system().startswith("linux") else probe.system()
    family = linux_family(probe.os_release()) if system != "windows" else ""
    commands = install_commands("windows" if system == "windows" else "linux", family)
    wasm_needed = rebuild_wasm or not wasm_is_current(root)
    tools: list[Tool] = []

    python_ok = False
    python_version = ""
    python_detail = "Python 3.11 or newer was not found."
    for prefix in _python_candidates(probe):
        code, out, err = probe.run([*prefix, "-c", PYTHON_VERSION_CODE])
        version = parse_version(out or err)
        if code != 0 or not version:
            continue
        python_version = ".".join(str(part) for part in version)
        if version_at_least(version, MIN_PYTHON):
            python_ok = True
            python_detail = ""
            break
        python_detail = f"Python {python_version} is older than 3.11."
    tools.append(_tool(
        "python", "Python", ok=python_ok, required=True, detail=python_detail,
        command="" if python_ok else commands["python"], version=python_version,
    ))

    venv_ok = False
    venv_detail = "The Python venv module is not available."
    if python_ok:
        prefix = _python_candidates(probe)[0]
        # Re-find the interpreter that passed the version check.
        for candidate in _python_candidates(probe):
            code, out, err = probe.run([*candidate, "-c", PYTHON_VERSION_CODE])
            if code == 0 and version_at_least(parse_version(out or err), MIN_PYTHON):
                prefix = candidate
                break
        code, _out, err = probe.run([*prefix, "-c", PYTHON_VENV_CODE])
        venv_ok = code == 0
        if not venv_ok:
            venv_detail = err.strip() or "python -m venv cannot create an environment yet."
    tools.append(_tool(
        "venv", "Python venv", ok=venv_ok, required=True,
        detail="" if venv_ok else venv_detail,
        command="" if venv_ok else commands["venv"],
    ))

    node_path = probe.which("node")
    node_version = ""
    node_ok = False
    node_detail = "Node 20 or newer was not found."
    if node_path:
        code, out, err = probe.run([node_path, "--version"])
        node_version = (out or err).strip()
        parsed = parse_version(node_version)
        if code == 0 and version_at_least(parsed, MIN_NODE):
            node_ok = True
            node_detail = ""
        elif code == 0:
            node_detail = f"Node {node_version or parsed} is older than 20."
    node_command = "" if node_ok else commands["node"]
    if not node_ok and family == "apt" and node_command:
        node_detail = node_detail + " If the distro package is older than Node 20, install Node 20 from https://nodejs.org/."
    tools.append(_tool(
        "node", "Node", ok=node_ok, required=True, detail=node_detail,
        command=node_command, version=node_version,
    ))

    npm_ok = probe.which("npm") is not None
    tools.append(_tool(
        "npm", "npm", ok=npm_ok, required=True,
        detail="" if npm_ok else "npm was not found next to Node.",
        command="" if npm_ok else commands["npm"],
    ))

    rust_ok = False
    rust_version = ""
    rustc = None
    # Do not run rustc unless a rebuild needs it. rustup would otherwise read
    # rust-toolchain.toml and install pieces before anyone says yes.
    if wasm_needed:
        rustc = probe.which("rustc")
        cargo = probe.which("cargo")
        if rustc and cargo:
            code, out, err = probe.run([rustc, "--version"])
            rust_version = (out or err).strip()
            rust_ok = code == 0
    rust_detail = ""
    if wasm_needed and not rust_ok:
        rust_detail = "Rust is required because the wasm module is out of date."
        if system == "windows":
            rust_detail += " Download rustup-init.exe from https://win.rustup.rs/x86_64, or run: winget install --id Rustlang.Rustup -e"
    elif not wasm_needed:
        rust_detail = "Not required. The wasm module matches native sources."
    tools.append(_tool(
        "rust", "Rust", ok=rust_ok or not wasm_needed, required=wasm_needed,
        detail=rust_detail, command="" if rust_ok or not wasm_needed else commands["rust"],
        version=rust_version,
    ))

    linker_ok = linker_present(probe)
    linker_label = "Visual Studio C++ Build Tools" if system == "windows" else "C linker"
    linker_detail = ""
    if wasm_needed and not linker_ok:
        if system == "windows":
            linker_detail = "Rust's default MSVC toolchain needs the Visual Studio C++ build tools (vswhere did not find them)."
        else:
            linker_detail = "A C linker (gcc or clang) was not found."
    tools.append(_tool(
        "linker", linker_label, ok=linker_ok or not wasm_needed, required=wasm_needed,
        detail=linker_detail, command="" if linker_ok or not wasm_needed else commands["linker"],
    ))

    target_ok = False
    if rust_ok:
        target_ok = _wasm_target_installed(probe, rustc or "")
    target_detail = ""
    if wasm_needed and rust_ok and not target_ok:
        target_detail = "The wasm32-unknown-unknown target is not installed."
    tools.append(_tool(
        "wasm-target", "wasm32 target", ok=target_ok or not wasm_needed, required=wasm_needed and rust_ok,
        detail=target_detail, command="" if target_ok or not wasm_needed else "rustup target add wasm32-unknown-unknown",
    ))

    wanted = wasm_bindgen_version(root)
    bindgen = probe.which("wasm-bindgen") if wasm_needed else None
    bindgen_version = ""
    bindgen_ok = False
    if bindgen:
        code, out, err = probe.run([bindgen, "--version"])
        bindgen_version = ".".join(str(part) for part in parse_version(out or err))
        bindgen_ok = code == 0 and wanted is not None and bindgen_version == wanted
    bindgen_detail = ""
    bindgen_command = ""
    if wasm_needed and rust_ok and not bindgen_ok:
        if not wanted:
            bindgen_detail = "native/Cargo.lock does not name a wasm-bindgen version."
        else:
            found = f" Found {bindgen_version}." if bindgen_version else ""
            bindgen_detail = f"wasm-bindgen-cli {wanted} is required to match the crate.{found}"
            bindgen_command = f"cargo install wasm-bindgen-cli --version {wanted} --force"
    tools.append(_tool(
        "wasm-bindgen", "wasm-bindgen", ok=bindgen_ok or not (wasm_needed and rust_ok),
        required=wasm_needed and rust_ok, detail=bindgen_detail, command=bindgen_command,
        version=bindgen_version,
    ))

    return Report(tools=tools, wasm_needed=wasm_needed, family=family)


def _wasm_target_installed(probe: Probe, rustc: str) -> bool:
    rustup = probe.which("rustup")
    if rustup:
        code, out, _err = probe.run([rustup, "target", "list", "--installed"])
        if code == 0:
            return any(line.strip() == "wasm32-unknown-unknown" for line in out.splitlines())
    code, out, _err = probe.run([rustc, "--print", "sysroot"])
    if code != 0:
        return False
    return probe.exists(str(Path(out.strip()) / "lib" / "rustlib" / "wasm32-unknown-unknown"))


def render_report(report: Report) -> str:
    lines = ["Forge prerequisites", ""]
    if report.wasm_needed:
        lines.append("  Wasm module     native sources changed, so Rust is required")
    else:
        lines.append("  Wasm module     current, so Rust is not required")
    for tool in report.tools:
        if tool.name in RUST_PIECES and not report.wasm_needed:
            continue
        if tool.name == "linker" and not report.wasm_needed:
            continue
        if not tool.required and tool.name != "rust":
            continue
        if tool.required and tool.ok:
            state = tool.version or "ok"
        elif tool.required:
            state = "missing"
        else:
            state = "not required"
        lines.append(f"  {tool.label:<32}{state}")
    missing = [tool for tool in report.tools if tool.required and not tool.ok]
    if missing:
        lines.append("")
        for tool in missing:
            if tool.detail:
                lines.append(tool.detail)
            if tool.command:
                lines.append(f"  {tool.command}")
        lines.append("")
        lines.append("Install what is missing, then run the builder again.")
    else:
        lines.append("")
        lines.append("Ready.")
    lines.append("")
    return "\n".join(lines)


def shell_status(report: Report) -> str:
    """Assignments ``build.sh`` can eval. Values are shell-quoted."""
    missing = [tool.name for tool in report.tools if tool.name in SHELL_TOOLS and tool.required and not tool.ok]
    commands = {tool.name: tool.command for tool in report.tools if tool.name in SHELL_TOOLS}
    lines = [
        f"ok={shlex.quote('1' if report.ok else '0')}",
        f"wasm_needed={shlex.quote('1' if report.wasm_needed else '0')}",
        f"missing={shlex.quote(' '.join(missing))}",
    ]
    for name in SHELL_TOOLS:
        lines.append(f"{name}_cmd={shlex.quote(commands.get(name, ''))}")
    return "\n".join(lines) + "\n"


def json_status(report: Report) -> str:
    missing = [tool.name for tool in report.tools if tool.name in SHELL_TOOLS and tool.required and not tool.ok]
    commands = {tool.name: tool.command for tool in report.tools if tool.name in SHELL_TOOLS}
    payload = {
        "ok": report.ok,
        "wasmNeeded": report.wasm_needed,
        "missing": missing,
        "commands": commands,
    }
    return json.dumps(payload, indent=2) + "\n"


def run_command(args: list[str], *, cwd: Path, label: str) -> None:
    say(f"  {label}")
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        raise BuilderError(f"{label} could not start: {exc}") from exc
    if done.returncode != 0:
        combined = ((done.stdout or "") + "\n" + (done.stderr or "")).strip()
        tail = "\n".join(combined.splitlines()[-40:])
        detail = f"\n{tail}" if tail else ""
        raise BuilderError(f"{label} failed (exit {done.returncode}).{detail}")


def ensure_venv(root: Path) -> Path:
    python = venv_python(root)
    if not python.is_file():
        run_command([sys.executable, "-m", "venv", str(root / ".venv")], cwd=root, label="Creating the virtual environment")
    if not requirements_current(root):
        run_command(
            [str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")],
            cwd=root,
            label="Installing Python packages",
        )
        write_stamp(root / ".ocarina-forge" / REQUIREMENTS_STAMP_NAME, file_digest(root / "requirements.txt"))
    else:
        say("  Python packages are up to date")
    return python


def ensure_rust_pieces(root: Path) -> None:
    report = prerequisite_report(root, SystemProbe(), rebuild_wasm=True)
    rust = report.get("rust")
    if rust and not rust.ok:
        command = f"\n  {rust.command}" if rust.command else ""
        raise BuilderError(f"{rust.detail}{command}")
    pieces = [tool for tool in report.tools if tool.name in RUST_PIECES and tool.required and not tool.ok]
    if not pieces:
        return
    version = wasm_bindgen_version(root)
    if any(tool.name == "wasm-bindgen" for tool in pieces) and not version:
        raise BuilderError("native/Cargo.lock does not name a wasm-bindgen version.")
    names = " and ".join(tool.label for tool in pieces)
    if not confirm(f"Install {names}?"):
        lines = [tool.detail for tool in pieces if tool.detail]
        for tool in pieces:
            if tool.command:
                lines.append(tool.command)
        raise BuilderError("\n".join(lines) or f"{names} are required to rebuild the wasm module.")
    if any(tool.name == "wasm-target" for tool in pieces):
        run_command(
            ["rustup", "target", "add", "wasm32-unknown-unknown"],
            cwd=root,
            label="Adding the wasm32-unknown-unknown target",
        )
    if any(tool.name == "wasm-bindgen" for tool in pieces):
        run_command(
            ["cargo", "install", "wasm-bindgen-cli", "--version", version or "", "--force"],
            cwd=root,
            label=f"Installing wasm-bindgen-cli {version}",
        )


def build_wasm(root: Path) -> None:
    cargo = shutil.which("cargo")
    bindgen = shutil.which("wasm-bindgen")
    if not cargo or not bindgen:
        raise BuilderError("cargo and wasm-bindgen are required to rebuild the wasm module.")
    target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or root / "native" / "target")
    manifest = root / "native" / "Cargo.toml"
    run_command(
        [
            cargo, "build", "--release", "--target", "wasm32-unknown-unknown",
            "--no-default-features", "--features", "wasm", "--manifest-path", str(manifest),
        ],
        cwd=root,
        label="Building the wasm module",
    )
    artifact = target_dir / "wasm32-unknown-unknown" / "release" / "ocarina_forge_native.wasm"
    if not artifact.is_file():
        raise BuilderError(f"The wasm build did not write {artifact}.")
    run_command(
        [bindgen, str(artifact), "--out-dir", str(root / "frontend" / "wasm"), "--target", "web"],
        cwd=root,
        label="Writing the wasm bindings",
    )
    digest = wasm_source_digest(root)
    write_stamp(root / ".ocarina-forge" / WASM_STAMP_NAME, digest)
    write_stamp(root / COMMITTED_WASM_STAMP, digest)


def ensure_frontend_packages(root: Path) -> None:
    if npm_current(root):
        say("  Frontend packages are up to date")
        return
    npm = shutil.which("npm")
    if not npm:
        raise BuilderError("npm is not on PATH.")
    run_command([npm, "install"], cwd=root / "frontend", label="Installing frontend packages")
    write_stamp(
        root / ".ocarina-forge" / NPM_STAMP_NAME,
        tree_digest(root, ("frontend/package.json", "frontend/package-lock.json")),
    )


def build_frontend(root: Path) -> None:
    if frontend_is_current(root):
        say("  Editor bundle is up to date")
        return
    npm = shutil.which("npm")
    if not npm:
        raise BuilderError("npm is not on PATH.")
    run_command([npm, "run", "build"], cwd=root / "frontend", label="Building the editor")
    if not (root / "frontend" / "dist" / "index.html").is_file():
        raise BuilderError("The editor build did not write frontend/dist/index.html.")
    write_stamp(root / ".ocarina-forge" / FRONTEND_STAMP_NAME, frontend_source_digest(root))


def prompt_dump(root: Path) -> None:
    known = saved_dump(root)
    if known:
        say(f"  Dump        {known}")
        return
    if load_settings(root).get("askedDump"):
        say("  Dump        not set. Choose one in the editor, or set OCARINA_FORGE_DUMP.")
        return
    if not sys.stdin.isatty():
        say("  Dump        not set. The editor will ask, or set OCARINA_FORGE_DUMP.")
        return
    say("")
    say("Forge can open a game dump you already have.")
    say("Paste the folder that contains romfs, or press Enter to choose inside the editor.")
    try:
        entered = input("Dump folder: ").strip()
    except EOFError:
        entered = ""
    if not entered:
        mark_dump_prompted(root)
        say("  The editor will ask for a dump.")
        return
    path = Path(entered).expanduser()
    if not path.exists():
        say(f"  {path} is not there. The editor will ask for a dump.")
        mark_dump_prompted(root)
        return
    remember_dump(root, str(path))
    say(f"  Dump        {path}")


def _api_endpoint() -> tuple[str, int]:
    host = setting("HOST", "127.0.0.1") or "127.0.0.1"
    raw = setting("PORT", str(API_PORT_DEFAULT)) or str(API_PORT_DEFAULT)
    try:
        port = int(raw)
    except ValueError:
        port = API_PORT_DEFAULT
    return host, port


def _port_busy(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((probe_host, port)) == 0


def _popen(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    kwargs: dict = {"cwd": cwd, "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def _stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_http(url: str, proc: subprocess.Popen, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise BuilderError(f"The editor exited before it was ready (exit {proc.returncode}).")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.3)
    raise BuilderError(f"The editor did not answer at {url}.")


def _open_browser(url: str) -> None:
    """Open the editor without waiting on a desktop helper."""
    if sys.platform.startswith("linux"):
        for opener in ("xdg-open", "gio"):
            if shutil.which(opener):
                subprocess.Popen(
                    [opener, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return
        say(f"  Open {url}")
        return
    try:
        if not webbrowser.open(url):
            say(f"  Open {url}")
    except webbrowser.Error:
        say(f"  Open {url}")


def launch_editor(root: Path, python: Path, *, open_browser: bool) -> int:
    host, port = _api_endpoint()
    if _port_busy("127.0.0.1", EDITOR_PORT) or _port_busy(host, port):
        raise BuilderError(
            f"Port {EDITOR_PORT} or {port} is already in use. Quit the other Forge window and run this again."
        )
    npm = shutil.which("npm")
    if not npm:
        raise BuilderError("npm is not on PATH.")
    env = os.environ.copy()
    env["OCARINA_FORGE_HOST"] = host
    env["OCARINA_FORGE_PORT"] = str(port)
    url = f"http://127.0.0.1:{EDITOR_PORT}"
    api = preview = None
    try:
        say(f"  API         http://{host}:{port}")
        api = _popen([str(python), "-m", "ocarina_forge", "--host", host, "--port", str(port)], cwd=root, env=env)
        preview = _popen([npm, "run", "preview"], cwd=root / "frontend", env=env)
        _wait_http(url, preview)
        say(f"  Editor      {url}")
        if open_browser:
            _open_browser(url)
        say("  Press Ctrl+C to stop.")
        code = preview.wait()
        return code if isinstance(code, int) else 0
    except KeyboardInterrupt:
        say("")
        say("Stopping.")
        return 0
    finally:
        _stop(preview)
        _stop(api)


def publish_bench_package(root: Path) -> None:
    source = root / "frontend" / "wasm"
    destination = root / "tools" / "wasm-bench" / "pkg"
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for file in source.iterdir():
        if file.is_file() and file.name != "source.sha256":
            shutil.copy2(file, destination / file.name)
            copied += 1
    if copied == 0:
        raise BuilderError("frontend/wasm has no module to copy into the bench.")


def run_bench(root: Path, python: Path) -> int:
    publish_bench_package(root)
    fixture = root / "tools" / "wasm-bench" / "data" / "hyrule-field-child.bin"
    if not fixture.is_file():
        dump = saved_dump(root)
        if not dump:
            raise BuilderError("The bench fixture is missing. Set a dump with OCARINA_FORGE_DUMP, or run the builder once and enter the dump folder.")
        run_command(
            [str(python), str(root / "tools" / "export_wasm_fixture.py"), "--dump", dump],
            cwd=root,
            label="Exporting bench fixtures",
        )
    npm = shutil.which("npm")
    if not npm:
        raise BuilderError("npm is not on PATH.")
    bench = root / "tools" / "wasm-bench"
    if not (bench / "node_modules").is_dir():
        run_command([npm, "install"], cwd=bench, label="Installing bench packages")
    say("  Running the wasm bench")
    done = subprocess.run([npm, "run", "bench"], cwd=bench)
    return done.returncode


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ocarina_forge.builder", description="Set up, build, and launch Ocarina Forge.")
    parser.add_argument("--rebuild-wasm", action="store_true", help="Rebuild the wasm module even when it matches native sources.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser after the editor starts.")
    parser.add_argument("--check", action="store_true", help="Report prerequisites and exit.")
    parser.add_argument("--bench", action="store_true", help="Run the wasm benchmark instead of the editor.")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, *, probe: Probe | None = None, root: Path | None = None) -> int:
    args = parse_args(argv)
    root = Path(root) if root is not None else repo_root()
    probe = probe or SystemProbe()
    report = prerequisite_report(root, probe, rebuild_wasm=args.rebuild_wasm)
    status = os.environ.get("FORGE_BUILDER_STATUS")
    if args.check and status == "shell":
        sys.stdout.write(shell_status(report))
        return 0
    if args.check and status == "json":
        sys.stdout.write(json_status(report))
        return 0
    if args.check:
        sys.stdout.write(render_report(report))
        return 0 if report.ok else 1

    os.chdir(root)
    say("Forge")
    if report.wasm_needed:
        say("  Wasm module needs a rebuild")
    else:
        say("  Wasm module is current, so Rust stays unused")
    blocked = [tool for tool in report.tools if tool.name in SHELL_TOOLS and tool.required and not tool.ok]
    if blocked:
        sys.stderr.write(render_report(report))
        return 1
    if report.wasm_needed:
        ensure_rust_pieces(root)
    if not in_project_venv(root):
        ensure_venv(root)
        _reexec(root)
    elif not requirements_current(root):
        ensure_venv(root)
    else:
        say("  Python packages are up to date")
    if report.wasm_needed:
        build_wasm(root)

    if args.bench:
        return run_bench(root, venv_python(root))

    ensure_frontend_packages(root)
    build_frontend(root)
    prompt_dump(root)
    return launch_editor(root, venv_python(root), open_browser=not args.no_browser)


def _reexec(root: Path) -> None:
    python = venv_python(root)
    if not python.is_file():
        raise BuilderError(f"The virtual environment did not create {python}.")
    os.chdir(root)
    os.execv(str(python), [str(python), "-m", "ocarina_forge.builder", *sys.argv[1:]])


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except BuilderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
