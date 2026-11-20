"""Local HTTP API for the Ocarina Forge editor. It binds to localhost in normal use."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import shutil
import signal
import threading
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ocarina_forge import __version__
from ocarina_forge.changes import project_report, scene_changes
from ocarina_forge.config import load_config, save_config
from ocarina_forge.dump import Dump
from ocarina_forge.errors import BancError
from ocarina_forge.export import build
from ocarina_forge.fixtures import build_fixture
from ocarina_forge.importers.ato import import_scenes
from ocarina_forge.models import (
    CacheMiss,
    _cached_glb,
    describe_actor,
    describe_actor_at,
    describe_terrain,
    describe_terrain_at,
)
from ocarina_forge.project import Project
from ocarina_forge.recipes import recipe_actors
from ocarina_forge.scene import apply_command, replace_diff, scene_view, sibling_bancs
from ocarina_forge.streamlog import STREAM
from ocarina_forge.warm import neighbor_bancs, scene_model_names, warm_queue

def _ignore_sigint() -> None:
    """Worker processes must not print KeyboardInterrupt when the terminal gets Ctrl+C."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _close_pool() -> None:
    """Drop queued converts and return without waiting on the workers."""
    global _POOL, _POOL_SIZE, _POOL_WANT, _POOL_INFLIGHT
    with _POOL_LOCK:
        pool = _POOL
        _POOL = None
        _POOL_SIZE = 0
        _POOL_WANT = None
        _POOL_INFLIGHT = 0
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


@asynccontextmanager
async def _lifespan(_app):
    yield
    if _WARM_TASK is not None:
        _WARM_TASK.cancel()
    _close_pool()


