"""Print the shader assign and uniforms for one model, without writing a glb.

``python -m bancwright material-debug Land_HrlCastle_Corridor_Garden_A_01``

The report is what this reader stores on each material: the shader archive
and shading model, the attribute assign (``_u0`` / ``_u1`` / ``_u2`` are UV
channels), the sampler assign, the option strings, and the shader parameters.
Each sampler line names the UV channel when the assign says one, the texture
SRT whose name matches that sampler, and the options that look like a
projection mode. None of that is applied to the mesh. Stretched roof tiles
and the long garden beds stay as they are until this report comes back.
"""

from __future__ import annotations

import re

from bancwright.formats.bfres import bind_role, inspect, parse
from bancwright.modeldebug import load_bytes

_UV_NAME = re.compile(r"^(?:_?u\d|_?uv\d|texcoord\d|uvset\d)$", re.I)
_PROJECTION = re.compile(r"uv|coord|project|triplanar|world|manual|planar|sphere|cube", re.I)


def inspect_materials(dump, target: str) -> dict:
    source, blob = load_bytes(dump, target)
    header = inspect(blob)
    models = parse(blob) if not header.get("error") else []
    rows = []
    for model in models:
        for material in model.materials:
            rows.append(_row(model.name, material))
    return {
        "target": target,
        "source": source,
        "version": header.get("version"),
        "error": header.get("error") or "",
        "materials": rows,
    }


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


def _row(model: str, material) -> dict:
    lines = sampler_lines(material)
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
    }


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
    lines.append("")
    return "\n".join(lines)


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
