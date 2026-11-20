"""A far mesh from meshoptimizer, and EXT_meshopt_compression for cached glbs.

Node stays up for the life of this process. A forked child does not reuse or
kill the parent's helper: helpers are keyed by pid, and the child drops the
inherited pipes without flushing them. Stderr goes to
``~/.cache/ocarina-forge/meshopt.log``.

Requests are length-prefixed frames, not newline JSON. A multi-megabyte glb
is one frame, so a line reader cannot die on the line length and take the
pipe down with it.

The grid quantize in gltf.cluster_lod is the fallback when Node or the
simplifier is not there. A mesh that the simplifier cannot shrink stays
without a generated LOD; the game's own coarser level is unchanged.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import selectors
import shutil
import struct
import subprocess
import threading
import time
from array import array
from pathlib import Path

from ocarina_forge.locate import user_cache_root

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "frontend" / "tools" / "meshopt-worker.mjs"
_FRONTEND = _ROOT / "frontend"
_LOCK = threading.Lock()
_PROCS: dict[int, subprocess.Popen] = {}
_READY: bool | None = None
# Tests replace this with a Path. None means the log sits beside the model cache.
_LOG: Path | None = None


def _log_file() -> Path:
    if isinstance(_LOG, Path):
        return _LOG
    return user_cache_root() / "meshopt.log"


_FRAME_LIMIT = 512 * 1024 * 1024
_READY_TIMEOUT = 20.0
_CALL_TIMEOUT = 180.0


def _log(text: str) -> None:
    try:
        path = _log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} {text}\n")
    except OSError:
        return


def _quiet_detach(stream) -> None:
    """Close a pipe without flushing. A dead helper turns that flush into BrokenPipe."""
    if stream is None:
        return
    detach = getattr(stream, "detach", None)
    raw = None
    try:
        if detach is not None and not stream.closed:
            raw = stream.detach()
    except (OSError, ValueError, AttributeError):
        raw = None
    closer = raw if raw is not None else stream
    try:
        closer.close()
    except (OSError, BrokenPipeError, ValueError):
        return


def _retire(proc: subprocess.Popen | None, *, kill: bool) -> None:
    if proc is None:
        return
    if kill and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        code = proc.poll()
        if code not in (None, 0):
            _log(f"helper exited {code}")
    for name in ("stdin", "stdout", "stderr"):
        _quiet_detach(getattr(proc, name, None))
        try:
            setattr(proc, name, None)
        except (AttributeError, TypeError):
            pass


def _disarm_foreign() -> None:
    """A fork copies the parent's Popen. Do not kill that node, and do not flush its pipes."""
    me = os.getpid()
    for owner, proc in list(_PROCS.items()):
        if owner == me:
            continue
        _retire(proc, kill=False)
        _PROCS.pop(owner, None)


def _stop() -> None:
    proc = _PROCS.pop(os.getpid(), None)
    _retire(proc, kill=True)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_disarm_foreign)
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
    _disarm_foreign()
    pid = os.getpid()
    proc = _PROCS.get(pid)
    if proc is not None:
        if proc.poll() is None:
            return proc
        _log(f"restarting helper after exit {proc.poll()}")
        _retire(proc, kill=False)
        _PROCS.pop(pid, None)
    if shutil.which("node") is None or not _SCRIPT.is_file():
        return None
    try:
        path = _log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        log = path.open("a", encoding="utf-8")
        try:
            log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} pid {pid} start ---\n")
            log.flush()
            proc = subprocess.Popen(
                ["node", str(_SCRIPT)],
                cwd=str(_FRONTEND),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                bufsize=0,
            )
        finally:
            log.close()
    except OSError as exc:
        _log(f"could not start helper: {exc}")
        return None
    _PROCS[pid] = proc
    ready = _read_frame(proc, _READY_TIMEOUT)
    if not ready or not ready.get("ready"):
        _log(f"helper was not ready: {ready}")
        _retire(proc, kill=True)
        _PROCS.pop(pid, None)
        return None
    return proc


