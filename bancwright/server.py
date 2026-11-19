"""Local HTTP API for the Bancwright editor. It binds to localhost in normal use."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from bancwright import __version__
from bancwright.changes import project_report, scene_changes
from bancwright.config import load_config, save_config
from bancwright.dump import Dump
from bancwright.errors import BancError
from bancwright.export import build
from bancwright.fixtures import build_fixture
from bancwright.importers.ato import import_scenes
from bancwright.models import actor_glb, describe_actor, describe_terrain, terrain_glb
from bancwright.project import Project
from bancwright.recipes import recipe_actors
from bancwright.scene import apply_command, replace_diff, scene_view, sibling_bancs

app = FastAPI(title="Bancwright", version=__version__)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_dump_cache: dict = {"key": None, "dump": None}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@app.exception_handler(BancError)
def _banc_error(_request, exc: BancError):
    status = {"dump_missing": 409, "dump_invalid": 409, "not_found": 404, "project": 404}.get(exc.code, 400)
    return JSONResponse({"error": exc.code, "message": exc.message}, status_code=status)


def _open_dump() -> Dump:
    config = load_config()
    if not config["dump"]:
        raise BancError(
            "No game dump is configured. Set the path to your Longshot folder, the one that contains romfs/ "
            "(usually ~/Games/Longshot/rc/v3.0.0).",
            "dump_missing",
        )
    if _dump_cache["key"] != config["dump"]:
        _dump_cache["dump"] = Dump(config["dump"])
        _dump_cache["key"] = config["dump"]
    return _dump_cache["dump"]


def _open_project() -> Project:
    config = load_config()
    if not config["project"]:
        raise BancError("No mod project is open. Create one, or open a folder that has a manifest.yaml.", "project")
    return Project(config["project"])


def _dump_status() -> dict | None:
    config = load_config()
    if not config["dump"]:
        return None
    try:
        dump = _open_dump()
        scenes = dump.list_scenes()
        return {
            "root": str(dump.root),
            "romfs": str(dump.romfs),
            "synthetic": dump.synthetic,
            "scenes": len(scenes),
            "actors": len(dump.actor_names()),
            "rstb": dump.rstb_relative(),
        }
    except BancError as exc:
        return {"error": exc.code, "message": exc.message}


def _project_status() -> dict | None:
    config = load_config()
    if not config["project"]:
        return None
    try:
        return Project(config["project"]).summary()
    except BancError as exc:
        return {"error": exc.code, "message": exc.message, "root": config["project"]}


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status():
    config = load_config()
    dump = _dump_status()
    project = _project_status()
    ready = bool(dump) and "error" not in dump and bool(project) and "error" not in project
    return {
        "version": __version__,
        "config": config,
        "dump": dump,
        "project": project,
        "ready": ready,
        "repo": str(repo_root()),
    }


@app.put("/api/config")
def put_config(body: dict):
    dump = body.get("dump") if "dump" in body else None
    project = body.get("project") if "project" in body else None
    dump_to_save = None
    project_to_save = None
    if dump is not None:
        if str(dump).strip():
            dump_to_save = str(Dump(dump).root)
        else:
            dump_to_save = ""
    if project is not None:
        if str(project).strip():
            project_to_save = str(Project(project).root)
        else:
            project_to_save = ""
    _dump_cache["key"] = None
    saved = save_config(dump=dump_to_save, project=project_to_save)
    return {"config": saved, "dump": _dump_status(), "project": _project_status()}


@app.post("/api/setup/sample")
def setup_sample():
    """Point the app at the synthetic dump and a copy of the sample project."""
    root = repo_root()
    dump_path = build_fixture(root / "fixtures" / "dump")
    destination = root / "projects" / "sample"
    example = root / "examples" / "sample-project"
    if not (destination / "manifest.yaml").is_file():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(example, destination)
    _dump_cache["key"] = None
    save_config(dump=str(dump_path), project=str(destination))
    return status()


@app.post("/api/project")
def create_project(body: dict):
    directory = body.get("directory")
    if not directory:
        raise BancError("Choose a folder for the project.", "project")
    project = Project.create(
        Path(directory),
        name=str(body.get("name") or "my-mod"),
        title=str(body.get("title") or body.get("name") or "My mod"),
        author=str(body.get("author") or ""),
        version=str(body.get("version") or "0.1.0"),
    )
    save_config(project=str(project.root))
    return project.summary()


@app.post("/api/project/open")
def open_project(body: dict):
    directory = body.get("directory")
    if not directory:
        raise BancError("Choose a project folder.", "project")
    project = Project(directory)
    save_config(project=str(project.root))
    return project.summary()


@app.get("/api/scenes")
def list_scenes():
    dump = _open_dump()
    modified: set[str] = set()
    project_only: list[str] = []
    config = load_config()
    if config["project"]:
        try:
            project = Project(config["project"])
            modified = set(project.modified_scenes())
            recipes = set(project.summary()["catalog"]["recipeBancs"])
        except BancError:
            modified = set()
            recipes = set()
    else:
        recipes = set()
    scenes = dump.list_scenes()
    known = {scene["path"] for scene in scenes}
    for path in sorted((modified | recipes) - known):
        project_only.append(path)
    change_counts = {}
    if config["project"] and config["dump"]:
        try:
            report = project_report(dump, Project(config["project"]))
            change_counts = {item["banc"]: item["counts"] for item in report["maps"]}
        except BancError:
            change_counts = {}
    return {
        "scenes": scenes,
        "modified": sorted(modified & known),
        "recipes": sorted(recipes & known),
        "projectOnly": project_only,
        "changes": change_counts,
    }


@app.get("/api/changes")
def project_changes():
    dump = _open_dump()
    project = _open_project()
    return project_report(dump, project)


@app.get("/api/scene/changes")
def scene_change_list(banc: str):
    if not banc:
        raise BancError("Which scene?", "edit")
    dump = _open_dump()
    project = _open_project()
    return scene_changes(dump, project, banc)


def _attach_companions(view: dict, banc: str, dump: Dump) -> dict:
    """Sibling Static/Dynamic actors, kept separate so the host diff stays the host."""
    try:
        known = {scene["path"] for scene in dump.list_scenes()}
    except BancError:
        return view
    companions = []
    for path in sibling_bancs(banc, known):
        try:
            document, _dict_id, _raw = dump.read_byml(path)
        except BancError:
            continue
        other = scene_view(document, _diff_for(path))
        other["banc"] = path
        other = _attach_recipes(other, path)
        companions.append(
            {
                "banc": path,
                "actors": other.get("actors") or [],
                "removed": other.get("removed") or [],
                "diff": other.get("diff") or {"actors": {}},
            }
        )
    if companions:
        view["companions"] = companions
    return view


def _attach_recipes(view: dict, banc: str) -> dict:
    config = load_config()
    if not config.get("project"):
        return view
    try:
        extras = recipe_actors(Project(config["project"]), banc)
    except BancError:
        return view
    if extras:
        view["actors"] = [*(view.get("actors") or []), *extras]
    return view


@app.get("/api/scene")
def get_scene(banc: str):
    dump = _open_dump()
    document, _dict_id, _raw = dump.read_byml(banc)
    diff = _diff_for(banc)
    view = scene_view(document, diff)
    view["banc"] = banc
    view = _attach_recipes(view, banc)
    return _attach_companions(view, banc, dump)


@app.post("/api/scene/edit")
def edit_scene(body: dict):
    banc = body.get("banc")
    if not banc:
        raise BancError("Which scene? Open one from the list.", "edit")
    dump = _open_dump()
    project = _open_project()
    command = body.get("command") or {}
    ident = str(command.get("id") or "")
    if ident.startswith("recipe:") or command.get("command") in ("recipe_restore",):
        from bancwright.recipes import apply_recipe_command

        focus = apply_recipe_command(project, banc, command)
        document, _dict_id, _raw = dump.read_byml(banc)
        view = scene_view(document, project.load_scene_diff(banc))
        view["banc"] = banc
        view["focus"] = focus
        return _attach_recipes(view, banc)
    document, _dict_id, _raw = dump.read_byml(banc)
    diff = project.load_scene_diff(banc)
    updated, focus = apply_command(document, diff, command)
    project.save_scene_diff(updated)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    view["focus"] = focus
    return _attach_recipes(view, banc)


@app.put("/api/scene/diff")
def put_diff(body: dict):
    banc = body.get("banc")
    if not banc:
        raise BancError("Which scene?", "edit")
    dump = _open_dump()
    project = _open_project()
    document, _dict_id, _raw = dump.read_byml(banc)
    updated = replace_diff(document, banc, (body.get("actors") or {}))
    project.save_scene_diff(updated)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    return _attach_recipes(view, banc)


@app.put("/api/scene/recipes")
def put_recipes(body: dict):
    banc = body.get("banc")
    if not banc:
        raise BancError("Which scene?", "edit")
    dump = _open_dump()
    project = _open_project()
    from bancwright.recipes import replace_recipes

    replace_recipes(project, banc, body.get("recipes") or [])
    document, _dict_id, _raw = dump.read_byml(banc)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    return _attach_recipes(view, banc)


@app.get("/api/models/{gyaml}")
def model_info(gyaml: str):
    return describe_actor(_open_dump(), gyaml)


@app.get("/api/models/{gyaml}/model.glb")
def model_file(gyaml: str):
    path = actor_glb(_open_dump(), gyaml)
    if path is None:
        raise BancError("This actor has no converted model.", "not_found")
    return FileResponse(path, media_type="model/gltf-binary", filename=f"{gyaml}.glb")


@app.get("/api/terrain")
def terrain_info(banc: str):
    return describe_terrain(_open_dump(), banc)


@app.get("/api/terrain/model.glb")
def terrain_file(banc: str):
    path = terrain_glb(_open_dump(), banc)
    if path is None:
        raise BancError("This scene has no converted map model.", "not_found")
    return FileResponse(path, media_type="model/gltf-binary", filename="terrain.glb")


@app.get("/api/actors")
def actors(q: str = ""):
    dump = _open_dump()
    names = dump.actor_names()
    query = q.strip().lower()
    if query:
        names = [name for name in names if query in name.lower()]
    return {"names": names[:400], "total": len(dump.actor_names()), "matched": len(names)}


@app.post("/api/export")
def export_mod(body: dict | None = None):
    body = body or {}
    dump = _open_dump()
    project = _open_project()
    return build(project, dump, install=bool(body.get("install")), make_zip=bool(body.get("zip")))


@app.get("/api/export/download")
def download_zip():
    project = _open_project()
    folder = project.root / "build"
    zips = sorted(folder.glob("*.zip")) if folder.is_dir() else []
    if not zips:
        raise BancError("No zip has been built yet. Build the mod with the zip option first.", "not_found")
    return FileResponse(zips[-1], filename=zips[-1].name, media_type="application/zip")


@app.post("/api/import/ato")
def import_ato(body: dict):
    path = body.get("path")
    if not path:
        raise BancError("Give the path to a scenes folder or a single YAML file.", "import")
    project = _open_project()
    messages = import_scenes(project, Path(path))
    return {"ok": True, "messages": messages, "project": project.summary()}


def _diff_for(banc: str) -> dict:
    config = load_config()
    if not config["project"]:
        return {"format": 1, "banc": banc, "actors": {}}
    try:
        return Project(config["project"]).load_scene_diff(banc)
    except BancError:
        return {"format": 1, "banc": banc, "actors": {}}


def mount_frontend(directory: Path) -> None:
    if (directory / "index.html").is_file():
        app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")
