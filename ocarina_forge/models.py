"""Resolve an actor or a scene to a cached glTF mesh.

The dump is only read. Converted glTF and PNG live under the user cache
(`$OCARINA_FORGE_CACHE`, or `$XDG_CACHE_HOME/ocarina-forge/models`, or
`~/.cache/ocarina-forge/models`). That directory is outside the repo and is
never committed.

A real actor pack does not contain the bfres. `Component/ModelInfo/*.bgyml`
names `ModelProjectName` and `FmdbName`, and the mesh is
`Model/<Project>.<Fmdb>.bfres.zs` (zstd dictionary 1). Base color is a
separate `Tex/<name>.bntx.zs`, not a BNTX embedded in the bfres. Older
`Model/<name>.bfres` paths and a bfres stored in the pack are still accepted.
"""

from __future__ import annotations

import array
import contextvars
import hashlib
import json
import math
import os
import re
import struct
import tempfile
import threading
import zlib
from dataclasses import replace
from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.locate import setting, user_cache_root
from ocarina_forge.cache_prune import (
    atlas_version,
    bntx_version,
    dump_model_meta,
    load_model_meta,
    model_version,
    note_access,
    note_written,
    write_version_sidecar,
)
from ocarina_forge.streamlog import ConvertClock, bind_clock, current_clock, reset_clock
from ocarina_forge.formats.bfres import FresError, base_color_name, bind_role, parse, texture_rank
from ocarina_forge.blend import color_span, overlay_plan
from ocarina_forge.surface import collect, emissive_image, pack_layer, surface_role
from ocarina_forge.materialdebug import name_hints, projection_options, sampler_lines


class UndecodedGeometry(Exception):
    """The bfres is real, but its shapes are a mesh codec this reader does not decode."""

    def __init__(self, bounds: dict | None):
        super().__init__("geometry format not decoded (FSHP flag 0x10)")
        self.bounds = bounds
from ocarina_forge.formats.bntx import BntxError, image_names, textures as read_bntx
from ocarina_forge.formats.byml import loads
from ocarina_forge.formats.gltf import Primitive, cluster_lod, encode_albedo, to_glb
from ocarina_forge.formats.meshopt import available as meshopt_available, finish_glb, simplify_mesh
from ocarina_forge.formats.sarc import Sarc
from ocarina_forge.formats import terrain as heightmap

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH = re.compile(rb"(?:Model|Terrain|Map)/[A-Za-z0-9_./+-]{1,180}\.bfres(?:\.zs)?")
_NUMBERED = re.compile(r"^(?P<stem>.+?)_(?P<num>\d+)\.(?P=stem)_(?P=num)\.bfres(?:\.zs)?$")
_KEYS = {"ModelProjectName", "ModelName", "Model", "BFRES", "ModelFile", "FmdbName"}
# Bump this when the glb or texture output changes. The cache key also hashes
# the converter sources (see _converter_files), so a forgotten bump still
# misses old glbs. tests/test_models.py pins the stamp to this version and
# fails until both move together.
_CACHE_VERSION = b"models-57\0"
_BNTX_VERSION = b"bntx-6\0"
_NEUTRAL = (0.62, 0.58, 0.5, 1.0)
# Untextured static-map stand-in. The real ground is TerrainArc, not this bfres.
_TERRAIN = (0.36, 0.45, 0.28, 1.0)
# Heightmap colour when the tile has no MaterialAlb index.
_EARTH = (0.40, 0.36, 0.22, 1.0)
_GROUND_GYAML = re.compile(r"land|ground|terra|cliff|field|path|moss|paving|earth|road|hill|mound", re.IGNORECASE)
# Unlit translucent water when the shader cannot be reproduced.
# Deep enough that a moat does not read as a pale sheet, with a generated ripple.
_WATER = (0.18, 0.42, 0.48, 0.62)
_WATER_TEX = "ocarina-forge-water"
_ROCK = (0.45, 0.40, 0.34, 1.0)
_VINE = (0.28, 0.42, 0.22, 1.0)
_CLOTH = (0.55, 0.42, 0.28, 1.0)
_BROW = (0.25, 0.16, 0.10, 1.0)
_TERRA_LAYER = re.compile(r"terra[_]?material[^\d]*(\d+)", re.IGNORECASE)
_WATER_TOKENS = {
    "water",
    "lake",
    "waterfall",
    "ocean",
    "pond",
    "sea",
    "river",
    "stream",
    "moat",
    "aqua",
    "mizu",
    "brook",
}


def _converter_files() -> tuple[Path, ...]:
    """Sources that decide glTF and texture output. A change must miss the cache."""
    root = Path(__file__).resolve().parent
    return (
        root / "models.py",
        root / "surface.py",
        root / "formats" / "bfres.py",
        root / "formats" / "meshlet.py",
        root / "formats" / "bntx.py",
        root / "formats" / "swizzle.py",
        root / "formats" / "terrain.py",
        root / "formats" / "gltf.py",
        root / "formats" / "gpu.py",
        root / "formats" / "meshopt.py",
        root.parent / "frontend" / "tools" / "meshopt-worker.mjs",
    )


def _converter_stamp() -> bytes:
    """sha256 of the converter sources, names first so a swap cannot collide."""
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent.parent
    for path in _converter_files():
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.digest()


_CONVERTER_STAMP = _converter_stamp()


def _cache_prefix() -> bytes:
    """Human version plus the converter-source hash. Both go into every glb key."""
    return _CACHE_VERSION + _CONVERTER_STAMP


_LOCK = threading.Lock()
_LISTING: dict[str, tuple[int, list[str]]] = {}
_TEX_INDEX: dict[str, tuple[int, dict[str, str]]] = {}
_FILE_SIG: dict[str, tuple[int, int, str]] = {}
_BNTX_MEM: dict[str, list] = {}
_BNTX_ORDER: list[str] = []
_FILES: contextvars.ContextVar[list | None] = contextvars.ContextVar("banc_tex_files", default=None)
# One convert. The same Tex name is asked for again by the next material, and
# the decoded-image cache does not skip the zstd read or the magenta scan.
_LOAD_MEM: contextvars.ContextVar[dict | None] = contextvars.ContextVar("forge_load_mem", default=None)
_READ_MEM: contextvars.ContextVar[dict | None] = contextvars.ContextVar("forge_read_mem", default=None)


class CacheMiss(Exception):
    """The converted glTF is not on disk yet. The server converts it in a worker."""

_cache_dir: Path | None = None


def cache_dir() -> Path:
    """Per-user directory for converted models. Created on first use.

    `OCARINA_FORGE_CACHE` is read every call so a test can point at a temp folder.
    `BANCWRIGHT_CACHE` is still read so an existing shell keeps its cache.
    The default path is remembered after the first successful create. An
    existing ``~/.cache/bancwright`` directory is renamed to
    ``~/.cache/ocarina-forge`` when the new one is absent, so the files are
    not rebuilt for the rename.
    """
    global _cache_dir
    override = setting("CACHE")
    if override:
        root = Path(override).expanduser()
    elif _cache_dir is not None:
        return _cache_dir
    else:
        root = user_cache_root() / "models"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        root = Path(tempfile.gettempdir()) / "ocarina-forge-models"
        root.mkdir(parents=True, exist_ok=True)
    if not override:
        _cache_dir = root
    return root


def describe_actor(dump, gyaml: str, *, convert: bool = True, flat: bool = False) -> dict:
    if not _NAME.fullmatch(gyaml or ""):
        return _box(gyaml, "That is not an actor pack name.")
    try:
        plan, tried, referenced = _actor_plan(dump, gyaml)
    except BancError as exc:
        return _box(gyaml, exc.message)
    if not referenced:
        return _box(gyaml, "No mesh.")
    if not any(item[0] == "blob" or (dump.romfs / item[1]).is_file() for item in plan):
        return _box(gyaml, _missing(gyaml, tried))
    url = f"/api/models/{gyaml}/model.glb"
    if flat:
        url += "?flat=1"
    return _first_mesh(gyaml, plan, url, dump, tried, convert=convert, flat=flat)


def describe_actor_textures(dump, gyaml: str) -> dict:
    """Images for one actor, keyed by material. This does not write or read a glb.

    The editor keeps one geometry file (`?flat=1`) and asks for this manifest
    only when textures are on. A second call reads `texman/` and does not
    decode BNTX again.
    """
    empty = {"gyaml": gyaml or "", "materials": {}}
    if not _NAME.fullmatch(gyaml or ""):
        return empty
    try:
        plan, _tried, referenced = _actor_plan(dump, gyaml)
    except BancError:
        return empty
    if not referenced:
        return empty
    errors: list[str] = []
    for _source, blob in _realize(dump, plan, errors):
        try:
            return _textures_for_blob(dump, gyaml, blob)
        except (FresError, BntxError, ValueError, struct.error, IndexError, OSError):
            continue
    return empty


def _textures_for_blob(dump, gyaml: str, blob: bytes) -> dict:
    digest = hashlib.sha256(_cache_prefix() + b"texman-1\0" + blob).hexdigest()
    path = cache_dir() / "texman" / digest[:2] / f"{digest}.json"
    if path.is_file():
        note_access(path)
        return json.loads(path.read_text(encoding="utf-8"))
    models = parse(blob)
    files: list[dict] = []
    token = _FILES.set(files)
    try:
        _fill_texture_names(models, dump)
        external, _log, _raws = _resolve_materials(dump, models, gyaml, False)
    finally:
        _FILES.reset(token)
    manifest = _texture_manifest(gyaml, models, external)
    encoded = json.dumps(manifest, sort_keys=True)
    with _LOCK:
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded, encoding="utf-8")
            note_written(path)
        else:
            note_access(path)
    return manifest


def _published_image(image, alpha: str, urls: dict[int, str]) -> str | None:
    if image is None or not getattr(image, "rgba", None):
        return None
    key = id(image)
    cached = urls.get(key)
    if cached:
        return cached
    encoded = _encode_cached(image, alpha, gpu=True)
    from ocarina_forge.shared_models import publish_texture_bytes

    url = publish_texture_bytes(bytes(encoded))
    urls[key] = url
    return url


def _texture_manifest(gyaml: str, models, external: dict) -> dict:
    _mark_omitted(models)
    materials: dict[str, dict] = {}
    urls: dict[int, str] = {}

    def taken(model, name: str):
        if not name:
            return None
        image = model.images.get(name) or external.get(name) or external.get(name.lower())
        if image is None or not getattr(image, "rgba", None):
            return None
        return image

    for model in models:
        for part in model.parts:
            if part.omit:
                continue
            image = taken(model, part.texture)
            albedo = _published_image(image, "png" if part.wrap == "repeat" else part.alpha, urls)
            normal = _published_image(taken(model, part.normal_texture), "mask", urls)
            normal1 = _published_image(taken(model, getattr(part, "normal1_texture", "")), "mask", urls)
            orm = _published_image(getattr(part, "orm_image", None), "png", urls)
            orm1 = _published_image(getattr(part, "orm1_image", None), "png", urls)
            emissive = _published_image(getattr(part, "emission_image", None), "png", urls) or _published_image(
                taken(model, part.emission_texture), "mask", urls
            )
            moss = None
            if part.blend and part.overlay_texture:
                moss = _published_image(taken(model, part.overlay_texture), "mask", urls)
            if not any((albedo, normal, normal1, orm, orm1, emissive, moss)):
                continue
            if part.stand_in == "water":
                color = _WATER if part.texture == _WATER_TEX or image is None else (0.35, 0.55, 0.55, 0.2)
                alpha = "blend"
            elif part.proxy and image is None:
                color = _TERRAIN
                alpha = "opaque"
            else:
                color = (1.0, 1.0, 1.0, 1.0) if image is not None else (part.base_color or _NEUTRAL)
                alpha = part.alpha if image is not None or part.alpha == "blend" else "opaque"
            slot = {
                "map": albedo,
                "normalMap": normal,
                "normal1Map": normal1,
                "orm": orm,
                "orm1": orm1,
                "emissiveMap": emissive,
                "moss": moss,
                "alpha": alpha,
                "cutoff": part.cutoff,
                "color": [float(channel) for channel in color[:4]],
            }
            if moss and part.blend:
                slot["blend"] = {
                    "channel": part.blend.get("channel") or "r",
                    "uv": part.blend.get("uv") or "_u0",
                }
            materials[f"{part.name}:{part.material_index}"] = slot
    return {"gyaml": gyaml, "materials": materials}


def describe_terrain(dump, banc: str, *, convert: bool = True) -> dict:
    if not banc or ".." in banc or banc.startswith("/"):
        return {"status": "none", "reason": "That scene path is not in the dump.", "source": "", "url": None}
    located = _locate_heightmap(dump, banc)
    if located.get("tscb"):
        return _describe_heightmap(dump, banc, located, convert=convert)
    if located.get("missing"):
        return {
            "status": "none",
            "reason": located["missing"],
            "source": located.get("param") or "",
            "url": None,
            "banc": banc,
            "tiles": [],
            "terrain": located.get("terrain") or {},
        }
    plan = _terrain_plan(dump, banc)
    tried = [item[1] for item in plan]
    if not any((dump.romfs / item[1]).is_file() for item in plan):
        return {
            "status": "none",
            "reason": f"No static map model for this scene. Tried {', '.join(tried[:4])}.",
            "source": "",
            "url": None,
            "banc": banc,
        }
    info = _first_mesh(banc, plan, f"/api/terrain/model.glb?banc={banc}", dump, tried, terrain=True, convert=convert)
    info["banc"] = banc
    return info


def _box(gyaml: str, reason: str, bounds: dict | None = None) -> dict:
    return {
        "gyaml": gyaml,
        "status": "box",
        "reason": reason,
        "source": "",
        "triangles": 0,
        "textures": 0,
        "bounds": bounds,
        "url": None,
        "digest": "",
        "textureLog": [],
    }


def _missing(gyaml: str, tried: list[str]) -> str:
    shown = ", ".join(tried[:6])
    if len(tried) > 6:
        shown += ", ..."
    if shown:
        return f"No model for {gyaml}. Tried {shown}."
    return (
        f"No model for {gyaml}. Looked in Pack/Actor/{gyaml}.pack.zs and "
        f"Model/{gyaml}.{gyaml}.bfres.zs."
    )


def _first_mesh(label: str, plan: list, url: str, dump, tried: list[str], terrain: bool = False, convert: bool = True, flat: bool = False) -> dict:
    errors = []
    first = ""
    for source, blob in _realize(dump, plan, errors):
        if not first:
            first = source
        try:
            digest, info = _convert_cached(blob, dump, hint=f"{label} {source}", terrain=terrain, convert=convert, flat=flat)
        except CacheMiss:
            raise
        except UndecodedGeometry as exc:
            result = _box(label, str(exc), exc.bounds)
            result["source"] = source
            return result
        except (FresError, BntxError, ValueError, struct.error, IndexError, OSError) as exc:
            errors.append(f"{source}: {exc}")
            continue
        notes = [note for note in info.get("notes") or [] if note]
        return {
            "gyaml": label,
            "status": "mesh",
            "reason": " ".join(notes),
            "source": source,
            "triangles": info["triangles"],
            "textures": info["textures"],
            "bounds": info["bounds"],
            "url": url,
            "digest": digest,
            "textureLog": info.get("textureLog") or [],
            "timing": info.get("timing") or {},
        }
    reason = errors[0] if len(errors) == 1 else " ".join(errors[:3])
    result = _box(label, reason or _missing(label, tried))
    if first:
        result["source"] = first
    return result


def _realize(dump, plan: list, errors: list[str]):
    for item in plan:
        if item[0] == "blob":
            yield item[1], item[2]
            continue
        relative = item[1]
        if not (dump.romfs / relative).is_file():
            continue
        try:
            blob, _dict_id = dump.read_file(relative)
        except BancError as exc:
            errors.append(f"{relative}: {exc.message}")
            continue
        yield relative, blob


def _cached_glb(digest: str) -> Path | None:
    if not digest:
        return None
    path = cache_dir() / digest[:2] / f"{digest}.glb"
    if not path.is_file():
        return None
    note_access(path)
    return path


