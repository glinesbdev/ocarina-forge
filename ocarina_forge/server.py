"""Local HTTP API for the Ocarina Forge editor. It binds to localhost in normal use."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import threading
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from ocarina_forge import __version__
from ocarina_forge.buildstamp import read_build
from ocarina_forge.editor_static import EditorStaticFiles
from ocarina_forge.actors import is_marker
from ocarina_forge.changes import project_report, scene_changes
from ocarina_forge.cache_prune import cache_public, note_access, start_maintenance, stop_maintenance
from ocarina_forge.config import load_config, parse_cache_limit, save_config
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
    describe_actor_textures,
    describe_terrain,
    describe_terrain_at,
)
from ocarina_forge.procjob import (
    ensure_server_job,
    log_leftover_workers,
    pool_executor_kwargs,
    prepare_pool_worker,
    terminate_executor,
)
from ocarina_forge.project import Project
from ocarina_forge.recipes import recipe_actors
from ocarina_forge.scene import apply_command, keep_unloaded_diff, replace_diff, scene_view, sibling_bancs
from ocarina_forge.streamlog import STREAM
from ocarina_forge.grounddebug import query_ground
from ocarina_forge.lighting import lighting_report
from ocarina_forge.surface_pack import load_or_build_surface
from ocarina_forge.scene_preview import corrected_terrain_path, prepare_scene_terrain, preview_bank, preview_path, terrain_fit_pending
from ocarina_forge.shared_models import VERSION as SHARED_MODEL_VERSION, shared_model_path, shared_texture_path
from ocarina_forge.warm import scene_warm_queue

def _ignore_sigint() -> None:
    """Worker processes must not print KeyboardInterrupt when the terminal gets Ctrl+C."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _worker_init() -> None:
    """Pool start: ignore Ctrl+C, then tie this process to the API's lifetime."""
    _ignore_sigint()
    prepare_pool_worker()


def _close_pool() -> None:
    """Drop queued converts and kill the workers instead of waiting on them."""
    global _POOL, _POOL_SIZE, _POOL_WANT, _POOL_INFLIGHT
    with _POOL_LOCK:
        pool = _POOL
        _POOL = None
        _POOL_SIZE = 0
        _POOL_WANT = None
        _POOL_INFLIGHT = 0
    if pool is not None:
        terminate_executor(pool)


@asynccontextmanager
async def _lifespan(_app):
    ensure_server_job()
    log_leftover_workers()
    start_maintenance()
    try:
        yield
    finally:
        stop_maintenance()
        if _WARM_TASK is not None:
            _WARM_TASK.cancel()
        _close_pool()


_BUILD = read_build()

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
_PREFETCH: set[asyncio.Task] = set()
_TERRAIN_FIT_LOCK = asyncio.Lock()
_GROUND = {}
_GROUND_LOCK = threading.Lock()
_GROUND_QUERY = threading.Lock()


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
    ensure_server_job()
    if count > 1:
        _POOL = ProcessPoolExecutor(**pool_executor_kwargs(count, _worker_init))
    if old is not None:
        terminate_executor(old)


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


@dataclass
class _WarmJob:
    job_id: int
    scene: str
    queue: list[str]
    total: int
    done: int = 0
    in_flight: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    phase: str = "warming"


_WARM_JOB: _WarmJob | None = None
_WARM_JOB_ID = 0
_WARM_TASK: asyncio.Task | None = None
_WARM_EVENT: asyncio.Event | None = None


