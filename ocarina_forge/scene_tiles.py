"""Editor cache for a scene's static scenery tiles.

The tileset is not a game file and not a mod file. Longshot's actor API
loads a BFRES pack; it does not load a 3D Tiles tileset. These files live
under the model cache and are served from ``/api/scene-tiles/<hash>/``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.models import CacheMiss, _cached_glb, cache_dir, describe_actor

TILE_CELL_METRES = 256
TILE_LAYERS = frozenset({"models", "decorations"})
_WATER = re.compile(r"water|lake|river|ocean|pond|waterfall", re.IGNORECASE)
_KEY = re.compile(r"^[0-9a-f]{64}$")
_FILE = re.compile(r"^(tileset\.json|cells/-?\d+_-?\d+\.glb|hlod/-?\d+_-?\d+\.glb)$")
_LOCK = threading.Lock()
_log = logging.getLogger("uvicorn.error")


def tile_eligible(actor: dict) -> bool:
    if not isinstance(actor, dict) or actor.get("volume") or actor.get("region"):
        return False
    banc = str(actor.get("banc") or "")
    if "_Static" not in banc or "_Dynamic" in banc:
        return False
    if str(actor.get("layer") or "") not in TILE_LAYERS:
        return False
    if str(actor.get("category") or "") in ("area", "system"):
        return False
    gyaml = str(actor.get("gyaml") or "")
    if not gyaml or _WATER.search(gyaml):
        return False
    return True


def cell_id(x: float, z: float, cell: int = TILE_CELL_METRES) -> str:
    return f"{math.floor(float(x) / cell)}_{math.floor(float(z) / cell)}"


def tileset_box(min_xyz: list[float], max_xyz: list[float]) -> list[float]:
    center = [(min_xyz[axis] + max_xyz[axis]) / 2 for axis in range(3)]
    half = [max((max_xyz[axis] - min_xyz[axis]) / 2, 1) for axis in range(3)]
    return [
        center[0], center[1], center[2],
        half[0], 0, 0,
        0, half[1], 0,
        0, 0, half[2],
    ]


def tile_file(key: str, relative: str) -> Path:
    if not _KEY.fullmatch(key or ""):
        raise BancError("Unknown tile set.", "not_found")
    relative = str(relative or "").replace("\\", "/").lstrip("/")
    if not _FILE.fullmatch(relative):
        raise BancError("Unknown tile file.", "not_found")
    root = (cache_dir() / "tiles" / "sets" / key).resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise BancError("Unknown tile file.", "not_found")
    if not path.is_file():
        raise BancError("That tile is not baked.", "not_found")
    return path


def _round(values, places: int) -> list[float]:
    return [round(float(value), places) for value in values]


def _parts(actor: dict) -> list[dict]:
    parts = []
    for part in actor.get("parts") or []:
        if not isinstance(part, dict):
            continue
        parts.append({
            "family": str(part.get("family") or "")[:80],
            "image": str(part.get("image") or "")[:180],
            "alpha": str(part.get("alpha") or "opaque")[:16],
        })
        if len(parts) >= 32:
            break
    parts.sort(key=lambda item: item["family"])
    return parts


def _canonical(actor: dict, digest: str) -> dict:
    return {
        "featureId": int(actor["featureId"]),
        "gyaml": str(actor["gyaml"]),
        "digest": digest,
        "translation": _round(actor["translation"], 4),
        "rotation": _round(actor["rotation"], 5),
        "scale": _round(actor["scale"], 4),
        "parts": _parts(actor),
    }


def _digest(payload) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _vec(value, size: int) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < size:
        return None
    try:
        return [float(item) for item in value[:size]]
    except (TypeError, ValueError):
        return None


def _cached_model(dump, gyaml: str) -> tuple[Path | None, str]:
    try:
        info = describe_actor(dump, gyaml, convert=False, flat=True)
    except CacheMiss:
        return None, ""
    if not isinstance(info, dict) or info.get("status") != "mesh":
        return None, ""
    digest = str(info.get("digest") or "")
    return _cached_glb(digest), digest


def _link(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        return
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def _write_tileset(directory: Path, metas: list[dict]) -> dict[str, dict]:
    children = []
    actors: dict[str, dict] = {}
    lo = [math.inf, math.inf, math.inf]
    hi = [-math.inf, -math.inf, -math.inf]
    for meta in metas:
        cell = str(meta["id"])
        leaf = f"cells/{cell}.glb"
        hlod = f"hlod/{cell}.glb"
        box = meta.get("box") or tileset_box(meta["bounds"]["min"], meta["bounds"]["max"])
        for feature in meta.get("actors") or []:
            actors[str(int(feature))] = {"leaf": leaf, "hlod": hlod}
        bounds = meta.get("bounds") or {}
        for axis, value in enumerate(bounds.get("min") or []):
            lo[axis] = min(lo[axis], float(value))
        for axis, value in enumerate(bounds.get("max") or []):
            hi[axis] = max(hi[axis], float(value))
        children.append({
            "boundingVolume": {"box": box},
            "geometricError": 48,
            "refine": "REPLACE",
            "content": {"uri": hlod},
            "children": [{
                "boundingVolume": {"box": box},
                "geometricError": 0,
                "refine": "REPLACE",
                "content": {"uri": leaf},
            }],
        })
    if not math.isfinite(lo[0]):
        lo = [0, 0, 0]
        hi = [0, 0, 0]
    tileset = {
        "asset": {"version": "1.1", "gltfUpAxis": "z"},
        "geometricError": 256,
        "extras": {"forge": "editor-cache"},
        "root": {
            "boundingVolume": {"box": tileset_box(lo, hi)},
            "geometricError": 128,
            "refine": "REPLACE",
            "children": children,
        },
    }
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "tileset.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(tileset, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)
    return actors


def _script() -> Path:
    return Path(__file__).resolve().parents[1] / "frontend" / "tools" / "bake-scene-tiles.mjs"


def _node_binary() -> str:
    chosen = os.environ.get("OCARINA_FORGE_NODE")
    if chosen:
        return chosen
    found = shutil.which("node")
    return found or "node"


def _bake_failure_text(stderr: str, stdout: str, code: int) -> str:
    """The first 4 KB of the baker's output, plus the stack if the log is longer."""
    err = (stderr or "").replace("\r\n", "\n").strip()
    out = (stdout or "").replace("\r\n", "\n").strip()
    text = err or out or f"exit {code}"
    if len(text) <= 4096:
        body = text
    else:
        stack = [
            line for line in text.splitlines()
            if line.lstrip().startswith("at ") or line.startswith("Error") or "Error:" in line[:120]
        ]
        body = text[:4096] + "\n…\n" + "\n".join(stack[-30:])
    return f"The tile bake failed (exit {code}).\n{body}"


