"""Print the shader assign and uniforms for one model, without writing a glb.

``python -m ocarina_forge material-debug Land_HrlCastle_Corridor_Garden_A_01``

The report is what this reader stores on each material: the shader archive
and shading model, the attribute assign (``_u0`` / ``_u1`` / ``_u2`` are UV
channels), the sampler assign, the option strings, and the shader parameters.
Each sampler line names the UV channel when the assign says one, the texture
SRT whose name matches that sampler, and the options that look like a
projection mode. A retail v10 ``+0x10`` block is printed as attribute names,
sampler names, and the byte index table (``0xFF`` unused). ``+0xA8`` is
printed the same way. Each shape lists every vertex attribute and the UV
sets, with format and min/max. The ranked albedo is the texture the viewer
binds, with its pixel size when the Tex file is in the dump. ``--strings``
prints the whole string pool. A dump-relative ``Model/...bfres.zs`` path is
that file. None of that is applied to the mesh.
"""

from __future__ import annotations

import re

from ocarina_forge.formats.bfres import bind_role, bfres_strings, inspect, interesting_strings, parse
from ocarina_forge.modeldebug import load_bytes

_UV_NAME = re.compile(r"^(?:_?u\d|_?uv\d|texcoord\d|uvset\d)$", re.I)
_PROJECTION = re.compile(r"uv|coord|project|triplanar|world|manual|planar|sphere|cube", re.I)


def inspect_materials(dump, target: str, *, strings: bool = False) -> dict:
    source, blob = load_bytes(dump, target)
    header = inspect(blob)
    models = parse(blob) if not header.get("error") else []
    rows = []
    for model in models:
        for material in model.materials:
            rows.append(_row(model.name, material, dump))
    report = {
        "target": target,
        "source": source,
        "version": header.get("version"),
        "error": header.get("error") or "",
        "materials": rows,
        "shapes": _shapes(models),
        "strings": interesting_strings(blob) if not header.get("error") else [],
    }
    if strings and not header.get("error"):
        report["stringPool"] = bfres_strings(blob)
    return report


def name_hints(name: str) -> list[str]:
    token = (name or "").lower().replace("_", "")
    hints = []
    if "manualuv" in token:
        hints.append("ManualUV")
    return hints


def projection_options(options: dict) -> dict:
    """Options whose name or value talks about UVs or a projection."""
    found = {}
    for key, value in (options or {}).items():
        if _PROJECTION.search(str(key)) or _PROJECTION.search(str(value)):
            found[key] = value
    return found


def sampler_lines(material) -> list[dict]:
    """One row per sampler: role, UV channel from the assign, and a matching SRT."""
    assign = getattr(material, "assign", {}) or {}
    attributes = getattr(material, "attributes", {}) or {}
    params = getattr(material, "params", []) or []
    albedo = set(getattr(material, "albedo", []) or [])
    rows = []
    for slot, tex in getattr(material, "binds", []) or []:
        role = bind_role(slot, tex)
        uv, source = _uv_for(slot, assign, attributes)
        srt = _srt_for(slot, role, params)
        rows.append(
            {
                "sampler": slot,
                "texture": tex,
                "role": role,
                "albedo": (slot, tex) in albedo,
                "uv": uv,
                "uvSource": source,
                "srt": srt,
            }
        )
    return rows


def _uv_for(slot: str, assign: dict, attributes: dict) -> tuple[str, str]:
    if slot in assign and _uv_name(assign[slot]):
        return assign[slot], "sampler assign"
    for key, target in assign.items():
        if target == slot and _uv_name(key):
            return key, "sampler assign"
    token = _alnum(slot)
    for key, value in attributes.items():
        if token and len(token) >= 2 and token in _alnum(key) and _uv_name(value):
            return value, "attribute assign"
        if token and len(token) >= 2 and token in _alnum(value) and _uv_name(key):
            return key, "attribute assign"
    return "", ""