def _warm_status() -> dict:
    job = _WARM_JOB
    if job is None:
        return {
            "jobId": None,
            "scene": "",
            "phase": "idle",
            "done": 0,
            "total": 0,
            "inFlight": 0,
            "left": 0,
            "failed": 0,
            "failures": [],
            "paused": _INTERACTIVE > 0,
        }
    return {
        "jobId": job.job_id,
        "scene": job.scene,
        "phase": job.phase,
        "done": job.done,
        "total": job.total,
        "inFlight": job.in_flight,
        "left": len(job.queue),
        "failed": job.failed,
        "failures": job.failures[:5],
        "paused": _INTERACTIVE > 0,
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


def _warm_failure(name: str, result) -> dict | None:
    if isinstance(result, BaseException):
        message = str(result) or result.__class__.__name__
    elif not isinstance(result, dict):
        message = "Model conversion returned no metadata."
    elif result.get("status") != "mesh":
        reason = result.get("reason") or f"Model is unavailable ({result.get('status', 'unknown')})."
        if reason.startswith(("No mesh.", "Marker")) or (result.get("status") == "box" and is_marker(name)):
            return None
        message = reason
    else:
        return None
    return {"name": name, "message": message[:500]}


def _record_warm_result(job: _WarmJob, name: str, result) -> None:
    if _WARM_JOB is not job:
        return
    job.in_flight = max(0, job.in_flight - 1)
    job.done += 1
    failure = _warm_failure(name, result)
    if failure:
        job.failed += 1
        if len(job.failures) < 20:
            job.failures.append(failure)


async def _warm_loop() -> None:
    """Prepare a scene with bounded concurrency, yielding to interactive converts."""
    active: dict[asyncio.Task, tuple[_WarmJob, str]] = {}
    try:
        while True:
            job = _WARM_JOB
            if job is not None and job.phase == "warming":
                if not job.queue and not job.in_flight:
                    job.phase = "failed" if job.failed else "ready"
                    continue
                worker_count, _cpus, _source = _configured_workers()
                limit = min(2, max(1, worker_count))
                while job.queue and _INTERACTIVE == 0 and len(active) < limit:
                    name = job.queue.pop(0)
                    task = asyncio.create_task(_describe_model(name, interactive=False))
                    active[task] = (job, name)
                    job.in_flight += 1
                if active:
                    completed, _pending = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                    for task in completed:
                        owner, name = active.pop(task)
                        try:
                            result = task.result()
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            result = exc
                        _record_warm_result(owner, name, result)
                    continue
            if active:
                completed, _pending = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in completed:
                    owner, name = active.pop(task)
                    try:
                        result = task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        result = exc
                    _record_warm_result(owner, name, result)
                continue
            assert _WARM_EVENT is not None
            _WARM_EVENT.clear()
            if _WARM_JOB is not job:
                continue
            if job is not None and job.phase == "warming" and job.queue and _INTERACTIVE == 0:
                continue
            await _WARM_EVENT.wait()
    except asyncio.CancelledError:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        raise


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
    version = digest
    if url.startswith("/api/models/") and shared_model_path(digest).is_file():
        version = f"{digest}-{SHARED_MODEL_VERSION}"
    published["url"] = f"{url}{join}v={version}"
    return published


def _cached_file(request: Request, path: Path, filename: str, digest: str):
    note_access(path)
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


async def _describe_model(gyaml: str, *, interactive: bool = True, flat: bool = False) -> dict:
    if interactive:
        _enter_interactive()
    try:
        dump = _open_dump()
        try:
            return _note_stream(
                "model",
                gyaml,
                _publish(describe_actor(dump, gyaml, convert=False, flat=True) if flat else describe_actor(dump, gyaml, convert=False)),
            )
        except CacheMiss:
            pass

        async def run():
            count, _cpus, _source = _configured_workers()
            if count <= 1:
                info = await _submit(_describe_actor_here, dump, gyaml, flat)
            else:
                info = await _submit(describe_actor_at, str(dump.root), gyaml, flat)
            return _publish(info)

        return _note_stream("model", gyaml, await _convert(f"model:{gyaml}:{'flat' if flat else 'textured'}", run))
    finally:
        if interactive:
            _leave_interactive()


def _describe_actor_here(dump, gyaml: str, flat: bool = False):
    if flat:
        return describe_actor(dump, gyaml, flat=True)
    return describe_actor(dump, gyaml)


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



def _clear_ground_cache() -> None:
    with _GROUND_LOCK:
        _GROUND.clear()


def query_ground_cached(dump, banc: str, points: list[tuple[float, float]], exclude_ids=None) -> list[dict]:
    """Reuse height tiles and floor meshes across snap queries for one dump+scene."""
    key = (str(dump.root), banc)
    with _GROUND_QUERY:
        with _GROUND_LOCK:
            state = _GROUND.setdefault(key, {"report": None, "mesh_cache": {}})
            report = state["report"]
            mesh_cache = state["mesh_cache"]
        result = query_ground(
            dump, banc, points, mesh_cache=mesh_cache, tile_report=report, exclude_ids=exclude_ids,
        )
        with _GROUND_LOCK:
            state = _GROUND.setdefault(key, {"report": None, "mesh_cache": mesh_cache})
            state["report"] = result["report"]
            state["mesh_cache"] = mesh_cache
        return result["samples"]


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
    return {"ok": True, "version": __version__, "build": _BUILD}


@app.get("/api/stream")
def stream_summary():
    """Convert, texture-decode, and cache-hit totals since the server started."""
    summary = STREAM.summary()
    summary["warm"] = _warm_status()
    return summary


@app.post("/api/warm")
async def start_warm(body: dict):
    """Prepare one scene and its Static/Dynamic sibling before opening it."""
    global _WARM_JOB, _WARM_JOB_ID
    banc = str(body.get("banc") or "")
    nearest = [name for name in (body.get("names") or []) if isinstance(name, str) and name]
    if not banc:
        raise BancError("Which scene?", "edit")
    dump = _open_dump()

    try:
        names = await asyncio.to_thread(scene_warm_queue, dump, banc, nearest)
    except ValueError as exc:
        raise BancError(str(exc), "not_found") from exc
    spec = _load_spec(str(body.get("load") or ""), str(body.get("textures") or ""))
    if spec is not None:
        names = [name for name in names if _layer_allowed(name, spec)]
    _WARM_JOB_ID += 1
    _WARM_JOB = _WarmJob(
        job_id=_WARM_JOB_ID,
        scene=banc,
        queue=names,
        total=len(names),
        phase="warming" if names else "ready",
    )
    _ensure_warm()
    _kick_warm()
    return _warm_status()


@app.get("/api/warm")
def warm_status():
    return _warm_status()


@app.get("/api/status")
def status():
    config = load_config()
    dump = _dump_status()
    project = _project_status()
    ready = bool(dump) and "error" not in dump and bool(project) and "error" not in project
    return {
        "version": __version__,
        "build": _BUILD,
        "config": config,
        "dump": dump,
        "project": project,
        "ready": ready,
        "repo": str(repo_root()),
        "workers": _worker_public(),
        "cache": cache_public(),
    }


@app.get("/api/load-filters")
def get_load_filters():
    from ocarina_forge.layers import default_filters

    try:
        project = _open_project()
    except BancError:
        return default_filters()
    try:
        return project.load_filters()
    except Exception:
        return default_filters()


@app.put("/api/load-filters")
def put_load_filters(body: dict):
    from ocarina_forge.layers import normalize_filters

    normal = normalize_filters(body if isinstance(body, dict) else {})
    try:
        project = _open_project()
    except BancError:
        return normal
    return project.write_load_filters(normal)


@app.put("/api/config")
def put_config(body: dict):
    dump = body.get("dump") if "dump" in body else None
    project = body.get("project") if "project" in body else None
    dump_to_save = None
    project_to_save = None
    workers = None
    cache_limit = None
    set_cache_limit = False
    if dump is not None:
        if str(dump).strip():
            dump_to_save = str(Dump(dump).root)
        else:
            dump_to_save = ""
        _dump_cache["key"] = None
        _clear_ground_cache()
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
    if "cacheLimit" in body:
        cache_limit = parse_cache_limit(body.get("cacheLimit"))
        if cache_limit is None:
            raise BancError(
                "Cache limit must be a byte count, 0 for unlimited, or a size such as 20G.",
                "config",
            )
        set_cache_limit = True
    saved = save_config(
        dump=dump_to_save,
        project=project_to_save,
        workers=workers,
        **({"cache_limit": cache_limit} if set_cache_limit else {}),
    )
    return {
        "config": saved,
        "dump": _dump_status(),
        "project": _project_status(),
        "workers": _worker_public(),
        "cache": cache_public(),
    }


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


@app.get("/api/projects")
def get_projects():
    from ocarina_forge.projects import list_projects, projects_root

    config = load_config()
    return {"root": str(projects_root(config)), "projects": list_projects(config)}


@app.get("/api/local-mods")
def get_local_mods():
    from ocarina_forge.projects import game_root, list_local_mods

    config = load_config()
    return {
        "root": str(game_root(config) / "mods" / "local"),
        "mods": list_local_mods(config),
    }


@app.post("/api/local-mods/open")
def open_local_mod(body: dict):
    from ocarina_forge.importers.longshot_mod import import_local_mod, local_slug
    from ocarina_forge.projects import game_root, list_projects, projects_root

    identifier = body.get("id")
    if not isinstance(identifier, str):
        raise BancError("Choose a local mod id.", "mod_import")
    slug = local_slug(identifier)
    config = load_config()
    existing = next((row for row in list_projects(config) if row["modId"] == identifier), None)
    if existing:
        project = Project(existing["root"])
    else:
        source = game_root(config) / "mods" / "local" / slug
        project = import_local_mod(source, projects_root(config) / slug, identifier)
    save_config(project=str(project.root))
    return project.summary()


@app.post("/api/project")
def create_project(body: dict):
    from ocarina_forge.projects import create_in_library

    name = str(body.get("name") or "my-mod")
    title = str(body.get("title") or body.get("name") or "My mod")
    author = str(body.get("author") or "")
    version = str(body.get("version") or "0.1.0")
    description = str(body.get("description") or "")
    directory = body.get("directory")
    if directory:
        project = Project.create(
            Path(directory),
            name=name,
            title=title,
            author=author,
            version=version,
            description=description,
        )
    else:
        project = create_in_library(
            name,
            title=title,
            author=author,
            version=version,
            description=description,
            config=load_config(),
        )
    save_config(project=str(project.root))
    return project.summary()


@app.post("/api/project/open")
def open_project(body: dict):
    from ocarina_forge.projects import resolve_project

    directory = body.get("directory")
    if not directory:
        raise BancError("Choose a project folder.", "project")
    project = Project(resolve_project(str(directory), load_config()))
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


@app.get("/api/scene/mesh-audit")
def scene_mesh_audit(banc: str):
    from ocarina_forge.meshaudit import audit_scene_meshes

    dump = _open_dump()

    def view(bank):
        document, _, _ = dump.read_byml(bank)
        return _attach_recipes(scene_view(document, _diff_for(bank)), bank)

    return audit_scene_meshes(dump, banc, view=view)


def _load_spec(load: str = "", textures: str = ""):
    from ocarina_forge.layers import parse_load_query

    return parse_load_query(load or "", textures)


def _skipped_model(gyaml: str) -> dict:
    return {
        "gyaml": gyaml,
        "status": "skipped",
        "reason": "This layer is not loaded.",
        "source": "",
        "triangles": 0,
        "textures": 0,
        "bounds": None,
        "url": None,
        "digest": "",
        "textureLog": [],
    }


def _apply_load_filter(view: dict, load: str = "", textures: str = "") -> dict:
    spec = _load_spec(load, textures)
    if spec is None:
        return view
    from ocarina_forge.layers import filter_attached

    return filter_attached(view, spec)


def _layer_allowed(gyaml: str, spec) -> bool:
    if spec is None:
        return True
    from ocarina_forge.layers import actor_layer

    return actor_layer(gyaml) in spec.layers


@app.get("/api/scene")
def get_scene(banc: str, load: str = "", textures: str = ""):
    dump = _open_dump()
    document, _dict_id, _raw = dump.read_byml(banc)
    diff = _diff_for(banc)
    view = scene_view(document, diff)
    view["banc"] = banc
    view = _attach_recipes(view, banc)
    view = _attach_companions(view, banc, dump)
    preview = preview_bank(dump, banc, {extra["banc"] for extra in view.get("companions", [])})
    try:
        version = preview_path(dump, preview).stat().st_mtime_ns
    except FileNotFoundError:
        pass
    else:
        view["previewUrl"] = "/api/scene/preview.glb?" + urlencode({"banc": preview, "v": version})
    return _apply_load_filter(view, load, textures)


@app.get("/api/scene/preview.glb")
def scene_preview_file(banc: str, request: Request):
    path = preview_path(_open_dump(), banc)
    try:
        version = str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        raise BancError("This scene has no preview pack.", "not_found") from None
    return _cached_file(request, path, "scene-preview.glb", version)


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
        return _apply_load_filter(_attach_recipes(view, banc), str(body.get("load") or ""), str(body.get("textures") or ""))
    document, _dict_id, _raw = dump.read_byml(banc)
    diff = project.load_scene_diff(banc)
    updated, focus = apply_command(document, diff, command)
    project.save_scene_diff(updated)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    view["focus"] = focus
    _schedule_hot_reload()
    return _apply_load_filter(_attach_recipes(view, banc), str(body.get("load") or ""), str(body.get("textures") or ""))


@app.put("/api/scene/diff")
def put_diff(body: dict):
    banc = body.get("banc")
    if not banc:
        raise BancError("Which scene?", "edit")
    dump = _open_dump()
    project = _open_project()
    document, _dict_id, _raw = dump.read_byml(banc)
    stored = project.load_scene_diff(banc)
    if isinstance(body.get("loaded"), list):
        updated = keep_unloaded_diff(document, banc, body.get("actors") or {}, stored, body.get("loaded"))
    else:
        updated = replace_diff(document, banc, (body.get("actors") or {}))
    project.save_scene_diff(updated)
    view = scene_view(document, project.load_scene_diff(banc))
    view["banc"] = banc
    _schedule_hot_reload()
    return _apply_load_filter(_attach_recipes(view, banc), str(body.get("load") or ""), str(body.get("textures") or ""))


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
            if spec is not None and not _layer_allowed(name, spec):
                found[name] = _skipped_model(name)
                continue
            try:
                if flat:
                    found[name] = _publish(describe_actor(dump, name, convert=False, flat=True))
                else:
                    found[name] = _publish(describe_actor(dump, name, convert=False))
            except CacheMiss:
                queued = _queued_model(name)
                if flat:
                    queued["url"] = f"/api/models/{name}/model.glb?flat=1"
                found[name] = queued
        return found

    spec = _load_spec(str(body.get("load") or ""), str(body.get("textures") or ""))
    # `flat` is the geometry file. The editor always asks for it, then loads
    # images from /textures when that layer is on. A texture flag must not
    # switch this URL back to an embedded-image glb.
    flat = bool(body.get("flat")) or bool(spec is not None and not spec.textures)
    found = await asyncio.to_thread(lookup)
    # The page only asks for a handful of glbs at once. Start the rest on the
    # process pool now, so a scene open is not limited to those slots. The
    # loop keeps only a weak reference to a task, so the set holds them. One
    # worker keeps the old path: the batch stays a lookup, and the GET starts
    # the convert. That is what the single-worker cache test locks.
    count, _, _ = _configured_workers()
    if count > 1 and body.get("prefetch", True):
        for name, info in found.items():
            if info.get("status") == "queued":
                task = asyncio.create_task(_describe_model(name, interactive=True, flat=flat))
                _PREFETCH.add(task)
                task.add_done_callback(_PREFETCH.discard)
    return {"models": found}


@app.get("/api/models/{gyaml}")
async def model_info(gyaml: str, load: str = "", textures: str = "", flat: int = 0):
    spec = _load_spec(load, textures)
    if spec is not None and not _layer_allowed(gyaml, spec):
        return _skipped_model(gyaml)
    use_flat = bool(flat) or bool(spec is not None and not spec.textures)
    return await _describe_model(gyaml, flat=use_flat)


@app.get("/api/models/{gyaml}/textures")
async def model_textures(gyaml: str, load: str = "", textures: str = ""):
    """Decoded images for one actor. Does not convert or return the glb."""
    from ocarina_forge.layers import parse_textures_flag

    spec = _load_spec(load, textures)
    if parse_textures_flag(textures) is False or (spec is not None and not spec.textures):
        return {"gyaml": gyaml, "skipped": True, "reason": "Textures are not loaded.", "materials": {}}
    if spec is not None and not _layer_allowed(gyaml, spec):
        return {"gyaml": gyaml, "skipped": True, "reason": "This layer is not loaded.", "materials": {}}
    dump = _open_dump()
    return await asyncio.to_thread(describe_actor_textures, dump, gyaml)


@app.get("/api/models/{gyaml}/model.glb")
async def model_file(gyaml: str, request: Request, flat: int = 0, load: str = "", textures: str = ""):
    spec = _load_spec(load, textures)
    if spec is not None and not _layer_allowed(gyaml, spec):
        raise BancError("This layer is not loaded.", "not_found")
    use_flat = bool(flat) or bool(spec is not None and not spec.textures)
    info = await _describe_model(gyaml, flat=use_flat)
    digest = info.get("digest") or ""
    path = _cached_glb(digest) if info.get("status") == "mesh" else None
    if path is None:
        raise BancError(info.get("reason") or "This actor has no converted model.", "not_found")
    variant = f"{digest}-{SHARED_MODEL_VERSION}"
    if request.query_params.get("v") == variant:
        shared = shared_model_path(digest)
        if shared.is_file():
            return _cached_file(request, shared, f"{gyaml}.glb", variant)
    return _cached_file(request, path, f"{gyaml}.glb", digest)


@app.get("/api/model-textures/{name}")
def shared_texture_file(name: str):
    path = shared_texture_path(name)
    if path is None or not path.is_file():
        raise BancError("This shared model texture is not in the cache.", "not_found")
    note_access(path)
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".ktx2": "image/ktx2"}[path.suffix]
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "public, max-age=31536000, immutable"})