app = FastAPI(title="Ocarina Forge", version=__version__, lifespan=_lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_dump_cache: dict = {"key": None, "dump": None}
_POOL = None
_POOL_LOCK = threading.Lock()


_POOL_SIZE = 0
_POOL_INFLIGHT = 0
_POOL_WANT: int | None = None


def _configured_workers() -> tuple[int, int, str]:
    """1..cpu_count. The env var wins, then the saved setting, then one core left free."""
    from ocarina_forge.locate import setting

    cpus = os.cpu_count() or 1
    raw = setting("WORKERS")
    if raw is not None and str(raw).strip() != "":
        source = "env"
        try:
            count = int(raw)
        except ValueError:
            count = max(1, cpus - 1)
    else:
        saved = load_config().get("workers")
        if isinstance(saved, int) and not isinstance(saved, bool) and saved >= 1:
            source = "config"
            count = saved
        else:
            source = "default"
            count = max(1, cpus - 1)
    return max(1, min(cpus, count)), cpus, source


def _worker_public() -> dict:
    count, cpus, source = _configured_workers()
    return {"count": count, "max": cpus, "source": source, "saved": load_config().get("workers")}


def _install_pool(count: int) -> None:
    """Replace the pool. The caller holds ``_POOL_LOCK`` and no job is in flight."""
    global _POOL, _POOL_SIZE, _POOL_WANT
    old = _POOL
    _POOL = None
    _POOL_SIZE = count
    _POOL_WANT = None
    if count > 1:
        _POOL = ProcessPoolExecutor(
            max_workers=count,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_ignore_sigint,
        )
    if old is not None:
        old.shutdown(wait=False, cancel_futures=True)


def _job_finished(_future=None) -> None:
    global _POOL_INFLIGHT
    with _POOL_LOCK:
        _POOL_INFLIGHT = max(0, _POOL_INFLIGHT - 1)
        if _POOL_INFLIGHT == 0 and _POOL_WANT is not None and _POOL_WANT != _POOL_SIZE:
            _install_pool(_POOL_WANT)


def _pool():
    """Cold model converts run in child processes. A warm cache hit stays here.

    One worker stays in this process. A new count waits until the current jobs
    finish, then the pool is rebuilt.
    """
    global _POOL_WANT
    count, _cpus, _source = _configured_workers()
    with _POOL_LOCK:
        if count <= 1:
            _POOL_WANT = 1
            if _POOL_INFLIGHT == 0 and _POOL is not None:
                _install_pool(1)
            return None if _POOL_SIZE <= 1 else _POOL
        if _POOL is not None and _POOL_SIZE == count:
            return _POOL
        if _POOL is not None and _POOL_INFLIGHT > 0:
            _POOL_WANT = count
            return _POOL
        _install_pool(count)
        return _POOL


async def _submit(fn, *args):
    """Run in the pool when there is one, and rebuild that pool only between jobs."""
    global _POOL_INFLIGHT
    pool = _pool()
    if pool is None:
        return await asyncio.to_thread(fn, *args)
    with _POOL_LOCK:
        _POOL_INFLIGHT += 1
        future = pool.submit(fn, *args)
    future.add_done_callback(_job_finished)
    return await asyncio.wrap_future(future)


_CONVERTING: dict[str, asyncio.Future] = {}
_CONVERT_LOCK = asyncio.Lock()
_INTERACTIVE = 0
_WARM_QUEUE: list[tuple[str, str]] = []
_WARM_DONE = 0
_WARM_TOTAL = 0
_WARM_SCENE = ""
_WARM_SEEN: set[str] = set()
_WARM_TASK: asyncio.Task | None = None
_WARM_EVENT: asyncio.Event | None = None


def _warm_status() -> dict:
    return {
        "scene": _WARM_SCENE,
        "done": _WARM_DONE,
        "total": _WARM_TOTAL,
        "paused": _INTERACTIVE > 0,
        "left": sum(1 for kind, _name in _WARM_QUEUE if kind == "model"),
    }


def _kick_warm() -> None:
    if _WARM_EVENT is not None:
        _WARM_EVENT.set()


def _enter_interactive() -> None:
    global _INTERACTIVE
    _INTERACTIVE += 1
    _kick_warm()


def _leave_interactive() -> None:
    global _INTERACTIVE
    _INTERACTIVE = max(0, _INTERACTIVE - 1)
    _kick_warm()


async def _warm_loop() -> None:
    """One model at a time, and only while nothing interactive is waiting."""
    global _WARM_DONE, _WARM_TOTAL, _WARM_QUEUE
    while True:
        if _INTERACTIVE > 0 or not _WARM_QUEUE:
            assert _WARM_EVENT is not None
            _WARM_EVENT.clear()
            if _INTERACTIVE > 0 or not _WARM_QUEUE:
                await _WARM_EVENT.wait()
                continue
        kind, name = _WARM_QUEUE.pop(0)
        if _INTERACTIVE > 0:
            _WARM_QUEUE.insert(0, (kind, name))
            continue
        if kind == "scene":
            try:
                extra = await asyncio.to_thread(scene_model_names, _open_dump(), name)
            except Exception:
                extra = []
            fresh = []
            for item in extra:
                if item in _WARM_SEEN:
                    continue
                _WARM_SEEN.add(item)
                fresh.append(("model", item))
            _WARM_QUEUE[0:0] = fresh
            _WARM_TOTAL += len(fresh)
            _kick_warm()
            continue
        try:
            await _describe_model(name, interactive=False)
        except Exception:
            pass
        _WARM_DONE += 1


def _ensure_warm() -> None:
    global _WARM_TASK, _WARM_EVENT
    if _WARM_EVENT is None:
        _WARM_EVENT = asyncio.Event()
    if _WARM_TASK is None or _WARM_TASK.done():
        _WARM_TASK = asyncio.get_running_loop().create_task(_warm_loop())


def _publish(info: dict) -> dict:
    """Put the cache digest in the glb URL so a rebuild is a different browser cache entry."""
    digest = info.get("digest")
    url = info.get("url")
    if not digest or not url or "v=" in str(url):
        return info
    published = dict(info)
    join = "&" if "?" in url else "?"
    published["url"] = f"{url}{join}v={digest}"
    return published


def _cached_file(request: Request, path: Path, filename: str, digest: str):
    etag = f'"{digest}"'
    versioned = request.query_params.get("v") == digest
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="model/gltf-binary", filename=filename, headers=headers)


