"""Flag block-height disagreements and score the two GOB orders.

``python -m ocarina_forge tex-audit`` reads every ``Tex/*.bntx[.zs]``.
A block-height row is a texture whose mip-0 size implies a different log2
than the usual height for that image. The decoder still uses textureLayout.

A GOB-order row is every texture. Mip 0 is decoded in the Tegra X1 order and
in the Switch 2 order, and the lower edge score is ``smoother``. The viewer
does not follow that score: a 16-byte element is always Switch 2. ``disagree``
is set when the smoother order is not that rule. See ``docs/textures.md``.
"""

from __future__ import annotations

from ocarina_forge.errors import BancError
from ocarina_forge.formats.bntx import score_gob_orders, texture_headers


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
        "flags": header.get("flags"),
        "swizzleValue": header.get("swizzleValue"),
        "gobOrder": header.get("gobOrder") or "",
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


def order_rows(data: bytes, path: str = "") -> list[dict]:
    """GOB-order scores for one BNTX. ``path`` is copied onto each row."""
    rows = []
    for row in score_gob_orders(data):
        copied = dict(row)
        copied["path"] = path
        copied["disagree"] = _disagrees(copied)
        rows.append(copied)
    return rows


def audit(dump) -> dict:
    """Block-height flags and GOB-order scores for every texture under ``Tex/``."""
    folder = dump.romfs / "Tex"
    flagged = []
    orders = []
    checked = 0
    errors = []
    if not folder.is_dir():
        return {"checked": 0, "flagged": [], "orders": [], "errors": [f"{folder} is not in this dump"]}
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
            orders.extend(order_rows(blob, relative))
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")
    flagged.sort(key=lambda row: (row.get("path") or "", row.get("name") or ""))
    orders.sort(key=lambda row: (row.get("path") or "", row.get("name") or ""))
    return {"checked": checked, "flagged": flagged, "orders": orders, "errors": errors}


def render_text(report: dict) -> str:
    flagged = report.get("flagged") or []
    orders = report.get("orders") or []
    counts = _order_counts(orders)
    lines = [
        "tex-audit",
        f"checked {report.get('checked') or 0}  block-height flagged {len(flagged)}",
        (
            "gob order  "
            f"switch2 {counts['switch2']}  tegra {counts['tegra']}  "
            f"tie {counts['tie']}  unscored {counts['unscored']}  "
            f"disagree {counts['disagree']}"
        ),
    ]
    if not flagged:
        lines.append("no texture whose mip-0 size implies a different block height than usual")
    else:
        lines.append("block height")
    for row in flagged:
        footprint = row.get("footprint") or ""
        lines.append(
            f"{row.get('name') or '?'}  {row.get('width')}x{row.get('height')} {row.get('format') or ''} {footprint}  "
            f"stored log2 {row.get('storedLog2')}  inferred log2 {row.get('inferredLog2')}  "
            f"usual log2 {row.get('usualLog2')} ({row.get('usualBytes')} bytes)  "
            f"mip0 {row.get('mip0')}  gob {row.get('gobOrder') or '?'}  {row.get('path') or ''}"
        )
    if orders:
        lines.append("gob order")
    for row in orders:
        mark = "disagree " if row.get("disagree") else ""
        lines.append(
            f"{mark}{row.get('name') or '?'}  {row.get('width')}x{row.get('height')} "
            f"{row.get('formatName') or ''} {row.get('footprint') or ''}  "
            f"{row.get('bytesPer') or '?'} B  "
            f"flags 0x{int(row.get('flags') or 0):02X}  swizzle 0x{int(row.get('swizzleValue') or 0):04X}  "
            f"decoder {row.get('decoderOrder') or '?'}  smoother {row.get('smoother') or '?'}  "
            f"tegra {_score(row.get('tegra'))}  switch2 {_score(row.get('switch2'))}  "
            f"{row.get('path') or ''}"
        )
        if row.get("error"):
            lines.append(f"  error {row['error']}")
    for error in report.get("errors") or []:
        lines.append(f"error {error}")
    lines.append("")
    return "\n".join(lines)


def _disagrees(row: dict) -> bool:
    """True when the smoother order is not the element-size rule."""
    smoother = row.get("smoother")
    decoder = row.get("decoderOrder")
    if smoother in (None, "", "tie", "unscored", "linear"):
        return False
    if decoder in (None, "", "linear"):
        return False
    return smoother != decoder


def _order_counts(orders: list[dict]) -> dict[str, int]:
    counts = {"switch2": 0, "tegra": 0, "tie": 0, "unscored": 0, "disagree": 0}
    for row in orders:
        smoother = row.get("smoother") or "unscored"
        if smoother not in counts:
            smoother = "unscored"
        counts[smoother] += 1
        if row.get("disagree"):
            counts["disagree"] += 1
    return counts


def _score(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def _footprint(header: dict) -> str:
    width = header.get("blockWidth")
    height = header.get("blockHeightPixels")
    if not width or not height:
        return ""
    return f"{width}x{height}"