def describe_actor_at(root: str, gyaml: str, flat: bool = False) -> dict:
    """Worker entry. Rebuilds the dump from its root so a child process can convert."""
    from ocarina_forge.dump import Dump

    return describe_actor(Dump(Path(root)), gyaml, flat=bool(flat))


def describe_terrain_at(root: str, banc: str) -> dict:
    from ocarina_forge.dump import Dump

    return describe_terrain(Dump(Path(root)), banc)


def _with_timing(info: dict, cache: str, clock: ConvertClock) -> dict:
    reported = dict(info)
    reported["timing"] = clock.finish(cache)
    return reported


def _convert_cached(blob: bytes, dump, hint: str = "", terrain: bool = False, convert: bool = True, flat: bool = False) -> tuple[str, dict]:
    if blob[:4] in (b"MCPK", b"FMSH"):
        raise FresError("this is a mesh-codec .bfres.mc stream, which this viewer does not decode")
    clock = ConvertClock()
    token = bind_clock(clock)
    try:
        return _convert_cached_timed(blob, dump, hint, terrain, convert, clock, flat)
    finally:
        reset_clock(token)


def _convert_cached_timed(blob: bytes, dump, hint: str, terrain: bool, convert: bool, clock: ConvertClock, flat: bool = False) -> tuple[str, dict]:
    source = _source_id(blob, hint, terrain, flat)
    pointer = cache_dir() / "src" / source[:2] / f"{source}.json"
    cached = _read_pointer(pointer, dump)
    if cached is not None:
        if cached.get("kind") == "box":
            raise UndecodedGeometry(cached.get("bounds"))
        return cached["digest"], _with_timing(cached["info"], "hit", clock)
    if not convert:
        raise CacheMiss()
    load_token = _LOAD_MEM.set({})
    read_token = _READ_MEM.set({})
    try:
        return _convert_uncached(blob, dump, hint, terrain, clock, pointer, flat)
    finally:
        _LOAD_MEM.reset(load_token)
        _READ_MEM.reset(read_token)


def _drop_textures(models) -> None:
    """Layout loads the mesh and does not decode BNTX."""
    for model in models:
        for material in model.materials or []:
            material.binds = []
        for part in model.parts:
            part.texture = ""
            part.candidates = []
            part.normal_texture = ""
            part.emission_texture = ""
            part.overlay_texture = ""


def _convert_uncached(blob: bytes, dump, hint: str, terrain: bool, clock: ConvertClock, pointer: Path, flat: bool = False) -> tuple[str, dict]:
    models = parse(blob)
    if models and not any(model.parts for model in models):
        flagged = [model for model in models if any(note.startswith("geometry format not decoded") for note in model.notes)]
        if flagged:
            bounds = next((model.shape_bounds for model in flagged if model.shape_bounds), None)
            _write_pointer(pointer, {"kind": "box", "bounds": bounds})
            raise UndecodedGeometry(bounds)
    files: list[dict] = []
    token = _FILES.set(files)
    try:
        if flat:
            _drop_textures(models)
            external, log, raws = {}, [], []
        else:
            _fill_texture_names(models, dump)
            external, log, raws = _resolve_materials(dump, models, hint, terrain)
    finally:
        _FILES.reset(token)
    digest = hashlib.sha256(_cache_prefix() + (b"flat\0" if flat else b"") + blob + _texture_key(raws)).hexdigest()
    folder = cache_dir() / digest[:2]
    glb_path = folder / f"{digest}.glb"
    meta_path = folder / f"{digest}.json"
    with _LOCK:
        if glb_path.is_file() and meta_path.is_file():
            info = load_model_meta(meta_path.read_text(encoding="utf-8"))
            _write_pointer(
                pointer,
                {"kind": "mesh", "digest": digest, "info": info, "files": files, "texDir": _tex_token(dump)},
            )
            return digest, _with_timing(info, "hit", clock)
    glb, info = _assemble(models, external, log)
    info = _with_timing(info, "miss", clock)
    encoded = dump_model_meta(info)
    with _LOCK:
        if not (glb_path.is_file() and meta_path.is_file()):
            folder.mkdir(parents=True, exist_ok=True)
            glb_path.write_bytes(glb)
            meta_path.write_text(encoded, encoding="utf-8")
            note_written(glb_path, meta_path)
        _write_pointer(
            pointer,
            {"kind": "mesh", "digest": digest, "info": info, "files": files, "texDir": _tex_token(dump)},
        )
    return digest, info


def _source_id(blob: bytes, hint: str, terrain: bool, flat: bool = False) -> str:
    flag = b"f" if flat else (b"t" if terrain else b"a")
    return hashlib.sha256(_cache_prefix() + flag + hint.encode("utf-8", "replace") + b"\0" + blob).hexdigest()


def _tex_token(dump) -> dict:
    if dump is None:
        return {"mtime_ns": 0}
    folder = dump.romfs / "Tex"
    if not folder.is_dir():
        return {"mtime_ns": 0}
    try:
        return {"mtime_ns": folder.stat().st_mtime_ns}
    except OSError:
        return {"mtime_ns": 0}


def _write_pointer(path: Path, payload: dict) -> None:
    body = dict(payload)
    body["cacheVersion"] = model_version()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)
    note_written(path)


def _read_pointer(path: Path, dump) -> dict | None:
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("kind") == "box":
        note_access(path)
        return record
    digest = record.get("digest") or ""
    if not digest or not (cache_dir() / digest[:2] / f"{digest}.glb").is_file():
        return None
    saved = record.get("texDir") or {}
    current = _tex_token(dump)
    if int(saved.get("mtime_ns") or 0) != int(current.get("mtime_ns") or 0):
        return None
    if not _files_match(record.get("files") or [], dump):
        return None
    if not isinstance(record.get("info"), dict):
        return None
    note_access(path)
    glb = cache_dir() / digest[:2] / f"{digest}.glb"
    if glb.is_file():
        note_access(glb)
    return record


def _files_match(files: list, dump) -> bool:
    if dump is None:
        return not files
    for item in files:
        relative = item.get("path") or ""
        path = dump.romfs / relative
        if not path.is_file():
            return False
        try:
            st = path.stat()
        except OSError:
            return False
        if st.st_size == item.get("size") and st.st_mtime_ns == item.get("mtime_ns"):
            continue
        if _file_sig(path, st) != item.get("sig"):
            return False
    return True


def _track_file(dump, relative: str) -> None:
    files = _FILES.get()
    if files is None or dump is None or not relative or relative.startswith("embedded"):
        return
    if any(item.get("path") == relative for item in files):
        return
    path = dump.romfs / relative
    try:
        st = path.stat()
    except OSError:
        return
    files.append(
        {
            "path": relative,
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "sig": _file_sig(path, st),
        }
    )


def _file_sig(path: Path, st=None) -> str:
    st = st or path.stat()
    key = str(path)
    found = _FILE_SIG.get(key)
    if found and found[0] == st.st_mtime_ns and found[1] == st.st_size:
        return found[2]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _FILE_SIG[key] = (st.st_mtime_ns, st.st_size, digest)
    return digest


def _decode_bntx_cached(blob: bytes, tex_path: str = "") -> list:
    key = _bntx_cache_key(blob, tex_path)
    cached = _BNTX_MEM.get(key)
    if cached is not None:
        _touch_bntx(key)
        return cached
    path = cache_dir() / "bntx" / key[:2] / f"{key}.bwtx"
    loaded = _read_bwtx(path)
    if loaded is not None:
        note_access(path)
        _remember_bntx(key, loaded)
        return loaded
    clock = current_clock()
    if clock is not None:
        clock.begin_texture()
    try:
        decoded = list(read_bntx(blob))
    finally:
        if clock is not None:
            clock.end_texture()
    _write_bwtx(path, decoded)
    _remember_bntx(key, decoded)
    return decoded


def _bntx_cache_key(blob: bytes, tex_path: str = "", extra: bytes = b"") -> str:
    """Whole-file key. A shared Tex path reuses one decode across models."""
    if tex_path:
        stamp = struct.pack("<Q", len(blob))
        stamp += blob[:16]
        stamp += blob[-16:] if len(blob) > 16 else b""
        raw = _BNTX_VERSION + b"path\0" + tex_path.encode("utf-8", "replace") + b"\0" + stamp + extra
    else:
        raw = _BNTX_VERSION + blob + extra
    return hashlib.sha256(raw).hexdigest()


_BLOB_KEYS: dict[tuple, bytes] = {}


def _blob_key(blob: bytes) -> bytes:
    """Hash a BNTX once per process. A 59MB array is not rehashed for every layer."""
    ident = (len(blob), blob[:48], blob[-48:] if len(blob) > 48 else b"")
    found = _BLOB_KEYS.get(ident)
    if found is not None:
        return found
    digest = hashlib.sha256(blob).digest()
    if len(_BLOB_KEYS) > 64:
        _BLOB_KEYS.clear()
    _BLOB_KEYS[ident] = digest
    return digest


def _decode_layer_cached(blob: bytes, index: int, *, flatten_alpha: bool, tex_path: str = "") -> object:
    """One array layer, cached on its own so the rest of the stack is not decoded."""
    from ocarina_forge.formats.bntx import texture_layer

    extra = b"layer\0" + struct.pack("<IB", index & 0xFFFFFFFF, 1 if flatten_alpha else 0)
    if tex_path:
        token = _bntx_cache_key(blob, tex_path, extra)
    else:
        token = hashlib.sha256(_BNTX_VERSION + extra + _blob_key(blob)).hexdigest()
    cached = _BNTX_MEM.get(token)
    if cached:
        _touch_bntx(token)
        return cached[0]
    path = cache_dir() / "bntx" / token[:2] / f"{token}.bwtx"
    loaded = _read_bwtx(path)
    if loaded:
        note_access(path)
        _remember_bntx(token, loaded)
        return loaded[0]
    clock = current_clock()
    if clock is not None:
        clock.begin_texture()
    try:
        image = texture_layer(blob, index, flatten_alpha=flatten_alpha)
    finally:
        if clock is not None:
            clock.end_texture()
    _write_bwtx(path, [image])
    _remember_bntx(token, [image])
    return image


def _touch_bntx(key: str) -> None:
    if key in _BNTX_ORDER:
        _BNTX_ORDER.remove(key)
    _BNTX_ORDER.append(key)


def _remember_bntx(key: str, decoded: list) -> None:
    _BNTX_MEM[key] = decoded
    _touch_bntx(key)
    # A scene reuses the same bricks across hundreds of models. 32 slots
    # threw them out, and the next model zlib-decompressed the cache file
    # again. That read sits outside the texture clock, in the profile's other.
    while len(_BNTX_ORDER) > 512:
        old = _BNTX_ORDER.pop(0)
        _BNTX_MEM.pop(old, None)


def _write_bwtx(path: Path, decoded: list) -> None:
    payload = bytearray()
    payload += struct.pack("<I", len(decoded))
    for image in decoded:
        payload += _pack_str(image.name)
        payload += _pack_str(image.codec)
        payload += _pack_str(image.swizzle)
        payload += _pack_str(image.note)
        rgba = image.rgba or b""
        payload += struct.pack("<IIIIII", image.width, image.height, image.fmt, image.tile, image.block_height, len(rgba))
        payload += rgba
        blocks = getattr(image, "blocks", b"") or b""
        payload += struct.pack("<HHHI", getattr(image, "block_w", 0), getattr(image, "block_h", 0), getattr(image, "bytes_per", 0), len(blocks))
        payload += blocks
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(b"BWTX" + struct.pack("<H", 2) + zlib.compress(bytes(payload), 1))
    os.replace(tmp, path)
    write_version_sidecar(path, bntx_version())
    note_written(path)


def _pack_str(text: str) -> bytes:
    raw = (text or "").encode("utf-8", "replace")
    return struct.pack("<H", len(raw)) + raw


def _read_bwtx(path: Path) -> list | None:
    if not path.is_file():
        return None
    try:
        blob = path.read_bytes()
        if blob[:4] != b"BWTX":
            return None
        version = struct.unpack_from("<H", blob, 4)[0]
        if version not in (1, 2):
            return None
        payload = zlib.decompress(blob[6:])
        count = struct.unpack_from("<I", payload, 0)[0]
        at = 4
        found = []
        from ocarina_forge.formats.bntx import Texture

        for _index in range(count):
            name, at = _unpack_str(payload, at)
            codec, at = _unpack_str(payload, at)
            swizzle, at = _unpack_str(payload, at)
            note, at = _unpack_str(payload, at)
            width, height, fmt, tile, block_height, size = struct.unpack_from("<IIIIII", payload, at)
            at += 24
            rgba = payload[at : at + size]
            at += size
            blocks = b""
            block_w = block_h = bytes_per = 0
            if version == 2 and at + 10 <= len(payload):
                block_w, block_h, bytes_per, block_size = struct.unpack_from("<HHHI", payload, at)
                at += 10
                blocks = payload[at : at + block_size]
                at += block_size
            found.append(
                Texture(
                    name=name,
                    width=width,
                    height=height,
                    rgba=bytes(rgba),
                    fmt=fmt,
                    note=note,
                    codec=codec,
                    tile=tile,
                    swizzle=swizzle,
                    block_height=block_height,
                    blocks=bytes(blocks),
                    block_w=block_w,
                    block_h=block_h,
                    bytes_per=bytes_per,
                )
            )
        return found
    except (OSError, zlib.error, struct.error, ValueError):
        return None


def _unpack_str(payload: bytes, at: int) -> tuple[str, int]:
    size = struct.unpack_from("<H", payload, at)[0]
    at += 2
    text = payload[at : at + size].decode("utf-8", "replace")
    return text, at + size


def _texture_key(blobs: list[bytes]) -> bytes:
    return b"".join(blob + b"\0" for blob in blobs)


_IMG_MEM: dict[str, bytes] = {}
_IMG_ORDER: list[str] = []


def _encode_cached(image, alpha: str, gpu: bool = False) -> bytes:
    """JPEG, PNG, or a GPU container. The same pixels are not encoded twice."""
    from ocarina_forge.formats.gpu import gpu_image

    token = hashlib.sha256(
        _BNTX_VERSION
        + struct.pack("<IIB", image.width, image.height, 2 if gpu else 0)
        + (alpha or "opaque").encode()
        + image.rgba
    ).hexdigest()
    hit = _IMG_MEM.get(token)
    if hit is not None:
        return hit
    path = cache_dir() / "img" / token[:2] / f"{token}.bin"
    if path.is_file():
        try:
            stored = path.read_bytes()
        except OSError:
            stored = b""
        if stored[:4] == b"OCFG" or stored[:2] in (b"\xff\xd8", b"\x89P"):
            _remember_img(token, stored)
            note_access(path)
            return stored
    encoded = gpu_image(image, alpha) if gpu else None
    if not encoded:
        encoded, _mime = encode_albedo(image.width, image.height, image.rgba, alpha)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(encoded)
    os.replace(tmp, path)
    write_version_sidecar(path, bntx_version())
    note_written(path)
    _remember_img(token, encoded)
    return encoded


def _remember_img(token: str, encoded: bytes) -> None:
    _IMG_MEM[token] = encoded
    if token in _IMG_ORDER:
        _IMG_ORDER.remove(token)
    _IMG_ORDER.append(token)
    while len(_IMG_ORDER) > 1024:
        _IMG_MEM.pop(_IMG_ORDER.pop(0), None)


def _apply_stand_in(part, choice: dict, images: dict | None = None) -> None:
    if choice.get("kind") != "water":
        return
    part.stand_in = "water"
    part.alpha = "blend"
    part.proxy = False
    part.base_color = _WATER
    if choice.get("image") is None:
        part.texture = _WATER_TEX
        part.wrap = "repeat"
        if images is not None and _WATER_TEX not in images:
            images[_WATER_TEX] = _water_ripple()