def _exclude_ids(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item not in (None, "")]
    return [item for item in str(value).split(",") if item.strip()]


@app.get("/api/lighting")
def scene_lighting(banc: str):
    """Emitters, GameBancParam key names, and an imported capture when one exists."""
    if not banc or ".." in banc or banc.startswith("/"):
        raise BancError("That scene path is not in the dump.", "not_found")
    return lighting_report(_open_dump(), banc)


@app.get("/api/ground")
async def ground_at(banc: str, x: float, z: float, exclude: str = "", load: str = "", textures: str = ""):
    """Composed walkable height at one XZ: height tile plus Land_* island/cliff/hill meshes."""
    if not banc or ".." in banc or banc.startswith("/"):
        raise BancError("That scene path is not in the dump.", "not_found")
    spec = _load_spec(load, textures)
    if spec is not None and "terrain" not in spec.layers:
        return {"status": "skipped", "reason": "Terrain is not loaded.", "hit": False}
    dump = _open_dump()
    samples = await asyncio.to_thread(query_ground_cached, dump, banc, [(x, z)], _exclude_ids(exclude))
    return samples[0]


@app.get("/api/ground/surface")
async def ground_surface(banc: str, load: str = "", textures: str = ""):
    """Gzipped walkable-surface pack for local Snap in the viewer."""
    if not banc or ".." in banc or banc.startswith("/"):
        raise BancError("That scene path is not in the dump.", "not_found")
    spec = _load_spec(load, textures)
    if spec is not None and "terrain" not in spec.layers:
        return {"status": "skipped", "reason": "Terrain is not loaded."}
    dump = _open_dump()
    path, meta = await asyncio.to_thread(load_or_build_surface, dump, banc)
    headers = {
        "Content-Encoding": "gzip",
        "Cache-Control": "public, max-age=31536000",
        "X-Forge-Surface-Triangles": str(meta.get("triangles") or 0),
        "X-Forge-Surface-Bytes": str(meta.get("gzipBytes") or path.stat().st_size),
    }
    if meta.get("digest"):
        headers["ETag"] = meta["digest"]
    return FileResponse(path, media_type="application/octet-stream", headers=headers, filename="surface.ofgs.gz")