async def _convert(key: str, call):
    """One conversion per key. Waiters share it, and the process pool does the work."""
    async with _CONVERT_LOCK:
        existing = _CONVERTING.get(key)
        if existing is not None:
            owner = False
        else:
            existing = asyncio.get_running_loop().create_future()
            _CONVERTING[key] = existing
            owner = True
    if not owner:
        return await existing
    try:
        info = await call()
        existing.set_result(info)
        return info
    except Exception as exc:
        existing.set_exception(exc)
        existing.exception()
        raise
    finally:
        _CONVERTING.pop(key, None)


def _note_stream(kind: str, name: str, info: dict) -> dict:
    timing = info.get("timing") if isinstance(info, dict) else None
    if isinstance(timing, dict) and timing.get("cache"):
        STREAM.record(kind, name, timing)
    return info


async def _describe_model(gyaml: str, *, interactive: bool = True) -> dict:
    if interactive:
        _enter_interactive()
    try:
        dump = _open_dump()
        try:
            return _note_stream("model", gyaml, _publish(describe_actor(dump, gyaml, convert=False)))
        except CacheMiss:
            pass

        async def run():
            count, _cpus, _source = _configured_workers()
            if count <= 1:
                info = await _submit(describe_actor, dump, gyaml)
            else:
                info = await _submit(describe_actor_at, str(dump.root), gyaml)
            return _publish(info)

        return _note_stream("model", gyaml, await _convert(f"model:{gyaml}", run))
    finally:
        if interactive:
            _leave_interactive()


async def _describe_terrain_model(banc: str) -> dict:
    _enter_interactive()
    try:
        dump = _open_dump()
        try:
            return _note_stream("terrain", banc, _publish(describe_terrain(dump, banc, convert=False)))
        except CacheMiss:
            pass

        async def run():
            count, _cpus, _source = _configured_workers()
            if count <= 1:
                info = await _submit(describe_terrain, dump, banc)
            else:
                info = await _submit(describe_terrain_at, str(dump.root), banc)
            return _publish(info)

        return _note_stream("terrain", banc, await _convert(f"terrain:{banc}", run))
    finally:
        _leave_interactive()


def _queued_model(gyaml: str) -> dict:
    return {
        "gyaml": gyaml,
        "status": "queued",
        "reason": "Waiting to convert.",
        "source": "",
        "triangles": 0,
        "textures": 0,
        "url": f"/api/models/{gyaml}/model.glb",
    }


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


def _schedule_hot_reload() -> None:
    """Queue one publish. The request returns before the build finishes."""
    from ocarina_forge.hotreload import publish, publisher, describe

    config = load_config()
    hot = config.get("hotReload") or {}
    if not hot.get("enabled") or not hot.get("path"):
        return

    def run() -> None:
        current = load_config().get("hotReload") or {}
        if not current.get("enabled") or not current.get("path"):
            publisher.mark(status="off", message="Hot reload is off.", files=0)
            return
        try:
            result = publish(_open_project(), _open_dump(), current["path"])
            publisher.mark(status="ok", message=describe(result), files=len(result["files"]))
        except BancError as exc:
            publisher.mark(status="error", message=f"Hot reload: {exc.message}", files=0)
        except Exception as exc:
            publisher.mark(status="error", message=f"Hot reload: {exc}", files=0)

    publisher.schedule(run)


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


@app.get("/api/stream")
def stream_summary():
    """Convert, texture-decode, and cache-hit totals since the server started."""
    summary = STREAM.summary()
    summary["warm"] = _warm_status()
    return summary