def _srt_for(slot: str, role: str, params: list) -> dict | None:
    token = _alnum(slot)
    role_word = role if role in {"albedo", "normal", "emission", "mask"} else ""
    matches = []
    for item in params:
        if item.get("kind") != "texsrt":
            continue
        name = _alnum(str(item.get("name") or ""))
        if (token and len(token) >= 2 and token in name) or (role_word and role_word in name):
            matches.append(item)
    if len(matches) == 1:
        return matches[0]
    return None


def _uv_name(text: str) -> bool:
    return bool(_UV_NAME.fullmatch(str(text or "")))


def _alnum(text: str) -> str:
    return "".join(char for char in str(text or "").lower() if char.isalnum())


def _row(model: str, material, dump=None) -> dict:
    lines = sampler_lines(material)
    albedo = _viewer_albedo(dump, material)
    return {
        "model": model,
        "name": material.name,
        "hints": name_hints(material.name),
        "shader": material.shader,
        "archive": material.archive,
        "options": material.options,
        "projection": projection_options(material.options),
        "assign": material.assign,
        "attributes": material.attributes,
        "binds": lines,
        "alpha": material.alpha,
        "cutoff": material.cutoff,
        "doubleSided": material.double_sided,
        "renderState": material.render_state,
        "states": [text for text in material.states if text],
        "renderInfo": material.render_info,
        "params": material.params,
        "baseColor": list(material.base_color) if material.base_color else None,
        "note": material.note,
        "slots": list(getattr(material, "slots", []) or []),
        "shaderBlocks": list(getattr(material, "shader_blocks", []) or []),
        "viewerAlbedo": albedo,
    }


def _viewer_albedo(dump, material) -> dict:
    """The texture rank_albedo hands the viewer, and its size when a file resolves.

    Sibling borrow is not used. A missing Tex file stays missing.
    """
    chosen = list(getattr(material, "albedo", []) or [])[:1]
    if not chosen:
        return {"sampler": "", "texture": "", "size": "", "path": "", "note": "no albedo sampler"}
    slot, name = chosen[0]
    info = {"sampler": slot, "texture": name, "size": "", "path": "", "note": ""}
    if dump is None:
        info["note"] = "no dump, size not read"
        return info
    from ocarina_forge.formats.bntx import image_sizes
    from ocarina_forge.models import _resolve_tex

    path, tried = _resolve_tex(dump, name, [])
    if not path:
        info["note"] = "no Tex file matched"
        info["tried"] = tried[:6]
        return info
    from ocarina_forge.errors import BancError

    try:
        blob, _dict_id = dump.read_file(path)
    except (BancError, OSError, ValueError) as exc:
        info["path"] = path
        info["note"] = getattr(exc, "message", None) or str(exc)
        return info
    sizes = image_sizes(blob)
    info["path"] = path
    wanted = (name or "").lower()
    match = next((item for item in sizes if (item.get("name") or "").lower() == wanted), None)
    if match is None and len(sizes) == 1:
        match = sizes[0]
    if match and match.get("width") and match.get("height"):
        info["size"] = f"{match['width']}x{match['height']}"
        image_name = match.get("name") or ""
        if image_name and image_name.lower() != wanted:
            info["note"] = f"image {image_name}"
    else:
        info["note"] = "BNTX has no size"
    return info


def _shapes(models) -> list[dict]:
    """Near LOD only. The ranges are the vertex buffer, before seam splitting."""
    rows = []
    seen = set()
    for model in models:
        for part in model.parts:
            if part.lod:
                continue
            key = (model.name, part.name, part.material)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "model": model.name,
                    "name": part.name,
                    "material": part.material,
                    "uvs": list(getattr(part, "uv_sets", []) or []),
                    "attributes": list(getattr(part, "vertex_attributes", []) or []),
                }
            )
    return rows