@app.get("/api/terrain")
async def terrain_info(banc: str, refine: bool = False, load: str = "", textures: str = ""):
    spec = _load_spec(load, textures)
    if spec is not None and "terrain" not in spec.layers:
        return {"banc": banc, "status": "skipped", "reason": "Terrain is not loaded.", "url": None}
    info = await _describe_terrain_model(banc)
    if info.get("status") != "mesh":
        return info
    dump = _open_dump()
    if refine and terrain_fit_pending(dump, banc, info):
        async with _TERRAIN_FIT_LOCK:
            await asyncio.to_thread(prepare_scene_terrain, dump, banc, info)
    _, version = _corrected_terrain(dump, banc, info)
    if version:
        return {**info, "url": "/api/terrain/model.glb?" + urlencode({"banc": banc, "v": version})}
    if not refine and terrain_fit_pending(dump, banc, info):
        return {**info, "fitPending": True}
    return info


def _corrected_terrain(dump, banc: str, info: dict) -> tuple[Path | None, str]:
    digest = info.get("digest") or ""
    if info.get("status") != "mesh" or not digest:
        return None, ""
    path = corrected_terrain_path(dump, banc, digest)
    if path:
        try:
            return path, f"{path.stem}-{path.stat().st_mtime_ns}"
        except FileNotFoundError:
            pass
    return None, ""


