"""Keep ``~/.cache/ocarina-forge`` from growing without a limit.

Converted files are rebuilt on demand, so a startup pass can drop folders from
older cache versions, and a size cap can drop whatever was used least recently.
Model digests hash the version in, so a sidecar records it. Files with no
recorded version are left for the size cap.

The pass runs on a daemon thread. It skips files still being written (a
``.tmp`` name, or an mtime inside a short grace window) and leaves a file in
place when the OS says it is in use.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ocarina_forge.locate import setting, user_cache_root

_log = logging.getLogger("uvicorn.error")

# 20 GiB. A bare 0 in config or in OCARINA_FORGE_CACHE_LIMIT means unlimited.
DEFAULT_CACHE_LIMIT = 20 * 1024 ** 3
# Evict down to this fraction of the cap so one new model does not prune again.
LRU_TARGET = 0.90
# Do not delete a file whose bytes may still be landing on disk.
GRACE_SECONDS = 120
# Re-check the cap this often, and sooner when a write pushes the estimate over.
MAINTENANCE_INTERVAL = 900
# Touch atime at most this often. mtime stays put: preview URLs embed it.
ACCESS_INTERVAL = 300
MODEL_VERSION_KEY = "cacheVersion"

_VERSION_DIR = re.compile(r"^(?:scene-terrain-v|scene-preview-v|shared-v)\d+$")
_HASH_GROUP = re.compile(r"^([0-9a-f]{64})(?:[.-].*)?$")
_HEX_BUCKET = re.compile(r"^[0-9a-f]{2}$")
_MISSING = object()
_MAX_META_BYTES = 8 * 1024 * 1024

_state_lock = threading.Lock()
_run_lock = threading.Lock()
_prune_requested = threading.Event()
_stop: threading.Event | None = None
_thread: threading.Thread | None = None
_bytes_estimate: int | None = None
_applied_limit = 0
_last_pruned_bytes = 0
_announced = False


@dataclass
class PruneStats:
    """Bytes removed by one maintenance pass, and what is left on disk."""

    version_bytes: int = 0
    lru_bytes: int = 0
    remaining_bytes: int = 0
    skipped: int = 0
    path: str = ""

    @property
    def removed_bytes(self) -> int:
        return self.version_bytes + self.lru_bytes


def model_version() -> str:
    """Current glb prefix plus the converter-source stamp.

    Both are hashed into the digest, so either one changing orphans old files.
    """
    from ocarina_forge.models import _CACHE_VERSION, _CONVERTER_STAMP

    name = _CACHE_VERSION.split(b"\0", 1)[0].decode()
    return f"{name}:{_CONVERTER_STAMP.hex()}"


def bntx_version() -> str:
    from ocarina_forge.models import _BNTX_VERSION

    return _BNTX_VERSION.split(b"\0", 1)[0].decode()


def atlas_version() -> str:
    from ocarina_forge.models import _ATLAS_VERSION

    return _ATLAS_VERSION.split(b"\0", 1)[0].decode()


def dump_model_meta(info: dict) -> str:
    """Serialize model metadata with the version a later prune can see."""
    stamped = dict(info)
    stamped[MODEL_VERSION_KEY] = model_version()
    return json.dumps(stamped)


def load_model_meta(text: str) -> dict:
    """The metadata handed back to the editor, without the prune tag."""
    info = json.loads(text)
    if isinstance(info, dict):
        info.pop(MODEL_VERSION_KEY, None)
    return info


def write_version_sidecar(path: Path, version: str) -> None:
    """Write ``<stem>.json`` next to a hashed cache file."""
    side = path.with_suffix(".json")
    side.parent.mkdir(parents=True, exist_ok=True)
    # ``.json.tmp``, not ``.tmp``: the data file's own write uses that name.
    tmp = side.with_suffix(side.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps({"version": version}), encoding="utf-8")
        os.replace(tmp, side)
    finally:
        tmp.unlink(missing_ok=True)
    note_written(side)


def note_access(path: Path) -> None:
    """Remember a read via atime. mtime is the write time and is left alone.

    Preview URLs embed ``st_mtime_ns``, so the nanoseconds have to stay put.
    """
    try:
        st = path.stat()
        now_ns = time.time_ns()
        if now_ns - st.st_atime_ns < ACCESS_INTERVAL * 1_000_000_000:
            return
        os.utime(path, ns=(now_ns, st.st_mtime_ns))
    except OSError:
        return


def note_written(*paths: Path) -> None:
    """Add just-written bytes to the estimate and ask for a prune when over the cap.

    Cheap on purpose: it stats the new files and does not walk the cache.
    Before the first walk the estimate is unknown, so this is a no-op.
    """
    global _bytes_estimate
    added = 0
    for path in paths:
        try:
            if path.is_file():
                added += path.stat().st_size
        except OSError:
            continue
    if added <= 0:
        return
    with _state_lock:
        if _bytes_estimate is None:
            return
        _bytes_estimate += added
        over = _applied_limit > 0 and _bytes_estimate > _applied_limit
    if over:
        _prune_requested.set()


def managed_root() -> Path:
    """The directory the cap applies to.

    ``OCARINA_FORGE_CACHE`` replaces the models directory. Otherwise the cap
    covers ``~/.cache/ocarina-forge`` (models, surfaces, and the rest).
    """
    override = setting("CACHE")
    if override:
        return Path(override).expanduser()
    return user_cache_root()


def cache_limit_setting() -> dict:
    """Effective cap. The env var wins, then the config file, then 20 GiB.

    ``source`` is ``env``, ``config``, or ``default``. ``bytes`` of 0 is unlimited.
    """
    from ocarina_forge.config import load_config, parse_cache_limit

    raw = setting("CACHE_LIMIT")
    if raw is not None and str(raw).strip() != "":
        parsed = parse_cache_limit(raw)
        if parsed is not None:
            return {"bytes": parsed, "source": "env"}
        _log.warning("Ignoring OCARINA_FORGE_CACHE_LIMIT=%r", raw)
    saved = load_config().get("cacheLimit")
    if isinstance(saved, int) and not isinstance(saved, bool) and saved >= 0:
        return {"bytes": saved, "source": "config"}
    return {"bytes": DEFAULT_CACHE_LIMIT, "source": "default"}


def cache_public() -> dict:
    """The status payload. ``bytes`` stays null until the first walk finishes."""
    limit = cache_limit_setting()
    with _state_lock:
        measured = _bytes_estimate
        pruned = _last_pruned_bytes
    return {
        "path": str(managed_root()),
        "bytes": measured,
        "limitBytes": limit["bytes"],
        "unlimited": limit["bytes"] == 0,
        "source": limit["source"],
        "prunedBytes": pruned,
    }


def maintain(
    root: Path | None = None,
    *,
    limit: int | None = None,
    now: float | None = None,
) -> PruneStats:
    """Drop stale versions, then evict least-recently-used files down to 90% of ``limit``.

    ``limit`` of 0 skips eviction. An explicit ``root`` is not recorded as the
    live cache size (tests pass one). The no-argument call uses the real cache
    and updates ``cache_public``.
    """
    own = root is None
    root = managed_root() if root is None else Path(root)
    if limit is None:
        limit = cache_limit_setting()["bytes"]
    if now is None:
        now = time.time()
    with _run_lock:
        stats = PruneStats(path=str(root))
        if root.is_dir():
            freed, skipped = _prune_versions(root, now)
            stats.version_bytes = freed
            stats.skipped = skipped
            lru_freed, remaining, lru_skipped = _evict(root, limit, now)
            stats.lru_bytes = lru_freed
            stats.remaining_bytes = remaining
            stats.skipped += lru_skipped
            _remove_empty_dirs(root)
        _log_stats(stats, limit)
        if own:
            _remember(stats, limit)
        return stats


def start_maintenance(*, interval: float = MAINTENANCE_INTERVAL) -> None:
    """Start the daemon thread. A second call does nothing while it is alive."""
    global _thread, _stop
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop = threading.Event()
        _thread = threading.Thread(
            target=_loop,
            args=(_stop, interval),
            name="cache-prune",
            daemon=True,
        )
        _thread.start()


def stop_maintenance() -> None:
    """Ask the daemon to exit. It is not an error when it was never started."""
    global _thread
    with _state_lock:
        stop = _stop
        thread = _thread
        _thread = None
    if stop is not None:
        stop.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5)


def _loop(stop: threading.Event, interval: float) -> None:
    while not stop.is_set():
        _prune_requested.clear()
        try:
            maintain()
        except Exception:
            _log.exception("cache prune failed")
        if stop.is_set():
            return
        if _prune_requested.is_set():
            if stop.wait(5):
                return
            continue
        deadline = time.monotonic() + interval
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if stop.wait(min(1.0, remaining)):
                return
            if _prune_requested.is_set():
                break


def _remember(stats: PruneStats, limit: int) -> None:
    global _bytes_estimate, _applied_limit, _last_pruned_bytes
    with _state_lock:
        _bytes_estimate = stats.remaining_bytes
        _applied_limit = limit
        _last_pruned_bytes = stats.removed_bytes


def _log_stats(stats: PruneStats, limit: int) -> None:
    global _announced
    limit_text = "unlimited" if limit <= 0 else f"{limit} bytes"
    message = (
        f"cache prune removed {stats.removed_bytes} bytes "
        f"(version {stats.version_bytes}, lru {stats.lru_bytes}); "
        f"cache is {stats.remaining_bytes} bytes; limit {limit_text}; "
        f"skipped {stats.skipped}; path {stats.path}"
    )
    if stats.removed_bytes or stats.skipped:
        _log.info(message)
        return
    with _state_lock:
        announce = not _announced
        _announced = True
    if announce:
        _log.info(message)
    else:
        _log.debug(message)


def _layout(root: Path) -> tuple[Path, Path | None]:
    """``(models dir, surfaces dir or None)`` for the default tree or a flat override."""
    models = root / "models"
    surfaces = root / "surfaces"
    if models.is_dir():
        return models, surfaces if surfaces.is_dir() else None
    return root, surfaces if surfaces.is_dir() else None


def _kept_version_dirs() -> set[str]:
    from ocarina_forge.scene_preview import PREVIEW_VERSION, TERRAIN_FIELD_VERSION, TERRAIN_VERSION
    from ocarina_forge.shared_models import TEXTURE_STORE, VERSION

    return {PREVIEW_VERSION, TERRAIN_VERSION, TERRAIN_FIELD_VERSION, TEXTURE_STORE, VERSION}


def _prune_versions(root: Path, now: float) -> tuple[int, int]:
    models, surfaces = _layout(root)
    removed = 0
    skipped = 0
    keep = _kept_version_dirs()
    if models.is_dir():
        for child in list(models.iterdir()):
            if child.is_dir() and _VERSION_DIR.fullmatch(child.name) and child.name not in keep:
                freed, skip = _delete_tree(child, now)
                removed += freed
                skipped += skip
        removed_models, skip_models = _prune_model_entries(models, now)
        removed += removed_models
        skipped += skip_models
        for folder, current, suffix in (
            (models / "bntx", bntx_version(), ".bwtx"),
            (models / "img", bntx_version(), ".bin"),
            (models / "atlas", atlas_version(), ".img"),
        ):
            freed, skip = _prune_sidecars(folder, current, suffix, now)
            removed += freed
            skipped += skip
        freed, skip = _prune_src(models / "src", now)
        removed += freed
        skipped += skip
        freed, skip = _prune_audit(models / "material-audit", now)
        removed += freed
        skipped += skip
    if surfaces is not None:
        freed, skip = _prune_surfaces(surfaces, now)
        removed += freed
        skipped += skip
    return removed, skipped


def _prune_model_entries(models: Path, now: float) -> tuple[int, int]:
    current = model_version()
    removed = 0
    skipped = 0
    for bucket in models.iterdir():
        if not (bucket.is_dir() and _HEX_BUCKET.fullmatch(bucket.name)):
            continue
        for meta in bucket.glob("*.json"):
            version = _stored_version(meta, MODEL_VERSION_KEY)
            if version is _MISSING or version == current:
                continue
            freed, skip = _delete_group([meta, meta.with_suffix(".glb")], now)
            removed += freed
            skipped += skip
    return removed, skipped


def _prune_src(folder: Path, now: float) -> tuple[int, int]:
    if not folder.is_dir():
        return 0, 0
    current = model_version()
    removed = 0
    skipped = 0
    for meta in folder.rglob("*.json"):
        if not meta.is_file() or _is_temp_name(meta.name):
            continue
        version = _stored_version(meta, MODEL_VERSION_KEY)
        if version is _MISSING or version == current:
            continue
        freed, skip = _delete_group([meta], now)
        removed += freed
        skipped += skip
    return removed, skipped


def _prune_sidecars(folder: Path, current: str, suffix: str, now: float) -> tuple[int, int]:
    if not folder.is_dir():
        return 0, 0
    removed = 0
    skipped = 0
    for path in folder.rglob(f"*{suffix}"):
        if not path.is_file() or _is_temp_name(path.name):
            continue
        side = path.with_suffix(".json")
        version = _stored_version(side, "version") if side.is_file() else _MISSING
        if version is _MISSING or version == current:
            continue
        freed, skip = _delete_group([path, side], now)
        removed += freed
        skipped += skip
    return removed, skipped


def _prune_audit(folder: Path, now: float) -> tuple[int, int]:
    if not folder.is_dir():
        return 0, 0
    from ocarina_forge.materialaudit import _VERSION

    removed = 0
    skipped = 0
    for meta in folder.rglob("*.json"):
        if not meta.is_file() or _is_temp_name(meta.name):
            continue
        version = _stored_version(meta, "version")
        if version is _MISSING or version == _VERSION:
            continue
        freed, skip = _delete_group([meta], now)
        removed += freed
        skipped += skip
    return removed, skipped


def _prune_surfaces(folder: Path, now: float) -> tuple[int, int]:
    from ocarina_forge.surface_pack import PACK_VERSION

    removed = 0
    skipped = 0
    for meta in folder.glob("*.json"):
        if not meta.is_file() or _is_temp_name(meta.name):
            continue
        version = _stored_version(meta, "version")
        if version is _MISSING or version == PACK_VERSION:
            continue
        archive = meta.parent / f"{meta.stem}.ofgs.gz"
        freed, skip = _delete_group([meta, archive], now)
        removed += freed
        skipped += skip
    return removed, skipped


@dataclass
class _Entry:
    paths: list[Path]
    size: int
    recency: float


def _evict(root: Path, limit: int, now: float) -> tuple[int, int, int]:
    entries, locked = _scan(root, now)
    total = locked + sum(entry.size for entry in entries)
    if limit <= 0 or total <= limit:
        return 0, total, 0
    target = int(limit * LRU_TARGET)
    entries.sort(key=lambda entry: (entry.recency, str(entry.paths[0])))
    removed = 0
    skipped = 0
    for entry in entries:
        if total <= target:
            break
        freed, skip = _delete_group(entry.paths, now)
        skipped += skip
        if freed <= 0:
            continue
        total -= freed
        removed += freed
    return removed, total, skipped


def _scan(root: Path, now: float) -> tuple[list[_Entry], int]:
    groups: dict[str, list[tuple[Path, os.stat_result]]] = {}
    locked = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=_walk_error):
        dirnames[:] = [name for name in dirnames if name != "profile-tmp"]
        for name in filenames:
            path = Path(dirpath) / name
            if _is_temp_name(name) or name.endswith(".log"):
                locked += _file_size(path)
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            groups.setdefault(_group_key(path), []).append((path, st))
    entries: list[_Entry] = []
    for items in groups.values():
        # A glb still being written keeps its sidecar, and the other way around.
        if any(now - st.st_mtime < GRACE_SECONDS for _path, st in items):
            locked += sum(st.st_size for _path, st in items)
            continue
        alive = [path for path, _st in items]
        size = sum(st.st_size for _path, st in items)
        recency = max(max(st.st_atime, st.st_mtime) for _path, st in items)
        entries.append(_Entry(alive, size, recency))
    return entries, locked


def _group_key(path: Path) -> str:
    match = _HASH_GROUP.match(path.name)
    if match:
        return f"{path.parent}\0{match.group(1)}"
    return str(path)


def _delete_tree(root: Path, now: float) -> tuple[int, int]:
    removed = 0
    skipped = 0
    for dirpath, _dirnames, filenames in os.walk(root, topdown=False, onerror=_walk_error):
        current = Path(dirpath)
        if "profile-tmp" in current.parts:
            continue
        for name in filenames:
            path = current / name
            if _is_temp_name(name) or name.endswith(".log"):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if now - st.st_mtime < GRACE_SECONDS:
                continue
            freed, skip = _unlink(path)
            removed += freed
            skipped += skip
        _try_rmdir(current)
    _try_rmdir(root)
    return removed, skipped


def _delete_group(paths: list[Path], now: float) -> tuple[int, int]:
    existing: list[Path] = []
    for path in paths:
        if not path.is_file() or _is_temp_name(path.name):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if now - st.st_mtime < GRACE_SECONDS:
            return 0, 0
        existing.append(path)
    removed = 0
    skipped = 0
    for path in existing:
        freed, skip = _unlink(path)
        removed += freed
        skipped += skip
    return removed, skipped


def _unlink(path: Path) -> tuple[int, int]:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    try:
        path.unlink()
    except FileNotFoundError:
        return 0, 0
    except OSError as exc:
        _log.warning("cache prune left %s in place (%s)", path, exc)
        return 0, 1
    return size, 0


def _remove_empty_dirs(root: Path) -> None:
    keep = {root}
    models, surfaces = _layout(root)
    if models != root and models.is_dir():
        keep.add(models)
    if surfaces is not None and surfaces.is_dir():
        keep.add(surfaces)
    for dirpath, dirnames, _filenames in os.walk(root, topdown=False, onerror=_walk_error):
        current = Path(dirpath)
        if current in keep or "profile-tmp" in current.parts:
            continue
        dirnames[:] = [name for name in dirnames if name != "profile-tmp"]
        _try_rmdir(current)


def _try_rmdir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


def _stored_version(path: Path, key: str):
    """Read one metadata field. The read is not a use, so atime is restored."""
    try:
        st = path.stat()
    except OSError:
        return _MISSING
    if st.st_size > _MAX_META_BYTES:
        return _MISSING
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return _MISSING
    finally:
        try:
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError:
            pass
    if key not in text:
        return _MISSING
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _MISSING
    if not isinstance(data, dict) or key not in data:
        return _MISSING
    return data[key]


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _is_temp_name(name: str) -> bool:
    if name.endswith(".tmp") or name.endswith(".temp"):
        return True
    # tempfile.NamedTemporaryFile(delete=False) uses tmp + random letters and no dot.
    return name.startswith("tmp") and "." not in name


def _walk_error(err: OSError) -> None:
    _log.warning("cache prune skipped %s (%s)", getattr(err, "filename", ""), err)
