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
from dataclasses import replace
from pathlib import Path

from bancwright.errors import BancError
from bancwright.formats.bfres import FresError, base_color_name, parse, texture_rank
from bancwright.formats.bntx import BntxError, image_names, textures as read_bntx
from bancwright.formats.byml import loads
from bancwright.formats.gltf import Primitive, png_rgba, to_glb
from bancwright.formats.sarc import Sarc

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH = re.compile(rb"(?:Model|Terrain|Map)/[A-Za-z0-9_./+-]{1,180}\.bfres(?:\.zs)?")
_NUMBERED = re.compile(r"^(?P<stem>.+?)_(?P<num>\d+)\.(?P=stem)_(?P=num)\.bfres(?:\.zs)?$")
_KEYS = {"ModelProjectName", "ModelName", "Model", "BFRES", "ModelFile", "FmdbName"}
_CACHE_VERSION = b"models-5\0"
_NEUTRAL = (0.62, 0.58, 0.5, 1.0)
# Untextured static-map stand-in. The real ground is TerrainArc, not this bfres.
_TERRAIN = (0.36, 0.45, 0.28, 1.0)
_LOCK = threading.Lock()
_LISTING: dict[str, tuple[int, list[str]]] = {}
_TEX_INDEX: dict[str, tuple[int, dict[str, str]]] = {}

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
    info = _first_mesh(banc, plan, f"/api/terrain/model.glb?banc={banc}", dump, tried, terrain=True)
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


def _first_mesh(label: str, plan: list, url: str, dump, tried: list[str], terrain: bool = False) -> dict:
    errors = []
    first = ""
    for source, blob in _realize(dump, plan, errors):
        if not first:
            first = source
        try:
            digest, info = _convert_cached(blob, dump, hint=f"{label} {source}", terrain=terrain)
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


def _convert_cached(blob: bytes, dump, hint: str = "", terrain: bool = False) -> tuple[str, dict]:
    if blob[:4] in (b"MCPK", b"FMSH"):
        raise FresError("this is a mesh-codec .bfres.mc stream, which this viewer does not decode")
    models = parse(blob)
    _fill_texture_names(models, dump)
    external, log, raws = _resolve_materials(dump, models, hint, terrain)
    digest = hashlib.sha256(_CACHE_VERSION + blob + _texture_key(raws)).hexdigest()
    folder = cache_dir() / digest[:2]
    glb_path = folder / f"{digest}.glb"
    meta_path = folder / f"{digest}.json"
    with _LOCK:
        if glb_path.is_file() and meta_path.is_file():
            return digest, json.loads(meta_path.read_text(encoding="utf-8"))
    glb, info = _assemble(models, external, log)
    encoded = json.dumps(info)
    with _LOCK:
        if not (glb_path.is_file() and meta_path.is_file()):
            folder.mkdir(parents=True, exist_ok=True)
            glb_path.write_bytes(glb)
            meta_path.write_text(encoded, encoding="utf-8")
    return digest, info


def _texture_key(blobs: list[bytes]) -> bytes:
    return b"".join(blob + b"\0" for blob in blobs)


def _assemble(models, external: dict, log: list[dict] | None = None) -> tuple[bytes, dict]:
    log = log if log is not None else []
    by_wanted = {}
    for entry in log:
        if entry.get("wanted") and entry["wanted"] not in by_wanted:
            by_wanted[entry["wanted"]] = entry
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
            image = None
            if part.texture:
                image = model.images.get(part.texture) or external.get(part.texture) or external.get(part.texture.lower())
            if image is not None and not image.rgba:
                image = None
            png = None
            if image is not None:
                png = png_for.get(id(image))
                if png is None:
                    png = png_rgba(image.width, image.height, image.rgba)
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
            if part.proxy and image is None:
                color = _TERRAIN
            else:
                color = (1.0, 1.0, 1.0, 1.0) if image is not None else (part.base_color or _NEUTRAL)
            alpha = part.alpha if image is not None or part.alpha == "blend" else "opaque"
            primitives.append(
                Primitive(
                    part.positions,
                    part.indices,
                    part.uvs,
                    png=png,
                    alpha=alpha,
                    cutoff=part.cutoff,
                    double_sided=part.double_sided,
                    base_color=color,
                )
            )
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
                log.append(choice["entry"])
                for part in model.parts:
                    if part.material_index < 0 or part.material_index >= len(model.materials):
                        continue
                    if model.materials[part.material_index] is not material:
                        continue
                    part.texture = choice["wanted"]
                    part.albedo_sampler = choice["sampler"]
                    part.alpha = choice["alpha"]
                    part.cutoff = choice["cutoff"]
                    if choice.get("proxy"):
                        part.proxy = True
                        part.base_color = _TERRAIN
                        part.texture = ""
                    if choice.get("image") is not None:
                        external[choice["wanted"]] = choice["image"]
        else:
            for part in model.parts:
                choice = _choose_loose(dump, model, part, hints, terrain, external, raws)
                log.append(choice["entry"])
                part.texture = choice["wanted"]
                part.alpha = choice["alpha"]
                if choice.get("image") is not None and choice["wanted"]:
                    external[choice["wanted"]] = choice["image"]
    return external, log, raws


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
    last = {"image": None, "error": "", "tried": [], "found": "", "alpha": material.alpha, "cutoff": material.cutoff, "images": 0}
    for sampler, name in sources:
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
    if winner is not None:
        sampler, wanted, found = winner
    else:
        sampler, wanted, found = "", (sources[0][1] if sources else ""), dict(last)
        found["tried"] = tried or found.get("tried") or []
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
    return {
        "entry": entry,
        "wanted": "" if proxy and winner is None else (wanted if (found.get("image") is not None or wanted) else ""),
        "sampler": "" if proxy and winner is None else sampler,
        "alpha": alpha if found.get("image") is not None else ("blend" if material.alpha == "blend" else "opaque"),
        "cutoff": cutoff,
        "image": None if proxy and winner is None else found.get("image"),
        "proxy": proxy and winner is None,
    }


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
    }
    if found.get("image") is not None:
        _fill_image_log(entry, found)
    return {"entry": entry, "wanted": entry["wanted"], "alpha": found.get("alpha") or "opaque", "image": found.get("image")}


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


