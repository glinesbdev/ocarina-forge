"""Local HTTP API for the Bancwright editor. It binds to localhost in normal use."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from bancwright import __version__
from bancwright.config import load_config, save_config
from bancwright.dump import Dump
from bancwright.errors import BancError
from bancwright.export import build
from bancwright.fixtures import build_fixture
from bancwright.importers.ato import import_scenes
from bancwright.project import Project
from bancwright.scene import apply_command, replace_diff, scene_view

app = FastAPI(title="Bancwright", version=__version__)
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
        except BancError:
            modified = set()
    scenes = dump.list_scenes()
    known = {scene["path"] for scene in scenes}
    for path in sorted(modified - known):
        project_only.append(path)
    return {"scenes": scenes, "modified": sorted(modified & known), "projectOnly": project_only}


@app.get("/api/scene")
def get_scene(banc: str):
    dump = _open_dump()
    document, _dict_id, _raw = dump.read_byml(banc)
    diff = _diff_for(banc)
    view = scene_view(document, diff)
    view["banc"] = banc
    return view


@app.post("/api/scene/edit")
def edit_scene(body: dict):
    banc = body.get("banc")
    if not banc:
        raise BancError("Which scene? Open one from the list.", "edit")
    dump = _open_dump()
    project = _open_project()
    document, _dict_id, _raw = dump.read_byml(banc)
    diff = project.load_scene_diff(banc)
    updated, focus = apply_command(document, diff, body.get("command") or {})
    project.save_scene_diff(updated)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    view["focus"] = focus
    return view


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
    return view


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
