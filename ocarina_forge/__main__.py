"""python -m ocarina_forge  —  start the local server.

``problems <banc>`` prints scene diagnostics. ``model-debug`` prints a bind-pose
bake or the actors near a point. ``ground-debug <banc>`` prints the ground
layer. ``material-debug`` prints the shader assign and the uniforms.
``material-audit`` groups every model material and lists what the viewer does not draw yet.
``tex-debug <name>`` prints a BNTX header and can write the current decode.
``tex-audit`` flags textures whose mip-0 size implies a different block height than usual.
``behavior <gyaml>`` prints the actor's AI graph, schedule, and event-flow hooks.
``behavior-audit`` counts undecoded node types across every Npc_ pack.
``behavior-export`` writes saved set-field ops into the project build folder.
With no subcommand, the server starts, which is what dev.sh runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import uvicorn

from ocarina_forge.locate import setting


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ocarina Forge, a visual modding workbench for the Ocarina of Time remake.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["problems", "model-debug", "ground-debug", "material-debug", "material-audit", "tex-debug", "tex-audit", "behavior", "behavior-audit", "behavior-export", "serve"],
        help="problems, model-debug, ground-debug, material-debug, material-audit, tex-debug, tex-audit, behavior, behavior-audit, and behavior-export print reports. The default is the editor server.",
    )
    parser.add_argument("banc", nargs="?", help="Scene path, gyaml, bfres path, or actor class for behavior.")
    parser.add_argument("--id", help="Actor hash. behavior reads that placement as well as the actor pack.")
    parser.add_argument("--trace", action="store_true", help="behavior prints the pack, each component, and every path it tried.")
    parser.add_argument("--nodes", action="store_true", help="behavior lists every node. A large graph prints the files and entry points without this.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument(
        "--banc",
        dest="scene_banc",
        help="Scene to read placement from. model-debug also checks its Static or Dynamic sibling.",
    )
    parser.add_argument("--near", help="x,y,z in metres. List actors within --radius of this point. No gyaml is required.")
    parser.add_argument("--radius", type=float, default=10.0, help="Metres around --near. Default 10.")
    parser.add_argument("--at", action="append", default=[], help="x,z in metres. ground-debug prints the ground height there. Repeat for another point.")
    parser.add_argument("--dump", help="Dump folder. Defaults to the saved config.")
    parser.add_argument(
        "--strings",
        action="store_true",
        help="material-debug also prints the whole BFRES string pool.",
    )
    parser.add_argument("--png", help="tex-debug writes the current decode to this PNG.")
    parser.add_argument("--out", help="material-audit also writes the report to this file.")
    parser.add_argument("--project", help="Project folder. Defaults to the saved config, or an empty project.")
    parser.add_argument("--host", default=setting("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(setting("PORT", "47232") or "47232"))
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
    if args.command == "ground-debug":
        if not args.banc:
            parser.error("ground-debug needs a banc path")
        raise SystemExit(print_ground_debug(args.banc, points=args.at, as_json=args.json, dump=args.dump))
    if args.command == "material-debug":
        if not args.banc:
            parser.error("material-debug needs a gyaml or a bfres path")
        raise SystemExit(print_material_debug(args.banc, as_json=args.json, dump=args.dump, strings=args.strings))
    if args.command == "material-audit":
        raise SystemExit(print_material_audit(as_json=args.json, dump=args.dump, out=args.out))
    if args.command == "behavior":
        if not args.banc and not args.id:
            parser.error("behavior needs an actor class, or --banc and --id")
        raise SystemExit(
            print_behavior(
                args.banc or "",
                actor_id=args.id or "",
                banc=args.scene_banc or "",
                as_json=args.json,
                dump=args.dump,
                trace=args.trace,
                nodes=args.nodes,
            )
        )
    if args.command == "behavior-audit":
        raise SystemExit(print_behavior_audit(as_json=args.json, dump=args.dump, out=args.out))
    if args.command == "behavior-export":
        raise SystemExit(print_behavior_export(args.banc or "", dump=args.dump, project=args.project))
    if args.command == "tex-debug":
        if not args.banc:
            parser.error("tex-debug needs a texture name or a Tex path")
        raise SystemExit(print_tex_debug(args.banc, png=args.png, as_json=args.json, dump=args.dump))
    if args.command == "tex-audit":
        raise SystemExit(print_tex_audit(as_json=args.json, dump=args.dump, out=args.out))
    if args.static:
        from ocarina_forge.server import mount_frontend

        mount_frontend(Path(args.static))
    uvicorn.run("ocarina_forge.server:app", host=args.host, port=args.port, reload=False)


def print_problems(banc: str, *, as_json: bool = False, dump: str | None = None, project: str | None = None) -> int:
    """Write the diagnostics and return 1 when any of them is an error."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.project import Project
    from ocarina_forge.validate import diagnose, render_markdown

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
            Path(tempfile.mkdtemp(prefix="ocarina-forge-problems-")),
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