def render_text(report: dict) -> str:
    lines = [
        "material-debug",
        f"version {report.get('version') or '?'}",
        f"source {report.get('source') or report.get('target') or ''}",
    ]
    if report.get("error"):
        lines.append(f"error {report['error']}")
    materials = report.get("materials") or []
    if not materials:
        lines.append("no materials")
    for row in materials:
        lines.append("")
        lines.append(row.get("name") or "(unnamed)")
        if row.get("model"):
            lines.append(f"  model {row['model']}")
        shader = row.get("shader") or ""
        archive = row.get("archive") or ""
        if shader or archive:
            lines.append(f"  shader {shader or '(no shading model)'}")
            if archive:
                lines.append(f"  archive {archive}")
        else:
            lines.append("  shader (no shader assign in this file)")
        for block in row.get("shaderBlocks") or []:
            lines.extend(_shader_block_lines(block))
        slots = row.get("slots") or []
        if slots:
            lines.append("  header")
            for slot in slots:
                lines.append(f"    {slot}")
        if row.get("hints"):
            lines.append("  name hint " + ", ".join(row["hints"]))
        attributes = row.get("attributes") or {}
        if attributes:
            lines.append("  attributes")
            for key, value in attributes.items():
                lines.append(f"    {key} = {value}")
        else:
            lines.append("  attributes (none)")
        options = row.get("options") or {}
        if options:
            lines.append("  options")
            for key, value in options.items():
                lines.append(f"    {key} = {value}")
        else:
            lines.append("  options (none)")
        projection = row.get("projection") or {}
        if projection:
            lines.append("  projection")
            for key, value in projection.items():
                lines.append(f"    {key} = {value}")
        else:
            lines.append("  projection (no uv or projection option)")
        alpha = row.get("alpha") or "opaque"
        cutoff = row.get("cutoff")
        sided = "double" if row.get("doubleSided") else "single"
        extra = f"  cutoff {cutoff:.2f}" if alpha == "mask" and isinstance(cutoff, (int, float)) else ""
        lines.append(f"  alpha {alpha}  {sided}{extra}")
        if row.get("renderState"):
            lines.append(f"  render state {row['renderState']}")
        states = row.get("states") or []
        if states:
            shown = ", ".join(f"{index}:{text}" for index, text in enumerate(states) if text)
            if shown:
                lines.append(f"  states {shown}")
        binds = row.get("binds") or []
        if binds:
            lines.append("  samplers")
            for item in binds:
                mark = "  used as albedo" if item.get("albedo") else ""
                if item.get("uv"):
                    uv = f"  uv {item['uv']} ({item.get('uvSource') or 'assign'})"
                else:
                    uv = "  uv (not in the shader assign)"
                srt = f"  srt {_param_line(item['srt'])}" if item.get("srt") else "  srt (none named for this sampler)"
                lines.append(
                    f"    {item.get('sampler') or '?'} = {item.get('texture') or '?'}  {item.get('role') or 'other'}{mark}{uv}{srt}"
                )
        else:
            lines.append("  samplers (none)")
        albedo = row.get("viewerAlbedo") or {}
        if albedo.get("texture") or albedo.get("note"):
            bits = [f"viewer albedo  {albedo.get('sampler') or '?'} = {albedo.get('texture') or '?'}"]
            if albedo.get("size"):
                bits.append(albedo["size"])
            if albedo.get("path"):
                bits.append(albedo["path"])
            if albedo.get("note"):
                bits.append(f"({albedo['note']})")
            lines.append("  " + "  ".join(bits))
        info = row.get("renderInfo") or []
        if info:
            lines.append("  render info")
            for item in info:
                lines.append(f"    {item.get('name')} = {_brief(item.get('value'))}")
        params = row.get("params") or []
        if params:
            lines.append("  params")
            for item in params:
                lines.append(f"    {_param_line(item)}")
        else:
            lines.append("  params (none)")
        if row.get("baseColor"):
            lines.append("  base color " + " ".join(f"{channel:.3f}" for channel in row["baseColor"]))
        if row.get("note"):
            lines.append(f"  note {row['note']}")
    shapes = report.get("shapes") or []
    if shapes:
        lines.append("")
        lines.append("shapes")
        for shape in shapes:
            title = shape.get("name") or "(shape)"
            material = shape.get("material") or ""
            lines.append(f"  {title}" + (f"  material {material}" if material else ""))
            uvs = shape.get("uvs") or []
            if not uvs:
                lines.append("    uv (none)")
            for item in uvs:
                lines.append("    " + _attr_line(item, uv=True))
                for sample in item.get("samples") or []:
                    lines.append("      " + _sample_line(sample))
            attributes = shape.get("attributes") or []
            if attributes:
                lines.append("    attributes")
                for item in attributes:
                    lines.append("      " + _attr_line(item, uv=False))
                    if _UV_NAME.fullmatch(str(item.get("name") or "")):
                        continue
                    for sample in (item.get("samples") or [])[:4]:
                        lines.append("        " + _sample_line(sample))
    strings = report.get("strings") or []
    lines.append("")
    lines.append("strings")
    if not strings:
        lines.append("  (no gsys, texcoord, uv_, o_, enable_, or shader-archive name)")
    for item in strings[:80]:
        refs = " ".join(f"{ref:#x}" for ref in (item.get("refs") or [])[:6])
        lines.append(f"  {item.get('offset', 0):#x} {item.get('text') or ''}  refs {refs or '(none)'}")
    if len(strings) > 80:
        lines.append(f"  ... {len(strings) - 80} more")
    pool = report.get("stringPool")
    if isinstance(pool, list):
        lines.append("")
        lines.append(f"string pool ({len(pool)})")
        shown = pool[:2000]
        for item in shown:
            lines.append(f"  {item.get('offset', 0):#x} {item.get('text') or ''}")
        if len(pool) > len(shown):
            lines.append(f"  ... {len(pool) - len(shown)} more")
    lines.append("")
    return "\n".join(lines)


