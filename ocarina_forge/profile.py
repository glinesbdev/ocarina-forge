"""Time a cold load and the warm load after it.

The numbers are the Python side: zstd, BYML, BFRES parse, block deswizzle,
and block decode. Browser download and GPU upload are not in this process.
Those are the Stats ``dl`` and ``up`` figures. If those dominate a cold open
on a real dump, the Rust port stops, as decided for R1.

This command uses a fresh cache directory so it does not touch
``~/.cache/ocarina-forge``. Retail Kokiri and Hyrule Castle are timed only
when the dump you pass actually contains them.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def profile_dump(dump, *, log=print) -> dict:
    """Cold then warm ``--scene-only`` loads. Castle scenes are omitted when the dump has none."""
    scenes = [item["path"] for item in dump.list_scenes()]
    kokiri = [path for path in scenes if "KokiriForest_Child_" in path]
    castle = [path for path in scenes if "HyruleCastle" in path and ("_Static" in path or "_Dynamic" in path)]
    if not kokiri and not castle:
        raise SystemExit("This dump has no KokiriForest_Child scene and no Hyrule Castle scene.")

    cache = Path(tempfile.mkdtemp(prefix="ocarina-forge-profile-"))
    previous = os.environ.get("OCARINA_FORGE_CACHE")
    os.environ["OCARINA_FORGE_CACHE"] = str(cache)
    try:
        report = {
            "cache": str(cache),
            "kokiri": [_scene(dump, path, log) for path in kokiri],
            "castle": [_scene(dump, path, log) for path in castle],
            "missing": [],
        }
    finally:
        if previous is None:
            os.environ.pop("OCARINA_FORGE_CACHE", None)
        else:
            os.environ["OCARINA_FORGE_CACHE"] = previous
    if not kokiri:
        report["missing"].append("KokiriForest_Child")
        log("No KokiriForest_Child scene in this dump.")
    if not castle:
        report["missing"].append("HyruleCastle")
        log("No Hyrule Castle Static or Dynamic scene in this dump.")
    log(
        "Browser dl and up are not in these numbers. "
        "If those dominate Stats on a cold open, stop the Rust port."
    )
    return report


def _scene(dump, banc: str, log) -> dict:
    from ocarina_forge.warm import run_warm

    log(f"\n{banc}")
    banc_ms = _time_banc(dump, banc)
    log(f"banc read {banc_ms} ms (zstd + BYML, one read)")
    buckets: dict[str, float] = {}
    restore = _patch(buckets)
    try:
        cold = run_warm(dump, banc, log=log, neighbors=False)
        cold_phases = _snapshot(buckets)
        _zero(buckets)
        warm = run_warm(dump, banc, log=log, neighbors=False)
        warm_phases = _snapshot(buckets)
    finally:
        restore()
    row = {
        "banc": banc,
        "bancReadMs": banc_ms,
        "cold": {**cold, "phases": cold_phases},
        "warm": {**warm, "phases": warm_phases},
    }
    log(
        f"phases cold zstd {cold_phases['zstd']} ms, byml {cold_phases['byml']} ms, "
        f"bfres {cold_phases['bfres']} ms, swizzle {cold_phases['swizzle']} ms, "
        f"decode {cold_phases['decode']} ms"
    )
    log(
        f"phases warm zstd {warm_phases['zstd']} ms, byml {warm_phases['byml']} ms, "
        f"bfres {warm_phases['bfres']} ms, swizzle {warm_phases['swizzle']} ms, "
        f"decode {warm_phases['decode']} ms"
    )
    named = sum(cold_phases.values())
    gap = round(float(cold.get("convertMs") or 0) - named, 2)
    if gap > 5 and gap > named:
        log(
            f"other cold convert time {gap} ms is outside zstd, BYML, BFRES parse, swizzle, and decode"
        )
    return row


def _time_banc(dump, banc: str) -> float:
    started = time.perf_counter()
    try:
        dump.read_byml(banc)
    except Exception:
        return 0.0
    return round((time.perf_counter() - started) * 1000, 2)


def _snapshot(buckets: dict[str, float]) -> dict[str, float]:
    return {name: round(buckets.get(name, 0.0) * 1000, 2) for name in ("zstd", "byml", "bfres", "swizzle", "decode")}


def _zero(buckets: dict[str, float]) -> None:
    for key in list(buckets):
        buckets[key] = 0.0


def _patch(buckets: dict[str, float]):
    """Time the functions the converters actually call, including already-imported names."""
    import ocarina_forge.dump as dump_mod
    import ocarina_forge.formats.bntx as bntx
    import ocarina_forge.models as models

    targets = (
        (dump_mod, "decompress", "zstd"),
        (dump_mod, "loads", "byml"),
        (models, "parse", "bfres"),
        (bntx, "deswizzle", "swizzle"),
        (bntx, "_decode", "decode"),
    )
    saved = [(module, name, getattr(module, name)) for module, name, _bucket in targets]

    def wrap(original, bucket: str):
        def inner(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                buckets[bucket] = buckets.get(bucket, 0.0) + (time.perf_counter() - started)

        return inner

    for module, name, bucket in targets:
        setattr(module, name, wrap(getattr(module, name), bucket))

    def restore() -> None:
        for module, name, original in saved:
            setattr(module, name, original)

    return restore