def print_behavior(
    target: str,
    *,
    actor_id: str = "",
    banc: str = "",
    as_json: bool = False,
    dump: str | None = None,
    trace: bool = False,
    nodes: bool = False,
) -> int:
    """Print the read-only behavior graph. 2 when the dump is missing."""
    from ocarina_forge.behavior import load_actor_graph
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    scene = banc
    gyaml = ""
    if target.endswith(".bcett.byml.zs") or target.startswith("Banc/"):
        scene = target
    else:
        gyaml = target
    try:
        graph = load_actor_graph(Dump(dump_path), gyaml=gyaml, banc=scene, actor_id=actor_id)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(graph, indent=2))
        return 0
    print(f"behavior {graph.get('gyaml') or gyaml}")
    if graph.get("note"):
        print(graph["note"])
    opened = graph.get("open") or ""
    for source in graph.get("sources") or []:
        mark = " open" if source.get("id") == opened else ""
        print(f"{source['kind']} {source['status']} {source.get('count', 0)} {source['path']}{mark}")
        shown = source.get("entries") or []
        for entry in shown[:16]:
            print(f"  entry {entry['name']} ({entry['raw']})")
        if len(shown) > 16:
            print(f"  entries {len(shown) - 16} more")
    node_rows = graph.get("nodes") or []
    print(f"nodes {len(node_rows)}")
    print(f"edges {len(graph.get('edges') or [])}")
    print(f"undecoded {graph.get('undecoded', 0)}")
    if trace or not node_rows:
        for line in graph.get("trace") or []:
            print(line)
    if node_rows and not nodes and len(node_rows) > 40:
        print("Pass --nodes to list every node.")
        return 0
    for node in node_rows:
        mark = "raw" if not node.get("decoded") else "friendly" if node.get("known") else "named"
        print(f"  {mark} {node.get('raw')} -> {node.get('name')}  {node.get('id')}")
    return 0


def print_behavior_export(gyaml: str = "", *, dump: str | None = None, project: str | None = None) -> int:
    """Write saved set-field ops. 1 when a file cannot round-trip. 2 when dump or project is missing."""
    from ocarina_forge.behavior_edit import export_behaviors
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.project import Project

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    project_path = project or config.get("project") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    if not project_path:
        print("No project is configured. Pass --project, or open one in the editor.", file=sys.stderr)
        return 2
    try:
        results = export_behaviors(Project(project_path), Dump(dump_path), gyaml=gyaml)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    if not results:
        print("No behavior edits to export.")
        return 0
    for row in results:
        print(row["message"])
    return 1 if any(not row.get("exported") for row in results) else 0


def print_behavior_audit(*, as_json: bool = False, dump: str | None = None, out: str | None = None) -> int:
    """Count undecoded node types on every Npc_ pack. 2 when the dump is missing."""
    from ocarina_forge.behavior import audit_npc_behavior, render_behavior_audit
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    try:
        opened = Dump(dump_path)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    report = audit_npc_behavior(opened)
    text = json.dumps(report, indent=2) if as_json else render_behavior_audit(report)
    if out:
        destination = Path(out).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


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
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.formats.bfres import FresError
    from ocarina_forge.modeldebug import debug_actor, neighbors, parse_near, placements, render_near, render_text

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


