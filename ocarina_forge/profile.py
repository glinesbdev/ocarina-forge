"""Time a cold load and the warm load after it.

Profile points ``OCARINA_FORGE_CACHE`` at a fresh directory unless
``--home-cache`` is set, so it does not read ``~/.cache/ocarina-forge``.
A cold ``cache=miss`` is that private directory. It lives under
``~/.cache/ocarina-forge/profile-tmp`` and is deleted when the command
finishes, unless ``--keep-cache`` is set. The glb cache key is unchanged
by this command.

``--model`` times one actor and can write a cProfile file. A scene walk is
one model at a time unless ``--jobs`` is greater than 1. ``--limit`` stops
each scene after N models. Browser download and GPU upload are not in this
process.
"""

from __future__ import annotations

import functools
import io
import os
import shutil
import tempfile
import time
from pathlib import Path

# Nested or outside the convert clock. Printed, and not added into the remainder.
_INSIDE = {"zstd", "vertices", "indices", "swizzle", "decode"}
_ORDER = (
    ("zstd", "file decompress, including the read before the convert clock"),
    ("bfres", "BFRES parse, including vertex and index decode"),
    ("vertices", "inside bfres: positions, uvs, and colors"),
    ("indices", "inside bfres: index buffer"),
    ("swizzle", "inside texture: deswizzle"),
    ("decode", "inside texture: ASTC, BC, or RGBA decode"),
    ("texread", "texture file read and zstd, outside the texture clock"),
    ("meshopt", "meshopt simplify, or the Python lod fallback"),
    ("encode", "JPEG, PNG, or GPU container"),
    ("glb", "glTF pack and meshopt compression"),
)


def profile_dump(
    dump,
    *,
    model: list[str] | None = None,
    cprofile: str | None = None,
    limit: int = 0,
    jobs: int = 1,
    home_cache: bool = False,
    keep_cache: bool = False,
    log=print,
) -> dict:
    """Cold then warm. One model, or Kokiri child and Hyrule Castle when no model is named."""
    names = [item.strip() for item in (model or []) if item and str(item).strip()]
    if cprofile and len(names) != 1:
        raise SystemExit("--cprofile needs exactly one --model.")
    if jobs < 1:
        raise SystemExit("--jobs must be at least 1.")
    if limit < 0:
        raise SystemExit("--limit must be 0 or a positive count. 0 means every model.")

    from ocarina_forge.models import _CACHE_VERSION

    key = _CACHE_VERSION.split(b"\0", 1)[0].decode()
    from ocarina_forge.formats.gpu import _native_mod

    native = _native_mod()
    bc7 = "rust" if native is not None else "python"
    pixels = "rust" if native is not None and hasattr(native, "limit_rgba") else "python"
    mesh = "rust" if native is not None and hasattr(native, "decode_attribute") else "python"
    normals = "rust" if native is not None and hasattr(native, "face_normal_bytes") else "python"
    skin = "rust" if native is not None and hasattr(native, "transform_positions") else "python"
    previous = os.environ.get("OCARINA_FORGE_CACHE")
    private = None
    if not home_cache:
        private = _private_cache_dir()
        os.environ["OCARINA_FORGE_CACHE"] = str(private)
    _install()
    try:
        _announce_cache(private, key, keep_cache, log)
        if names:
            report = _profile_names(dump, names, cprofile=cprofile, jobs=jobs, log=log)
        else:
            report = _profile_areas(dump, limit=limit, jobs=jobs, log=log)
    finally:
        _restore()
        if previous is None:
            os.environ.pop("OCARINA_FORGE_CACHE", None)
        else:
            os.environ["OCARINA_FORGE_CACHE"] = previous
        if private is not None and not keep_cache:
            shutil.rmtree(private, ignore_errors=True)
            log(f"cache removed {private}")
    report["cache"] = str(private) if private is not None else "home"
    report["cacheKept"] = bool(private is not None and keep_cache)
    report["cachePrivate"] = private is not None
    report["cacheKey"] = key
    report["bc7"] = bc7
    report["pixels"] = pixels
    report["mesh"] = mesh
    report["normals"] = normals
    report["skin"] = skin
    log(f"native bc7/decode: {bc7}")
    log(f"native pixels: {pixels}")
    log(f"native mesh: {mesh}")
    log(f"native normals: {normals}")
    log(f"native skin: {skin}")
    log(
        "Browser dl and up are not in these numbers. "
        "If those dominate Stats on a cold open, stop the Rust port."
    )
    return report


