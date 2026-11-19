"""Compare two romfs trees the way a build should match.

Byte-identical files are silent. When the bytes differ, compressed files are
unpacked and BYML, event flows, SARC members and MSBT labels are compared.
A difference that is only compression, or the same parsed value, is explained.
Anything else is a real mismatch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class Diff:
    def __init__(self, path: str, kind: str, detail: str):
        self.path = path
        self.kind = kind  # "explained" or "mismatch"
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.kind}: {self.path}: {self.detail}"


def _files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _tools():
    from bancwright.engine.ato import ensure_imported

    ensure_imported()
    from after_ocarina.paths import require_tools

    tools = str(require_tools())
    import sys

    if tools not in sys.path:
        sys.path.insert(0, tools)
    import byml
    import zstool

    return byml, zstool


def _unpack(data: bytes, dicts):
    from after_ocarina.zsio import unpack

    try:
        return unpack(data, dicts)
    except Exception:
        return None


def _same_byml(left: bytes, right: bytes, byml) -> bool:
    def load(raw: bytes):
        payload = raw
        if raw[:2] == b"BY":
            from after_ocarina.gamedata import to_little_endian

            payload = to_little_endian(raw)
        return byml.loads(payload)

    try:
        return bool(byml.same(load(left), load(right)))
    except Exception:
        return False


def _flow_text(raw: bytes) -> str | None:
    if not raw.startswith(b"EVFL"):
        return None
    try:
        from after_ocarina.evflow import format_flow, read_flow_bytes
    except Exception:
        return None
    try:
        return format_flow(read_flow_bytes(raw))
    except Exception:
        return None


def _msbt_labels(raw: bytes) -> dict[str, str] | None:
    if not raw.startswith(b"MsgStdBn"):
        return None
    try:
        from bancwright.engine.ato import ensure_imported

        ensure_imported()
        from after_ocarina.paths import require_tools
        import sys

        tools = str(require_tools())
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import msbtmod
    except Exception:
        return None
    # msbtmod's public entry is the CLI. A missing helper just leaves the bytes.
    reader = getattr(msbtmod, "read_labels", None) or getattr(msbtmod, "labels", None)
    if reader is None:
        return None
    try:
        return dict(reader(raw))
    except Exception:
        return None


def _compare_payload(rel: str, left: bytes, right: bytes, dicts, byml, sarc_type, into: list[Diff]) -> None:
    if left == right:
        return
    unpacked_left = _unpack(left, dicts)
    unpacked_right = _unpack(right, dicts)
    if unpacked_left is not None and unpacked_right is not None:
        raw_left, _id_left = unpacked_left
        raw_right, _id_right = unpacked_right
        if raw_left == raw_right:
            into.append(Diff(rel, "explained", "same payload, compressed bytes differ"))
            return
        left, right = raw_left, raw_right
    if left[:2] in (b"YB", b"BY") or right[:2] in (b"YB", b"BY"):
        if _same_byml(left, right, byml):
            into.append(Diff(rel, "explained", "BYML values match; the bytes differ"))
            return
        into.append(Diff(rel, "mismatch", f"BYML differs ({len(left)} vs {len(right)} bytes, {_sha(left)} vs {_sha(right)})"))
        return
    flow_left = _flow_text(left)
    flow_right = _flow_text(right)
    if flow_left is not None or flow_right is not None:
        if flow_left == flow_right and flow_left is not None:
            into.append(Diff(rel, "explained", "event flow matches; the bytes differ"))
            return
        into.append(Diff(rel, "mismatch", "event flow differs"))
        return
    labels_left = _msbt_labels(left)
    labels_right = _msbt_labels(right)
    if labels_left is not None or labels_right is not None:
        if labels_left == labels_right and labels_left is not None:
            into.append(Diff(rel, "explained", "MSBT labels match; the bytes differ"))
            return
        into.append(Diff(rel, "mismatch", "MSBT labels differ"))
        return
    if sarc_type is not None:
        try:
            left_sarc = sarc_type(left)
            right_sarc = sarc_type(right)
        except Exception:
            left_sarc = right_sarc = None
        if left_sarc is not None and right_sarc is not None:
            left_names = list(left_sarc.names())
            right_names = list(right_sarc.names())
            if left_names != right_names:
                into.append(
                    Diff(
                        rel,
                        "mismatch",
                        f"archive members differ: only left {sorted(set(left_names) - set(right_names))}, "
                        f"only right {sorted(set(right_names) - set(left_names))}",
                    )
                )
                return
            for name in left_names:
                _compare_payload(
                    f"{rel}::{name}",
                    left_sarc.read(name),
                    right_sarc.read(name),
                    dicts,
                    byml,
                    sarc_type,
                    into,
                )
            return
    into.append(Diff(rel, "mismatch", f"bytes differ ({len(left)} vs {len(right)}, {_sha(left)} vs {_sha(right)})"))


def diff_trees(left_root: Path, right_root: Path, romfs: Path | None = None) -> list[Diff]:
    """Compare two built romfs directories. ``left`` is After the Ocarina, ``right`` is Bancwright."""
    left_root = Path(left_root)
    right_root = Path(right_root)
    left = _files(left_root)
    right = _files(right_root)
    diffs: list[Diff] = []
    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    for path in only_left:
        diffs.append(Diff(path, "mismatch", "only in the After the Ocarina build"))
    for path in only_right:
        diffs.append(Diff(path, "mismatch", "only in the Bancwright build"))
    dicts = {}
    byml = None
    sarc_type = None
    if romfs is not None and Path(romfs).is_dir():
        try:
            from bancwright.engine.ato import ensure_imported

            ensure_imported()
            from after_ocarina.zsio import dicts_for

            dicts = dicts_for(Path(romfs))
            byml, zstool = _tools()
            sarc_type = zstool.Sarc
        except Exception:
            byml = None
            sarc_type = None
    if byml is None:
        try:
            byml, zstool = _tools()
            sarc_type = zstool.Sarc
        except Exception:
            byml = None
            sarc_type = None
    for rel in sorted(set(left) & set(right)):
        _compare_payload(rel, left[rel].read_bytes(), right[rel].read_bytes(), dicts, byml, sarc_type, diffs)
    return diffs


def summarize(diffs: list[Diff]) -> tuple[int, int]:
    mismatches = sum(1 for item in diffs if item.kind == "mismatch")
    explained = sum(1 for item in diffs if item.kind == "explained")
    return mismatches, explained
