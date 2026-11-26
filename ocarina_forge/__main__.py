"""python -m ocarina_forge  —  start the local server.

``problems <banc>`` prints scene diagnostics. ``model-debug`` prints a bind-pose
bake or the actors near a point. ``ground-debug <banc>`` prints the ground
layer. ``material-debug`` prints the shader assign and the uniforms.
``mesh-audit <banc>`` checks model conversion for the scene and its companion.
``material-audit`` groups every model material and lists what the viewer does not draw yet.
``tex-debug <name>`` prints a BNTX header and can write the current decode.
``tex-audit`` flags a mip-0 size that implies a different block height than usual, and scores the Tegra and Switch 2 GOB orders.
``warm <scene>`` converts that scene's models, then neighboring scenes. ``warm --scene-only <scene>`` skips the neighbors. ``warm --all`` converts every scene. A file with no Actors array is skipped.
``behavior <gyaml>`` prints the actor's AI graph, schedule, and event-flow hooks.
``behavior-audit`` counts undecoded node types across every Npc_ pack.
``behavior-export`` writes saved set-field ops into the project build folder.
``height-diff <results.json>`` compares a height-check scan to Forge terrain and water.
``lighting-import <results.json>`` splits a lighting-capture storage JSON into per-scene files in the user data directory.
``lighting-survey <banc>`` lists effect actors and GameBancParam key names. It does not invent light values.
``lighting-mod`` prints how to run the capture mod. ``--install`` copies it into the game's mods/local folder.
``collision-follow-test`` writes the Kokiri variant B scene from a dump and exports it. ``--collision-follow off`` is the control. ``--romfs`` also writes a whole-banc mod. Do not enable both at once.
With no subcommand, the server starts, which is what the builder runs.
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
        choices=[
            "problems",
            "model-debug",
            "mesh-audit",
            "ground-debug",
            "material-debug",
            "material-audit",
            "tex-debug",
            "tex-audit",
            "behavior",
            "behavior-audit",
            "behavior-export",
            "warm",
            "scene-preview",
            "serve",
            "project",
            "edit",
            "mod-export",
            "collision-follow-test",
            "profile",
            "height-diff",
            "lighting-import",
            "lighting-survey",
            "lighting-mod",
        ],
        help="Commands for auditing, editing, and preparing scenes. The default is the editor server.",
    )
    parser.add_argument("banc", nargs="?", help="Scene path, gyaml, project action, actor class, a height-diff JSON, a lighting-capture JSON, or a Longshot game folder.")
    parser.add_argument("extra", nargs="?", help="Project name for 'project create' and 'project open'.")
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
    parser.add_argument("--resources-only", action="store_true", help="ground-debug reads TSCB resource records and skips terrain payloads.")
    parser.add_argument("--dump", help="Dump folder. Defaults to the saved config.")
    parser.add_argument("--fallback-dump", help="For scene-preview, use this dump when no dump is configured.")
    parser.add_argument("--share-textures", action="store_true",
                        help="For scene-preview, prepare texture-sharing variants for cached models in the map.")
    parser.add_argument("--prepare-missing", action="store_true",
                        help="Convert missing meshes when running scene-preview or mesh-audit.")
    parser.add_argument(
        "--strings",
        action="store_true",
        help="material-debug also prints the whole BFRES string pool.",
    )
    parser.add_argument("--png", help="tex-debug writes the current decode to this PNG.")
    parser.add_argument("--out", help="material-audit or height-diff also writes the report to this file.")
    parser.add_argument("--project", help="Project folder or library name. Defaults to the saved config.")
    parser.add_argument("--title", default="", help="Project title. Shown as the mod's name.")
    parser.add_argument("--author", default="", help="Stored on the project. Written to mod.json when set.")
    parser.add_argument("--version", default="0.1.0", help="Mod version, major.minor.patch.")
    parser.add_argument("--description", default="", help="Project description. Stays in the project, not in mod.json.")
    parser.add_argument("--gyaml", default="", help="Actor class to edit, for example Obj_Kokiri_Signboard_A_01.")
    parser.add_argument("--translate", default="", help="x,y,z metres for edit.")
    parser.add_argument("--rotate", default="", help="x,y,z radians for edit.")
    parser.add_argument("--scale", default="", help="x,y,z for edit.")
    parser.add_argument("--install", action="store_true", help="mod-export copies the mod into the game's mods/local folder.")
    parser.add_argument(
        "--collision-follow",
        choices=["off", "srt", "bake"],
        default="",
        help="Override manifest collisionFollow for this export. off leaves the bake. srt mints SRTHash (variant B) and writes no shape. bake moves the compound instance and keeps the shape bytes.",
    )
    parser.add_argument(
        "--romfs",
        action="store_true",
        help="collision-follow-test also writes a whole-banc romfs mod. Do not enable it together with the patch mod.",
    )
    parser.add_argument("--gfx-probe", action="store_true", help="lighting-mod --install copies the entry that static-imports longshot/gfx. Use it on a build that already reports has(\"gfx.world\").")
    parser.add_argument("--game", default="", help="Game install. Default ~/Games/Longshot/mod-test, or config game, or OCARINA_FORGE_GAME.")
    parser.add_argument("--all", action="store_true", help="Prepare every scene with warm or scene-preview.")
    parser.add_argument("--scene-only", action="store_true", help="warm converts the named scene and does not walk neighboring scenes.")
    parser.add_argument("--model", action="append", default=[], help="Limit profile or mesh-audit to this actor class. Repeat to add another.")
    parser.add_argument("--cprofile", default="", help="profile writes a cProfile file for one --model cold convert.")
    parser.add_argument("--limit", type=int, default=0, help="Profile or scene-preview stops each scene after N models. 0 means every model.")
    parser.add_argument("--jobs", type=int, default=1, help="profile converts this many models at once. 1 stays in this process.")
    parser.add_argument("--home-cache", action="store_true", help="profile uses the normal model cache. The default is a private directory under ~/.cache/ocarina-forge/profile-tmp.")
    parser.add_argument("--keep-cache", action="store_true", help="profile leaves the private cache directory in place. The default is to delete it on exit.")
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
    if args.command == "mesh-audit":
        if not args.banc:
            parser.error("mesh-audit needs a banc path")
        raise SystemExit(print_mesh_audit(args.banc, dump=args.dump, models=args.model,
                                          prepare_missing=args.prepare_missing, as_json=args.json))
    if args.command == "ground-debug":
        if not args.banc:
            parser.error("ground-debug needs a banc path")
        raise SystemExit(print_ground_debug(args.banc, points=args.at, as_json=args.json, dump=args.dump,
                                            resources_only=args.resources_only))
    if args.command == "height-diff":
        if not args.banc:
            parser.error("height-diff needs a height-check results JSON")
        raise SystemExit(print_height_diff(args.banc, dump=args.dump, as_json=args.json, out=args.out))
    if args.command == "lighting-import":
        if not args.banc:
            parser.error("lighting-import needs a lighting-capture JSON")
        raise SystemExit(print_lighting_import(args.banc, out=args.out))
    if args.command == "lighting-survey":
        if not args.banc:
            parser.error("lighting-survey needs a banc path")
        raise SystemExit(print_lighting_survey(args.banc, dump=args.dump, as_json=args.json, out=args.out))
    if args.command == "lighting-mod":
        # The flag is also read from argv. A probe install must not depend on
        # the store_true attribute alone: the 6409840 run left the dynamic entry.
        requested = list(argv if argv is not None else sys.argv[1:])
        gfx_probe = bool(args.gfx_probe) or "--gfx-probe" in requested
        raise SystemExit(print_lighting_mod(install=args.install, game=args.game, gfx_probe=gfx_probe))
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
    if args.command == "scene-preview":
        if bool(args.banc) == args.all:
            parser.error("scene-preview needs a banc path or --all")
        raise SystemExit(print_scene_preview(args.banc, dump=args.dump, fallback=args.fallback_dump, all_scenes=args.all,
                                            limit=args.limit, prepare_missing=args.prepare_missing,
                                            share_textures=args.share_textures))
    if args.command == "warm":
        if not args.all and not args.banc:
            parser.error("warm needs a banc path or --all")
        raise SystemExit(print_warm("all" if args.all else args.banc, dump=args.dump, scene_only=args.scene_only))
    if args.command == "project":
        raise SystemExit(print_project(args))
    if args.command == "edit":
        if not args.banc:
            parser.error("edit needs a scene path or a name such as KokiriForest_Child")
        raise SystemExit(print_edit(args))
    if args.command == "mod-export":
        raise SystemExit(print_mod_export(args))
    if args.command == "collision-follow-test":
        raise SystemExit(print_collision_follow_test(args))
    if args.command == "profile":
        raise SystemExit(print_profile(args))
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




def print_height_diff(results_path: str, *, dump: str | None = None, as_json: bool = False, out: str | None = None) -> int:
    """Compare a height-check JSON to Forge terrain and water. 2 on a missing dump or file."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.heightdiff import compare_results, json_ready, load_results, render_text

    path = Path(results_path)
    if not path.is_file():
        print(f"No results file at {results_path}.", file=sys.stderr)
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
        results = load_results(path)
        report = compare_results(opened, results)
    except (BancError, OSError, ValueError) as exc:
        print(getattr(exc, "message", None) or str(exc), file=sys.stderr)
        return 2
    ready = json_ready(report)
    if out:
        Path(out).write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")
    if as_json:
        print(json.dumps(ready, indent=2))
    else:
        print(render_text(report), end="")
    return 0