def _consider_water(names: list[str], binds, result: dict) -> dict:
    labels = [name for name in names if name]
    labels.extend(tex for _slot, tex in binds if tex)
    if not _water_names(*labels):
        return result
    reject = _water_reject(result.get("image"), result.get("wanted") or "")
    result["alpha"] = "blend"
    result["kind"] = "water"
    entry = result.get("entry") or {}
    entry["alpha"] = "blend"
    if reject:
        result["image"] = None
        result["wanted"] = ""
        result["sampler"] = ""
        entry["wanted"] = ""
        entry["sampler"] = ""
        entry["decoded"] = False
        entry["textured"] = False
        entry["flat"] = True
        entry["standIn"] = "water"
        previous = entry.get("error") or ""
        entry["error"] = f"water stand-in ({reject})" if not previous else f"water stand-in ({reject}). {previous}"
    else:
        extra = "water material, unlit translucent, depth write off"
        previous = entry.get("error") or ""
        entry["error"] = extra if not previous else f"{previous}. {extra}"
    result["entry"] = entry
    return result


def _water_ripple():
    """A small tiled colour so a moat is not a flat sheet when the albedo is missing."""
    from ocarina_forge.formats.bntx import Texture

    width = height = 16
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            wave = ((x * 3 + y * 5) % 8) / 7
            band = 1 if (x // 4 + y // 4) % 2 == 0 else 0
            red = int(18 + wave * 20)
            green = int(70 + wave * 40 + band * 12)
            blue = int(88 + wave * 36)
            at = (y * width + x) * 4
            rgba[at : at + 4] = bytes((red, green, blue, 210))
    return Texture(_WATER_TEX, width, height, bytes(rgba), 0, "generated water stand-in")


def _axis_bounds(positions):
    from ocarina_forge.formats.bfres import Vecs

    if not positions:
        return None
    if isinstance(positions, Vecs):
        found = positions.bounds()
        if not found or len(found[0]) < 3:
            return None
        return found[0][:3], found[1][:3]
    return (
        [min(point[axis] for point in positions) for axis in range(3)],
        [max(point[axis] for point in positions) for axis in range(3)],
    )


def _span(positions) -> float:
    found = _axis_bounds(positions)
    if not found:
        return 0.0
    return max(high - low for low, high in zip(found[0], found[1], strict=False))


def _untextured_bounds_box(part, labeled) -> bool:
    """A coarse untextured box sitting under a smaller detailed mesh.

    Hyrule Castle draws a solid cream cube with a miniature courtyard on it
    when a bounds or occluder shape shares a model with the real mesh and
    never receives an albedo. The detailed mesh stays.
    """
    if part.uvs or len(part.positions) > 24 or _span(part.positions) < 4:
        return False
    return any(
        other is not part and other.uvs and len(other.positions) > len(part.positions)
        for other, _name in labeled
    )


def _water_names(*names: str) -> bool:
    tokens: set[str] = set()
    for name in names:
        tokens.update(re.findall(r"[a-z0-9]+", (name or "").lower()))
    if "npc" in tokens:
        return False
    return bool(tokens & _WATER_TOKENS)


def _water_reject(image, wanted: str) -> str:
    if image is None or not getattr(image, "rgba", None):
        return "no usable albedo"
    if texture_rank(wanted) >= 2:
        return "non-color sampler"
    note = getattr(image, "note", "") or ""
    if "still looks noisy" in note:
        return "swizzle still looks noisy"
    if _looks_like_normal(image.rgba):
        return "albedo looks like a normal map"
    if _looks_blank(image.rgba):
        return "albedo is a flat pale sheet"
    return ""


def _pixel_samples(rgba: bytes) -> list[bytes]:
    total = len(rgba) // 4
    if total < 16:
        return []
    step = max(1, total // 512)
    return [rgba[index * 4 : index * 4 + 4] for index in range(0, total, step)]


def _looks_like_normal(rgba: bytes) -> bool:
    samples = _pixel_samples(rgba)
    if len(samples) < 16:
        return False
    count = len(samples)
    red = sum(pixel[0] for pixel in samples) / count
    green = sum(pixel[1] for pixel in samples) / count
    blue = sum(pixel[2] for pixel in samples) / count
    return 90 <= red <= 175 and 90 <= green <= 175 and blue >= 185 and blue > red + 40 and blue > green + 40


def _looks_blank(rgba: bytes) -> bool:
    samples = _pixel_samples(rgba)
    if len(samples) < 16:
        return False
    count = len(samples)
    lum = sum(0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2] for pixel in samples) / count
    chroma = sum(max(pixel[0], pixel[1], pixel[2]) - min(pixel[0], pixel[1], pixel[2]) for pixel in samples) / count
    return lum > 200 and chroma < 28


def _terra_layer(material) -> tuple[int, str] | None:
    """Layer of MaterialAlb for a terra material, or the sand-cliff guess.

    Version-10 FMAT does not keep the version-9 render-info dictionary.
    ``+0x18`` and ``+0x20`` are the texture list, so those keys come back
    empty. A numbered ``Mt_TerraMaterial_NNN`` uses layer NNN. The sand
    cliff has no number, so it uses layer 16 and says so.
    """
    name = getattr(material, "name", "") or ""
    numbered = _TERRA_LAYER.search(name)
    if numbered:
        return int(numbered.group(1)), ""
    low = name.lower()
    if "sand" in low and "cliff" in low:
        info = (getattr(material, "note", "") or "").strip()
        if not info:
            info = (
                "v10 FMAT has no render-info dictionary "
                "(version-9 offsets +0x18 and +0x20 are the texture list, so the keys are empty)"
            )
        return 16, f"layer 16 is a guess; {info}"
    return None


def _eyebrow_face(material_name: str, texture_name: str) -> bool:
    return "eyebrow" in (material_name or "").lower() and "face" in (texture_name or "").lower()


def _category_color(name: str) -> tuple[tuple[float, float, float, float], str] | None:
    low = (name or "").lower()
    if any(word in low for word in ("rock", "stone")):
        return _ROCK, "rock"
    if any(word in low for word in ("vine", "leaf", "ivy")):
        return _VINE, "vine"
    if any(word in low for word in ("cloth", "wood", "plank", "fabric")):
        return _CLOTH, "cloth"
    return None


def _letter_siblings(name: str) -> list[str]:
    """``FieldProp_J`` borrows ``FieldProp_I`` and ``FieldProp_K``."""
    stem = _stem(name)
    parts = stem.split("_")
    found = []
    for index, part in enumerate(parts):
        if len(part) != 1 or not part.isalpha():
            continue
        base = ord(part.upper())
        for delta in (-1, 1):
            nxt = base + delta
            if nxt < ord("A") or nxt > ord("Z"):
                continue
            letter = chr(nxt)
            clone = parts[:]
            clone[index] = letter if part.isupper() else letter.lower()
            sibling = "_".join(clone)
            if sibling not in found:
                found.append(sibling)
    return found


def _borrow_sibling(dump, model, sources, hints, material, external, raws):
    for sampler, name in sources:
        if not name:
            continue
        for sibling in _letter_siblings(name):
            found = _load_named(dump, model, sibling, hints, material.alpha, material.cutoff, external, raws)
            if found.get("image") is not None:
                return sampler, sibling, found, name
    return None


def _omit_reason(shape: str, material: str) -> str:
    compact = f"{shape} {material}".lower().replace("_", "").replace(" ", "")
    if "watervolume" in compact:
        return "water volume is not drawn"
    if "bakeshadowproxy" in compact:
        return "bake shadow proxy is not drawn"
    if "fadeblack" in compact:
        return "fade black is not drawn"
    if "innermesh" in compact:
        return "inner mesh is not drawn"
    if "eyeouter" in compact or "eyeshadow" in compact:
        return "eye overlay is not drawn"
    for word, reason in (
        ("occluder", "occluder is not drawn"),
        ("navmesh", "navmesh proxy is not drawn"),
        ("minimap", "minimap model is not drawn"),
        ("mapicon", "map icon is not drawn"),
        ("sightblock", "sight blocker is not drawn"),
        ("charactersight", "sight blocker is not drawn"),
        ("collision", "collision mesh is not drawn"),
        ("collider", "collision mesh is not drawn"),
        ("impostor", "impostor is not drawn"),
    ):
        if word in compact:
            return reason
    return ""


def _mark_omitted(models) -> None:
    """Drop helper shapes, and the back water sheet when a front sheet exists."""
    for model in models:
        materials = model.materials or []

        def label(part) -> str:
            if 0 <= part.material_index < len(materials):
                return materials[part.material_index].name
            return part.material or ""

        labeled = [(part, label(part)) for part in model.parts]
        front = any(
            "front" in f"{part.name} {name}".lower() and ("water" in f"{part.name} {name}".lower() or "flow" in f"{part.name} {name}".lower())
            for part, name in labeled
        )
        for part, name in labeled:
            reason = _omit_reason(part.name, name)
            low = f"{part.name} {name}".lower()
            if not reason and front and "back" in low and ("water" in low or "flow" in low):
                reason = "back water layer is not drawn"
            if not reason and _untextured_bounds_box(part, labeled):
                reason = "untextured bounds box is not drawn"
            if reason:
                part.omit = True
                part.omit_reason = reason


def _luma_alpha(rgba: bytes) -> bytes:
    """Put the colour into alpha so a BC1 mask with an empty alpha still cuts out."""
    out = bytearray(rgba)
    for at in range(0, len(out) - 3, 4):
        luma = int(0.2126 * out[at] + 0.7152 * out[at + 1] + 0.0722 * out[at + 2])
        out[at + 3] = luma
    return bytes(out)


def _assemble(models, external: dict, log: list[dict] | None = None) -> tuple[bytes, dict]:
    log = log if log is not None else []
    by_wanted = {}
    for entry in log:
        if entry.get("wanted") and entry["wanted"] not in by_wanted:
            by_wanted[entry["wanted"]] = entry
    _mark_omitted(models)
    primitives: list[Primitive] = []
    notes: list[str] = []
    triangles = 0
    texture_count = 0
    seen_images: set[int] = set()
    boxes: list[tuple] = []
    for model in models:
        notes.extend(model.notes)
        png_for: dict[int, bytes] = {}
        for part in model.parts:
            if part.omit:
                continue
            def taken(name: str):
                if not name:
                    return None
                image = model.images.get(name) or external.get(name) or external.get(name.lower())
                if image is None or not image.rgba:
                    return None
                return image

            image = taken(part.texture)
            png = None
            water = part.stand_in == "water"
            if water and (image is None or part.texture == _WATER_TEX):
                if "water stand-in" not in notes:
                    notes.append("water stand-in")
            if image is not None:
                png = png_for.get(id(image))
                if png is None:
                    # A terra array layer is stored as PNG. Opaque JPEG is for ordinary albedos.
                    png = _encode_cached(image, "png" if part.wrap == "repeat" else part.alpha, gpu=True)
                    png_for[id(image)] = png
                    if id(image) not in seen_images:
                        texture_count += 1
                        seen_images.add(id(image))
                    if image.note and "punched" in image.note:
                        notes.append(image.note)
            else:
                detail = by_wanted.get(part.texture) or {}
                why = detail.get("error") or ""
                if part.proxy:
                    if "terrain proxy" not in notes:
                        notes.append("terrain proxy")
                elif part.texture:
                    notes.append(f"Base color “{part.texture}” {why or 'was not usable'}, so this mesh uses a flat colour.")
                elif not model.images and not external:
                    reason = why or "No albedo sampler was usable and no Tex file matched."
                    notes.append(f"{reason} This mesh uses a flat colour.")
            unlit = False
            kind = ""
            if water:
                # A real albedo keeps the previous pale multiply so a forest
                # stream does not change. The stand-in ripple uses the deeper tint.
                color = _WATER if part.texture == _WATER_TEX or image is None else (0.35, 0.55, 0.55, 0.2)
                alpha = "blend"
                unlit = True
                kind = "water"
            elif part.proxy and image is None:
                color = _TERRAIN
                alpha = "opaque"
            else:
                color = (1.0, 1.0, 1.0, 1.0) if image is not None else (part.base_color or _NEUTRAL)
                alpha = part.alpha if image is not None or part.alpha == "blend" else "opaque"

            def extra_png(name: str):
                nonlocal texture_count
                extra = taken(name)
                if extra is None:
                    return None
                encoded = png_for.get(id(extra))
                if encoded is None:
                    encoded = _encode_cached(extra, "mask", gpu=True)
                    png_for[id(extra)] = encoded
                    if id(extra) not in seen_images:
                        texture_count += 1
                        seen_images.add(id(extra))
                return encoded

            def image_png(image):
                nonlocal texture_count
                if image is None or not getattr(image, "rgba", None):
                    return None
                encoded = png_for.get(id(image))
                if encoded is None:
                    # ORM is four data channels. JPEG would crush roughness and metal.
                    # Pack BC7 like albedos: Field's unique normals and ORM were the remaining RGBA cost.
                    encoded = _encode_cached(image, "png", gpu=True)
                    png_for[id(image)] = encoded
                    if id(image) not in seen_images:
                        texture_count += 1
                        seen_images.add(id(image))
                return encoded

            normal_png = extra_png(part.normal_texture)
            normal1_png = extra_png(part.normal1_texture)
            orm_png = image_png(part.orm_image)
            orm1_png = image_png(part.orm1_image)
            emissive_png = image_png(part.emission_image) or extra_png(part.emission_texture)
            if part.surface_note and part.surface_note not in notes:
                notes.append(part.surface_note)
            if part.emission_note and part.emission_note not in notes:
                notes.append(part.emission_note)
            moss_png = None
            blend_extra = None
            overlay_uvs = None
            if part.blend and part.overlay_texture:
                moss_png = extra_png(part.overlay_texture)
            if moss_png:
                blend_extra = {
                    "blend": {
                        "channel": part.blend.get("channel") or "r",
                        "uv": part.blend.get("uv") or "_u0",
                        "scale": list(part.blend.get("scale") or [1, 1]),
                        "translate": list(part.blend.get("translate") or [0, 0]),
                        "sampler": part.blend.get("sampler") or "",
                        "texture": part.overlay_texture,
                    }
                }
                wanted_uv = part.blend.get("uv") or "_u0"
                if wanted_uv != "_u0":
                    overlay_uvs = (part.uv_extra or {}).get(wanted_uv)
                    if overlay_uvs is not None and len(overlay_uvs) != len(part.positions):
                        overlay_uvs = None
                if not any(note.startswith("blend ") for note in notes):
                    notes.append(
                        f"blend {_c0_label(part.blend)} on {part.blend.get('sampler') or '_a1'} {part.overlay_texture}"
                    )
            primitives.append(
                Primitive(
                    part.positions,
                    part.indices,
                    part.uvs,
                    normals=part.normals,
                    png=png,
                    alpha=alpha,
                    cutoff=part.cutoff,
                    double_sided=part.double_sided,
                    base_color=color,
                    lod=part.lod,
                    name=f"{part.name}:{part.material_index}",
                    unlit=unlit,
                    kind=kind,
                    colors=part.colors if blend_extra else None,
                    uvs2=overlay_uvs,
                    normal_png=normal_png,
                    emissive_png=emissive_png,
                    moss_png=moss_png,
                    extras=blend_extra,
                    orm_png=orm_png,
                    orm1_png=orm1_png,
                    normal1_png=normal1_png,
                )
            )
            if part.lod == 0 and not any(other.name == part.name and other.lod for other in model.parts):
                if meshopt_available():
                    coarser = simplify_mesh(part.positions, part.indices, part.uvs, part.colors if blend_extra else None)
                else:
                    coarser = cluster_lod(part.positions, part.indices, part.uvs, colors=part.colors if blend_extra else None)
                if coarser:
                    primitives.append(
                        Primitive(
                            coarser[0],
                            coarser[1],
                            coarser[2],
                            png=png,
                            alpha=alpha,
                            cutoff=part.cutoff,
                            double_sided=part.double_sided,
                            base_color=color,
                            lod=1,
                            name=f"{part.name}:{part.material_index}",
                            unlit=unlit,
                            kind=kind,
                            colors=coarser[3] if blend_extra and len(coarser) > 3 else None,
                            normal_png=normal_png,
                            emissive_png=emissive_png,
                            moss_png=moss_png,
                            extras=blend_extra,
                            orm_png=orm_png,
                            orm1_png=orm1_png,
                            normal1_png=normal1_png,
                        )
                    )
            triangles += len(part.indices) // 3
            span = _axis_bounds(part.positions)
            if span:
                boxes.append(span)
    if not primitives:
        reasons = []
        for model in models:
            for part in model.parts:
                if part.omit_reason and part.omit_reason not in reasons:
                    reasons.append(part.omit_reason)
        raise FresError("; ".join(reasons) if reasons else "the model has no triangles")
    glb = finish_glb(to_glb(primitives))
    bounds = None
    if boxes:
        bounds = {
            "min": [min(box[0][axis] for box in boxes) for axis in range(3)],
            "max": [max(box[1][axis] for box in boxes) for axis in range(3)],
        }
    unique_notes = list(dict.fromkeys(note for note in notes if note))[:12]
    return glb, {
        "triangles": triangles,
        "textures": texture_count,
        "bounds": bounds,
        "notes": unique_notes,
        "textureLog": log,
    }


def _fill_texture_names(models, dump) -> None:
    index = _tex_index(dump) if dump is not None else {}
    for model in models:
        if any(slot for material in model.materials for slot, _tex in material.binds):
            continue
        pooled = _pool_albedo(model.string_pool or model.extra_textures, index)
        if not pooled:
            pooled = base_color_name([name for name in model.extra_textures if texture_rank(name) < 2])
        for part in model.parts:
            if part.texture or part.material_index >= 0:
                continue
            if pooled:
                part.texture = pooled
                part.candidates = [("", pooled)]


def _resolve_materials(dump, models, hint: str, terrain: bool) -> tuple[dict, list[dict], list[bytes]]:
    hints = _hint_tokens(hint)
    external: dict = {}
    log: list[dict] = []
    raws: list[bytes] = []
    remembered: dict[int, dict] = {}
    for model in models:
        if model.materials:
            for material in model.materials:
                key = id(material)
                if key not in remembered:
                    remembered[key] = _choose_material(dump, model, material, hints, terrain, external, raws)
                choice = remembered[key]
                shapes = [
                    part.name
                    for part in model.parts
                    if 0 <= part.material_index < len(model.materials) and model.materials[part.material_index] is material
                ]
                choice["entry"]["shapes"] = list(dict.fromkeys(shapes))
                choice["entry"]["textured"] = choice.get("image") is not None and not choice.get("proxy")
                choice["entry"]["flat"] = not choice["entry"]["textured"] and bool(choice["entry"]["shapes"])
                log.append(choice["entry"])
                for part in model.parts:
                    if part.material_index < 0 or part.material_index >= len(model.materials):
                        continue
                    if model.materials[part.material_index] is not material:
                        continue
                    part.texture = choice["wanted"]
                    part.albedo_sampler = choice["sampler"]
                    part.normal_texture = (choice.get("maps") or {}).get("normal") or ""
                    part.emission_texture = (choice.get("maps") or {}).get("emission") or ""
                    overlay = choice.get("overlay") or {}
                    part.overlay_texture = overlay.get("texture") or ""
                    part.blend = overlay.get("plan")
                    surface = choice.get("surface") or {}
                    part.orm_image = surface.get("orm")
                    part.orm1_image = surface.get("orm1")
                    part.normal1_texture = surface.get("normal1") or ""
                    part.surface_note = surface.get("note") or ""
                    part.emission_image = (choice.get("emission") or {}).get("image")
                    part.emission_note = (choice.get("emission") or {}).get("note") or ""
                    part.alpha = choice["alpha"]
                    part.cutoff = choice["cutoff"]
                    if choice.get("wrap"):
                        part.wrap = choice["wrap"]
                    if choice.get("base_color") and choice.get("image") is None:
                        part.base_color = choice["base_color"]
                    if choice.get("proxy"):
                        part.proxy = True
                        part.base_color = _TERRAIN
                        part.texture = ""
                    _apply_stand_in(part, choice, external)
                    if choice.get("image") is not None:
                        external[choice["wanted"]] = choice["image"]
        else:
            for part in model.parts:
                choice = _choose_loose(dump, model, part, hints, terrain, external, raws)
                log.append(choice["entry"])
                part.texture = choice["wanted"]
                part.alpha = choice["alpha"]
                _apply_stand_in(part, choice, external)
                if choice.get("image") is not None and choice["wanted"]:
                    external[choice["wanted"]] = choice["image"]
    return external, log, raws


def _c0_label(plan: dict) -> str:
    return f"_c0.{plan.get('channel') or 'r'}"


def _overlay_choice(dump, model, material, hints, external, raws) -> dict:
    """The decoded ``_a1`` layer, or nothing when this material is a single albedo."""
    span = None
    for part in model.parts:
        if part.material_index < 0 or part.material_index >= len(model.materials):
            continue
        if model.materials[part.material_index] is not material:
            continue
        span = color_span(part.vertex_attributes)
        if span:
            break
    plan = overlay_plan(material, span)
    if not plan:
        return {}
    found = _load_named(dump, model, plan["texture"], hints, "opaque", material.cutoff, external, raws)
    image = found.get("image")
    located = found.get("found") or ""
    if image is None or not (located == "embedded" or _stem(located).lower() == _stem(plan["texture"]).lower()):
        return {}
    external[plan["texture"]] = image
    return {"texture": plan["texture"], "plan": plan}


def _choose_material(dump, model, material, hints, terrain, external, raws) -> dict:
    sources = list(material.albedo)
    has_samplers = any(slot for slot, _tex in material.binds)
    pool_note = ""
    # A material that names samplers and has no _a0/_a1/_a2 stays flat.
    # The string pool is what painted later shapes magenta. Dummy terrain
    # (no textures, no UVs) is a solid colour, not a Tex lookup.
    proxy = bool(terrain and not sources and not has_samplers)
    if not sources and not has_samplers and not proxy:
        pooled = _pool_albedo(model.string_pool or model.extra_textures, _tex_index(dump) if dump is not None else {})
        if not pooled:
            pooled = base_color_name([name for name in model.extra_textures if texture_rank(name) < 2])
        if pooled:
            sources = [("", pooled)]
            pool_note = f"texture name taken from the file string pool ({pooled})"
    misses = []
    winner = None
    tried: list[str] = []
    wrap = ""
    flat_color = None
    last = {"image": None, "error": "", "tried": [], "found": "", "alpha": material.alpha, "cutoff": material.cutoff, "images": 0}
    terra = _terra_layer(material)
    if terra is not None:
        layer, guess = terra
        found = _load_named(
            dump,
            model,
            "MaterialAlb",
            hints,
            "opaque",
            material.cutoff,
            external,
            raws,
            layer=layer,
            flatten_alpha=True,
        )
        last = found
        for path in found.get("tried") or []:
            if path not in tried:
                tried.append(path)
        if found["image"] is not None:
            winner = ("ta", f"MaterialAlb#{layer}", found)
            wrap = "repeat"
            if guess:
                found["error"] = f"{guess}. {found.get('error') or ''}".strip(". ")
            else:
                extra = f"MaterialAlb layer {layer}"
                found["error"] = f"{extra}. {found.get('error') or ''}".strip(". ")
        elif found.get("error"):
            misses.append(f"MaterialAlb layer {layer}: {found['error']}")
        else:
            misses.append(f"MaterialAlb layer {layer}: no Tex file matched")
    for sampler, name in sources:
        if winner is not None:
            break
        if _eyebrow_face(material.name, name):
            misses.append(f"{name}: eyebrow does not use the face texture")
            flat_color = _BROW
            continue
        found = _load_named(dump, model, name, hints, material.alpha, material.cutoff, external, raws)
        last = found
        for path in found.get("tried") or []:
            if path not in tried:
                tried.append(path)
        if found["image"] is not None:
            winner = (sampler, name, found)
            break
        if found["error"]:
            misses.append(f"{sampler or 'texture'} {name}: {found['error']}")
        elif name:
            misses.append(f"{sampler or 'texture'} {name}: no Tex file matched")
    if winner is None and terrain and dump is not None and sources and not proxy:
        hinted = _hint_alb_path(dump, hints)
        if hinted:
            stem = _stem(hinted)
            found = _load_named(dump, model, stem, hints, material.alpha, material.cutoff, external, raws, forced=hinted)
            last = found
            for path in found.get("tried") or []:
                if path not in tried:
                    tried.append(path)
            if found["image"] is not None:
                winner = ("", stem, found)
                pool_note = f"no albedo name matched; used {hinted}"
            elif found["error"]:
                misses.append(f"terrain {stem}: {found['error']}")
    if winner is None and dump is not None and sources and flat_color is None:
        borrowed = _borrow_sibling(dump, model, sources, hints, material, external, raws)
        if borrowed is not None:
            sampler, sibling, found, missing = borrowed
            winner = (sampler, sibling, found)
            last = found
            for path in found.get("tried") or []:
                if path not in tried:
                    tried.append(path)
            note = f"borrowed {sibling} because {missing} is missing"
            found["error"] = f"{note}. {found.get('error') or ''}".strip(". ")
    if winner is None and flat_color is None:
        label = " ".join(name for _sampler, name in sources) or material.name
        category = _category_color(label)
        if category is not None:
            flat_color, kind = category
            misses.append(f"no Tex file; flat {kind} colour")
    if winner is not None:
        sampler, wanted, found = winner
    else:
        sampler, wanted, found = "", (sources[0][1] if sources else ""), dict(last)
        found["tried"] = tried or found.get("tried") or []
        if flat_color == _BROW:
            wanted = ""
    error = found.get("error") or ""
    if proxy and winner is None:
        sampler, wanted = "", ""
        error = "terrain proxy"
    elif misses and winner is not None:
        error = "; ".join(misses + ([error] if error else []))
    elif misses and winner is None:
        error = "; ".join(misses)
    elif not sources and not error:
        if material.binds:
            shown = ", ".join(f"{slot or '?'}={tex}" for slot, tex in material.binds)
            error = f"only non-color samplers ({shown})"
        else:
            error = "material has no texture names"
    if pool_note:
        error = f"{pool_note}. {error}".strip() if error and winner is None else (pool_note if not error else f"{error}. {pool_note}")
    alpha = found.get("alpha", material.alpha) if found.get("image") is not None else material.alpha
    cutoff = found.get("cutoff", material.cutoff)
    if proxy and winner is None:
        wanted = ""
    entry = _log_entry(material, sampler, wanted, found, alpha, cutoff, material.double_sided, error if not (found.get("image") is not None and not error) else error)
    if found.get("image") is not None and not proxy:
        entry["decoded"] = True
        _fill_image_log(entry, found)
    if proxy and winner is None:
        entry["wanted"] = ""
        entry["error"] = "terrain proxy"
        entry["decoded"] = False
    result = _consider_water(
        [material.name, getattr(material, "shader", ""), *[tex for _slot, tex in sources]],
        list(material.binds),
        {
            "entry": entry,
            "wanted": "" if proxy and winner is None else (wanted if (found.get("image") is not None or wanted) else ""),
            "sampler": "" if proxy and winner is None else sampler,
            "alpha": alpha if found.get("image") is not None else ("blend" if material.alpha == "blend" else "opaque"),
            "cutoff": cutoff,
            "image": None if proxy and winner is None else found.get("image"),
            "proxy": proxy and winner is None,
            "wrap": wrap,
            "base_color": None if (proxy and winner is None) or found.get("image") is not None else flat_color,
        },
    )
    if wrap and result.get("image") is not None:
        result["alpha"] = "opaque"
        result["entry"]["alpha"] = "opaque"
    maps = {}
    for slot, tex in material.binds:
        role = bind_role(slot, tex)
        if role not in {"normal", "emission"} or not tex or role in maps:
            continue
        # _n1 is the overlay normal. It must not replace the base normal.
        if surface_role(slot) == "normal1":
            continue
        extra = _load_named(dump, model, tex, hints, "opaque", material.cutoff, external, raws)
        found = extra.get("found") or ""
        # A missing normal must not borrow an albedo through the hint search.
        if extra.get("image") is not None and (found == "embedded" or _stem(found).lower() == _stem(tex).lower()):
            maps[role] = tex
            external[tex] = extra["image"]
    result["maps"] = maps
    result["overlay"] = _overlay_choice(dump, model, material, hints, external, raws)
    result["surface"] = _surface_choice(dump, model, material, hints, external, raws)
    result["emission"] = _emission_choice(dump, model, material, hints, external, raws)
    return result


def _emission_choice(dump, model, material, hints, external, raws) -> dict:
    """``_em`` and ``_ec`` only. ``_fx`` and ``_th`` are not light."""
    found = []
    for slot, texture in material.binds:
        if bind_role(slot, texture) != "emission" or not texture:
            continue
        image = _exact_data(dump, model, texture, hints, material, external, raws)
        if image is not None:
            found.append((slot, image))
    image, note = emissive_image(found)
    if image is None:
        return {}
    return {"image": image, "note": note}


def _exact_data(dump, model, name, hints, material, external, raws):
    """A surface map only when the file is this texture, including a BC4 channel."""
    extra = _load_named(
        dump, model, name, hints, "opaque", material.cutoff, external, raws, keep_data=True
    )
    found = extra.get("found") or ""
    image = extra.get("image")
    if image is not None and (found == "embedded" or _stem(found).lower() == _stem(name).lower()):
        external[name] = image
        return image
    return None


def _surface_choice(dump, model, material, hints, external, raws) -> dict:
    """Pack roughness, metal, and AO for layer 0 and layer 1, and name the overlay normal."""
    entries = collect(material)
    if not entries:
        return {}
    layers: dict[int, list] = {}
    normal1 = ""
    for sampler, texture, role, layer in entries:
        if role == "normal1":
            if _exact_data(dump, model, texture, hints, material, external, raws) is not None:
                normal1 = texture
            continue
        image = _exact_data(dump, model, texture, hints, material, external, raws)
        if image is None:
            continue
        layers.setdefault(layer, []).append((sampler, role, image))
    orm, note0 = pack_layer(layers.get(0) or [])
    orm1, note1 = pack_layer(layers.get(1) or [])
    notes = []
    if note0:
        notes.append(f"surface {note0}")
    if note1:
        notes.append(f"surface layer 1 {note1}")
    if normal1:
        notes.append(f"surface overlay normal {normal1}")
    if orm is None and orm1 is None and not normal1:
        return {}
    return {"orm": orm, "orm1": orm1, "normal1": normal1, "note": "; ".join(notes)}


def _choose_loose(dump, model, part, hints, terrain, external, raws) -> dict:
    name = part.texture
    found = _load_named(dump, model, name, hints, part.alpha, part.cutoff, external, raws) if name else {"image": None, "error": "material has no texture names", "tried": [], "found": "", "alpha": part.alpha, "cutoff": part.cutoff}
    if found.get("image") is None and terrain and dump is not None:
        hinted = _hint_alb_path(dump, hints)
        if hinted:
            stem = _stem(hinted)
            alt = _load_named(dump, model, stem, hints, part.alpha, part.cutoff, external, raws, forced=hinted)
            if alt.get("image") is not None:
                name = stem
                found = alt
                found["error"] = f"no albedo name matched; used {hinted}"
    entry = {
        "material": part.material or part.name,
        "sampler": part.albedo_sampler,
        "samplers": [part.albedo_sampler] if part.albedo_sampler else [],
        "binds": [[part.albedo_sampler, part.texture]] if part.texture else [],
        "textures": [part.texture] if part.texture else [],
        "wanted": name if found.get("image") is not None or name else "",
        "tried": found.get("tried") or [],
        "found": found.get("found") or "",
        "decoded": found.get("image") is not None,
        "format": "",
        "size": "",
        "swizzle": "",
        "blockHeight": 0,
        "image": "",
        "images": 0,
        "renderState": "",
        "alpha": _alpha_label(found.get("alpha") or part.alpha, found.get("cutoff") or part.cutoff) if found.get("image") is not None else "opaque",
        "doubleSided": part.double_sided,
        "error": found.get("error") or "",
        "shapes": [part.name] if part.name else [],
        "textured": found.get("image") is not None,
        "flat": found.get("image") is None,
    }
    if found.get("image") is not None:
        _fill_image_log(entry, found)
    return _consider_water(
        [part.material or part.name],
        [(part.albedo_sampler, part.texture)] if part.texture else [],
        {
            "entry": entry,
            "wanted": entry["wanted"],
            "sampler": part.albedo_sampler,
            "alpha": found.get("alpha") or "opaque",
            "image": found.get("image"),
        },
    )


def _log_entry(material, sampler, wanted, found, alpha, cutoff, sided, error) -> dict:
    decoded = found.get("image") is not None
    return {
        "material": material.name,
        "sampler": sampler,
        "samplers": [slot for slot, _tex in material.binds],
        "binds": [[slot, tex] for slot, tex in material.binds],
        "textures": [tex for _slot, tex in material.binds if tex],
        "wanted": wanted,
        "tried": found.get("tried") or [],
        "found": found.get("found") or "",
        "decoded": decoded,
        "format": "",
        "size": "",
        "swizzle": "",
        "image": "",
        "images": 0,
        "alpha": _alpha_label(alpha, cutoff) if decoded or alpha == "blend" else _alpha_label(alpha, cutoff),
        "doubleSided": sided,
        "renderState": getattr(material, "render_state", "") or "",
        "blockHeight": 0,
        "error": error or "",
        "shader": getattr(material, "shader", "") or "",
        "archive": getattr(material, "archive", "") or "",
        "options": dict(getattr(material, "options", {}) or {}),
        "projection": projection_options(getattr(material, "options", {}) or {}),
        "attributes": dict(getattr(material, "attributes", {}) or {}),
        "hints": name_hints(getattr(material, "name", "") or ""),
        "params": list(getattr(material, "params", []) or []),
        "renderInfo": list(getattr(material, "render_info", []) or []),
        "states": [text for text in (getattr(material, "states", []) or []) if text],
        "roles": [
            [item["sampler"], item["texture"], item["role"], item["uv"], (item.get("srt") or {}).get("name") or ""]
            for item in sampler_lines(material)
        ],
    }


def _fill_image_log(entry: dict, found: dict) -> None:
    image = found.get("image")
    if image is None:
        return
    entry["decoded"] = True
    entry["found"] = found.get("found") or entry.get("found") or ""
    fmt = f"0x{image.fmt:04X}" if image.fmt else ""
    entry["format"] = " ".join(part for part in (image.codec, fmt) if part)
    entry["size"] = f"{image.width}x{image.height}" if image.width else ""
    entry["swizzle"] = image.swizzle
    entry["blockHeight"] = getattr(image, "block_height", 0) or 0
    entry["image"] = image.name
    entry["images"] = found.get("images") or 1
    if image.note and not entry.get("error"):
        entry["error"] = image.note
    elif image.note and image.note not in (entry.get("error") or ""):
        entry["error"] = f"{entry['error']}. {image.note}".strip(". ")


def _alpha_label(mode: str, cutoff: float) -> str:
    if mode == "mask":
        return f"mask {cutoff:.2f}"
    if mode == "blend":
        return "blend"
    return "opaque"


def _load_cache_key(dump, model, name, hints, alpha, cutoff, forced, layer, flatten_alpha, keep_data):
    if isinstance(hints, (list, tuple)):
        hint_key = tuple(str(item) for item in hints)
    else:
        hint_key = (str(hints),)
    try:
        cut = float(cutoff)
    except (TypeError, ValueError):
        cut = 0.0
    return (
        id(dump) if dump is not None else 0,
        id(model) if model is not None else 0,
        name or "",
        hint_key,
        alpha or "",
        cut,
        forced or "",
        None if layer is None else int(layer),
        bool(flatten_alpha),
        bool(keep_data),
    )


def _remember_load(key, result, blob, relative) -> None:
    mem = _LOAD_MEM.get()
    if mem is None or key is None:
        return
    copied = dict(result)
    copied["tried"] = list(result.get("tried") or [])
    mem[key] = {"result": copied, "blob": blob, "relative": relative or ""}


def _load_from_mem(stored, dump, raws) -> dict:
    blob = stored.get("blob")
    if blob is not None:
        raws.append(blob)
    relative = stored.get("relative") or ""
    if relative:
        _track_file(dump, relative)
    result = dict(stored["result"])
    result["tried"] = list(stored["result"].get("tried") or [])
    return result


def _read_tex(dump, relative: str) -> bytes:
    mem = _READ_MEM.get()
    if mem is not None and relative in mem:
        return mem[relative]
    blob, _dict_id = dump.read_file(relative)
    if mem is not None:
        mem[relative] = blob
    return blob


def _load_named(
    dump,
    model,
    name,
    hints,
    alpha,
    cutoff,
    external,
    raws,
    forced: str = "",
    layer: int | None = None,
    flatten_alpha: bool = False,
    keep_data: bool = False,
) -> dict:
    mem = _LOAD_MEM.get()
    key = None
    if mem is not None:
        key = _load_cache_key(dump, model, name, hints, alpha, cutoff, forced, layer, flatten_alpha, keep_data)
        stored = mem.get(key)
        if stored is not None:
            return _load_from_mem(stored, dump, raws)

    def finish(result, blob=None, relative=""):
        _remember_load(key, result, blob, relative)
        return result

    result = {"image": None, "error": "", "tried": [], "found": "", "alpha": alpha, "cutoff": cutoff, "images": 0}
    if not name and not forced:
        result["error"] = "material has no texture names"
        return finish(result)
    embedded = None
    if model is not None and name:
        embedded = model.images.get(name) or model.images.get(name.lower())
    if embedded is not None and layer is None:
        result["tried"] = ["embedded in the bfres"]
        result["found"] = "embedded"
        result["images"] = 1
        image, mode, used_cut, note = _accept(embedded, alpha, cutoff, keep_data=keep_data)
        result["alpha"] = mode
        result["cutoff"] = used_cut
        result["error"] = note
        result["image"] = image
        return finish(result)
    if dump is None:
        result["error"] = "no Tex file matched"
        return finish(result)
    relative = forced
    tried: list[str] = []
    if not relative:
        relative, tried = _resolve_tex(dump, name, hints)
        if not relative:
            bundled, extra = _search_bundles(dump, name, hints)
            tried.extend(path for path in extra if path not in tried)
            relative = bundled
    else:
        tried = [relative]
    result["tried"] = tried
    if not relative:
        result["error"] = "no Tex file matched"
        return finish(result)
    try:
        blob = _read_tex(dump, relative)
    except BancError as exc:
        result["found"] = relative
        result["error"] = exc.message
        return finish(result)
    raws.append(blob)
    _track_file(dump, relative)
    if blob[:4] != b"BNTX":
        result["found"] = relative
        result["error"] = f"{relative} is not a BNTX"
        return finish(result, blob, relative)
    result["found"] = relative
    if layer is not None:
        try:
            picked = _decode_layer_cached(blob, layer, flatten_alpha=flatten_alpha, tex_path=relative)
        except BntxError as exc:
            result["error"] = str(exc)
            return finish(result, blob, relative)
        result["images"] = 1
        image, mode, used_cut, note = _accept(picked, "opaque" if flatten_alpha else alpha, cutoff, keep_data=keep_data)
        if flatten_alpha:
            mode = "opaque"
        result["alpha"] = mode
        result["cutoff"] = used_cut
        result["image"] = image
        result["error"] = note or ""
        return finish(result, blob, relative)
    try:
        decoded = _decode_bntx_cached(blob, relative)
    except BntxError as exc:
        result["error"] = str(exc)
        return finish(result, blob, relative)
    result["images"] = len(decoded)
    picked, why = pick_image(decoded, name)
    if picked is None:
        failed = next((image for image in decoded if image.note), None)
        result["error"] = why or (failed.note if failed else "BNTX has no usable image")
        if failed is not None and failed.fmt and "format" not in result["error"]:
            result["error"] = f"{result['error']} (0x{failed.fmt:04X})"
        return finish(result, blob, relative)
    image, mode, used_cut, note = _accept(picked, alpha, cutoff, keep_data=keep_data)
    result["alpha"] = mode
    result["cutoff"] = used_cut
    result["image"] = image
    if image is None:
        result["error"] = note or why or "texture was not usable"
    elif note:
        result["error"] = note
    elif why:
        result["error"] = why
    return finish(result, blob, relative)


def pick_image(decoded, wanted: str):
    """The image to bind, or (None, reason). A multi-image file does not fall back to image 0."""
    usable = [image for image in decoded if image.rgba]
    if not usable:
        failed = next((image for image in decoded if image.note), None)
        return None, (failed.note if failed else "BNTX has no images")
    wanted_l = (wanted or "").lower()
    stem = _stem(wanted).lower()
    for image in usable:
        if image.name == wanted or (image.name or "").lower() == wanted_l:
            return image, ""
    for image in usable:
        current = (image.name or "").lower()
        if stem and (current == f"{stem}_alb" or current == f"{stem}_albedo" or (stem in current and "alb" in current)):
            note = "" if current == wanted_l else f"used image {image.name}"
            return image, note
    if len(decoded) == 1 and usable:
        only = usable[0]
        if not only.name or (only.name or "").lower() in {wanted_l, stem, f"{stem}_alb"}:
            return only, ""
        return only, f"used image {only.name}"
    names = ", ".join(image.name or "?" for image in decoded[:6])
    for image in decoded:
        if image.rgba or not image.note:
            continue
        if (image.name or "").lower() in {wanted_l, stem, f"{stem}_alb"}:
            return None, image.note
    return None, f"no image named {wanted} in this bntx ({names})"


def _accept(image, alpha: str, cutoff: float, keep_data: bool = False):
    if image is None or not image.rgba:
        return None, alpha, cutoff, (image.note if image is not None else "") or "texture did not decode"
    if not keep_data and (getattr(image, "codec", "") or "").lower().startswith("bc4"):
        return None, alpha, cutoff, "BC4 is a single channel and is not a colour"
    fraction = _magenta_fraction(image.rgba)
    if fraction >= 0.85:
        return None, alpha, cutoff, "decoded image is a magenta placeholder"
    note = image.note or ""
    rgba = image.rgba
    mode = alpha
    if mode == "opaque" and _has_near_magenta(rgba):
        rgba = _inpaint_magenta(rgba, image.width, image.height)
        extra = "magenta texels were replaced with a neighbour"
        note = f"{note}. {extra}".strip(". ")
    elif fraction >= 0.02:
        rgba = _punch_magenta(rgba)
        if mode == "opaque":
            mode = "mask"
            cutoff = 0.5 if cutoff == 0.5 or alpha == "opaque" else cutoff
        extra = "magenta texels were punched out and drawn as cutout"
        note = f"{note}. {extra}".strip(". ")
    if mode == "mask":
        _low, high = _alpha_span(rgba)
        if high < 8:
            rgba = _luma_alpha(rgba)
            if cutoff >= 0.5:
                cutoff = 0.08
            note = f"{note}. alpha was empty, so the cutout uses the colour".strip(". ")
    if rgba is not image.rgba or (note and note != image.note):
        image = replace(image, rgba=rgba, note=note)
    return image, mode, cutoff, note


def _strict_magenta(red: int, green: int, blue: int, alpha: int) -> bool:
    return alpha >= 200 and red >= 235 and green <= 25 and blue >= 235


def _near_magenta(red: int, green: int, blue: int, alpha: int) -> bool:
    """A missing-texture dash that is not a clean 255,0,255 placeholder."""
    return alpha >= 200 and red >= 190 and blue >= 190 and green <= 80 and abs(red - blue) <= 48


def _has_near_magenta(rgba: bytes) -> bool:
    from ocarina_forge.extension import module

    native = module()
    if native is not None:
        try:
            return bool(native.has_near_magenta(rgba))
        except Exception:
            pass
    return _has_near_magenta_py(rgba)


def _has_near_magenta_py(rgba: bytes) -> bool:
    for at in range(0, len(rgba) - 3, 4):
        if _near_magenta(rgba[at], rgba[at + 1], rgba[at + 2], rgba[at + 3]):
            return True
    return False


def _magenta_fraction(rgba: bytes) -> float:
    total = len(rgba) // 4
    if total <= 0:
        return 0.0
    step = max(1, total // 2048)
    seen = 0
    hit = 0
    for index in range(0, total, step):
        at = index * 4
        red, green, blue, alpha = rgba[at : at + 4]
        seen += 1
        if _strict_magenta(red, green, blue, alpha):
            hit += 1
    return hit / seen if seen else 0.0


def _punch_magenta(rgba: bytes) -> bytes:
    out = bytearray(rgba)
    for at in range(0, len(out) - 3, 4):
        if _strict_magenta(out[at], out[at + 1], out[at + 2], out[at + 3]):
            out[at + 3] = 0
    return bytes(out)


def _inpaint_magenta(rgba: bytes, width: int, height: int) -> bytes:
    """Replace magenta texels with a neighbour so the albedo stays opaque.

    Four sweeps left an ASTC-sized gutter (8 px) on the texture edge, and the
    trunk seam samples that edge. Sixteen sweeps cover that gutter. The search
    wraps, because the left and right edges are the same cut on the model.
    """
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return rgba
    current = bytearray(rgba)
    neighbours = ((-1, 0), (1, 0), (0, -1), (0, 1))
    for _sweep in range(16):
        nxt = bytearray(current)
        changed = False
        for y in range(height):
            for x in range(width):
                at = (y * width + x) * 4
                if not _near_magenta(current[at], current[at + 1], current[at + 2], current[at + 3]):
                    continue
                for dx, dy in neighbours:
                    xx = (x + dx) % width
                    yy = (y + dy) % height
                    src = (yy * width + xx) * 4
                    if _near_magenta(current[src], current[src + 1], current[src + 2], current[src + 3]):
                        continue
                    nxt[at : at + 3] = current[src : src + 3]
                    changed = True
                    break
        current = nxt
        if not changed:
            break
    return bytes(current)


def _alpha_span(rgba: bytes) -> tuple[int, int]:
    total = len(rgba) // 4
    if total <= 0:
        return 0, 0
    step = max(1, total // 2048)
    low = 255
    high = 0
    for index in range(0, total, step):
        alpha = rgba[index * 4 + 3]
        if alpha < low:
            low = alpha
        if alpha > high:
            high = alpha
    return low, high


def _pool_albedo(pool: list[str], index: dict[str, str]) -> str:
    hits = []
    for name in pool:
        if texture_rank(name) == 2:
            continue
        if not index:
            if texture_rank(name) == 0:
                hits.append(name)
            continue
        stem = _stem(name).lower()
        if stem in index or f"{stem}_alb" in index or f"{stem}_albedo" in index:
            hits.append(name)
    if not hits:
        return ""
    return base_color_name(hits)


def _search_bundles(dump, wanted: str, hints: list[str]) -> tuple[str, list[str]]:
    index = _tex_index(dump)
    stem = _stem(wanted).lower()
    if len(stem) < 3 or not index:
        return "", []
    hits = []
    for key, path in index.items():
        if stem in key or (len(key) >= 4 and key in stem):
            hits.append(path)
    for hint in hints:
        token = hint.lower()
        if len(token) < 8:
            continue
        for key, path in index.items():
            if token in key and path not in hits:
                hits.append(path)
    hits = list(dict.fromkeys(hits))
    hits.sort(key=lambda item: (0 if "alb" in item.lower() else 1, len(item)))
    tried = []
    for path in hits[:6]:
        tried.append(path)
        if _bundle_match(_bntx_names(dump, path), wanted, path):
            return path, tried
    return "", tried


def _bundle_match(names: list[str], wanted: str, path: str) -> bool:
    low = (wanted or "").lower()
    stem = _stem(wanted).lower()
    file_stem = _stem(path).lower()
    named = [name for name in names if name]
    for name in named:
        current = name.lower()
        if current == low or current == f"{stem}_alb" or current == f"{stem}_albedo":
            return True
        if stem and stem in current and "alb" in current:
            return True
    if len(named) == 1 and stem and (stem in file_stem or file_stem in stem):
        return True
    return False


_BNTX_NAMES: dict[str, tuple[int, list[str]]] = {}


def _bntx_names(dump, relative: str) -> list[str]:
    path = dump.romfs / relative
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return []
    key = str(path)
    cached = _BNTX_NAMES.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        blob, _dict_id = dump.read_file(relative)
    except BancError:
        return []
    names = image_names(blob) if blob[:4] == b"BNTX" else []
    _BNTX_NAMES[key] = (stamp, names)
    return names


def _hint_alb_path(dump, hints: list[str]) -> str:
    index = _tex_index(dump)
    for hint in hints:
        token = hint.lower()
        if len(token) < 6:
            continue
        for suffix in ("_alb", "_albedo", ""):
            path = index.get(token + suffix)
            if path and (suffix or "alb" in token):
                return path
        hits = [path for key, path in index.items() if token in key and "alb" in key]
        if hits:
            hits.sort(key=len)
            return hits[0]
    return ""


def _hint_tokens(hint: str) -> list[str]:
    tokens: list[str] = []
    for piece in hint.replace("\\", "/").split():
        name = piece.rsplit("/", 1)[-1]
        for suffix in (".bfres.zs", ".bfres.mc", ".bfres", ".pack.zs", ".bcett.byml.zs"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        for token in name.split("."):
            if token and token not in tokens and token not in ("Model", "Tex"):
                tokens.append(token)
    return tokens


def _stem(name: str) -> str:
    text = name.strip().replace("\\", "/").lstrip("/")
    if text.startswith("Work/"):
        text = text[5:]
    stem = text.rsplit("/", 1)[-1]
    for suffix in (".bntx.zs", ".bntx"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def _tex_index(dump) -> dict[str, str]:
    folder = dump.romfs / "Tex"
    if not folder.is_dir():
        return {}
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return {}
    key = str(folder)
    cached = _TEX_INDEX.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    index: dict[str, str] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return {}
    for path in entries:
        if not path.is_file():
            continue
        index.setdefault(_stem(path.name).lower(), f"Tex/{path.name}")
    _TEX_INDEX[key] = (stamp, index)
    return index


def _resolve_tex(dump, name: str, hints: list[str]) -> tuple[str, list[str]]:
    tried: list[str] = []
    for relative in _tex_candidates(name):
        tried.append(relative)
        if (dump.romfs / relative).is_file():
            return relative, tried
    index = _tex_index(dump)
    stem = _stem(name).lower()
    keys = [stem]
    if stem.endswith("_alb"):
        keys.append(stem[: -len("_alb")])
    elif stem.endswith("_albedo"):
        keys.append(stem[: -len("_albedo")])
    else:
        keys.append(f"{stem}_alb")
    for key in keys:
        path = index.get(key)
        if path and path not in tried:
            tried.append(path)
            if (dump.romfs / path).is_file():
                return path, tried
    for hint in hints:
        key = hint.lower()
        if len(key) < 4:
            continue
        path = index.get(key)
        if path and path not in tried:
            tried.append(path)
            if (dump.romfs / path).is_file():
                return path, tried
        hits = [item for stem_key, item in index.items() if key in stem_key and "alb" in stem_key]
        hits = list(dict.fromkeys(hits))
        hits.sort(key=lambda item: (0 if item.lower().endswith(".bntx.zs") else 1, len(item)))
        for path in hits[:4]:
            if path not in tried:
                tried.append(path)
        if hits and (dump.romfs / hits[0]).is_file():
            return hits[0], tried
    return "", tried


def _tex_candidates(name: str) -> list[str]:
    text = name.strip().replace("\\", "/").lstrip("/")
    if text.startswith("Work/"):
        text = text[5:]
    paths: list[str] = []

    def add(path: str) -> None:
        if path and path not in paths and ".." not in path.split("/"):
            paths.append(path)

    if "/" in text:
        if text.endswith(".bntx.zs"):
            add(text)
            add(text[:-3])
        elif text.endswith(".bntx"):
            add(text + ".zs")
            add(text)
        else:
            add(f"{text}.bntx.zs")
            add(f"{text}.bntx")
    stem = text.rsplit("/", 1)[-1]
    for suffix in (".bntx.zs", ".bntx"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    add(f"Tex/{stem}.bntx.zs")
    add(f"Tex/{stem}.bntx")
    return paths


def _actor_plan(dump, gyaml: str) -> tuple[list, list[str], bool]:
    plan: list = []
    seen: set[str] = set()

    def add_file(relative: str) -> None:
        if not relative or relative in seen or ".." in relative.split("/"):
            return
        seen.add(relative)
        plan.append(("file", relative))

    pairs: list[tuple[str, str]] = []
    embedded: list[tuple[str, bytes]] = []
    named: list[str] = []
    pack_rel = f"Pack/Actor/{gyaml}.pack.zs"
    if (dump.romfs / pack_rel).is_file():
        payload, _dict_id = dump.read_file(pack_rel)
        try:
            archive = Sarc(payload)
        except ValueError as exc:
            raise BancError(f"{pack_rel} is not a SARC pack: {exc}", "bad_pack") from exc
        pairs = _modelinfo_pairs(archive)
        pairs.sort(key=lambda pair: (pair[1] != gyaml and pair[0] != gyaml, pair[0], pair[1]))
        for name in archive.names():
            blob = archive.read(name)
            if name.endswith(".bfres") or blob[:8] == b"FRES    ":
                embedded.append((f"{pack_rel}:{name}", blob))
            elif "ModelInfo" not in name:
                named.extend(_names_in(blob))
        embedded.sort(key=lambda item: (gyaml not in item[0], item[0]))

    listing = _model_listing(dump)
    for project, fmdb in pairs:
        exact = [f"Model/{project}.{fmdb}.bfres.zs", f"Model/{project}.{fmdb}.bfres"]
        for relative in exact:
            add_file(relative)
        # A present exact file that cannot be decoded must not be replaced by
        # another fmdb in the same project (the inside of a house drawn at
        # the outside actor).
        if any((dump.romfs / relative).is_file() for relative in exact):
            continue
        for extra in _project_files(listing, project):
            add_file(extra)
    add_file(f"Model/{gyaml}.{gyaml}.bfres.zs")
    add_file(f"Model/{gyaml}.{gyaml}.bfres")
    for text in named:
        for relative in _path_variants(text):
            add_file(relative)
    add_file(f"Model/{gyaml}.bfres.zs")
    add_file(f"Model/{gyaml}.bfres")
    for label, blob in embedded:
        plan.append(("blob", label, blob))
    tried = [item[1] for item in plan if item[0] == "file"]
    referenced = bool(pairs or embedded or named)
    conventional = (
        f"Model/{gyaml}.{gyaml}.bfres.zs",
        f"Model/{gyaml}.{gyaml}.bfres",
        f"Model/{gyaml}.bfres.zs",
        f"Model/{gyaml}.bfres",
    )
    if any((dump.romfs / relative).is_file() for relative in conventional):
        referenced = True
    return plan, tried, referenced


def _modelinfo_pairs(archive: Sarc) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name in archive.names():
        if "ModelInfo" not in name and not name.endswith("engine__component__ModelInfo.bgyml"):
            continue
        blob = archive.read(name)
        if blob[:2] != b"YB":
            continue
        try:
            document = loads(blob)
        except ValueError:
            continue
        pairs.extend(_pairs_in(document))
    unique: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in unique:
            unique.append(pair)
    return unique


def _pairs_in(document) -> list[tuple[str, str]]:
    if not isinstance(document, dict):
        return []
    found: list[tuple[str, str]] = []
    root = _one_pair(document)
    if root:
        found.append(root)
    subs = document.get("SubModels")
    if isinstance(subs, list):
        for item in subs:
            if isinstance(item, dict):
                pair = _one_pair(item)
                if pair and pair not in found:
                    found.append(pair)
    return found


def _one_pair(node: dict) -> tuple[str, str] | None:
    project = node.get("ModelProjectName") or node.get("ModelName") or ""
    fmdb = node.get("FmdbName") or ""
    if not isinstance(project, str):
        project = ""
    if not isinstance(fmdb, str):
        fmdb = ""
    project = project.strip()
    fmdb = fmdb.strip()
    if not project and not fmdb:
        return None
    if not project:
        project = fmdb
    if not fmdb:
        fmdb = project
    if not _NAME.fullmatch(project) or not _NAME.fullmatch(fmdb):
        return None
    return project, fmdb


def _project_files(listing: list[str], project: str) -> list[str]:
    prefix = project + "."
    found = []
    for name in listing:
        if not name.startswith(prefix):
            continue
        if ".anim." in name or name.endswith(".bfres.mc") or name.endswith(".bfres.mc.zs"):
            continue
        if name.endswith(".bfres.zs") or name.endswith(".bfres"):
            found.append(f"Model/{name}")
    found.sort()
    return found


def _model_listing(dump) -> list[str]:
    folder = dump.romfs / "Model"
    if not folder.is_dir():
        return []
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = str(folder)
    cached = _LISTING.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    names = []
    try:
        for entry in folder.iterdir():
            if entry.is_file():
                names.append(entry.name)
    except OSError:
        names = []
    _LISTING[key] = (stamp, names)
    return names


def _exists(dump, relative: str) -> bool:
    return bool(relative) and (dump.romfs / relative).is_file()


def _param_candidates(stem: str) -> list[str]:
    base = f"Banc/GameBancParam/{stem}.game__banc__GameBancParam"
    return [base + ".bgyml", base + ".bgyml.zs", base + ".byml", base + ".byml.zs"]


def _tscb_candidates(scene: str) -> list[str]:
    text = (scene or "").strip().replace("\\", "/").lstrip("/")
    if text.startswith("Work/"):
        text = text[5:]
    if text.endswith(".tscb.zs"):
        return [text]
    if text.endswith(".tscb"):
        return [text, text + ".zs"]
    return [
        f"TerrainArc/{text}.tscb",
        f"TerrainArc/{text}.tscb.zs",
        f"Terrain/{text}.tscb",
        f"Terrain/{text}.tscb.zs",
    ]


def _tscb_stem(relative: str) -> str:
    name = relative.rsplit("/", 1)[-1]
    for suffix in (".tscb.zs", ".tscb"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _tile_stem(name: str) -> str:
    text = (name or "").strip().replace("\\", "/").lstrip("/")
    for suffix in (".hght.zs", ".hght", ".mate.zs", ".mate", ".extm.zs", ".extm"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _files_in_dirs(dump, directories: list[str], name: str, extensions: tuple[str, ...]) -> list[tuple[str, str]]:
    """Existing TerrainArc files in directory order, and the directory each came from."""
    text = _tile_stem(name)
    found = []
    seen: set[str] = set()
    folders = [item for item in directories if item] + [""]
    for directory in folders:
        for extension in extensions:
            relative = f"TerrainArc/{directory}/{text}{extension}" if directory else f"TerrainArc/{text}{extension}"
            if relative in seen or not _exists(dump, relative):
                continue
            seen.add(relative)
            found.append((relative, directory))
    return found


def _file_in_dirs(dump, directories: list[str], name: str, extensions: tuple[str, ...]) -> tuple[str, str]:
    """The first existing TerrainArc file, and the directory it came from."""
    found = _files_in_dirs(dump, directories, name, extensions)
    return found[0] if found else ("", "")


def _sub_scene_names(scene: str, document, tscb: bytes) -> list[str]:
    found: list[str] = []

    def add(value: str) -> None:
        text = (value or "").strip().replace("\\", "/").lstrip("/")
        if text.startswith("Work/"):
            text = text[5:]
        for suffix in (".tscb.zs", ".tscb"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
        text = text.rsplit("/", 1)[-1]
        if text and text != scene and text not in found:
            found.append(text)

    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(value, str) and ("sub" in str(key).lower() or value.strip().endswith("_sub")):
                add(value)
    if scene:
        add(scene + "_sub")
    for name in heightmap.embedded_sub_names(tscb):
        add(name)
    return found


def _scene_present(dump, name: str) -> bool:
    if any(_exists(dump, relative) for relative in _tscb_candidates(name)):
        return True
    return (dump.romfs / "TerrainArc" / name).is_dir()


def _lookup_directories(dump, scene: str, document, tscb: bytes = b"") -> list[str]:
    """Every terrain folder for this scene. Each one is meshed on its own."""
    extra = [name for name in _sub_scene_names(scene, document, tscb) if _scene_present(dump, name)]
    ordered = extra + ([scene] if scene else [])
    found: list[str] = []
    for name in ordered:
        if name and name not in found:
            found.append(name)
    return found


def _terrain_dir_token(dump, directories: list[str]) -> list[dict]:
    tokens = []
    for name in sorted({item for item in directories if item}):
        folder = dump.romfs / "TerrainArc" / name
        stamp = 0
        if folder.is_dir():
            try:
                stamp = folder.stat().st_mtime_ns
            except OSError:
                stamp = 0
        tokens.append({"path": f"TerrainArc/{name}", "mtime_ns": stamp})
    return tokens


def _age_variant(banc: str) -> str:
    text = (banc or "").lower()
    if "child" in text:
        return "child"
    if "adult" in text:
        return "adult"
    return ""


def _merge_byml(parent, child):
    if not isinstance(parent, dict):
        return child if isinstance(child, dict) else {}
    if not isinstance(child, dict):
        return dict(parent)
    merged = dict(parent)
    for key, value in child.items():
        if key == "$parent":
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_byml(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parent_paths(text: str) -> list[str]:
    path = text.strip().replace("\\", "/")
    if path.startswith("?"):
        path = path[1:]
    if path.startswith("Work/"):
        path = path[5:]
    path = path.lstrip("/")
    if path.endswith(".gyml"):
        path = path[: -len(".gyml")] + ".bgyml"
    found = [path]
    if not path.endswith(".zs"):
        found.append(path + ".zs")
    return found


def _read_param_document(dump, relative: str, seen: set[str] | None = None, depth: int = 0):
    seen = seen or set()
    if depth > 8 or relative in seen or not _exists(dump, relative):
        return None, []
    seen.add(relative)
    document, _dict_id, _raw = dump.read_byml(relative)
    notes = []
    parent = document.get("$parent") if isinstance(document, dict) else None
    if isinstance(parent, str) and parent.strip():
        loaded = None
        tried = _parent_paths(parent)
        for path in tried:
            if path in seen or not _exists(dump, path):
                continue
            loaded, extra = _read_param_document(dump, path, seen, depth + 1)
            notes.extend(extra)
            if loaded is not None:
                document = _merge_byml(loaded, document)
                break
        if loaded is None:
            notes.append(f"$parent {parent} was not in the dump")
    return document, notes


def _scene_origin(document) -> tuple[float, float, float]:
    if not isinstance(document, dict):
        return (0.0, 0.0, 0.0)
    value = document.get("TerrainScenePosition")
    if isinstance(value, dict):
        parts = (value.get("X"), value.get("Y"), value.get("Z"))
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        parts = value[:3]
    else:
        return (0.0, 0.0, 0.0)
    origin = []
    for item in parts:
        try:
            origin.append(float(item))
        except (TypeError, ValueError):
            origin.append(0.0)
    return (origin[0], origin[1], origin[2])


def _find_named_file(dump, folder: str, stem: str, marker: str) -> str:
    directory = dump.romfs / folder
    if not directory.is_dir() or not stem:
        return ""
    try:
        names = sorted(entry.name for entry in directory.iterdir() if entry.is_file())
    except OSError:
        return ""
    for name in names:
        if name.startswith(stem + ".") and marker in name:
            return f"{folder}/{name}"
    return ""


def _locate_heightmap(dump, banc: str) -> dict:
    """The TerrainArc this banc names, or a reason it was named and is missing."""
    notes: list[str] = []
    param = ""
    document = None
    for stem in heightmap.level_stems(banc):
        for relative in _param_candidates(stem):
            if _exists(dump, relative):
                param = relative
                break
        if param:
            break
        named = _find_named_file(dump, "Banc/GameBancParam", stem, "GameBancParam")
        if named:
            param = named
            break
    scene = ""
    origin = (0.0, 0.0, 0.0)
    if param:
        try:
            document, notes = _read_param_document(dump, param)
        except BancError as exc:
            return {
                "missing": f"{param} could not be read: {exc.message}",
                "param": param,
                "terrain": {"param": param},
            }
        if isinstance(document, dict):
            named = document.get("TerrainSceneName")
            if isinstance(named, str):
                scene = named.strip()
            origin = _scene_origin(document)
    scenes: list[str] = []
    for text in (scene, *heightmap.level_stems(banc)):
        if text and text not in scenes:
            scenes.append(text)
    tried: list[str] = []
    tscb = ""
    for item in scenes:
        for relative in _tscb_candidates(item):
            tried.append(relative)
            if _exists(dump, relative):
                tscb = relative
                break
        if tscb:
            break
        folder = "TerrainArc" if not item.startswith("Terrain") else item.split("/", 1)[0]
        named = _find_named_file(dump, folder, item.rsplit("/", 1)[-1], ".tscb")
        if named:
            tscb = named
            break
    if tscb:
        return {
            "tscb": tscb,
            "scene": scene or _tscb_stem(tscb),
            "origin": origin,
            "param": param,
            "notes": notes,
            "document": document,
        }
    if scene or param:
        shown = ", ".join(tried[:6]) or "(none)"
        named = scene or "(no TerrainSceneName)"
        return {
            "missing": (
                f"{param or 'GameBancParam'} names terrain scene {named}, "
                f"but no TSCB was found. Tried {shown}."
            ),
            "param": param,
            "terrain": {"scene": scene, "origin": list(origin), "param": param, "notes": notes},
        }
    return {}


def _tile_row(info: heightmap.TileInfo, path: str = "", layer: str = "main") -> dict:
    return {
        "name": info.name,
        "status": info.status,
        "reason": info.reason,
        "triangles": info.triangles,
        "layers": list(info.layers),
        "layer": layer,
        "path": path or info.path,
        "center": [info.center[0], info.center[1]],
        "edge": info.edge,
        "heightMin": info.height_min,
        "heightMax": info.height_max,
        "directory": info.directory,
        "mate": info.mate,
        "mateNote": info.mate_note,
        "mateHistogram": info.mate_histogram,
        "ao": info.ao,
        "step": info.step,
        "holes": info.holes,
        "dropped": info.dropped,
        "zeros": info.zeros,
    }


def _choose_tiles(tiles, scale, origin, clip):
    centroid_x = origin[0] if clip is None else (clip[0] + clip[2]) / 2
    centroid_z = origin[2] if clip is None else (clip[1] + clip[3]) / 2
    ranked = []
    for tile in tiles:
        center_x, center_z, edge, name = tile
        box = heightmap._world_box(center_x, center_z, edge, scale, origin)
        distance = (box[0] + box[2]) / 2 - centroid_x
        other = (box[1] + box[3]) / 2 - centroid_z
        ranked.append((distance * distance + other * other, tile, box))
    inside = [item for item in ranked if heightmap._hits(item[2], clip)]
    notes = []
    pool = inside
    if clip is not None and not inside and ranked:
        pool = sorted(ranked, key=lambda item: item[0])[:24]
        notes.append("no tile overlapped the actors, so the 24 nearest tiles were used")
    chosen_ids = {id(item[1]) for item in pool}
    chosen = []
    skipped = []
    for _distance, tile, _box in ranked:
        if id(tile) in chosen_ids:
            chosen.append(tile)
            continue
        center_x, center_z, edge, name = tile
        info = heightmap.TileInfo(name=name, center=(center_x, center_z), edge=edge)
        info.reason = "outside the placed actors" if clip is not None else "farther than the tiles that were meshed"
        skipped.append(info)
    return chosen, skipped, notes


def _sparse_heightmap(rows: list[dict]) -> bool:
    """A quadtree that names hundreds of tiles and only ships a few height files.

    Hyrule Castle's index has 597 tiles and 14 `.hght` files. Those files are
    nested LODs of the same origin, and they disagree by metres, so the sheet
    is not the castle's walkable ground. A field that actually has height
    files (Kokiri) stays on: this needs at least 40 missing tiles and the
    readable share under a fifth.
    """
    missing = sum(1 for row in rows if row.get("reason") == "no heightmap file")
    drawn = sum(1 for row in rows if row.get("status") == "drawn")
    if missing < 40 or drawn <= 0:
        return False
    return drawn / (drawn + missing) < 0.2


def _height_source(banc: str, tscb: str, extras: str = "") -> str:
    blob = _cache_prefix() + b"height\0" + banc.encode("utf-8", "replace") + b"\0" + tscb.encode("utf-8", "replace")
    blob += b"\0" + extras.encode("utf-8", "replace")
    return hashlib.sha256(blob).hexdigest()


def _height_response(banc: str, digest: str, info: dict) -> dict:
    return {
        "status": "mesh",
        "reason": info.get("reason") or "terrain heightmap",
        "source": info.get("source") or "",
        "triangles": info.get("triangles") or 0,
        "textures": info.get("textures") or 0,
        "bounds": info.get("bounds"),
        "url": f"/api/terrain/model.glb?banc={banc}",
        "digest": digest,
        "textureLog": info.get("textureLog") or [],
        "timing": info.get("timing") or {},
        "banc": banc,
        "tiles": info.get("tiles") or [],
        "terrain": info.get("terrain") or {},
        "sparse": bool(info.get("sparse")),
    }


def _fit_extra_tiles(tiles, scale: float, height_scale: float, sub_scale: float, sub_height: float):
    factor = (sub_scale / scale) if scale else 1.0
    multiplier = (sub_height / height_scale) if height_scale else 1.0
    fitted = []
    for center_x, center_z, edge, name in tiles:
        fitted.append((center_x * factor, center_z * factor, edge * factor, name, multiplier))
    return fitted, abs(factor - 1.0) > 1e-3 or abs(multiplier - 1.0) > 1e-3


def _read_height_tiles(dump, chosen, directories, multipliers, layer: str, variant: str):
    """Load one scene's tiles. `.hght` and `.mate` come from that scene's folder only."""
    rows = []
    loaded = []
    mate_extensions = (".mate", ".mate.zs")
    if variant:
        mate_extensions = (f".{variant}.mate", f".{variant}.mate.zs") + mate_extensions
    for center_x, center_z, edge, name in chosen:
        info = heightmap.TileInfo(name=name, center=(center_x, center_z), edge=edge)
        path, directory = _file_in_dirs(dump, directories, name, (".hght", ".hght.zs"))
        info.path = path
        info.directory = directory
        if not path:
            info.reason = "no heightmap file"
            rows.append(_tile_row(info, "", layer))
            continue
        try:
            blob, _dict_id = dump.read_file(path)
            payload = heightmap.unpack_hght(blob, dump.dicts())
            heights, grid, note = heightmap.read_height_file(payload)
        except (BancError, heightmap.TerrainError, struct.error, ValueError, OSError) as exc:
            message = exc.message if isinstance(exc, BancError) else str(exc)
            info.reason = message
            rows.append(_tile_row(info, path, layer))
            continue
        multiplier = multipliers.get(name, 1.0)
        if multiplier != 1.0:
            heights = [value * multiplier for value in heights]
        mate_dirs = [directory] if directory else list(directories)
        mate_errors = []
        pairs = None
        for mate_path, _mate_dir in _files_in_dirs(dump, mate_dirs, name, mate_extensions):
            _track_file(dump, mate_path)
            try:
                mate_blob, _dict_id = dump.read_file(mate_path)
                mate_layers, mate_pairs, mate_note = heightmap.read_terrain_material(mate_blob, dump.dicts())
            except (BancError, heightmap.TerrainError, struct.error, ValueError, OSError) as exc:
                message = exc.message if isinstance(exc, BancError) else str(exc)
                mate_layers, mate_pairs, mate_note = None, None, message
            if mate_layers is not None:
                grid = mate_layers
                pairs = mate_pairs
                note = mate_note
                info.mate = mate_path
                info.mate_histogram, info.mate_note = heightmap.split_histogram(mate_note)
                if not info.mate_note:
                    info.mate_note = mate_note
                mate_errors = []
                break
            mate_errors.append(f"{mate_path}: {mate_note}")
        if mate_errors and not info.mate:
            info.mate = mate_errors[0].split(":", 1)[0]
            info.mate_note = " ".join(mate_errors)
            note = f"{note}. {info.mate_note}"
        occlusion = None
        ao_ext = []
        if variant:
            ao_ext.extend((f".{variant}.bake_ao.extm", f".{variant}.bake_ao.extm.zs"))
        ao_ext.extend((".bake_ao.extm", ".bake_ao.extm.zs"))
        ao_errors = []
        for ao_path, _ao_dir in _files_in_dirs(dump, mate_dirs, name, tuple(ao_ext)):
            _track_file(dump, ao_path)
            try:
                ao_blob, _dict_id = dump.read_file(ao_path)
                found_ao, ao_note = heightmap.read_occlusion(ao_blob, dump.dicts())
            except (BancError, heightmap.TerrainError, struct.error, ValueError, OSError) as exc:
                message = exc.message if isinstance(exc, BancError) else str(exc)
                found_ao, ao_note = None, message
            if found_ao is not None:
                occlusion = found_ao
                info.ao = f"{ao_path}: {ao_note}"
                break
            ao_errors.append(f"{ao_path}: {ao_note}")
        if occlusion is None and ao_errors:
            info.ao = " ".join(ao_errors)
        custom_files = _files_in_dirs(dump, mate_dirs, name, (".custom.extm", ".custom.extm.zs"))
        if custom_files:
            info.ao = (info.ao + " " if info.ao else "") + f"{custom_files[0][0]} is present and was not used"
        _track_file(dump, path)
        info.reason = note
        loaded.append((center_x, center_z, edge, name, heights, grid, occlusion, pairs, info, 1.0 if layer == "sub" else 0.0))
    return loaded, rows


def _stamp_layer(loaded, reports, rows, layer: str) -> None:
    by_name = {item[3]: item[8] for item in loaded}
    for report in reports:
        prior = by_name.get(report.name)
        path = prior.path if prior else ""
        if prior:
            report.path = prior.path
            report.directory = prior.directory
            report.mate = prior.mate
            report.mate_note = prior.mate_note
            report.mate_histogram = prior.mate_histogram
            report.ao = prior.ao
            if prior.reason:
                report.reason = prior.reason if not report.reason else f"{report.reason}. {prior.reason}"
        rows.append(_tile_row(report, path, layer))


def _describe_heightmap(dump, banc: str, located: dict, *, convert: bool) -> dict:
    tscb = located["tscb"]
    scene = located.get("scene") or _tscb_stem(tscb)
    try:
        raw, _dict_id = dump.read_file(tscb)
        scale, height_scale, tiles = heightmap.read_tscb(raw)
    except (BancError, heightmap.TerrainError, struct.error, ValueError) as exc:
        message = exc.message if isinstance(exc, BancError) else str(exc)
        return {
            "status": "none",
            "reason": f"{tscb} was not a usable terrain index: {message}",
            "source": tscb,
            "url": None,
            "banc": banc,
            "tiles": [],
            "terrain": {"param": located.get("param") or "", "scene": scene},
        }
    directories = _lookup_directories(dump, scene, located.get("document"), raw)
    dir_token = _terrain_dir_token(dump, directories)
    source = _height_source(banc, tscb, " ".join(item["path"] for item in dir_token))
    pointer = cache_dir() / "src" / source[:2] / f"{source}.json"
    cached = _read_pointer(pointer, dump)
    if cached is not None and cached.get("kind") == "mesh" and cached.get("terrainDirs") == dir_token:
        return _height_response(banc, cached["digest"], cached["info"])
    if not convert:
        raise CacheMiss()
    sub_paths = []
    # (layer, folder, tiles with a height multiplier). Sub is an overlay.
    # It is fitted into the main scene's scale and meshed on its own.
    scene_jobs = [("main", scene, [(item[0], item[1], item[2], item[3], 1.0) for item in tiles])]
    choice_notes = []
    for name in directories:
        if name == scene:
            continue
        path = next((item for item in _tscb_candidates(name) if _exists(dump, item)), "")
        if not path:
            choice_notes.append(f"TerrainArc/{name} is an extra heightmap folder with no TSCB")
            continue
        try:
            sub_raw, _dict_id = dump.read_file(path)
            sub_scale, sub_height, sub_tiles = heightmap.read_tscb(sub_raw)
        except (BancError, heightmap.TerrainError, struct.error, ValueError) as exc:
            message = exc.message if isinstance(exc, BancError) else str(exc)
            choice_notes.append(f"{path} was not a usable extra index: {message}")
            continue
        fitted, differs = _fit_extra_tiles(sub_tiles, scale, height_scale, sub_scale, sub_height)
        if differs:
            choice_notes.append(f"{path} used a different scale and was fitted onto the main scene")
        choice_notes.append(f"overlay {path} ({len(sub_tiles)} tiles); it does not replace main ground")
        sub_paths.append(path)
        scene_jobs.append(("sub", name, fitted))
    clip = None
    try:
        document, _dict_id, _payload = dump.read_byml(banc)
        clip = heightmap._actor_clip(document)
    except BancError:
        clip = None
    files: list[dict] = []
    token = _FILES.set(files)
    rows = []
    loaded = []
    parts = []
    tile_count = 0
    try:
        _track_file(dump, tscb)
        for extra in sub_paths:
            _track_file(dump, extra)
        if located.get("param"):
            _track_file(dump, located["param"])
        variant = _age_variant(banc)
        focus_x = located["origin"][0] if clip is None else (clip[0] + clip[2]) / 2
        focus_z = located["origin"][2] if clip is None else (clip[1] + clip[3]) / 2
        for layer, folder, scene_tiles in scene_jobs:
            tile_count += len(scene_tiles)
            quads = [(item[0], item[1], item[2], item[3]) for item in scene_tiles]
            multipliers = {item[3]: item[4] for item in scene_tiles}
            chosen, skipped, notes = _choose_tiles(quads, scale, located["origin"], clip)
            choice_notes.extend(notes)
            rows.extend(_tile_row(info, "", layer) for info in skipped)
            layer_loaded, failed = _read_height_tiles(dump, chosen, [folder] if folder else [], multipliers, layer, variant)
            rows.extend(failed)
            if not layer_loaded:
                continue
            steps, step_note = heightmap.sample_steps(
                [(item[0], item[1], item[2], item[3]) for item in layer_loaded],
                scale,
                located["origin"],
                (focus_x, focus_z),
            )
            choice_notes.append(f"{layer}: {step_note}")
            layer_parts, reports = heightmap.build_scene(
                [
                    (item[0], item[1], item[2], item[3], item[4], item[5], item[6], item[7], item[9])
                    for item in layer_loaded
                ],
                scale,
                height_scale,
                located["origin"],
                clip=None,
                steps=steps,
            )
            _stamp_layer(layer_loaded, reports, rows, layer)
            parts.extend(layer_parts)
            loaded.extend(layer_loaded)
        if not parts:
            reason = f"{tscb} has no tile that produced triangles."
            failed = [row["name"] for row in rows if row["status"] != "drawn"]
            if failed:
                reason += " Skipped " + ", ".join(failed[:8]) + "."
            return {
                "status": "none",
                "reason": reason,
                "source": tscb,
                "url": None,
                "banc": banc,
                "tiles": rows,
                "terrain": _terrain_meta(located, scale, height_scale, clip, choice_notes, sub_paths),
            }
        layer_ids = sorted({item for part in parts for item in (part.layer, part.layer_b) if item is not None})
        images, log, raws = _bind_terrain_layers(dump, layer_ids)
    finally:
        _FILES.reset(token)
    primitives, positions, triangles, used_images = _terrain_primitives(parts, images, layer_ids)
    if not primitives:
        return {
            "status": "none",
            "reason": f"{tscb} produced no triangles.",
            "source": tscb,
            "url": None,
            "banc": banc,
            "tiles": rows,
            "terrain": _terrain_meta(located, scale, height_scale, clip, choice_notes, sub_paths),
        }
    drawn = [row for row in rows if row["status"] == "drawn"]
    notes = list(located.get("notes") or []) + choice_notes
    if layer_ids:
        notes.append("MaterialAlb layers " + ", ".join(str(item) for item in layer_ids))
    else:
        notes.append("no material index in the heightmap, so the ground is a flat earth colour")
    notes.append(
        f"triplanar UV repeats every {heightmap._UV_METRES:.0f} m (a guess); "
        "each scene keeps its own finest tile, and the _sub overlay does not drop main cells. "
        "One atlas draw, skirts on tile edges. "
        "Height 0 is ground at the terrain origin; only a non-finite sample is a hole. "
        "The partial last column is read from its packed GOB slot. The field is front-side; skirts stay. "
        "Sub terrain starts off and shows the overlay"
    )
    notes.append("custom.extm is not decoded")
    notes.append(
        "TSCB bytes +0x0C..+0x5F and GameBancParam have no hole or hide-ground flag; "
        ".mate is a material index"
    )
    sparse = _sparse_heightmap(rows)
    if sparse:
        missing = sum(1 for row in rows if row.get("reason") == "no heightmap file")
        present = sum(1 for row in rows if row.get("status") == "drawn")
        notes.insert(
            0,
            f"sparse heightmap ({present} of {present + missing} in-clip tiles have a height file); Ground starts off",
        )
    reason = "terrain heightmap. " + " ".join(notes)
    bounds = None
    if positions:
        bounds = {
            "min": [min(point[axis] for point in positions) for axis in range(3)],
            "max": [max(point[axis] for point in positions) for axis in range(3)],
        }
    info = {
        "reason": reason,
        "source": tscb,
        "triangles": triangles,
        "textures": used_images,
        "bounds": bounds,
        "textureLog": log,
        "tiles": rows,
        "terrain": _terrain_meta(located, scale, height_scale, clip, notes, sub_paths),
        "notes": notes,
        "sparse": sparse,
    }
    info["terrain"]["drawn"] = len(drawn)
    info["terrain"]["sparse"] = sparse
    info["terrain"]["tiles"] = tile_count
    digest = hashlib.sha256(
        _cache_prefix() + b"height-mesh\0" + _height_blob(loaded) + _texture_key(raws)
    ).hexdigest()
    folder = cache_dir() / digest[:2]
    glb_path = folder / f"{digest}.glb"
    meta_path = folder / f"{digest}.json"
    glb = finish_glb(to_glb(primitives))
    with _LOCK:
        if not (glb_path.is_file() and meta_path.is_file()):
            folder.mkdir(parents=True, exist_ok=True)
            glb_path.write_bytes(glb)
            meta_path.write_text(dump_model_meta(info), encoding="utf-8")
            note_written(glb_path, meta_path)
        _write_pointer(
            pointer,
            {
                "kind": "mesh",
                "digest": digest,
                "info": info,
                "files": files,
                "texDir": _tex_token(dump),
                "terrainDirs": dir_token,
            },
        )
    return _height_response(banc, digest, info)


_ATLAS_CELL = 128
_ATLAS_KEEP = 16
# Cell bytes only. A models.py bump rebuilds the glb and still reads this PNG.
_ATLAS_VERSION = b"atlas-1\0"


def _atlas_key(cells: list[bytes]) -> str:
    digest = hashlib.sha256(_ATLAS_VERSION)
    digest.update(len(cells).to_bytes(4, "little"))
    for cell in cells:
        digest.update(len(cell).to_bytes(8, "little"))
        digest.update(cell)
    return digest.hexdigest()


def _solid_cell(color, size: int = _ATLAS_CELL) -> bytes:
    red, green, blue = [max(0, min(255, int(round(channel * 255)))) for channel in color[:3]]
    return bytes((red, green, blue, 255)) * (size * size)


def _image_cell(image, size: int = _ATLAS_CELL) -> bytes:
    width = int(getattr(image, "width", 0) or 0)
    height = int(getattr(image, "height", 0) or 0)
    rgba = getattr(image, "rgba", b"") or b""
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return _solid_cell(_EARTH, size)
    out = bytearray(size * size * 4)
    for y in range(size):
        sy = min(height - 1, y * height // size)
        row = sy * width
        for x in range(size):
            sx = min(width - 1, x * width // size)
            src = (row + sx) * 4
            dst = (y * size + x) * 4
            out[dst : dst + 4] = rgba[src : src + 4]
    return bytes(out)


def _atlas_image(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n" or (len(data) > 4 and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9")


def _pack_atlas(cells: list[bytes]) -> tuple[bytes, int]:
    count = max(1, len(cells))
    cols = max(1, math.ceil(math.sqrt(count)))
    path = cache_dir() / "atlas" / f"{_atlas_key(cells)}.img"
    with _LOCK:
        try:
            cached = path.read_bytes()
        except OSError:
            cached = b""
    if _atlas_image(cached):
        note_access(path)
        return cached, cols
    rows = max(1, math.ceil(count / cols))
    size = _ATLAS_CELL
    width = cols * size
    height = rows * size
    out = bytearray(width * height * 4)
    for index, cell in enumerate(cells):
        cx = (index % cols) * size
        cy = (index // cols) * size
        for y in range(size):
            src = y * size * 4
            dst = ((cy + y) * width + cx) * 4
            out[dst : dst + size * 4] = cell[src : src + size * 4]
    png, _mime = encode_albedo(width, height, bytes(out), "opaque")
    with _LOCK:
        try:
            cached = path.read_bytes()
        except OSError:
            cached = b""
        if _atlas_image(cached):
            note_access(path)
            return cached, cols
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        write_version_sidecar(path, atlas_version())
        note_written(path)
    return png, cols


def _terrain_primitives(parts, images, layer_ids):
    """One draw for the whole field. Layer ids live in TEXCOORD_1 and do not blend."""
    positions = []
    triangles = 0
    if not layer_ids:
        primitives = []
        for part in parts:
            primitives.append(
                Primitive(
                    part.positions,
                    part.indices,
                    part.uvs,
                    alpha="opaque",
                    double_sided=False,
                    base_color=_EARTH,
                    name="terrain",
                    kind="terrain",
                    colors=part.colors,
                    marks=part.marks,
                )
            )
            triangles += len(part.indices) // 3
            positions.extend(part.positions)
        return primitives, positions, triangles, 0
    counts: dict[int, int] = {}
    for part in parts:
        for layer in (part.layer, part.layer_b):
            if layer is None:
                continue
            counts[layer] = counts.get(layer, 0) + len(part.positions)
    ranked = sorted(counts, key=lambda item: (-counts[item], item))
    kept = ranked[:_ATLAS_KEEP]
    slots: dict[int, float] = {}
    cells = []
    for layer in kept:
        image = images.get(layer)
        cells.append(_image_cell(image) if image is not None and getattr(image, "rgba", None) else _solid_cell(_EARTH))
        slots[layer] = float(len(slots))
    earth = float(len(cells))
    cells.append(_solid_cell(_EARTH))
    png, cols = _pack_atlas(cells)

    def slot_for(layer):
        if layer is None:
            return earth
        found = slots.get(layer)
        if found is not None:
            return found
        if not kept:
            return earth
        nearest = min(kept, key=lambda item: (abs(item - layer), item))
        return slots[nearest]

    merged_pos = []
    merged_idx = []
    merged_uv = []
    merged_uv2 = []
    merged_shade = []
    merged_color = []
    any_color = any(part.colors for part in parts)
    for part in parts:
        base = len(merged_pos)
        slot_a = slot_for(part.layer)
        slot_b = slot_for(part.layer_b) if part.layer_b is not None else slot_a
        for index, point in enumerate(part.positions):
            merged_pos.append(point)
            if part.uvs and index < len(part.uvs):
                merged_uv.append(part.uvs[index])
            else:
                merged_uv.append((point[0] / heightmap._UV_METRES, point[2] / heightmap._UV_METRES))
            merged_uv2.append((slot_a, slot_b))
            weight = part.weights[index] if part.weights and index < len(part.weights) else 0.0
            shade = part.colors[index][0] if part.colors and index < len(part.colors) else 1.0
            merged_shade.append((float(weight), float(shade)))
            if any_color:
                merged_color.append(part.colors[index] if part.colors and index < len(part.colors) else (1.0, 1.0, 1.0))
        merged_idx.extend(base + item for item in part.indices)
        triangles += len(part.indices) // 3
        positions.extend(part.positions)
    if len(merged_pos) < 3 or len(merged_idx) < 3:
        return [], positions, 0, 0
    primitive = Primitive(
        merged_pos,
        merged_idx,
        merged_uv,
        png=png,
        alpha="opaque",
        double_sided=False,
        base_color=(1.0, 1.0, 1.0, 1.0),
        name="terrain",
        kind="terrain",
        colors=merged_color or None,
        uvs2=merged_uv2,
        shades=merged_shade,
        marks=[mark for part in parts for mark in (part.marks or [(0.0, 0.0)] * len(part.positions))],
        extras={"atlas": cols, "repeat": heightmap._UV_METRES},
    )
    return [primitive], positions, triangles, 1 if png else 0


def _terrain_meta(located, scale, height_scale, clip, notes, subs=None) -> dict:
    origin = located.get("origin") or (0.0, 0.0, 0.0)
    return {
        "scene": located.get("scene") or "",
        "param": located.get("param") or "",
        "origin": [origin[0], origin[1], origin[2]],
        "scale": scale,
        "heightScale": height_scale,
        "clip": list(clip) if clip else None,
        "notes": list(notes),
        "subs": list(subs or []),
    }


def _height_blob(loaded) -> bytes:
    chunks = []
    for item in loaded:
        center_x, center_z, edge, name, heights, layers, occlusion, pairs, _info = item[:9]
        flag = float(item[9]) if len(item) > 9 else 0.0
        chunks.append(
            f"{'sub' if flag else 'main'}:{name}:{center_x}:{center_z}:{edge}".encode("utf-8", "replace")
        )
        chunks.append(array.array("f", heights).tobytes())
        if layers:
            chunks.append(bytes((int(item) & 0xFF) if int(item) >= 0 else 0xFF for item in layers))
        if pairs:
            packed = bytearray()
            for first, second, weight in pairs:
                packed.append(int(first) & 0xFF if int(first) >= 0 else 0xFF)
                packed.append(int(second) & 0xFF if int(second) >= 0 else 0xFF)
                packed.append(max(0, min(255, int(round(float(weight) * 255)))))
            chunks.append(bytes(packed))
        if occlusion:
            chunks.append(array.array("f", occlusion).tobytes())
    return b"\0".join(chunks)


def _bind_terrain_layers(dump, layers: list[int]):
    images = {}
    log = []
    raws: list[bytes] = []
    for layer in layers:
        relative = ""
        for candidate in ("Tex/MaterialAlb.bntx.zs", "Tex/MaterialAlb.bntx"):
            if _exists(dump, candidate):
                relative = candidate
                break
        found = {
            "image": None,
            "error": "Tex/MaterialAlb.bntx.zs is not in this dump",
            "tried": ["Tex/MaterialAlb.bntx.zs", "Tex/MaterialAlb.bntx"],
            "found": "",
        }
        if relative:
            found = _load_named(
                dump,
                None,
                "MaterialAlb",
                [],
                "opaque",
                0.5,
                {},
                raws,
                forced=relative,
                layer=layer,
                flatten_alpha=True,
            )
        entry = {
            "material": f"Mt_TerraMaterial_{layer:03d}",
            "sampler": "ta",
            "samplers": ["ta"],
            "binds": [["ta", "MaterialAlb"]],
            "textures": ["MaterialAlb"],
            "wanted": f"MaterialAlb#{layer}",
            "tried": found.get("tried") or [],
            "found": found.get("found") or "",
            "decoded": found.get("image") is not None,
            "format": "",
            "size": "",
            "swizzle": "",
            "image": "",
            "images": found.get("images") or 0,
            "alpha": "opaque",
            "doubleSided": True,
            "renderState": "opaque",
            "blockHeight": 0,
            "error": found.get("error") or "",
            "shapes": ["terrain"],
            "textured": found.get("image") is not None,
            "flat": found.get("image") is None,
        }
        if found.get("image") is not None:
            _fill_image_log(entry, found)
            images[layer] = found["image"]
        log.append(entry)
    return images, log, raws


def flag10_ground(dump, banc: str) -> list[dict]:
    """Ground-like actors whose shapes are FSHP flag 0x10.

    Those shapes are not the field heightmap. The payload is not an FVTX, so
    they stay boxes. The list is what the diagnostic prints.
    """
    try:
        document, _dict_id, _raw = dump.read_byml(banc)
    except BancError as exc:
        return [{"gyaml": "", "status": "unread", "reason": exc.message, "shapes": []}]
    actors = document.get("Actors") if isinstance(document, dict) else None
    names: list[str] = []
    if isinstance(actors, list):
        for actor in actors:
            gyaml = actor.get("Gyaml") if isinstance(actor, dict) else None
            if isinstance(gyaml, str) and _GROUND_GYAML.search(gyaml) and gyaml not in names:
                names.append(gyaml)
    rows = []
    for gyaml in names[:80]:
        try:
            plan, _tried, _referenced = _actor_plan(dump, gyaml)
        except BancError as exc:
            rows.append({"gyaml": gyaml, "status": "unread", "reason": exc.message, "shapes": [], "source": ""})
            continue
        blob = b""
        source = ""
        for item in plan:
            if item[0] == "blob":
                source, blob = item[1], item[2]
                break
            if _exists(dump, item[1]):
                try:
                    blob, _dict_id = dump.read_file(item[1])
                except BancError as exc:
                    rows.append({"gyaml": gyaml, "status": "unread", "reason": exc.message, "shapes": [], "source": item[1]})
                    blob = b""
                    break
                source = item[1]
                break
        if not blob or blob[:4] != b"FRES":
            continue
        try:
            models = parse(blob)
        except FresError as exc:
            rows.append({"gyaml": gyaml, "status": "unread", "reason": str(exc), "shapes": [], "source": source})
            continue
        shapes = []
        for model in models:
            shapes.extend(name for name in model.flag10 if name)
        if shapes:
            rows.append(
                {
                    "gyaml": gyaml,
                    "status": "skipped",
                    "reason": "geometry format not decoded (FSHP flag 0x10). The payload is not an FVTX, so this is not the field.",
                    "shapes": shapes,
                    "source": source,
                }
            )
    return rows


def _terrain_plan(dump, banc: str) -> list:
    return [("file", path) for path in _terrain_paths(banc, _model_listing(dump))]


def _terrain_paths(banc: str, listing: list[str]) -> list[str]:
    name = banc.rsplit("/", 1)[-1]
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = banc.split("/")
    area = parts[1] if len(parts) >= 3 else name
    paths: list[str] = []

    def add(path: str) -> None:
        if path not in paths:
            paths.append(path)

    numbered = []
    for file_name in listing:
        match = _NUMBERED.match(file_name)
        if not match:
            continue
        stem = match.group("stem")
        if stem not in (area, name):
            continue
        numbered.append((0 if stem == area else 1, int(match.group("num")), file_name))
    for _rank, _number, file_name in sorted(numbered):
        add(f"Model/{file_name}")
    for stem in (area, name):
        add(f"Model/{stem}_01.{stem}_01.bfres.zs")
        add(f"Model/{stem}_01.{stem}_01.bfres")
        add(f"Model/{stem}.{stem}.bfres.zs")
        add(f"Model/{stem}.{stem}.bfres")
    for folder in ("Model", "Terrain", "Map"):
        for stem in (name, area, f"{area}_Static", f"{name}_Static"):
            if not stem:
                continue
            add(f"{folder}/{stem}.bfres.zs")
            add(f"{folder}/{stem}.bfres")
    return paths


def _names_in(blob: bytes) -> list[str]:
    found: list[str] = []
    if blob[:2] == b"YB":
        try:
            _walk(loads(blob), found)
        except ValueError:
            pass
    for match in _PATH.findall(blob):
        text = match.decode("ascii", "ignore")
        if text not in found:
            found.append(text)
    return found


def _walk(node, found: list[str], key: str = "") -> None:
    if isinstance(node, str):
        if key in _KEYS or ".bfres" in node:
            text = _normalize(node, key)
            if text and text not in found:
                found.append(text)
    elif isinstance(node, dict):
        for name, value in node.items():
            _walk(value, found, str(name))
    elif isinstance(node, list):
        for value in node:
            _walk(value, found, key)


def _normalize(text: str, key: str) -> str:
    cleaned = text.strip().replace("\\", "/")
    if cleaned.startswith("Work/"):
        cleaned = cleaned[5:]
    cleaned = cleaned.lstrip("/")
    if ".bfres" in cleaned or "/" in cleaned:
        return cleaned
    if key in _KEYS and cleaned:
        return f"Model/{cleaned}.bfres"
    return ""


def _path_variants(text: str) -> list[str]:
    if text.endswith(".bfres.zs"):
        return [text, text[:-3]]
    if text.endswith(".bfres"):
        return [text + ".zs", text]
    if text:
        return [f"Model/{text}.bfres.zs", f"Model/{text}.bfres"]
    return []
