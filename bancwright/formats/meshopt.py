"""A far mesh from meshoptimizer. Node stays up for the life of the process.

The grid quantize in gltf.cluster_lod is the fallback when Node or the
simplifier is not there. A mesh that the simplifier cannot shrink stays
without a generated LOD; the game's own coarser level is unchanged.
"""

from __future__ import annotations

import atexit
import base64
import json
import shutil
import subprocess
import threading
from array import array
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "frontend" / "tools" / "meshopt-worker.mjs"
_FRONTEND = _ROOT / "frontend"
_LOCK = threading.Lock()
_PROC: subprocess.Popen | None = None
_READY: bool | None = None


def _stop() -> None:
    global _PROC
    proc = _PROC
    _PROC = None
    if proc is None or proc.poll() is not None:
        return
    proc.kill()
    proc.wait(timeout=2)


atexit.register(_stop)


def available() -> bool:
    """True when the simplifier process answered once."""
    global _READY
    if _READY is not None:
        return _READY
    with _LOCK:
        if _READY is not None:
            return _READY
        _READY = _start() is not None
        return _READY


def _start() -> subprocess.Popen | None:
    global _PROC
    if _PROC is not None and _PROC.poll() is None:
        return _PROC
    if shutil.which("node") is None or not _SCRIPT.is_file():
        return None
    try:
        _PROC = subprocess.Popen(
            ["node", str(_SCRIPT)],
            cwd=str(_FRONTEND),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        _PROC = None
        return None
    return _PROC


def _call(payload: dict) -> dict | None:
    with _LOCK:
        proc = _start()
        if proc is None or proc.stdin is None or proc.stdout is None:
            return None
        try:
            proc.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
            proc.stdin.flush()
            line = proc.stdout.readline()
        except (OSError, BrokenPipeError):
            _stop()
            return None
    if not line:
        _stop()
        return None
    try:
        body = json.loads(line)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _ratio(index_count: int) -> float:
    if index_count > 80000:
        return 0.06
    if index_count > 15000:
        return 0.12
    if index_count > 4000:
        return 0.2
    return 0.35


def _pack_f32(values) -> str:
    raw = array("f")
    raw.fromlist([float(item) for item in values])
    return base64.b64encode(raw.tobytes()).decode("ascii")


def _pack_u32(values: list[int]) -> str:
    raw = array("I")
    raw.fromlist([int(item) for item in values])
    return base64.b64encode(raw.tobytes()).decode("ascii")


def _unpack_f32(payload: str, width: int) -> list[tuple]:
    raw = array("f")
    raw.frombytes(base64.b64decode(payload))
    return [tuple(raw[index : index + width]) for index in range(0, len(raw), width)]


def _unpack_u32(payload: str) -> list[int]:
    raw = array("I")
    raw.frombytes(base64.b64decode(payload))
    return list(raw)


def simplify_mesh(
    positions: list[tuple[float, float, float]],
    indices: list[int],
    uvs: list[tuple[float, float]] | None,
) -> tuple[list, list, list | None] | None:
    """A coarser mesh. None when the simplifier is missing or the mesh did not shrink."""
    count = len(indices)
    if count < 900 or len(positions) < 40 or count % 3:
        return None
    if not available():
        return None
    flat_pos = [axis for point in positions for axis in point]
    payload = {
        "op": "simplify",
        "positions": _pack_f32(flat_pos),
        "indices": _pack_u32(indices),
        "ratio": _ratio(count),
        "error": 0.05,
    }
    if uvs is not None and len(uvs) == len(positions):
        payload["uvs"] = _pack_f32([axis for point in uvs for axis in point])
    body = _call(payload)
    if not body or not body.get("ok"):
        return None
    new_pos = _unpack_f32(body["positions"], 3)
    new_idx = _unpack_u32(body["indices"])
    new_uv = _unpack_f32(body["uvs"], 2) if body.get("uvs") else None
    if len(new_idx) < 3 or len(new_idx) >= count * 0.85 or len(new_pos) < 3:
        return None
    return new_pos, new_idx, new_uv if uvs is not None else None


def _exchange_glb(op: str, glb: bytes) -> bytes | None:
    if not available():
        return None
    body = _call({"op": op, "glb": base64.b64encode(glb).decode("ascii")})
    if not body or not body.get("ok") or not body.get("glb"):
        return None
    try:
        return base64.b64decode(body["glb"])
    except (ValueError, TypeError):
        return None


def finish_glb(glb: bytes) -> bytes:
    """EXT_meshopt_compression. Textures stay PNG or JPEG. The bytes are unchanged if Node cannot pack."""
    packed = _exchange_glb("pack", glb)
    return packed if packed else glb


def plain_glb(glb: bytes) -> bytes:
    """Decoded floats and PNG, for tests that read the cache file. A plain glb is returned as-is."""
    if b"EXT_meshopt_compression" not in glb and b"image/ktx2" not in glb:
        return glb
    plain = _exchange_glb("plain", glb)
    return plain if plain else glb