def _run_node(job: dict) -> None:
    script = _script()
    if not script.is_file():
        raise BancError("The tile baker is missing from this build.", "tiles")
    blob = cache_dir() / "tiles" / "jobs"
    blob.mkdir(parents=True, exist_ok=True)
    path = blob / f"{_digest(job)[:16]}.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    node = _node_binary()
    # cwd is frontend so a package resolves from frontend/node_modules.
    try:
        run = subprocess.run(
            [node, str(script), str(path)],
            cwd=script.parent.parent,
            check=False,
            capture_output=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BancError(f"The tile bake did not finish ({exc}).", "tiles") from exc
    if run.returncode != 0:
        stderr = run.stderr.decode("utf-8", errors="replace")
        stdout = run.stdout.decode("utf-8", errors="replace")
        message = _bake_failure_text(stderr, stdout, run.returncode)
        _log.error(message)
        raise BancError(message, "tiles")


def bake_scene_tiles(dump, actors: list) -> dict:
    """Bake static scenery and return the tileset URL. Uncached actors are skipped."""
    skipped = []
    resolved = []
    seen = set()
    for actor in actors or []:
        if len(resolved) + len(skipped) >= 8000:
            break
        if not isinstance(actor, dict):
            continue
        feature = actor.get("featureId")
        try:
            feature_id = int(feature)
        except (TypeError, ValueError):
            feature_id = 0
        gyaml = str(actor.get("gyaml") or "")
        if feature_id <= 0 or feature_id > 0xFFFFFF or feature_id in seen:
            skipped.append({"featureId": feature_id, "gyaml": gyaml, "reason": "id"})
            continue
        if not tile_eligible(actor):
            skipped.append({"featureId": feature_id, "gyaml": gyaml, "reason": "kept"})
            continue
        translation = _vec(actor.get("translation"), 3)
        rotation = _vec(actor.get("rotation"), 3)
        scale = _vec(actor.get("scale"), 3)
        if translation is None or rotation is None or scale is None:
            skipped.append({"featureId": feature_id, "gyaml": gyaml, "reason": "transform"})
            continue
        path, digest = _cached_model(dump, gyaml)
        if path is None:
            skipped.append({"featureId": feature_id, "gyaml": gyaml, "reason": "not-cached"})
            continue
        seen.add(feature_id)
        row = _canonical({
            "featureId": feature_id,
            "gyaml": gyaml,
            "translation": translation,
            "rotation": rotation,
            "scale": scale,
            "parts": actor.get("parts") or [],
        }, digest)
        row["glb"] = str(path)
        row["cell"] = cell_id(translation[0], translation[2])
        resolved.append(row)

    grouped: dict[str, list[dict]] = {}
    for row in resolved:
        grouped.setdefault(row["cell"], []).append(row)
    for members in grouped.values():
        members.sort(key=lambda item: item["featureId"])

    if not resolved:
        return {"url": "", "hash": "", "bakeMs": 0, "tiles": 0, "cached": 0, "actors": {}, "skipped": skipped}

    started = time.perf_counter()
    blobs = cache_dir() / "tiles" / "blobs"
    metas = []
    cached = 0
    pending = []

    def cell_digest(members: list[dict]) -> str:
        return _digest([{key: value for key, value in member.items() if key != "glb"} for member in members])

    with _LOCK:
        for cell, members in sorted(grouped.items()):
            digest = cell_digest(members)
            leaf = blobs / f"{digest}.glb"
            hlod = blobs / f"{digest}-hlod.glb"
            meta_path = blobs / f"{digest}.json"
            if leaf.is_file() and hlod.is_file() and meta_path.is_file():
                cached += 1
                metas.append(json.loads(meta_path.read_text(encoding="utf-8")))
                continue
            pending.append({
                "id": cell,
                "leaf": str(leaf),
                "hlod": str(hlod),
                "meta": str(meta_path),
                "actors": members,
            })
        if pending:
            _run_node({"cells": pending})
            for cell in pending:
                metas.append(json.loads(Path(cell["meta"]).read_text(encoding="utf-8")))
        metas.sort(key=lambda meta: str(meta.get("id")))
        set_hash = _digest([[meta.get("id"), meta.get("actors")] for meta in metas])
        directory = cache_dir() / "tiles" / "sets" / set_hash
        if not (directory / "tileset.json").is_file():
            for cell, members in sorted(grouped.items()):
                digest = cell_digest(members)
                _link(blobs / f"{digest}.glb", directory / "cells" / f"{cell}.glb")
                _link(blobs / f"{digest}-hlod.glb", directory / "hlod" / f"{cell}.glb")
            _write_tileset(directory, metas)
        actors_out = {}
        for meta in metas:
            cell = str(meta.get("id"))
            for feature in meta.get("actors") or []:
                actors_out[str(int(feature))] = {
                    "leaf": f"cells/{cell}.glb",
                    "hlod": f"hlod/{cell}.glb",
                }
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    return {
        "url": f"/api/scene-tiles/{set_hash}/tileset.json" if resolved else "",
        "hash": set_hash if resolved else "",
        "bakeMs": elapsed,
        "tiles": len(metas),
        "cached": cached,
        "actors": actors_out if resolved else {},
        "skipped": skipped,
    }