@app.get("/api/terrain/model.glb")
async def terrain_file(banc: str, request: Request, load: str = "", textures: str = ""):
    spec = _load_spec(load, textures)
    if spec is not None and "terrain" not in spec.layers:
        raise BancError("Terrain is not loaded.", "not_found")
    info = await _describe_terrain_model(banc)
    digest = info.get("digest") or ""
    if info.get("status") == "mesh" and request.query_params.get("v") != digest:
        corrected, version = _corrected_terrain(_open_dump(), banc, info)
        if corrected:
            return _cached_file(request, corrected, "terrain.glb", version)
    path = _cached_glb(digest) if info.get("status") == "mesh" else None
    if path is None:
        raise BancError(info.get("reason") or "This scene has no converted map model.", "not_found")
    return _cached_file(request, path, "terrain.glb", digest)


@app.get("/api/behavior")
def behavior(gyaml: str = "", banc: str = "", id: str = "", load: str = "", textures: str = ""):
    spec = _load_spec(load, textures)
    if spec is not None and "behavior" not in spec.layers:
        return {"gyaml": gyaml, "skipped": True, "reason": "Behavior is not loaded.", "nodes": [], "edges": []}
    from ocarina_forge.behavior import load_actor_graph
    from ocarina_forge.behavior_edit import apply_ops

    dump = _open_dump()
    if "::" in id:
        source, _, placement = id.partition("::")
        available = {scene["path"] for scene in dump.list_scenes()}
        if not placement:
            raise BancError("The actor's placement ID is missing.", "actor")
        if source not in available or (banc and source != banc and source not in sibling_bancs(banc, available)):
            raise BancError("The actor's placement bank is not part of this scene.", "actor")
        banc, id = source, placement
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