def print_ground_debug(banc: str, *, points: list[str] | None = None, as_json: bool = False, dump: str | None = None) -> int:
    """Print the ground layer. 2 when the dump is missing or a point is not x,z."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.formats.terrain import TerrainError
    from ocarina_forge.grounddebug import inspect_ground, parse_at, render_text

    parsed = []
    try:
        for text in points or []:
            parsed.append(parse_at(text))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    try:
        opened = Dump(dump_path)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    try:
        report = inspect_ground(opened, banc, parsed)
    except (BancError, TerrainError, OSError, ValueError) as exc:
        print(getattr(exc, "message", None) or str(exc), file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report), end="")
    return 0


def print_material_debug(target: str, *, as_json: bool = False, dump: str | None = None, strings: bool = False) -> int:
    """Print shader assign and uniforms. 2 when the dump or the model is missing."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.formats.bfres import FresError
    from ocarina_forge.materialdebug import inspect_materials, render_text

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    opened = None
    if dump_path:
        try:
            opened = Dump(dump_path)
        except BancError as exc:
            print(exc.message, file=sys.stderr)
            return 2
    elif not Path(target).expanduser().is_file():
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    try:
        report = inspect_materials(opened, target, strings=strings)
    except (BancError, FresError, OSError, ValueError) as exc:
        print(getattr(exc, "message", None) or str(exc), file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report), end="")
    return 0


