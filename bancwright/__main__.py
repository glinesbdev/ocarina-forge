"""python -m bancwright  —  start the local server.

``python -m bancwright problems <banc> [--json]`` prints the scene diagnostics
instead. With no subcommand, the server starts, which is what dev.sh runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bancwright, a visual modding workbench for the Ocarina of Time remake.")
    parser.add_argument("command", nargs="?", choices=["problems", "serve"], help="problems prints diagnostics. The default is the editor server.")
    parser.add_argument("banc", nargs="?", help="Scene path, for the problems command.")
    parser.add_argument("--json", action="store_true", help="Print the problems as JSON.")
    parser.add_argument("--dump", help="Dump folder. Defaults to the saved config.")
    parser.add_argument("--project", help="Project folder. Defaults to the saved config, or an empty project.")
    parser.add_argument("--host", default=os.environ.get("BANCWRIGHT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BANCWRIGHT_PORT", "47232")))
    parser.add_argument("--static", help="Serve a built frontend from this directory.")
    args = parser.parse_args(argv)
    if args.command == "problems":
        if not args.banc:
            parser.error("problems needs a banc path")
        raise SystemExit(print_problems(args.banc, as_json=args.json, dump=args.dump, project=args.project))
    if args.static:
        from bancwright.server import mount_frontend

        mount_frontend(Path(args.static))
    uvicorn.run("bancwright.server:app", host=args.host, port=args.port, reload=False)


def print_problems(banc: str, *, as_json: bool = False, dump: str | None = None, project: str | None = None) -> int:
    """Write the diagnostics and return 1 when any of them is an error."""
    from bancwright.config import load_config
    from bancwright.dump import Dump
    from bancwright.project import Project
    from bancwright.validate import diagnose, render_markdown

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    project_path = project or config.get("project") or ""
    opened = Dump(dump_path)
    if project_path:
        mod = Project(project_path)
    else:
        mod = Project.create(
            Path(tempfile.mkdtemp(prefix="bancwright-problems-")),
            name="problems",
            title="Problems",
            author="",
            version="0",
        )
    report = diagnose(opened, mod, banc)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_markdown(report), end="")
    return 1 if any(row.get("severity") == "error" for row in report["diagnostics"]) else 0


if __name__ == "__main__":
    main()