@app.get("/api/permissions")
def get_permissions():
    from ocarina_forge.permissions import catalog

    return catalog()


@app.get("/api/project/permissions")
def get_project_permissions():
    from ocarina_forge.permissions import project_declaration

    config = load_config()
    view = project_declaration(_open_project())
    # Only a game folder the user already configured. The default path is not filled in.
    view["suggestedGame"] = str(config.get("game") or "")
    return view


@app.put("/api/project/permissions")
def put_project_permissions(body: dict):
    from ocarina_forge.permissions import save_project_permissions

    return save_project_permissions(_open_project(), body or {})


@app.post("/api/project/permissions/grant")
def grant_project_permissions(body: dict | None = None):
    from ocarina_forge.modexport import mod_id
    from ocarina_forge.permissions import grant_in_mods_json, project_declaration

    body = body or {}
    game = body.get("game")
    if not isinstance(game, str) or not game.strip():
        raise BancError("Point at the game folder. Forge does not grant permissions anywhere else.", "permissions")
    project = _open_project()
    view = project_declaration(project)
    if view["errors"]:
        raise BancError("Fix the permission errors before writing mods.json. " + " ".join(view["errors"]), "permissions")
    declared = list(view["declaration"]["permissions"]) + [
        name for name in view["declaration"]["optionalPermissions"] if name not in view["declaration"]["permissions"]
    ]
    for origin in view["declaration"]["net"]:
        permission = "net:*" if origin == "*" else f"net:{origin}"
        if permission not in declared:
            declared.append(permission)
    return grant_in_mods_json(Path(game), mod_id(project.manifest["name"]), declared)


@app.post("/api/mod-export")
def export_longshot_mod(body: dict | None = None):
    from ocarina_forge.modexport import export_mod as export_longshot

    body = body or {}
    game = body.get("game")
    return export_longshot(
        _open_project(),
        _open_dump(),
        install=bool(body.get("install")),
        game=Path(str(game)).expanduser() if game else None,
    )


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
        app.mount("/", EditorStaticFiles(directory=directory, html=True), name="frontend")