def _load_named(dump, model, name, hints, alpha, cutoff, external, raws, forced: str = "") -> dict:
    result = {"image": None, "error": "", "tried": [], "found": "", "alpha": alpha, "cutoff": cutoff, "images": 0}
    if not name and not forced:
        result["error"] = "material has no texture names"
        return result
    embedded = None
    if model is not None and name:
        embedded = model.images.get(name) or model.images.get(name.lower())
    if embedded is not None:
        result["tried"] = ["embedded in the bfres"]
        result["found"] = "embedded"
        result["images"] = 1
        image, mode, used_cut, note = _accept(embedded, alpha, cutoff)
        result["alpha"] = mode
        result["cutoff"] = used_cut
        result["error"] = note
        result["image"] = image
        return result
    if dump is None:
        result["error"] = "no Tex file matched"
        return result
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
        return result
    try:
        blob, _dict_id = dump.read_file(relative)
    except BancError as exc:
        result["found"] = relative
        result["error"] = exc.message
        return result
    raws.append(blob)
    if blob[:4] != b"BNTX":
        result["found"] = relative
        result["error"] = f"{relative} is not a BNTX"
        return result
    try:
        decoded = list(read_bntx(blob))
    except BntxError as exc:
        result["found"] = relative
        result["error"] = str(exc)
        return result
    result["found"] = relative
    result["images"] = len(decoded)
    picked, why = pick_image(decoded, name)
    if picked is None:
        failed = next((image for image in decoded if image.note), None)
        result["error"] = why or (failed.note if failed else "BNTX has no usable image")
        if failed is not None and failed.fmt and "format" not in result["error"]:
            result["error"] = f"{result['error']} (0x{failed.fmt:04X})"
        return result
    image, mode, used_cut, note = _accept(picked, alpha, cutoff)
    result["alpha"] = mode
    result["cutoff"] = used_cut
    result["image"] = image
    if image is None:
        result["error"] = note or why or "texture was not usable"
    elif note:
        result["error"] = note
    elif why:
        result["error"] = why
    return result


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


def _accept(image, alpha: str, cutoff: float):
    if image is None or not image.rgba:
        return None, alpha, cutoff, (image.note if image is not None else "") or "texture did not decode"
    fraction = _magenta_fraction(image.rgba)
    if fraction >= 0.85:
        return None, alpha, cutoff, "decoded image is a magenta placeholder"
    note = image.note or ""
    rgba = image.rgba
    mode = alpha
    if fraction >= 0.02:
        rgba = _punch_magenta(rgba)
        if mode == "opaque":
            mode = "mask"
            cutoff = 0.5 if cutoff == 0.5 or alpha == "opaque" else cutoff
        extra = "magenta texels were punched out and drawn as cutout"
        note = f"{note}. {extra}".strip(". ")
    if mode == "mask":
        _low, high = _alpha_span(rgba)
        if high < 8:
            mode = "opaque"
            note = f"{note}. alpha channel is empty, so cutout was not applied".strip(". ")
    if rgba is not image.rgba or (note and note != image.note):
        image = replace(image, rgba=rgba, note=note)
    return image, mode, cutoff, note


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
        if alpha >= 200 and red >= 235 and green <= 25 and blue >= 235:
            hit += 1
    return hit / seen if seen else 0.0


def _punch_magenta(rgba: bytes) -> bytes:
    out = bytearray(rgba)
    for at in range(0, len(out) - 3, 4):
        if out[at] >= 235 and out[at + 1] <= 25 and out[at + 2] >= 235 and out[at + 3] >= 200:
            out[at + 3] = 0
    return bytes(out)


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
