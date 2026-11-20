#!/usr/bin/env python3
"""Turn an After the Ocarina checkout into an Ocarina Forge project.

    python scripts/import_ato.py ~/Work/after-the-ocarina examples/after-the-ocarina

Copies YAML and mod.toml only. Game files are refused. The destination's
manifest is rewritten.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocarina_forge.errors import BancError
from ocarina_forge.importers.ato_project import import_ato_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ato", type=Path, help="checkout that contains src/mod.toml")
    parser.add_argument(
        "project",
        type=Path,
        nargs="?",
        default=ROOT / "examples" / "after-the-ocarina",
        help="project folder to write (default: examples/after-the-ocarina)",
    )
    args = parser.parse_args(argv)
    try:
        result = import_ato_project(args.ato, args.project)
    except BancError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {result['root']}")
    print(f"{len(result['files'])} source files")
    commit = result["manifest"]["source"].get("commit") or "(no git commit; the checkout is not a repository)"
    print(f"commit: {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
