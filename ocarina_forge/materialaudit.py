"""Group every model material by the samplers, attributes, and render state it uses.

``python -m ocarina_forge material-audit`` walks ``Model/*.bfres`` and
``Model/*.bfres.zs``. It reads the material header and the attribute names,
not the vertex buffer and not the texels. A parsed header is cached, and the
files are read in parallel. A texture is missing when no ``Tex/`` file has
that stem. The groups are sorted by how many materials share them, so the
shape the viewer still draws wrong comes first.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ocarina_forge.blend import sampler_indices
from ocarina_forge.formats.bfres import FresError, _File, bind_role
from ocarina_forge.formats.zsio import decompress, is_zstd
from ocarina_forge.models import _WATER_TOKENS, _stem, _tex_index, cache_dir

_VERSION = "audit-1"
_HANDLED_ALBEDO = {
    "a0",
    "a1",
    "alb",
    "alb0",
    "alb1",
    "albedo",
    "albedo0",
    "albedo1",
    "basecolor",
    "basecolor0",
    "diffuse",
    "diffuse0",
    "d0",
    "col0",
    "bc0",
    "color0",
    "base",
    "color",
}
_HANDLED_NORMAL = {"n0", "nrm", "nrm0", "normal", "normal0"}
_GLASS = {"glass", "crystal", "window"}
_EXAMPLE_LIMIT = 4


def sketch(data: bytes) -> dict:
    """Material headers for one BFRES. Vertices and embedded textures stay unread."""
    reader = _File(data)
    return {"version": reader.major, "models": _headers(reader)}


def _headers(reader) -> list[dict]:
    keys = reader.dict_keys(reader.model_dict)
    if not keys or not reader.model_values:
        raise FresError("the BFRES has no models")
    found = []
    at = reader.model_values
    for key in keys:
        item, at = _header_model(reader, at, key)
        found.append(item)
    return found


def _header_model(reader, at: int, fallback: str) -> tuple[dict, int]:
    if reader.data[at : at + 4] != b"FMDL":
        raise FresError(f"expected a model at {at:#x}")
    cursor = at + 4
    cursor += 4 if reader.major >= 9 else 12
    name = reader.string(reader.u64(cursor)) or fallback
    cursor += 16  # name, path
    cursor += 8  # skeleton
    cursor += 8  # vertex-buffer array
    shape_values = reader.u64(cursor)
    cursor += 8
    shape_dict = reader.u64(cursor)
    cursor += 8
    if reader.major >= 10:
        mat_values = reader.u64(cursor)
        cursor += 8
        mat_dict = reader.u64(cursor)
        cursor += 8
        cursor += 8
    elif reader.major >= 9:
        mat_values = reader.u64(cursor)
        cursor += 8
        maybe = reader.u64(cursor)
        cursor += 8
        mat_dict = reader.u64(cursor)
        cursor += 8
        if not mat_dict:
            mat_dict = maybe
    else:
        mat_values = reader.u64(cursor)
        cursor += 8
        mat_dict = reader.u64(cursor)
        cursor += 8
    cursor += 16  # user data
    cursor += 8  # user pointer
    material_count = reader.u16(cursor + 4)
    cursor += 6
    cursor += (2 + 2 + 2 + 4) if reader.major >= 9 else (2 + 4 + 4)
    end = cursor
    materials = reader._materials(mat_values, mat_dict, material_count)
    by_material: dict[int, list[str]] = {}
    shape_at = shape_values
    for _index, _shape_name in enumerate(reader.dict_keys(shape_dict)):
        material_index, attributes, shape_at = _header_shape(reader, shape_at)
        if material_index is None:
            continue
        bucket = by_material.setdefault(material_index, [])
        for attribute in attributes:
            if attribute not in bucket:
                bucket.append(attribute)
    rows = [_material_header(material, by_material.get(index) or []) for index, material in enumerate(materials)]
    return {"name": name, "materials": rows}, end


def _header_shape(reader, at: int) -> tuple[int | None, list[str], int]:
    if at + 4 > len(reader.data) or reader.data[at : at + 4] != b"FSHP":
        raise FresError(f"expected a shape at {at:#x}")
    cursor = at + 4
    cursor += 4 if reader.major >= 9 else 12
    cursor += 8  # name
    vtx = reader.u64(cursor)
    cursor += 8
    cursor += 8  # mesh
    cursor += 8  # skin indices
    cursor += 16  # key shapes
    cursor += 8  # bounds
    cursor += 8  # radius
    cursor += 8  # user pointer
    if reader.major < 9:
        cursor += 4
    cursor += 2  # section
    material_index = reader.u16(cursor)
    cursor += 2
    cursor += 2  # bone
    cursor += 2  # fvtx index
    cursor += 2  # skin bone count
    cursor += 1  # skin count
    mesh_count = reader.data[cursor]
    cursor += 1
    cursor += 1  # key shapes
    cursor += 1  # target attributes
    cursor += 2 if reader.major <= 3 or reader.major >= 9 else 6
    standard = bool(vtx and mesh_count and vtx + 4 <= len(reader.data) and reader.data[vtx : vtx + 4] == b"FVTX")
    names = _attribute_names(reader, vtx) if standard else []
    return material_index, names, cursor


def _attribute_names(reader, at: int) -> list[str]:
    if at + 0x40 > len(reader.data) or reader.data[at : at + 4] != b"FVTX":
        return []
    cursor = at + 4
    cursor += 4 if reader.major >= 9 else 12
    attr_at = reader.u64(cursor)
    cursor += 16
    cursor += 8  # memory pool
    cursor += 8  # runtime buffer
    if reader.major > 2:
        cursor += 8
    cursor += 8  # buffer sizes
    cursor += 8  # strides
    cursor += 8
    cursor += 4  # buffer offset
    count = reader.data[cursor]
    names = []
    pos = attr_at
    for _index in range(min(count, 32)):
        if pos + 16 > len(reader.data):
            break
        text = reader.string(reader.u64(pos))
        if text:
            names.append(text)
        pos += 16
    return names


def _material_header(material, attributes: list[str]) -> dict:
    blocks = []
    for block in material.shader_blocks or []:
        blocks.append(
            {
                "slot": block.get("slot") or "",
                "attributeNames": list(block.get("attributeNames") or []),
                "samplerNames": list(block.get("samplerNames") or []),
                "samplerIndices": list(block["samplerIndices"]) if block.get("samplerIndices") is not None else None,
                "byteMap": [int(item.get("value") or 0) for item in (block.get("byteMap") or []) if isinstance(item, dict)],
            }
        )
    return {
        "name": material.name,
        "binds": [{"sampler": sampler, "texture": texture} for sampler, texture in material.binds],
        "alpha": material.alpha,
        "renderState": material.render_state,
        "doubleSided": bool(material.double_sided),
        "archive": material.archive,
        "shader": material.shader,
        "assign": dict(material.assign),
        "options": dict(material.options),
        "vertexAttributes": list(attributes),
        "shaderBlocks": blocks,
        "slots": list(material.slots),
    }


def audit(dump, *, workers: int | None = None) -> dict:
    """One report for every model under ``Model/``. ``workers`` 1 stays in this process."""
    folder = dump.romfs / "Model"
    files = _model_files(folder)
    stems = set(_tex_index(dump))
    jobs = [_job(dump.romfs, path) for path in files]
    parsed = _read_all(dump, jobs, workers)
    return _report(parsed, stems)


def render_text(report: dict) -> str:
    """One line per sampler-role signature. ``--json`` is the full split."""
    lines = [
        "material-audit",
        f"models {report.get('models') or 0}  materials {report.get('materials') or 0}  "
        f"groups {len(report.get('groups') or [])}  failed {len(report.get('failed') or [])}",
    ]
    groups = report.get("groups") or []
    if not groups:
        lines.append("no materials")
    for group in groups:
        signature = group.get("signature") or "(no samplers)"
        splits = _split_phrase(group.get("splits") or {})
        lines.append(
            f"{group.get('count') or 0}  {group.get('modelCount') or 0} models  {signature}{splits}"
        )
    lines.append("unhandled")
    flags = report.get("unhandled") or []
    if not flags:
        lines.append("  none")
    for flag in flags:
        name = f" {flag['name']}" if flag.get("name") else ""
        lines.append(f"  {flag.get('count') or 0}  {flag.get('kind') or ''}{name}")
    failed = report.get("failed") or []
    if failed:
        lines.append("failed")
        for item in failed[:8]:
            lines.append(f"  {item.get('file') or '?'}  {item.get('error') or ''}")
        if len(failed) > 8:
            lines.append(f"  ... {len(failed) - 8} more")
    lines.append("")
    return "\n".join(lines)


def _split_phrase(splits: dict) -> str:
    parts = []
    for reason in ("samplers", "attributes", "render", "tags"):
        rows = splits.get(reason) or []
        if len(rows) > 1:
            parts.append(f"{reason} {len(rows)}")
    if not parts:
        return ""
    return "  splits on " + ", ".join(parts)


def _model_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if ".anim." in name:
            continue
        if name.endswith(".bfres") or name.endswith(".bfres.zs"):
            found.append(path)
    found.sort()
    return found


def _job(romfs: Path, path: Path) -> dict:
    relative = path.relative_to(romfs).as_posix()
    try:
        stat = path.stat()
        size, mtime = stat.st_size, stat.st_mtime_ns
    except OSError:
        size, mtime = 0, 0
    token = hashlib.sha256(f"{_VERSION}\0{relative}\0{size}\0{mtime}".encode()).hexdigest()
    return {"path": str(path), "relative": relative, "cache": str(_cache_root() / f"{token}.json")}


def _cache_root() -> Path:
    root = cache_dir() / "material-audit"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_all(dump, jobs: list[dict], workers: int | None) -> list[dict]:
    if not jobs:
        return []
    count = workers if workers is not None else min(8, os.cpu_count() or 1)
    count = max(1, min(count, len(jobs)))
    if count == 1:
        dicts = _dicts(dump)
        return [_read_one(job, dicts) for job in jobs]
    romfs = str(dump.romfs)
    import multiprocessing

    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=count, mp_context=context, initializer=_init_worker, initargs=(romfs,)) as pool:
        return list(pool.map(_read_job, jobs, chunksize=max(1, len(jobs) // (count * 4))))


_WORKER_DICTS = None


def _init_worker(romfs: str) -> None:
    global _WORKER_DICTS
    from ocarina_forge.dump import Dump

    _WORKER_DICTS = _dicts(Dump(romfs))


def _read_job(job: dict) -> dict:
    return _read_one(job, _WORKER_DICTS)


def _dicts(dump):
    try:
        return dump.dicts()
    except Exception:
        return None


def _read_one(job: dict, dicts) -> dict:
    cache = Path(job["cache"])
    if cache.is_file():
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = None
        if isinstance(stored, dict) and stored.get("version") == _VERSION:
            stored["file"] = job["relative"]
            stored["cached"] = True
            return stored
    path = Path(job["path"])
    try:
        raw = path.read_bytes()
        blob = decompress(raw, dicts)[0] if is_zstd(raw) else raw
        if not blob.startswith(b"FRES"):
            raise FresError("not a BFRES")
        body = sketch(blob)
    except Exception as exc:
        return {"file": job["relative"], "error": getattr(exc, "message", None) or str(exc), "cached": False}
    stored = {"version": _VERSION, "file": job["relative"], "models": body.get("models") or [], "cached": False}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(stored), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass
    return stored


def _report(parsed: list[dict], stems: set[str]) -> dict:
    groups: dict[str, dict] = {}
    flags: dict[tuple, dict] = {}
    failed = []
    model_count = 0
    material_count = 0
    for item in parsed:
        if item.get("error"):
            failed.append({"file": item.get("file") or "", "error": item["error"]})
            continue
        for model in item.get("models") or []:
            model_count += 1
            model_name = model.get("name") or ""
            for material in model.get("materials") or []:
                material_count += 1
                record = _classify(material, stems)
                signature = record["signature"]
                group = groups.get(signature)
                if group is None:
                    group = {
                        "signature": signature,
                        "count": 0,
                        "models": set(),
                        "examples": [],
                        "_splits": {"samplers": {}, "attributes": {}, "render": {}, "tags": {}},
                    }
                    groups[signature] = group
                group["count"] += 1
                group["models"].add(item.get("file") or model_name)
                if len(group["examples"]) < _EXAMPLE_LIMIT:
                    group["examples"].append(
                        {"material": material.get("name") or "", "model": model_name, "file": item.get("file") or ""}
                    )
                for reason, value in record["split"].items():
                    bucket = group["_splits"][reason]
                    bucket[value] = bucket.get(value, 0) + 1
                for flag in record["flags"]:
                    slot = flags.get(flag)
                    if slot is None:
                        slot = {"kind": flag[0], "name": flag[1], "count": 0, "examples": []}
                        flags[flag] = slot
                    slot["count"] += 1
                    if len(slot["examples"]) < _EXAMPLE_LIMIT:
                        slot["examples"].append(
                            {"material": material.get("name") or "", "model": model_name, "file": item.get("file") or ""}
                        )
    ordered = sorted(groups.values(), key=lambda row: (-row["count"], row["signature"]))
    for group in ordered:
        group["modelCount"] = len(group.pop("models"))
        group["splits"] = {
            reason: [
                {"value": value, "count": count}
                for value, count in sorted(bucket.items(), key=lambda item: (-item[1], item[0]))
            ]
            for reason, bucket in group.pop("_splits").items()
        }
    unhandled = sorted(flags.values(), key=lambda row: (-row["count"], row["kind"], row["name"]))
    return {
        "models": model_count,
        "materials": material_count,
        "failed": failed,
        "groups": ordered,
        "unhandled": unhandled,
    }


def _classify(material: dict, stems: set[str]) -> dict:
    binds = material.get("binds") or []
    shown = [str(item.get("sampler") or "") for item in binds if item.get("sampler")]
    attributes = " ".join(material.get("vertexAttributes") or []) or "(none)"
    alpha = material.get("alpha") or "opaque"
    state = material.get("renderState") or ""
    sided = "double" if material.get("doubleSided") else "single"
    render = alpha if not state or state == alpha else f"{alpha} {state}"
    render = f"{render} {sided}"
    tags = _tags(material, shown)
    return {
        "signature": _signature(shown),
        "split": {
            "samplers": "+".join(shown) if shown else "(none)",
            "attributes": attributes,
            "render": render,
            "tags": " · ".join(tags) if tags else "(none)",
        },
        "flags": _flags(material, stems),
    }


_ROLE_ORDER = (
    "albedo",
    "normal",
    "roughness",
    "metalness",
    "ao",
    "rah",
    "rmh",
    "emissive",
    "bake",
    "fx",
    "thickness",
    "gradient",
    "slt",
    "terrain",
)


def _signature(samplers: list[str]) -> str:
    """The role set, so ``_a0+_n0+_rgh0+bake0+bake1`` is one group whatever the attributes are."""
    roles = []
    for name in samplers:
        role = _sampler_role(name)
        if role not in roles:
            roles.append(role)
    roles.sort(key=lambda role: (_ROLE_ORDER.index(role) if role in _ROLE_ORDER else len(_ROLE_ORDER), role))
    return "+".join(roles) if roles else "(no samplers)"


def _sampler_role(name: str) -> str:
    token = _token(name)
    if token.startswith("bake"):
        return "bake"
    if token.startswith("rgh") or token.startswith("rough"):
        return "roughness"
    if token.startswith("rah"):
        return "rah"
    if token.startswith("rmh"):
        return "rmh"
    if token.startswith("mt") or token.startswith("metal"):
        return "metalness"
    if token.startswith("ao"):
        return "ao"
    if token.startswith(("nrm", "normal")) or (token.startswith("n") and token[1:].isdigit()) or token == "n":
        return "normal"
    if token.startswith(("emiss", "emit", "em", "ec")):
        return "emissive"
    if token.startswith("fx"):
        return "fx"
    if token.startswith("th"):
        return "thickness"
    if token.startswith("gn"):
        return "gradient"
    if token.startswith("slt"):
        return "slt"
    if token in {"ta", "tc"}:
        return "terrain"
    if token.startswith("a") and (token[1:].isdigit() or token.startswith(("alb", "albedo"))):
        return "albedo"
    if token in {"alb", "albedo", "basecolor", "diffuse", "color", "base"}:
        return "albedo"
    return token or "other"


def _tags(material: dict, samplers: list[str]) -> list[str]:
    tokens = {_token(name) for name in samplers}
    names = " ".join(
        [
            material.get("name") or "",
            material.get("shader") or "",
            material.get("archive") or "",
            " ".join(str(value) for value in (material.get("options") or {}).values()),
        ]
    ).lower()
    tags = []
    if "a0" in tokens and "a1" in tokens:
        tags.append("blend")
    elif "a0" in tokens or "alb0" in tokens or "albedo0" in tokens:
        tags.append("a0")
    if any(token.startswith("bake") for token in tokens) or "bake" in names:
        tags.append("bake")
    if names.replace("_", "").find("manualuv") >= 0:
        tags.append("ManualUV")
    words = set(_words(names))
    if words & _GLASS:
        tags.append("glass")
    if words & _WATER_TOKENS and "npc" not in words:
        tags.append("water")
    if any(bind_role(name) == "emission" for name in samplers) or "emiss" in names or "emit" in names:
        tags.append("emissive")
    alpha = material.get("alpha") or ""
    state = (material.get("renderState") or "").lower()
    if alpha == "mask" or state == "mask":
        tags.append("alpha mask")
    if alpha == "blend" or state == "translucent":
        tags.append("translucent")
    return tags


def _flags(material: dict, stems: set[str]) -> list[tuple[str, str]]:
    found = []
    seen = set()

    def add(kind: str, name: str) -> None:
        flag = (kind, name)
        if flag not in seen:
            seen.add(flag)
            found.append(flag)

    indices = _indices(material)
    for item in material.get("binds") or []:
        sampler = str(item.get("sampler") or "")
        texture = str(item.get("texture") or "")
        if sampler in indices and indices[sampler] == 0xFF:
            add("unused sampler", sampler)
        elif sampler and _unhandled(sampler):
            add("unknown sampler", sampler)
        if texture and not _neutral_texture(texture) and _stem(texture).lower() not in stems:
            add("missing texture", texture)
    if _unparsed(material):
        add("unparsed assign", "")
    return found


def _indices(material: dict) -> dict[str, int]:
    merged: dict[str, int] = {}
    for block in material.get("shaderBlocks") or []:
        if block.get("slot") not in (None, "", "+0x10"):
            continue
        prepared = dict(block)
        raw = prepared.get("byteMap") or []
        if raw and not isinstance(raw[0], dict):
            prepared["byteMap"] = [{"value": int(value)} for value in raw]
        merged.update(sampler_indices(prepared))
    return merged


def _unhandled(sampler: str) -> bool:
    role = bind_role(sampler)
    token = _token(sampler)
    if role == "albedo":
        return token not in _HANDLED_ALBEDO
    if role == "normal":
        return token not in _HANDLED_NORMAL
    if role == "emission":
        return False
    return True


def _unparsed(material: dict) -> bool:
    blocks = material.get("shaderBlocks") or []
    primary = next((block for block in blocks if block.get("slot") in (None, "", "+0x10")), None)
    if primary and (primary.get("samplerNames") or primary.get("attributeNames")):
        return False
    if material.get("assign"):
        return False
    if blocks or material.get("binds"):
        return True
    return False


def _neutral_texture(texture: str) -> bool:
    """CmnTex_BakeLightDefault and Dummy* are placeholders, not missing files."""
    stem = _stem(texture).lower()
    return "default" in stem or "dummy" in stem


def _token(name: str) -> str:
    return "".join(char for char in (name or "").lower() if char.isalnum())


def _words(text: str) -> list[str]:
    found = []
    token = []
    for char in text:
        if char.isalnum():
            token.append(char)
        elif token:
            found.append("".join(token))
            token = []
    if token:
        found.append("".join(token))
    return found
