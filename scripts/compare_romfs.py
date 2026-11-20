#!/usr/bin/env python3
"""Build After the Ocarina and the Ocarina Forge project, then diff the romfs trees.

From an ocarina-forge checkout, with the game dump already named in the
After the Ocarina config.toml:

    python scripts/compare_romfs.py --ato ~/Work/after-the-ocarina

That runs ``scripts/build.py --no-install`` inside the checkout with that
checkout's ``.venv/bin/python`` when the virtualenv exists, and otherwise
with the current interpreter. It writes ``build/romfs``. This script then
builds ``examples/after-the-ocarina`` with the same romfs. The two trees
are compared file by file. Compressed files are unpacked first. BYML,
event flows and SARC members are compared by value when the bytes differ.

Exit 0 when every file is byte-identical. Exit 3 when the only differences
are explained (same payload, different compression, or the same parsed
value). Exit 1 when a file does not match.

Regenerate the project from a newer checkout first when the two source
trees are not the same commit:

    python scripts/import_ato.py ~/Work/after-the-ocarina examples/after-the-ocarina
    python scripts/compare_romfs.py --ato ~/Work/after-the-ocarina
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocarina_forge.engine.ato import build_project
from ocarina_forge.equiv import diff_trees, summarize
from ocarina_forge.project import Project


def _ato_romfs(ato: Path) -> Path:
    config = ato / "config.toml"
    if not config.is_file():
        raise SystemExit(
            f"{config} is missing. Copy config.example.toml to config.toml and set romfs "
            "to the 1.1.1 dump (the folder that contains Banc/ and System/)."
        )
    with config.open("rb") as handle:
        data = tomllib.load(handle)
    raw = data.get("pristine_romfs") or data.get("romfs")
    if not isinstance(raw, str) or not raw.strip():
        raise SystemExit(f"{config} does not set romfs.")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = (ato / path).resolve()
    if path.name != "romfs" and (path / "romfs").is_dir():
        path = path / "romfs"
    if not path.is_dir():
        raise SystemExit(f"romfs from {config} is not a directory: {path}")
    return path


def ato_python(ato: Path) -> Path:
    """The interpreter that should run the checkout's ``scripts/build.py``.

    The checkout's own virtualenv has evfl, ainb, zstandard, and PyYAML.
    This repo's interpreter often does not, and launching ``build.py`` with
    it then fails with ``ModuleNotFoundError: evfl``.
    """
    names = (".venv/Scripts/python.exe", ".venv/bin/python") if sys.platform == "win32" else (".venv/bin/python",)
    for name in names:
        python = ato / name
        if python.is_file():
            return python
    pip = ".venv\\Scripts\\pip.exe" if sys.platform == "win32" else ".venv/bin/pip"
    print(
        f"{ato}/.venv was not found, so scripts/build.py will run with {sys.executable}.\n"
        "That interpreter needs After the Ocarina's dependencies (evfl, ainb, zstandard, PyYAML).\n"
        "From the checkout, install its requirements.txt:\n"
        f"  python -m venv .venv && {pip} install -r requirements.txt",
        file=sys.stderr,
    )
    return Path(sys.executable)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ato", type=Path, required=True, help="After the Ocarina checkout")
    parser.add_argument(
        "--project",
        type=Path,
        default=ROOT / "examples" / "after-the-ocarina",
        help="Ocarina Forge project (default: examples/after-the-ocarina)",
    )
    parser.add_argument("--romfs", type=Path, default=None, help="dump romfs, if it should override config.toml")
    parser.add_argument("--skip-ato-build", action="store_true", help="reuse the checkout's existing build/romfs")
    args = parser.parse_args(argv)
    ato = args.ato.expanduser().resolve()
    project = Project(args.project.expanduser().resolve())
    romfs = args.romfs.expanduser().resolve() if args.romfs else _ato_romfs(ato)
    if romfs.name != "romfs" and (romfs / "romfs").is_dir():
        romfs = romfs / "romfs"
    if not args.skip_ato_build:
        python = ato_python(ato)
        print(f"building After the Ocarina with {python} scripts/build.py --no-install ({romfs})")
        completed = subprocess.run(
            [str(python), "scripts/build.py", "--no-install"],
            cwd=ato,
            check=False,
        )
        if completed.returncode != 0:
            print("After the Ocarina's build failed. Fix that before comparing.", file=sys.stderr)
            return completed.returncode
    ato_out = ato / "build" / "romfs"
    if not ato_out.is_dir():
        raise SystemExit(f"missing {ato_out}. Run scripts/build.py --no-install in that checkout.")
    out = project.root / "build" / "romfs"
    print(f"building {project.manifest['name']} into {out}")
    try:
        build_project(
            project.root,
            romfs,
            out,
            mod_id=project.manifest["name"],
            mod_name=project.manifest.get("title") or project.manifest["name"],
            install=False,
        )
    except Exception as exc:
        print(f"Ocarina Forge build failed: {exc}", file=sys.stderr)
        return 1
    diffs = diff_trees(ato_out, out, romfs)
    mismatches, explained = summarize(diffs)
    if not diffs:
        print("identical: every file in the two romfs trees matches byte for byte.")
        return 0
    for item in diffs:
        print(item)
    print(f"{mismatches} mismatch(es), {explained} explained difference(s).")
    if mismatches:
        return 1
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