def _private_cache_dir() -> Path:
    """A disk folder, not ``/tmp``. ``/tmp`` on the measured machine is a 16 GB tmpfs."""
    from ocarina_forge.locate import user_cache_root

    root = user_cache_root() / "profile-tmp"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="ocarina-forge-profile-", dir=root))
    except OSError:
        return Path(tempfile.mkdtemp(prefix="ocarina-forge-profile-"))


def _announce_cache(private: Path | None, key: str, keep_cache: bool, log) -> None:
    from ocarina_forge.locate import user_cache_root

    home = user_cache_root() / "models"
    if private is None:
        active = os.environ.get("OCARINA_FORGE_CACHE") or str(home)
        log(f"cache: {active} (the normal model cache). Key {key}. A file already converted with that key is a hit.")
        return
    kept = "It is left in place." if keep_cache else "It is deleted when this command finishes."
    log(
        f"cache: private {private}. This does not read {home}, so a cold cache=miss is expected. "
        f"Key {key} is not changed by profile. {kept}"
    )


def _profile_names(dump, names: list[str], *, cprofile: str | None, jobs: int, log) -> dict:
    root = str(dump.root)
    if len(names) == 1 and cprofile:
        log(f"\n{names[0]}")
        cold_row = _profile_cprofile(root, names[0], cprofile, log)
        log("cold")
        _print_stages(log, cold_row)
        warm_row = _profile_model_job(root, names[0])
        log(f"warm {_brief(warm_row)}")
        _print_stages(log, warm_row)
        cold, warm = [cold_row], [warm_row]
    else:
        if jobs == 1 and len(names) > 1:
            log("jobs=1, so these models run one at a time. Pass --jobs N to use N processes.")
        cold = _run_pass(root, names, jobs, log, "cold")
        warm = _run_pass(root, names, jobs, log, "warm")
        log("cold total")
        _print_stages(log, _sum_rows(cold))
        log("warm total")
        _print_stages(log, _sum_rows(warm))
    return {"models": {"cold": cold, "warm": warm}, "kokiri": [], "castle": [], "missing": []}


def _profile_areas(dump, *, limit: int, jobs: int, log) -> dict:
    scenes = [item["path"] for item in dump.list_scenes()]
    kokiri = [path for path in scenes if "KokiriForest_Child_" in path]
    castle = [path for path in scenes if "HyruleCastle" in path and ("_Static" in path or "_Dynamic" in path)]
    if not kokiri and not castle:
        raise SystemExit("This dump has no KokiriForest_Child scene and no Hyrule Castle scene.")
    if jobs == 1 and limit == 0:
        log(
            "jobs=1 and no --limit, so each scene is one model at a time. "
            "Kokiri child did not finish in 600s that way. Pass --model, or --limit N and --jobs N."
        )
    report = {
        "kokiri": [_scene(dump, path, limit, jobs, log) for path in kokiri],
        "castle": [_scene(dump, path, limit, jobs, log) for path in castle],
        "missing": [],
    }
    if not kokiri:
        report["missing"].append("KokiriForest_Child")
        log("No KokiriForest_Child scene in this dump.")
    if not castle:
        report["missing"].append("HyruleCastle")
        log("No Hyrule Castle Static or Dynamic scene in this dump.")
    return report