def _write_all(stream, data: bytes) -> bool:
    view = memoryview(data)
    while view:
        try:
            written = stream.write(view)
        except (BrokenPipeError, OSError):
            return False
        if not written:
            return False
        view = view[written:]
    try:
        stream.flush()
    except (BrokenPipeError, OSError, AttributeError):
        return False
    return True


def _read_exact(stream, count: int, timeout: float) -> bytes | None:
    sel = selectors.DefaultSelector()
    try:
        sel.register(stream, selectors.EVENT_READ)
        out = bytearray()
        deadline = time.monotonic() + timeout
        while len(out) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not sel.select(remaining):
                return None
            try:
                chunk = stream.read(count - len(out))
            except OSError:
                return None
            if not chunk:
                return None
            out += chunk
        return bytes(out)
    finally:
        sel.close()


def _read_frame(proc: subprocess.Popen, timeout: float) -> dict | None:
    if proc.stdout is None or proc.poll() is not None:
        return None
    header = _read_exact(proc.stdout, 4, timeout)
    if header is None:
        return None
    size = struct.unpack("<I", header)[0]
    if size <= 0 or size > _FRAME_LIMIT:
        _log(f"helper frame length {size} is not usable")
        return None
    body = _read_exact(proc.stdout, size, timeout)
    if body is None:
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _call(payload: dict) -> dict | None:
    with _LOCK:
        proc = _start()
        if proc is None or proc.stdin is None:
            return None
        data = json.dumps(payload, separators=(",", ":")).encode()
        if not _write_all(proc.stdin, struct.pack("<I", len(data)) + data):
            _log("helper pipe broke while writing; closing it without another flush")
            _retire(proc, kill=False)
            _PROCS.pop(os.getpid(), None)
            return None
        body = _read_frame(proc, _CALL_TIMEOUT)
        if body is None:
            _log(f"helper gave no answer for {payload.get('op')}")
            _retire(proc, kill=True)
            _PROCS.pop(os.getpid(), None)
            return None
    if not body.get("ok"):
        _log(f"helper refused {payload.get('op')}: {body.get('reason') or body}")
    return body


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
    colors: list | None = None,
) -> tuple | None:
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
    keep_colors = colors is not None and len(colors) == len(positions)
    if keep_colors:
        flat_colors = []
        for item in colors:
            flat_colors.extend((float(item[0]), float(item[1]), float(item[2]), float(item[3]) if len(item) > 3 else 1.0))
        payload["colors"] = _pack_f32(flat_colors)
    body = _call(payload)
    if not body or not body.get("ok"):
        return None
    new_pos = _unpack_f32(body["positions"], 3)
    new_idx = _unpack_u32(body["indices"])
    new_uv = _unpack_f32(body["uvs"], 2) if body.get("uvs") else None
    new_colors = _unpack_f32(body["colors"], 4) if body.get("colors") else None
    if len(new_idx) < 3 or len(new_idx) >= count * 0.85 or len(new_pos) < 3:
        return None
    coarse_uv = new_uv if uvs is not None else None
    if keep_colors:
        return new_pos, new_idx, coarse_uv, new_colors
    return new_pos, new_idx, coarse_uv


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
    """EXT_meshopt_compression. Textures stay PNG or JPEG. The bytes are unchanged if Node cannot pack.

    The packer keeps every image and drops textures that no standard material
    slot uses. Overlay maps (orm1, normal1, moss) are cited from extras, so
    this puts a real texture index back before the file is cached.
    """
    from ocarina_forge.formats.gltf import rebind_extra_textures

    packed = _exchange_glb("pack", glb)
    return rebind_extra_textures(packed if packed else glb)


def plain_glb(glb: bytes) -> bytes:
    """Decoded floats and PNG, for tests that read the cache file. A plain glb is returned as-is."""
    if b"EXT_meshopt_compression" not in glb and b"image/ktx2" not in glb:
        return glb
    plain = _exchange_glb("plain", glb)
    return plain if plain else glb
