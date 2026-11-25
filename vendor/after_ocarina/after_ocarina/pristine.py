"""Refuse a dump that already contains this mod's edits.

The US English pack in a working dump can already hold the M1 line, because
that file was copied back over the extracted RomFS. The other language packs
are hard-linked originals and must be left alone. Building from the edited
pack would start from the mod's own output. Point ``pristine_romfs`` at a
clean tree, or put the vanilla file back, and build again.
"""

from __future__ import annotations

import re
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.sources import FlowPatch, TextEdit
from after_ocarina.zsio import dicts_for, unpack

_CODES = re.compile(r"\{[^{}]*\}")
_FLOW_MARKERS = (b"AtO_CheckGanon", b"AtO_SetEpilogue", b"AtO_OrigBoot")


def assert_pristine(romfs: Path, edits: list[TextEdit], patches: list[FlowPatch]) -> None:
    dicts = dicts_for(romfs)
    for edit in edits:
        needle = _CODES.sub("", edit.text).strip()
        if len(needle) < 12:
            continue
        path = romfs / edit.pack
        if not path.is_file():
            continue
        raw, _dict_id = unpack(path.read_bytes(), dicts)
        if needle.encode("utf-8") in raw:
            raise ModError(_text_message(edit.pack, needle))
    for patch in patches:
        path = romfs / patch.file
        if not path.is_file():
            continue
        raw, _dict_id = unpack(path.read_bytes(), dicts)
        for marker in _FLOW_MARKERS:
            if marker in raw:
                raise ModError(_flow_message(patch.file, marker.decode()))


def _text_message(pack: str, needle: str) -> str:
    return (
        f"{pack} already contains this mod's line \"{needle}\". "
        "The build will not apply it again. Restore the vanilla file in the dump. "
        "The other language packs are hard-linked originals; do not copy this file over those. "
        "Or set pristine_romfs in config.toml to a clean RomFS, a backup from before this "
        "pack was replaced, and build again."
    )


def _flow_message(rel: str, marker: str) -> str:
    return (
        f"{rel} already contains this mod's event {marker}. "
        "The build will not patch it again. Restore the vanilla file in the dump, "
        "or set pristine_romfs in config.toml to a clean RomFS and build again."
    )