def _scene(dump, banc: str, limit: int, jobs: int, log) -> dict:
    from ocarina_forge.warm import scene_model_names

    log(f"\n{banc}")
    banc_ms = _time_banc(dump, banc)
    log(f"banc read {banc_ms} ms (zstd + BYML, one read, before any model)")
    names = scene_model_names(dump, banc)
    total = len(names)
    if limit:
        names = names[:limit]
    log(f"{len(names)} of {total} models, jobs={jobs}")
    if jobs == 1 and len(names) > 1:
        log("jobs=1, so this scene is one model at a time.")
    root = str(dump.root)
    cold = _run_pass(root, names, jobs, log, "cold")
    cold_total = _sum_rows(cold)
    log("cold total")
    _print_stages(log, cold_total)
    warm = _run_pass(root, names, jobs, log, "warm")
    warm_total = _sum_rows(warm)
    log("warm total")
    _print_stages(log, warm_total)
    slowest = max(cold, key=lambda row: row["convertMs"], default=None)
    if slowest is not None:
        log(f"slowest cold {_brief(slowest)}")
    return {
        "banc": banc,
        "bancReadMs": banc_ms,
        "models": total,
        "profiled": len(names),
        "cold": cold,
        "warm": warm,
        "coldTotal": cold_total,
        "warmTotal": warm_total,
    }


def _run_pass(root: str, names: list[str], jobs: int, log, label: str) -> list[dict]:
    if jobs <= 1 or len(names) <= 1:
        rows = []
        for index, name in enumerate(names, start=1):
            row = _profile_model_job(root, name)
            rows.append(row)
            log(f"{label} {index}/{len(names)} {_brief(row)}")
        return rows
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    ctx = multiprocessing.get_context("spawn")
    rows = []
    workers = min(jobs, len(names))
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init_worker) as pool:
        futures = [pool.submit(_profile_model_job, root, name) for name in names]
        total = len(futures)
        for index, fut in enumerate(as_completed(futures), start=1):
            row = fut.result()
            rows.append(row)
            log(f"{label} {index}/{total} {_brief(row)}")
    return rows


def _profile_cprofile(root: str, name: str, cprofile: str, log) -> dict:
    import cProfile
    import pstats

    prof = cProfile.Profile()
    prof.enable()
    try:
        row = _profile_model_job(root, name)
    finally:
        prof.disable()
    path = Path(cprofile).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    prof.dump_stats(str(path))
    log(f"cprofile {path}")
    buf = io.StringIO()
    pstats.Stats(prof, stream=buf).sort_stats("cumulative").print_stats(20)
    log(buf.getvalue().rstrip())
    log(f"cold {_brief(row)}")
    return row


def _profile_model_job(root: str, name: str) -> dict:
    """One model. A worker installs the same stage timers in its own process."""
    from ocarina_forge.dump import Dump
    from ocarina_forge.models import describe_actor

    _install()
    _zero()
    info = describe_actor(Dump(root), name)
    timing = info.get("timing") or {}
    return {
        "name": name,
        "status": info.get("status") or "",
        "reason": info.get("reason") or "",
        "cache": timing.get("cache") or "",
        "convertMs": float(timing.get("convertMs") or 0),
        "textureMs": float(timing.get("textureMs") or 0),
        "stages": _snapshot(),
    }


_BUCKETS: dict[str, float] = {}
_RESTORE = None


def _init_worker() -> None:
    _install()


def _install() -> None:
    global _RESTORE
    if _RESTORE is not None:
        return
    _RESTORE = _patch(_BUCKETS)


def _restore() -> None:
    global _RESTORE
    if _RESTORE is None:
        return
    _RESTORE()
    _RESTORE = None


def _zero() -> None:
    for key in list(_BUCKETS):
        _BUCKETS[key] = 0.0


def _snapshot() -> dict[str, float]:
    return {name: round(_BUCKETS.get(name, 0.0) * 1000, 2) for name, _note in _ORDER}


