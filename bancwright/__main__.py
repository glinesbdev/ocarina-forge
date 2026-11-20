"""python -m bancwright  —  start the local server.

``problems <banc>`` prints scene diagnostics. ``model-debug <gyaml or bfres>``
prints the bind-pose bake for one model. ``model-debug --near x,y,z --banc <scene>``
lists the actors near that point. With no subcommand, the server starts,
which is what dev.sh runs.
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
    parser.add_argument(
        "command",
        nargs="?",
        choices=["problems", "model-debug", "serve"],
        help="problems and model-debug print reports. The default is the editor server.",
    )
    parser.add_argument("banc", nargs="?", help="Scene path, gyaml, or bfres path.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument(
        "--banc",
        dest="scene_banc",
        help="Scene to read placement from. model-debug also checks its Static or Dynamic sibling.",
    )
    parser.add_argument("--near", help="x,y,z in metres. List actors within --radius of this point. No gyaml is required.")
    parser.add_argument("--radius", type=float, default=10.0, help="Metres around --near. Default 10.")
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
    if args.command == "model-debug":
        if not args.banc and not args.near:
            parser.error("model-debug needs a gyaml, a bfres path, or --near x,y,z")
        raise SystemExit(
            print_model_debug(
                args.banc,
                as_json=args.json,
                dump=args.dump,
                banc=args.scene_banc,
                near=args.near,
                radius=args.radius,
            )
        )
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


def print_model_debug(
    target: str | None,
    *,
    as_json: bool = False,
    dump: str | None = None,
    banc: str | None = None,
    near: str | None = None,
    radius: float = 10.0,
) -> int:
    """Print the bind-pose report, a neighbor list, or both. 2 when the dump or the model is missing."""
    from bancwright.config import load_config
    from bancwright.dump import Dump
    from bancwright.errors import BancError
    from bancwright.formats.bfres import FresError
    from bancwright.modeldebug import debug_actor, neighbors, parse_near, placements, render_near, render_text

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    opened = None
    point = None
    if near:
        try:
            point = parse_near(near)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if radius < 0:
            print("--radius must be zero or more.", file=sys.stderr)
            return 2
        if not banc:
            print("--near needs --banc, so the search stays on that scene and its Static or Dynamic sibling.", file=sys.stderr)
            return 2
    if dump_path:
        try:
            opened = Dump(dump_path)
        except BancError as exc:
            print(exc.message, file=sys.stderr)
            return 2
    elif not target or not Path(target).expanduser().is_file():
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    if point is not None and opened is None:
        print("--near needs a dump.", file=sys.stderr)
        return 2
    near_rows = []
    report = None
    placed = []
    try:
        if point is not None and opened is not None:
            near_rows = neighbors(opened, point, radius, banc)
        if target:
            report = debug_actor(opened, target)
            placed = placements(opened, target, banc) if opened is not None else []
    except (BancError, FresError, OSError, ValueError) as exc:
        print(getattr(exc, "message", None) or str(exc), file=sys.stderr)
        return 2
    payload = {}
    if point is not None:
        payload["near"] = {"point": list(point), "radius": radius, "actors": near_rows}
    if report is not None:
        payload["target"] = target
        payload["placements"] = placed
        payload["files"] = report.get("files") or []
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        if point is not None:
            print(render_near(point, radius, near_rows), end="")
        if report is not None:
            print(render_text(report, placed), end="")
    return 0


if __name__ == "__main__":
    main()
