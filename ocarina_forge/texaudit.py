"""Flag textures whose mip-0 size implies a different block height than usual.

``python -m ocarina_forge tex-audit`` reads BNTX headers under ``Tex/``.
It does not decode texels. A row is flagged when the lowest log2 whose
padded surface equals mip 0 is not ``default_block_log2`` for that image.
The decoder still uses textureLayout. The flag is how to find every
texture in the same situation as the castle brick walls.
"""

from __future__ import annotations

from ocarina_forge.errors import BancError
from ocarina_forge.formats.bntx import texture_headers


def disagreement(header: dict) -> dict | None:
    """One flagged texture, or None when the size guess matches the usual height."""
    inferred = header.get("inferredBlockHeightLog2")
    usual = header.get("defaultBlockHeightLog2")
    if inferred is None or usual is None or inferred == usual:
        return None
    surfaces = header.get("surfaces") or {}
    return {
        "name": header.get("name") or "",
        "format": header.get("formatName") or "",
        "width": header.get("width"),
        "height": header.get("height"),
        "footprint": _footprint(header),
        "storedLog2": header.get("blockHeightLog2"),
        "inferredLog2": inferred,
        "usualLog2": usual,
        "mip0": int(header.get("mip0Size") or 0),
        "usualBytes": surfaces.get(usual),
        "textureLayout": header.get("textureLayout"),
        "sizeRange": header.get("sizeRange"),
    }


def audit_blob(data: bytes, path: str = "") -> list[dict]:
    """Flagged images in one BNTX. ``path`` is copied onto each row."""
    rows = []
    for header in texture_headers(data):
        row = disagreement(header)
        if row is None:
            continue
        row["path"] = path
        rows.append(row)
    return rows


def audit(dump) -> dict:
    """Every flagged texture under ``Tex/``. Headers only."""
    folder = dump.romfs / "Tex"
    flagged = []
    checked = 0
    errors = []
    if not folder.is_dir():
        return {"checked": 0, "flagged": [], "errors": [f"{folder} is not in this dump"]}
    paths = sorted(folder.glob("*.bntx")) + sorted(folder.glob("*.bntx.zs"))
    for file in paths:
        relative = file.relative_to(dump.romfs).as_posix()
        try:
            blob, _dict_id = dump.read_file(relative)
        except (BancError, OSError, ValueError) as exc:
            errors.append(f"{relative}: {getattr(exc, 'message', None) or exc}")
            continue
        checked += 1
        try:
            flagged.extend(audit_blob(blob, relative))
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")
    flagged.sort(key=lambda row: (row.get("path") or "", row.get("name") or ""))
    return {"checked": checked, "flagged": flagged, "errors": errors}


def render_text(report: dict) -> str:
    flagged = report.get("flagged") or []
    lines = [
        "tex-audit",
        f"checked {report.get('checked') or 0}  flagged {len(flagged)}",
    ]
    if not flagged:
        lines.append("no texture whose mip-0 size implies a different block height than usual")
    for row in flagged:
        footprint = row.get("footprint") or ""
        lines.append(
            f"{row.get('name') or '?'}  {row.get('width')}x{row.get('height')} {row.get('format') or ''} {footprint}  "
            f"stored log2 {row.get('storedLog2')}  inferred log2 {row.get('inferredLog2')}  "
            f"usual log2 {row.get('usualLog2')} ({row.get('usualBytes')} bytes)  "
            f"mip0 {row.get('mip0')}  {row.get('path') or ''}"
        )
    for error in report.get("errors") or []:
        lines.append(f"error {error}")
    lines.append("")
    return "\n".join(lines)


def _footprint(header: dict) -> str:
    width = header.get("blockWidth")
    height = header.get("blockHeightPixels")
    if not width or not height:
        return ""
    return f"{width}x{height}"