def _patch(buckets: dict[str, float]):
    """Time the names the converter already calls. The hashed sources stay untouched."""
    import ocarina_forge.dump as dump_mod
    import ocarina_forge.formats.bfres as bfres
    import ocarina_forge.formats.bntx as bntx
    import ocarina_forge.formats.gpu as gpu
    import ocarina_forge.models as models

    targets = (
        (dump_mod, "decompress", "zstd"),
        (models, "parse", "bfres"),
        (bfres._File, "_vertices", "vertices"),
        (bfres._File, "_lod", "indices"),
        (bfres, "_colors", "vertices"),
        (bfres, "_extra_uvs", "vertices"),
        (bntx, "deswizzle", "swizzle"),
        (bntx, "_decode", "decode"),
        (models, "_read_tex", "texread"),
        (models, "meshopt_available", "meshopt"),
        (models, "simplify_mesh", "meshopt"),
        (models, "cluster_lod", "meshopt"),
        (models, "encode_albedo", "encode"),
        (gpu, "gpu_image", "encode"),
        (models, "to_glb", "glb"),
        (models, "finish_glb", "glb"),
    )

    def wrap(original, bucket: str):
        @functools.wraps(original)
        def inner(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                buckets[bucket] = buckets.get(bucket, 0.0) + (time.perf_counter() - started)

        return inner

    saved = []
    try:
        for module, name, bucket in targets:
            original = getattr(module, name)
            saved.append((module, name, original))
            setattr(module, name, wrap(original, bucket))
    except Exception:
        for module, name, original in saved:
            setattr(module, name, original)
        raise

    def restore() -> None:
        for module, name, original in saved:
            setattr(module, name, original)

    return restore


def _sum_rows(rows: list[dict]) -> dict:
    stages: dict[str, float] = {}
    convert = 0.0
    texture = 0.0
    for row in rows:
        convert += float(row.get("convertMs") or 0)
        texture += float(row.get("textureMs") or 0)
        for name, ms in (row.get("stages") or {}).items():
            stages[name] = stages.get(name, 0.0) + float(ms)
    return {
        "cache": "",
        "convertMs": round(convert, 2),
        "textureMs": round(texture, 2),
        "stages": {name: round(ms, 2) for name, ms in stages.items()},
    }


def _print_stages(log, row: dict) -> None:
    stages = row.get("stages") or {}
    convert = float(row.get("convertMs") or 0)
    texture = float(row.get("textureMs") or 0)
    for name, note in _ORDER:
        log(f"  {name:10} {stages.get(name, 0):10.2f} ms  {note}")
    log(f"  {'texture':10} {texture:10.2f} ms  texture clock (swizzle and decode are inside this)")
    accounted = sum(float(stages.get(name, 0)) for name, _note in _ORDER if name not in _INSIDE) + texture
    other = round(convert - accounted, 2)
    log(f"  {'convert':10} {convert:10.2f} ms  cache={row.get('cache') or '-'}")
    log(
        f"  {'other':10} {other:10.2f} ms  "
        "convert minus bfres, texread, meshopt, encode, glb, and texture. zstd is not subtracted."
    )


def _brief(row: dict) -> str:
    reason = row.get("reason") or ""
    if row.get("status") == "box" and (reason.startswith("Marker") or reason.startswith("No mesh")):
        reason = ""
    else:
        reason = f" {reason}" if row.get("status") not in ("mesh", "") and reason else ""
    return (
        f"{row['name']} status={row.get('status') or '-'} cache={row.get('cache') or '-'} "
        f"convert={row.get('convertMs')} ms texture={row.get('textureMs')} ms{reason}"
    )


def _time_banc(dump, banc: str) -> float:
    started = time.perf_counter()
    try:
        dump.read_byml(banc)
    except Exception:
        return 0.0
    return round((time.perf_counter() - started) * 1000, 2)