@app.post("/api/warm")
async def start_warm(body: dict):
    """Queue this scene, nearest first, then neighboring scenes. Pauses while the view is converting."""
    global _WARM_QUEUE, _WARM_DONE, _WARM_TOTAL, _WARM_SCENE
    banc = str(body.get("banc") or "")
    nearest = [name for name in (body.get("names") or []) if isinstance(name, str) and name]
    dump = _open_dump()

    def plan() -> list[tuple[str, str]]:
        scenes = [item["path"] for item in dump.list_scenes()]
        if banc and banc not in scenes and banc not in {"all", "--all"}:
            raise BancError(f"No scene {banc}.", "not_found")
        if banc in {"all", "--all"}:
            return warm_queue([], [], scenes)
        return warm_queue(nearest, scene_model_names(dump, banc) if banc else [], neighbor_bancs(banc, scenes) if banc else [])

    _WARM_QUEUE = await asyncio.to_thread(plan)
    _WARM_DONE = 0
    _WARM_TOTAL = sum(1 for kind, _name in _WARM_QUEUE if kind == "model")
    _WARM_SEEN = {name for kind, name in _WARM_QUEUE if kind == "model"}
    _WARM_SCENE = banc
    _ensure_warm()
    _kick_warm()
    return _warm_status()


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
        "workers": _worker_public(),
    }


@app.put("/api/config")
def put_config(body: dict):
    dump = body.get("dump") if "dump" in body else None
    project = body.get("project") if "project" in body else None
    dump_to_save = None
    project_to_save = None
    workers = None
    if dump is not None:
        if str(dump).strip():
            dump_to_save = str(Dump(dump).root)
        else:
            dump_to_save = ""
        _dump_cache["key"] = None
    if project is not None:
        if str(project).strip():
            project_to_save = str(Project(project).root)
        else:
            project_to_save = ""
    if "workers" in body:
        try:
            workers = int(body.get("workers"))
        except (TypeError, ValueError) as exc:
            raise BancError("Converter cores must be a number.", "config") from exc
        _cpus = os.cpu_count() or 1
        if workers < 1 or workers > _cpus:
            raise BancError(f"Converter cores must be from 1 to {_cpus}.", "config")
    saved = save_config(dump=dump_to_save, project=project_to_save, workers=workers)
    return {"config": saved, "dump": _dump_status(), "project": _project_status(), "workers": _worker_public()}


@app.get("/api/hot-reload")
def get_hot_reload():
    from ocarina_forge.hotreload import publisher

    return publisher.public(load_config().get("hotReload") or {})


@app.put("/api/hot-reload")
def put_hot_reload(body: dict):
    from ocarina_forge.hotreload import destination_for, publisher

    current = load_config()
    stored = current.get("hotReload") or {}
    enabled = bool(body["enabled"]) if "enabled" in body else bool(stored.get("enabled"))
    path = str(body["path"]) if "path" in body else str(stored.get("path") or "")
    if enabled:
        dump = current.get("dump") or ""
        project = current.get("project") or ""
        destination_for(
            path,
            dump_root=Path(dump) if dump else None,
            project_root=Path(project) if project else None,
        )
    saved = save_config(hot_reload={"enabled": enabled, "path": path})
    if enabled:
        publisher.mark(
            status="idle",
            message=(
                f"Hot reload is on. The next save writes into {path}. "
                "Leave the map and enter it again to see a change."
            ),
            files=0,
        )
    else:
        publisher.mark(status="off", message="Hot reload is off.", files=0)
    return publisher.public(saved["hotReload"])


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


@app.get("/api/scene/problems")
def scene_problems(banc: str):
    if not banc:
        raise BancError("Which scene?", "edit")
    from ocarina_forge.validate import diagnose

    return diagnose(_open_dump(), _open_project(), banc)


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
        from ocarina_forge.recipes import apply_recipe_command

        focus = apply_recipe_command(project, banc, command)
        document, _dict_id, _raw = dump.read_byml(banc)
        view = scene_view(document, project.load_scene_diff(banc))
        view["banc"] = banc
        view["focus"] = focus
        _schedule_hot_reload()
        return _attach_recipes(view, banc)
    document, _dict_id, _raw = dump.read_byml(banc)
    diff = project.load_scene_diff(banc)
    updated, focus = apply_command(document, diff, command)
    project.save_scene_diff(updated)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    view["focus"] = focus
    _schedule_hot_reload()
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
    _schedule_hot_reload()
    return _attach_recipes(view, banc)


