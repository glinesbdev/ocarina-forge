"""Resolve an actor or a scene to a cached glTF mesh.

The dump is only read. Converted glTF and PNG live under the user cache
(`$BANCWRIGHT_CACHE`, or `$XDG_CACHE_HOME/bancwright/models`, or
`~/.cache/bancwright/models`). That directory is outside the repo and is
never committed.

A real actor pack does not contain the bfres. `Component/ModelInfo/*.bgyml`
names `ModelProjectName` and `FmdbName`, and the mesh is
`Model/<Project>.<Fmdb>.bfres.zs` (zstd dictionary 1). Base color is a
separate `Tex/<name>.bntx.zs`, not a BNTX embedded in the bfres. Older
`Model/<name>.bfres` paths and a bfres stored in the pack are still accepted.
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
from bancwright.formats.bntx import BntxError, textures as read_bntx
from bancwright.formats.byml import loads
from bancwright.formats.gltf import Primitive, png_rgba, to_glb
from bancwright.formats.sarc import Sarc

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH = re.compile(rb"(?:Model|Terrain|Map)/[A-Za-z0-9_./+-]{1,180}\.bfres(?:\.zs)?")
_NUMBERED = re.compile(r"^(?P<stem>.+?)_(?P<num>\d+)\.(?P=stem)_(?P=num)\.bfres(?:\.zs)?$")
_KEYS = {"ModelProjectName", "ModelName", "Model", "BFRES", "ModelFile", "FmdbName"}
_CACHE_VERSION = b"models-2\0"
_LOCK = threading.Lock()
_LISTING: dict[str, tuple[int, list[str]]] = {}

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
        plan, tried = _actor_plan(dump, gyaml)
    except BancError as exc:
        return _box(gyaml, exc.message)
    if not any(item[0] == "blob" or (dump.romfs / item[1]).is_file() for item in plan):
        return _box(gyaml, _missing(gyaml, tried))
    return _first_mesh(gyaml, plan, f"/api/models/{gyaml}/model.glb", dump, tried)


def actor_glb(dump, gyaml: str) -> Path | None:
    info = describe_actor(dump, gyaml)
    if info["status"] != "mesh":
        return None
    return _cached_glb(info["digest"])


def describe_terrain(dump, banc: str) -> dict:
    if not banc or ".." in banc or banc.startswith("/"):
        return {"status": "none", "reason": "That scene path is not in the dump.", "source": "", "url": None}
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
    info = _first_mesh(banc, plan, f"/api/terrain/model.glb?banc={banc}", dump, tried)
    info["banc"] = banc
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


def _first_mesh(label: str, plan: list, url: str, dump, tried: list[str]) -> dict:
    errors = []
    first = ""
    for source, blob in _realize(dump, plan, errors):
        if not first:
            first = source
        try:
            digest, info = _convert_cached(blob, dump)
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
    return path if path.is_file() else None


def _convert_cached(blob: bytes, dump) -> tuple[str, dict]:
    if blob[:4] in (b"MCPK", b"FMSH"):
        raise FresError("this is a mesh-codec .bfres.mc stream, which this viewer does not decode")
    models = parse(blob)
    textures = _texture_payloads(dump, models)
    digest = hashlib.sha256(_CACHE_VERSION + blob + _texture_key(textures)).hexdigest()
    folder = cache_dir() / digest[:2]
    glb_path = folder / f"{digest}.glb"
    meta_path = folder / f"{digest}.json"
    with _LOCK:
        if glb_path.is_file() and meta_path.is_file():
            return digest, json.loads(meta_path.read_text(encoding="utf-8"))
    glb, info = _assemble(models, textures)
    encoded = json.dumps(info)
    with _LOCK:
        if not (glb_path.is_file() and meta_path.is_file()):
            folder.mkdir(parents=True, exist_ok=True)
            glb_path.write_bytes(glb)
            meta_path.write_text(encoded, encoding="utf-8")
    return digest, info


def _texture_key(textures: list[tuple[str, str, bytes]]) -> bytes:
    return b"".join(name.encode("utf-8", "replace") + b"\0" + blob for name, _path, blob in textures)


def _assemble(models, textures: list[tuple[str, str, bytes]]) -> tuple[bytes, dict]:
    external: dict[str, object] = {}
    for name, _relative, blob in textures:
        if not blob.startswith(b"BNTX"):
            continue
        try:
            decoded = list(read_bntx(blob))
        except BntxError:
            continue
        if not decoded:
            continue
        chosen = next((image for image in decoded if image.name == name or image.name.lower() == name.lower()), decoded[0])
        external[name] = chosen
        external.setdefault(chosen.name, chosen)
    primitives: list[Primitive] = []
    notes: list[str] = []
    triangles = 0
    texture_count = 0
    seen_images: set[int] = set()
    positions: list[tuple[float, float, float]] = []
    for model in models:
        notes.extend(model.notes)
        png_for: dict[int, bytes] = {}
        for part in model.parts:
            image = model.images.get(part.texture) or external.get(part.texture)
            if image is None and len(model.images) == 1 and not external:
                image = next(iter(model.images.values()))
            png = None
            if image is not None:
                png = png_for.get(id(image))
                if png is None:
                    png = png_rgba(image.width, image.height, image.rgba)
                    png_for[id(image)] = png
                    if id(image) not in seen_images:
                        texture_count += 1
                        seen_images.add(id(image))
                    if image.note:
                        notes.append(image.note)
            elif part.texture:
                notes.append(
                    f"Base color “{part.texture}” was not in the bfres or in Tex/, so this mesh is untextured."
                )
            elif not model.images and not external:
                notes.append("No base-color texture was embedded in this bfres or found in Tex/, so the mesh is untextured.")
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
    unique_notes = list(dict.fromkeys(notes))
    return glb, {"triangles": triangles, "textures": texture_count, "bounds": bounds, "notes": unique_notes}


def _texture_payloads(dump, models) -> list[tuple[str, str, bytes]]:
    if dump is None:
        return []
    found: list[tuple[str, str, bytes]] = []
    seen: set[str] = set()
    for model in models:
        for part in model.parts:
            name = part.texture
            if not name or name in model.images or name in seen:
                continue
            seen.add(name)
            for relative in _tex_candidates(name):
                if not (dump.romfs / relative).is_file():
                    continue
                try:
                    blob, _dict_id = dump.read_file(relative)
                except BancError:
                    continue
                if blob[:4] == b"BNTX":
                    found.append((name, relative, blob))
                    break
    return found


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


def _actor_plan(dump, gyaml: str) -> tuple[list, list[str]]:
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
        add_file(f"Model/{project}.{fmdb}.bfres.zs")
        add_file(f"Model/{project}.{fmdb}.bfres")
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
    return plan, tried


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