def _shader_block_lines(block: dict) -> list[str]:
    slot = block.get("slot") or "?"
    at = block.get("at") or 0
    lines = [f"  shader block {slot} at {at:#x} (not applied to the mesh)"]
    labels = block.get("labels") or []
    if labels:
        lines.append("    names " + ", ".join(str(name) for name in labels))
    attributes = block.get("attributeNames") or []
    if attributes:
        lines.append("    attribute names " + ", ".join(str(name) for name in attributes))
    samplers = block.get("samplerNames") or []
    if samplers:
        lines.append("    sampler names " + ", ".join(str(name) for name in samplers))
    byte_map = block.get("byteMap") or []
    if byte_map:
        shown = " ".join("ff" if item.get("unused") else f"{int(item.get('value') or 0):02x}" for item in byte_map)
        lines.append(f"    byte map {shown}")
        if block.get("byteNote"):
            lines.append(f"    {block['byteNote']}")
    for child in block.get("children") or []:
        dwords = " ".join(f"{int(value):08x}" for value in child.get("dwords") or [])
        lines.append(f"    +{int(child.get('offset') or 0):#x} {child.get('text') or ''}  dwords {dwords}".rstrip())
    return lines


def _attr_line(item: dict, *, uv: bool) -> str:
    low = item.get("min") or []
    high = item.get("max") or []
    if uv and len(low) >= 2 and len(high) >= 2:
        span = f"u {low[0]}..{high[0]}  v {low[1]}..{high[1]}"
    else:
        span = f"min {_nums(low)}  max {_nums(high)}"
    return f"{item.get('name') or '?'}  {item.get('format') or 'format ?'}  {span}  n={item.get('count') or 0}"


def _sample_line(sample: dict) -> str:
    decoded = sample.get("decoded")
    shown = " ".join(str(value) for value in decoded) if isinstance(decoded, list) else "not decoded"
    return f"[{sample.get('index')}] raw {sample.get('raw') or ''}  decoded {shown}"


def _nums(values) -> str:
    return " ".join(str(value) for value in values)


def _param_line(item: dict) -> str:
    name = item.get("name") or "?"
    kind = item.get("kind") or ""
    if kind == "texsrt":
        scale = " ".join(str(value) for value in item.get("scale") or [])
        translate = " ".join(str(value) for value in item.get("translate") or [])
        return f"{name}  texsrt {item.get('mode')}  scale {scale}  rotate {item.get('rotate')}  translate {translate}"
    value = item.get("value")
    if isinstance(value, list):
        shown = " ".join(str(part) for part in value)
    else:
        shown = "" if value is None else str(value)
    return f"{name}  {kind} {shown}".rstrip()


def _brief(value) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return "" if value is None else str(value)