@app.put("/api/scene/recipes")
def put_recipes(body: dict):
    banc = body.get("banc")
    if not banc:
        raise BancError("Which scene?", "edit")
    dump = _open_dump()
    project = _open_project()
    from ocarina_forge.recipes import replace_recipes

    replace_recipes(project, banc, body.get("recipes") or [])
    document, _dict_id, _raw = dump.read_byml(banc)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    _schedule_hot_reload()
    return _attach_recipes(view, banc)


@app.post("/api/models/batch")
async def models_batch(body: dict):
    """Cache hits now, and a glb URL for everything still to convert. One round trip."""
    names: list[str] = []
    seen: set[str] = set()
    for name in body.get("names") or []:
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= 2000:
            break
    dump = _open_dump()

    def lookup() -> dict[str, dict]:
        found: dict[str, dict] = {}
        for name in names:
            try:
                found[name] = _publish(describe_actor(dump, name, convert=False))
            except CacheMiss:
                found[name] = _queued_model(name)
        return found

    return {"models": await asyncio.to_thread(lookup)}


@app.get("/api/models/{gyaml}")
async def model_info(gyaml: str):
    return await _describe_model(gyaml)


@app.get("/api/models/{gyaml}/model.glb")
async def model_file(gyaml: str, request: Request):
    info = await _describe_model(gyaml)
    digest = info.get("digest") or ""
    path = _cached_glb(digest) if info.get("status") == "mesh" else None
    if path is None:
        raise BancError(info.get("reason") or "This actor has no converted model.", "not_found")
    return _cached_file(request, path, f"{gyaml}.glb", digest)


@app.get("/api/terrain")
async def terrain_info(banc: str):
    return await _describe_terrain_model(banc)


@app.get("/api/terrain/model.glb")
async def terrain_file(banc: str, request: Request):
    info = await _describe_terrain_model(banc)
    digest = info.get("digest") or ""
    path = _cached_glb(digest) if info.get("status") == "mesh" else None
    if path is None:
        raise BancError(info.get("reason") or "This scene has no converted map model.", "not_found")
    return _cached_file(request, path, "terrain.glb", digest)


@app.get("/api/behavior")
def behavior(gyaml: str = "", banc: str = "", id: str = ""):
    from ocarina_forge.behavior import load_actor_graph
    from ocarina_forge.behavior_edit import apply_ops

    dump = _open_dump()
    graph = load_actor_graph(dump, gyaml=gyaml, banc=banc, actor_id=id)
    ops = []
    try:
        ops = _open_project().load_behavior_ops(graph["gyaml"])
    except BancError:
        ops = []
    return apply_ops(graph, ops)


@app.post("/api/behavior/field")
def behavior_field(body: dict):
    from ocarina_forge.behavior_edit import set_field

    gyaml = str(body.get("gyaml") or "")
    if not gyaml:
        raise BancError("Which actor?", "behavior")
    return set_field(
        _open_dump(),
        _open_project(),
        gyaml=gyaml,
        node_id=str(body.get("id") or ""),
        field=str(body.get("field") or ""),
        value=body.get("value"),
        type_name=str(body.get("type") or ""),
    )


@app.put("/api/behavior/ops")
def behavior_ops(body: dict):
    from ocarina_forge.behavior_edit import replace_ops

    gyaml = str(body.get("gyaml") or "")
    if not gyaml:
        raise BancError("Which actor?", "behavior")
    return replace_ops(_open_dump(), _open_project(), gyaml=gyaml, ops=body.get("ops") or [])


@app.post("/api/behavior/export")
def behavior_export(body: dict | None = None):
    from ocarina_forge.behavior_edit import export_behaviors

    body = body or {}
    project = _open_project()
    results = export_behaviors(project, _open_dump(), gyaml=str(body.get("gyaml") or ""))
    blocked = [row for row in results if not row.get("exported")]
    return {"ok": not blocked, "files": results}


@app.get("/api/flags")
def flags(q: str = ""):
    from ocarina_forge.validate import gamedata_names

    catalog = gamedata_names(_open_dump())
    query = q.strip().lower()
    if catalog is None or len(query) < 2:
        return {"names": []}
    names = sorted(name for name in catalog.strings if query in name.lower())
    return {"names": names[:40]}


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