def print_lighting_import(results_path: str, *, out: str | None = None) -> int:
    """Split a lighting-capture JSON, or a game folder, into per-scene files."""
    from ocarina_forge.lighting import find_store, import_capture, references_dir

    try:
        source = find_store(results_path)
        written = import_capture(source, out)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    folder = written[0].parent if written else references_dir()
    print(f"Read {source}")
    print(f"Wrote {len(written)} scene file(s) to {folder}")
    for item in written:
        print(f"{item.stem}  {item}")
    return 0


def print_lighting_survey(banc: str, *, dump: str | None = None, as_json: bool = False, out: str | None = None) -> int:
    """List lighting-related actors and parameter key names. 2 when the dump is missing."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.lighting import lighting_report, render_report

    config = load_config()
    dump_path = dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump, or set one in the editor.", file=sys.stderr)
        return 2
    try:
        opened = Dump(dump_path)
        report = lighting_report(opened, banc, survey=True)
    except (BancError, OSError, ValueError) as exc:
        print(getattr(exc, "message", None) or str(exc), file=sys.stderr)
        return 2
    if out:
        Path(out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_report(report), end="")
    return 0


def print_lighting_mod(*, install: bool = False, game: str = "", gfx_probe: bool = False) -> int:
    """Print the Windows capture steps. --install copies the mod into the game folder."""
    from ocarina_forge.config import load_config
    from ocarina_forge.lighting import install_mod, windows_steps
    from ocarina_forge.projects import game_root

    config = load_config()
    root = Path(game).expanduser() if game else game_root(config)
    print(windows_steps(root, gfx_probe=gfx_probe), end="")
    if not install:
        return 0
    try:
        dest = install_mod(root, gfx_probe=gfx_probe)
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    main_js = dest / "main.js"
    text = main_js.read_text(encoding="utf-8")
    kind = "static import of longshot/gfx" if gfx_probe else "dynamic entry"
    print(f"Installed {dest}")
    print(f"main.js {main_js.stat().st_size} bytes, {kind}")
    if gfx_probe and ("import(" in text or 'from "longshot/gfx"' not in text):
        print(f"{main_js} is not the static gfx entry.", file=sys.stderr)
        return 2
    return 0


def print_ground_debug(banc: str, *, points: list[str] | None = None, as_json: bool = False, dump: str | None = None,
                       resources_only: bool = False) -> int:
    """Print the ground layer. 2 when the dump is missing or a point is not x,z."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.formats.terrain import TerrainError
    from ocarina_forge.grounddebug import inspect_ground, inspect_resources, parse_at, render_resources, render_text

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
        report = inspect_resources(opened, banc, parsed) if resources_only else inspect_ground(opened, banc, parsed)
    except (BancError, TerrainError, OSError, ValueError) as exc:
        print(getattr(exc, "message", None) or str(exc), file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        render = render_resources if resources_only else render_text
        print(render(report), end="")
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


def print_mesh_audit(banc: str, *, dump: str | None, models: list[str], prepare_missing: bool, as_json: bool) -> int:
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.meshaudit import audit_scene_meshes, render_text

    path = dump or load_config().get("dump")
    if not path:
        print("Set a game dump to audit meshes.", file=sys.stderr)
        return 2
    try:
        report = audit_scene_meshes(Dump(path), banc, models=models, prepare_missing=prepare_missing)
    except (BancError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if as_json else render_text(report))
    issues = {"missing", "unsupported", "partial", "failed", "invalid"}
    return int(any(report["counts"].get(status) for status in issues))


def print_scene_preview(
    banc: str | None, *, dump: str | None, fallback: str | None, limit: int, prepare_missing: bool,
    share_textures: bool, all_scenes: bool = False,
) -> int:
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.scene_preview import build_all_scene_previews, build_scene_preview, prepare_scene_shared_models

    path = dump or load_config().get("dump") or fallback
    if not path or limit < 0:
        print("Set a dump and use a nonnegative model limit.", file=sys.stderr)
        return 2
    try:
        opened = Dump(path)
        if all_scenes:
            result = build_all_scene_previews(opened, limit, prepare_missing=prepare_missing, share_textures=share_textures,
                                              log=lambda message: print(message, flush=True))
            print(f"Forge previews ready for {result['ready']} scenes, {result['skipped']} skipped, {result['failed']} failed. "
                  f"Checked {result['models']} model types and {result['shared']} shared models.", flush=True)
            return 2 if result["failed"] else 0
        output, count = build_scene_preview(opened, banc, limit, prepare_missing=prepare_missing)
    except (BancError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Scene preview ready: {count} actor types, {output.stat().st_size:,} bytes", flush=True)
    if share_textures:
        try:
            variants = prepare_scene_shared_models(opened, banc)
        except (BancError, OSError, ValueError) as exc:
            print(f"Could not prepare shared model textures: {exc}", file=sys.stderr)
            return 2
        print(f"Shared textures ready for {variants} distinct cached models.")
    return 0


def print_warm(target: str, *, dump: str | None = None, scene_only: bool = False) -> int:
    """Convert one scene and its neighbors, or every scene. Prints each model's timing."""
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.warm import run_warm

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
        run_warm(opened, target, neighbors=not scene_only)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def print_tex_audit(*, as_json: bool = False, dump: str | None = None, out: str | None = None) -> int:
    """Block-height flags and GOB-order scores. 2 when Tex/ is missing."""
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
        lines.append(
            f"  flags 0x{int(header.get('flags') or 0):02X}  dim {header.get('dim')}  "
            f"swizzle 0x{int(header.get('swizzleValue') or 0):04X}  "
            f"gob {header.get('gobOrder') or '?'}"
            + (f"  {header.get('bytesPer')} bytes per element" if header.get("bytesPer") else "")
        )
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


def print_project(args) -> int:
    from ocarina_forge.config import load_config, save_config
    from ocarina_forge.errors import BancError
    from ocarina_forge.project import Project
    from ocarina_forge.modexport import mod_id
    from ocarina_forge.projects import create_in_library, list_projects, projects_root, resolve_project

    config = load_config()
    action = args.banc or "list"
    try:
        if action == "list":
            rows = list_projects(config)
            print(f"Projects in {projects_root(config)}")
            if not rows:
                print("(none)")
            for row in rows:
                print(f"{row['modId']}\t{row['title']}\t{row['root']}")
            return 0
        if action == "create":
            if not args.extra:
                print("project create needs a name, for example kokiri-sign.", file=sys.stderr)
                return 2
            project = create_in_library(
                args.extra,
                title=args.title or args.extra,
                author=args.author,
                version=args.version,
                description=args.description,
                config=config,
            )
            save_config(project=str(project.root))
            print(f"Created {project.manifest['name']} at {project.root}")
            print(f"Mod id {mod_id(project.manifest['name'])}")
            return 0
        if action == "open":
            if not args.extra and not args.project:
                print("project open needs a library name or a folder.", file=sys.stderr)
                return 2
            token = args.extra or args.project
            root = resolve_project(token, config)
            Project(root)
            save_config(project=str(root))
            print(f"Opened {root}")
            return 0
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    print("project needs list, create, or open.", file=sys.stderr)
    return 2


def print_edit(args) -> int:
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.project import Project
    from ocarina_forge.projects import resolve_project
    from ocarina_forge.scene import apply_command, scene_view

    config = load_config()
    dump_path = args.dump or config.get("dump") or ""
    if not dump_path:
        print("error: No dump is configured. Pass --dump. Nothing was written.", file=sys.stderr)
        return 2
    project_token = args.project or config.get("project") or ""
    if not project_token:
        print("error: No project is open. Pass --project, or run project create. Nothing was written.", file=sys.stderr)
        return 2
    try:
        project = Project(resolve_project(project_token, config))
        dump = Dump(dump_path)
        banc, ident, document = _find_actor(dump, args.banc, gyaml=args.gyaml, actor=args.id or "")
        diff = project.load_scene_diff(banc)
        command = {"command": "transform", "id": ident}
        if args.translate:
            command["translate"] = _triple(args.translate, "Position")
        if args.rotate:
            command["rotate"] = _triple(args.rotate, "Rotation")
        if args.scale:
            command["scale"] = _triple(args.scale, "Scale")
        if len(command) == 2:
            print("error: edit needs --translate, --rotate, or --scale. Nothing was written.", file=sys.stderr)
            return 2
        diff, focus = apply_command(document, diff, command)
        project.save_scene_diff(diff)
    except BancError as exc:
        text = exc.message
        if "Nothing was written" not in text:
            text = f"error: {text} Nothing was written."
        print(text, file=sys.stderr)
        return 2
    view = scene_view(document, diff)
    actor = next(row for row in view["actors"] if row["id"] == focus)
    print(f"{banc}")
    print(f"{actor.get('gyaml')} {focus}")
    print(f"translate {actor.get('translate')}")
    print("Saved in the project. Export with: python -m ocarina_forge mod-export --install")
    return 0


def print_mod_export(args) -> int:
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.modexport import export_mod
    from ocarina_forge.project import Project
    from ocarina_forge.projects import resolve_project

    config = load_config()
    dump_path = args.dump or config.get("dump") or ""
    project_token = args.project or config.get("project") or ""
    if not dump_path or not project_token:
        print("mod-export needs a dump and a project. Pass --dump and --project.", file=sys.stderr)
        return 2
    try:
        result = export_mod(
            Project(resolve_project(project_token, config)),
            Dump(dump_path),
            install=args.install,
            game=Path(args.game).expanduser() if args.game else None,
            collision_follow=args.collision_follow or None,
        )
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for line in result["log"]:
            print(f"{line['level']}: {line['message']}")
        if result["files"]:
            print("files: " + ", ".join(result["files"]))
        else:
            print("files: (none)")
    return 0


def print_collision_follow_test(args) -> int:
    """Write variant B (SRTHash re-mint) for the Kokiri sign and the nearest Land_."""
    from ocarina_forge.collision_follow import SIGN_BANC, prepare_variant_b
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.errors import BancError
    from ocarina_forge.export import build
    from ocarina_forge.modexport import export_mod
    from ocarina_forge.project import Project

    config = load_config()
    dump_path = args.dump or config.get("dump") or ""
    if not dump_path:
        print("collision-follow-test needs --dump (the Longshot folder that contains romfs/).", file=sys.stderr)
        return 2
    root = Path(args.project).expanduser() if args.project else Path(__file__).resolve().parents[1] / "examples" / "collision-follow-test"
    if not (root / "manifest.yaml").is_file():
        print(f"{root} has no manifest.yaml.", file=sys.stderr)
        return 2
    try:
        dump = Dump(dump_path)
        project = Project(root)
        lines = prepare_variant_b(dump, project, only_hash=args.id or None, banc=args.banc or SIGN_BANC)
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    print("Variant B moves each actor 4 m on +X and, unless --collision-follow off, mints a new SRTHash.")
    for line in lines:
        print(line)
    mode = args.collision_follow or None
    try:
        result = export_mod(
            project,
            dump,
            install=args.install,
            game=Path(args.game).expanduser() if args.game else None,
            collision_follow=mode,
        )
    except BancError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    for line in result["log"]:
        print(f"{line['level']}: {line['message']}")
    if result["files"]:
        print("files: " + ", ".join(result["files"]))
    print("In-game steps are in examples/collision-follow-test/README.md.")
    if args.romfs:
        print(
            "Writing a whole-banc romfs mod as well. Disable local/collision-follow-test before you test it, "
            "and do not leave both enabled."
        )
        try:
            built = build(project, dump, install=args.install, collision_follow=mode, level=19)
        except BancError as exc:
            print(exc.message, file=sys.stderr)
            return 2
        for line in built["log"]:
            print(f"romfs {line['level']}: {line['message']}")
        if built.get("installed"):
            print(f"romfs installed: {built['installed']}")
    return 0


def print_profile(args) -> int:
    from ocarina_forge.config import load_config
    from ocarina_forge.dump import Dump
    from ocarina_forge.profile import profile_dump

    config = load_config()
    dump_path = args.dump or config.get("dump") or ""
    if not dump_path:
        print("No dump is configured. Pass --dump.", file=sys.stderr)
        return 2
    if args.cprofile and len(args.model) != 1:
        print("--cprofile needs exactly one --model.", file=sys.stderr)
        return 2
    try:
        profile_dump(
            Dump(dump_path),
            model=args.model,
            cprofile=args.cprofile or None,
            limit=args.limit,
            jobs=args.jobs,
            home_cache=args.home_cache,
            keep_cache=args.keep_cache,
        )
    except SystemExit as exc:
        text = exc.code
        if text in (None, 0):
            return 0
        if isinstance(text, str):
            print(text, file=sys.stderr)
        return text if isinstance(text, int) else 2
    return 0


def _find_actor(dump, query: str, *, gyaml: str, actor: str):
    from ocarina_forge.actors import format_hash, parse_hash
    from ocarina_forge.errors import BancError
    from ocarina_forge.scene import scene_view

    scenes = [item["path"] for item in dump.list_scenes()]
    if query in scenes:
        bancs = [query]
    else:
        bancs = [path for path in scenes if query in path]
    if not bancs:
        raise BancError(f"No scene matches {query!r}.", "edit")
    wanted = format_hash(parse_hash(actor)) if actor else ""
    found = []
    for banc in bancs:
        try:
            document, _dict_id, _raw = dump.read_byml(banc)
        except BancError:
            continue
        view = scene_view(document, {"banc": banc, "actors": {}})
        for row in view["actors"]:
            if wanted and row["id"] != wanted:
                continue
            if gyaml and row.get("gyaml") != gyaml:
                continue
            if not wanted and not gyaml:
                continue
            found.append((banc, row["id"], document))
    if not gyaml and not wanted:
        raise BancError("Name the actor with --gyaml or --id.", "edit")
    if not found:
        raise BancError(f"No actor {gyaml or wanted} in {query}.", "edit")
    if len(found) > 1:
        shown = found[:8]
        listed = "; ".join(f"{item[1]} in {item[0]}" for item in shown)
        extra = "" if len(found) <= 8 else f"; and {len(found) - 8} more"
        who = gyaml or query
        raise BancError(
            f"error: {len(found)} actors match {who}. Nothing was written. "
            f"Pass --id with one hash. {listed}{extra}",
            "edit",
        )
    return found[0]


def _triple(text: str, label: str) -> list[float]:
    from ocarina_forge.errors import BancError

    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 3:
        raise BancError(f"{label} needs three comma-separated numbers.", "edit")
    try:
        return [float(part) for part in parts]
    except ValueError as exc:
        raise BancError(f"{label} needs three comma-separated numbers.", "edit") from exc


if __name__ == "__main__":
    main()
