"""Resolve an actor or a scene to a cached glTF mesh.

The dump is only read. Converted glTF and PNG live under the user cache
(`$BANCWRIGHT_CACHE`, or `$XDG_CACHE_HOME/bancwright/models`, or
`~/.cache/bancwright/models`). That directory is outside the repo and is
never committed.

An actor's model is the bfres inside `Pack/Actor/<Gyaml>.pack.zs`, or the
`Model/<name>.bfres.zs` named by that pack (ModelProjectName, a Model/*.bfres
string). `.zs` files are decompressed with the dump's zstd dictionaries.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
import threading
from pathlib import Path

from bancwright.errors import BancError
from bancwright.formats.bfres import FresError, parse
from bancwright.formats.bntx import BntxError
from bancwright.formats.byml import loads
from bancwright.formats.gltf import Primitive, png_rgba, to_glb
from bancwright.formats.sarc import Sarc

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH = re.compile(rb"(?:Model|Terrain|Map)/[A-Za-z0-9_./+-]{1,180}\.bfres(?:\.zs)?")
_KEYS = {"ModelProjectName", "ModelName", "Model", "BFRES", "ModelFile"}
_CACHE_VERSION = b"models-1\0"
_LOCK = threading.Lock()

_cache_dir: Path | None = None


def cache_dir() -> Path:
    """Per-user directory for converted models. Created on first use.

    `BANCWRIGHT_CACHE` is read every call so a test can point at a temp folder.
    The default path is remembered after the first successful create.
    """
    global _cache_dir
    override = os.environ.get("BANCWRIGHT_CACHE")
    if override:
        root = Path(override).expanduser()
    elif _cache_dir is not None:
        return _cache_dir
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        root = (Path(xdg).expanduser() if xdg else Path.home() / ".cache") / "bancwright" / "models"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        root = Path(tempfile.gettempdir()) / "bancwright-models"
        root.mkdir(parents=True, exist_ok=True)
    if not override:
        _cache_dir = root
    return root


def describe_actor(dump, gyaml: str) -> dict:
    if not _NAME.fullmatch(gyaml or ""):
        return _box(gyaml, "That is not an actor pack name.")
    try:
        candidates = _actor_sources(dump, gyaml)
    except BancError as exc:
        return _box(gyaml, exc.message)
    if not candidates:
        return _box(
            gyaml,
            f"No model for {gyaml}. Looked in Pack/Actor/{gyaml}.pack.zs and Model/{gyaml}.bfres.zs.",
        )
    return _first_mesh(gyaml, candidates, f"/api/models/{gyaml}/model.glb")


def actor_glb(dump, gyaml: str) -> Path | None:
    info = describe_actor(dump, gyaml)
    if info["status"] != "mesh":
        return None
    return _cached_glb(info["digest"])


def describe_terrain(dump, banc: str) -> dict:
    if not banc or ".." in banc or banc.startswith("/"):
        return {"status": "none", "reason": "That scene path is not in the dump.", "source": "", "url": None}
    try:
        candidates = _terrain_sources(dump, banc)
    except BancError as exc:
        return {"status": "box", "reason": exc.message, "source": "", "url": None, "banc": banc}
    if not candidates:
        tried = ", ".join(_terrain_paths(banc)[:4])
        return {
            "status": "none",
            "reason": f"No static map model for this scene. Tried {tried}.",
            "source": "",
            "url": None,
            "banc": banc,
        }
    info = _first_mesh(banc, candidates, f"/api/terrain/model.glb?banc={banc}")
    info["banc"] = banc
    if info["status"] == "box":
        info["status"] = "box"
    return info


def terrain_glb(dump, banc: str) -> Path | None:
    info = describe_terrain(dump, banc)
    if info.get("status") != "mesh":
        return None
    return _cached_glb(info["digest"])


def _box(gyaml: str, reason: str) -> dict:
    return {
        "gyaml": gyaml,
        "status": "box",
        "reason": reason,
        "source": "",
        "triangles": 0,
        "textures": 0,
        "bounds": None,
        "url": None,
        "digest": "",
    }


def _first_mesh(label: str, candidates: list[tuple[str, bytes]], url: str) -> dict:
    errors = []
    for source, blob in candidates:
        try:
            digest, info = _convert_cached(blob)
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
        }
    reason = errors[0] if len(errors) == 1 else " ".join(errors[:3])
    result = _box(label, reason or "The model could not be converted.")
    if candidates:
        result["source"] = candidates[0][0]
    return result


def _cached_glb(digest: str) -> Path | None:
    if not digest:
        return None
    path = cache_dir() / digest[:2] / f"{digest}.glb"
    return path if path.is_file() else None


def _convert_cached(blob: bytes) -> tuple[str, dict]:
    digest = hashlib.sha256(_CACHE_VERSION + blob).hexdigest()
    folder = cache_dir() / digest[:2]
    glb_path = folder / f"{digest}.glb"
    meta_path = folder / f"{digest}.json"
    with _LOCK:
        if glb_path.is_file() and meta_path.is_file():
            return digest, json.loads(meta_path.read_text(encoding="utf-8"))
        glb, info = _convert(blob)
        folder.mkdir(parents=True, exist_ok=True)
        glb_path.write_bytes(glb)
        meta_path.write_text(json.dumps(info), encoding="utf-8")
    return digest, info


def _convert(blob: bytes) -> tuple[bytes, dict]:
    if blob[:4] in (b"MCPK", b"FMSH"):
        raise FresError("this is a mesh-codec .bfres.mc stream, which this viewer does not decode")
    models = parse(blob)
    primitives: list[Primitive] = []
    notes: list[str] = []
    triangles = 0
    textures = 0
    seen_images: set[str] = set()
    positions: list[tuple[float, float, float]] = []
    for model in models:
        notes.extend(model.notes)
        png_for: dict[str, bytes] = {}
        for part in model.parts:
            image = model.images.get(part.texture)
            if image is None and len(model.images) == 1:
                image = next(iter(model.images.values()))
            png = None
            if image is not None:
                png = png_for.get(image.name)
                if png is None:
                    png = png_rgba(image.width, image.height, image.rgba)
                    png_for[image.name] = png
                    if image.name not in seen_images:
                        textures += 1
                        seen_images.add(image.name)
                    if image.note:
                        notes.append(image.note)
            elif part.texture:
                notes.append(f"Base color “{part.texture}” is not in the embedded BNTX, so this mesh is untextured.")
            elif not model.images:
                notes.append("No base-color texture was embedded in this bfres, so the mesh is untextured.")
            primitives.append(Primitive(part.positions, part.indices, part.uvs, png=png))
            triangles += len(part.indices) // 3
            positions.extend(part.positions)
    if not primitives:
        raise FresError("the model has no triangles")
    glb = to_glb(primitives)
    bounds = None
    if positions:
        bounds = {
            "min": [min(point[axis] for point in positions) for axis in range(3)],
            "max": [max(point[axis] for point in positions) for axis in range(3)],
        }
    # One note is enough when every shape missed the same texture.
    unique_notes = list(dict.fromkeys(notes))
    return glb, {"triangles": triangles, "textures": textures, "bounds": bounds, "notes": unique_notes}


def _actor_sources(dump, gyaml: str) -> list[tuple[str, bytes]]:
    sources: list[tuple[str, bytes]] = []
    pack_rel = f"Pack/Actor/{gyaml}.pack.zs"
    named: list[str] = []
    if (dump.romfs / pack_rel).is_file():
        payload, _dict_id = dump.read_file(pack_rel)
        try:
            archive = Sarc(payload)
        except ValueError as exc:
            raise BancError(f"{pack_rel} is not a SARC pack: {exc}", "bad_pack") from exc
        embedded = []
        for name in archive.names():
            blob = archive.read(name)
            if name.endswith(".bfres") or blob[:8] == b"FRES    ":
                embedded.append((f"{pack_rel}:{name}", blob))
            else:
                named.extend(_names_in(blob))
        embedded.sort(key=lambda item: (gyaml not in item[0], item[0]))
        sources.extend(embedded)
    paths: list[str] = []
    for text in named:
        paths.extend(_path_variants(text))
    paths.append(f"Model/{gyaml}.bfres.zs")
    paths.append(f"Model/{gyaml}.bfres")
    seen = {label for label, _blob in sources}
    for relative in paths:
        if relative in seen:
            continue
        seen.add(relative)
        file = dump.romfs / relative
        if not file.is_file():
            continue
        blob, _dict_id = dump.read_file(relative)
        sources.append((relative, blob))
    return sources


def _terrain_sources(dump, banc: str) -> list[tuple[str, bytes]]:
    sources = []
    for relative in _terrain_paths(banc):
        if not (dump.romfs / relative).is_file():
            continue
        blob, _dict_id = dump.read_file(relative)
        sources.append((relative, blob))
    return sources


def _terrain_paths(banc: str) -> list[str]:
    name = banc.rsplit("/", 1)[-1]
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = banc.split("/")
    area = parts[1] if len(parts) >= 3 else name
    stems = []
    for stem in (name, area, f"{area}_Static", f"{name}_Static"):
        if stem and stem not in stems:
            stems.append(stem)
    paths = []
    for folder in ("Model", "Terrain", "Map"):
        for stem in stems:
            paths.append(f"{folder}/{stem}.bfres.zs")
            paths.append(f"{folder}/{stem}.bfres")
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