def print_material_audit(*, as_json: bool = False, dump: str | None = None, out: str | None = None) -> int:
    """Group every model material. 2 when the dump or its Model folder is missing."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.materialaudit import audit, render_text

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    try:
        opened = Dump(dump_path)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    if not (opened.romfs / "Model").is_dir():
        print(f"{opened.romfs} has no Model folder.", file=sys.stderr)
        return 2
    report = audit(opened)
    text = json.dumps(report, indent=2) if as_json else render_text(report)
    if out:
        destination = Path(out).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def print_tex_audit(*, as_json: bool = False, dump: str | None = None, out: str | None = None) -> int:
    """Flag textures whose size-inferred block height is not the usual one. 2 when Tex/ is missing."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.texaudit import audit, render_text

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    try:
        opened = Dump(dump_path)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    report = audit(opened)
    if report.get("errors") and not report.get("checked"):
        print("\n".join(report["errors"]), file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2) if as_json else render_text(report)
    if out:
        destination = Path(out).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def print_tex_debug(name: str, *, png: str | None = None, as_json: bool = False, dump: str | None = None) -> int:
    """Print the BNTX header and, with --png, write the current decode. 2 on a miss."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.formats.bntx import BntxError, texture_headers, textures
    from ocarina_forge.formats.gltf import png_rgba
    from ocarina_forge.models import _resolve_tex

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    try:
        opened = Dump(dump_path)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    path, tried = _resolve_tex(opened, name, [])
    if not path:
        print(f"No texture for {name}. Tried {', '.join(tried[:6]) or 'Tex/'}.", file=sys.stderr)
        return 2
    try:
        blob, _dict_id = opened.read_file(path)
    except (BancError, OSError, ValueError) as exc:
        print(getattr(exc, "message", None) or str(exc), file=sys.stderr)
        return 2
    headers = texture_headers(blob)
    decoded = []
    note = ""
    try:
        for image in textures(blob):
            decoded.append(
                {
                    "name": image.name,
                    "width": image.width,
                    "height": image.height,
                    "format": image.fmt,
                    "codec": image.codec,
                    "tile": image.tile,
                    "blockHeight": image.block_height,
                    "swizzle": image.swizzle,
                    "note": image.note,
                    "rgba": image.rgba,
                }
            )
    except BntxError as exc:
        note = str(exc)
    wanted = name.rsplit("/", 1)[-1]
    for suffix in (".bntx.zs", ".bntx"):
        if wanted.endswith(suffix):
            wanted = wanted[: -len(suffix)]
    match = next((item for item in decoded if item["name"].lower() == wanted.lower()), None)
    if match is None and len(decoded) == 1:
        match = decoded[0]
    written = ""
    if png:
        if not match or not match.get("rgba"):
            print(note or f"{name} did not decode, so no PNG was written.", file=sys.stderr)
            return 2
        payload = png_rgba(match["width"], match["height"], match["rgba"])
        Path(png).expanduser().write_bytes(payload)
        written = str(Path(png).expanduser())
    report = {
        "name": name,
        "path": path,
        "headers": headers,
        "decoded": [{key: value for key, value in item.items() if key != "rgba"} for item in decoded],
        "png": written,
        "error": note,
    }
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_tex(report), end="")
    return 0


def _render_tex(report: dict) -> str:
    lines = ["tex-debug", f"name {report.get('name') or ''}", f"path {report.get('path') or ''}"]
    if report.get("error"):
        lines.append(f"decode error {report['error']}")
    headers = report.get("headers") or []
    if not headers:
        lines.append("no BRTI header")
    for header in headers:
        lines.append("")
        lines.append(header.get("name") or "(unnamed)")
        fmt = int(header.get("format") or 0)
        codec = header.get("formatName") or "unknown"
        lines.append(f"  format 0x{fmt:04X} {codec}")
        if header.get("formatNote"):
            lines.append(f"  {header['formatNote']}")
        lines.append(
            f"  size {header.get('width')}x{header.get('height')}  depth {header.get('depth')}  "
            f"array {header.get('arrayCount')}"
        )
        if header.get("blockWidth") and header.get("blockHeightPixels"):
            lines.append(f"  footprint {header['blockWidth']}x{header['blockHeightPixels']}")
        lines.append(f"  mips {header.get('mips')}  tile mode {header.get('tileMode')}")
        log2 = header.get("blockHeightLog2")
        height = header.get("blockHeight")
        lines.append(
            f"  block height log2 {log2 if log2 is not None else '?'}  "
            f"block height {height if height is not None else '?'}  layout {header.get('layout') or ''}"
        )
        lines.append(
            f"  textureLayout {int(header.get('textureLayout') or 0):#x}  "
            f"sizeRange {int(header.get('sizeRange') or 0):#x}"
        )
        inferred = header.get("inferredBlockHeightLog2")
        if inferred is not None and inferred != log2:
            lines.append(f"  mip0 size infers log2 {inferred}; the layout field is log2 {log2}")
        lines.append(
            f"  image size {int(header.get('imageSize') or 0):#x}  "
            f"mip0 {int(header.get('mip0Size') or 0):#x}  "
            f"alignment shift {header.get('alignmentShift')} (BNTX+0x0E)"
        )
        surface = header.get("surfaceBytes")
        mip0 = int(header.get("mip0Size") or 0)
        if surface:
            match = "matches" if mip0 == surface else "does not match"
            lines.append(
                f"  mip0 {match} the layout surface {surface} bytes "
                f"(log2 {header.get('blockHeightLog2')})"
            )
        default_log = header.get("defaultBlockHeightLog2")
        surfaces = header.get("surfaces") or {}
        if default_log is not None and default_log != header.get("blockHeightLog2"):
            lines.append(
                f"  usual block height for this size is log2 {default_log} "
                f"({surfaces.get(default_log) or '?'} bytes)"
            )
    for image in report.get("decoded") or []:
        lines.append(
            f"decoded {image.get('name') or '?'}  {image.get('width')}x{image.get('height')}  "
            f"{image.get('codec') or '?'}  block height {image.get('blockHeight')}  {image.get('swizzle') or ''}"
        )
        if image.get("note"):
            lines.append(f"  note {image['note']}")
    if report.get("png"):
        lines.append(f"png {report['png']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
