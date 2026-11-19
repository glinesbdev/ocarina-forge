"""Open and write ``.zs`` bytes with the dump's zstd dictionaries.

``zsmod.unzs`` ends the process when a dictionary is missing. The build raises
:class:`ModError` instead, so a missing ``Pack/ZsDic.pack.zs`` is a normal error.
"""

from __future__ import annotations

import sys
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.paths import ZSDIC_REL, require_tools

_ZSTD = b"\x28\xb5\x2f\xfd"


def load_zsmod():
    tools = str(require_tools())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import zsmod

    return zsmod


def dicts_for(romfs: Path):
    """Dictionary id to compressor dict. Empty when the dump has no ZsDic pack."""
    path = romfs / ZSDIC_REL
    if not path.is_file():
        return {}
    return load_zsmod().dicts_by_id(str(path))


def unpack(data: bytes, dicts) -> tuple[bytes, int | None]:
    """Return decompressed bytes and the dictionary id (0 or None when there is none)."""
    if data[:4] != _ZSTD:
        return data, None
    import zstandard

    dict_id = zstandard.get_frame_parameters(data).dict_id
    if dict_id and dict_id not in dicts:
        raise ModError(
            f"this file needs zstd dictionary {dict_id}. "
            f"The dump's {ZSDIC_REL} has to sit next to the file's RomFS root."
        )
    raw, used = load_zsmod().unzs(data, dicts)
    return raw, used


def pack(raw: bytes, dict_id: int | None, dicts) -> bytes:
    """Compress with the same dictionary id the source frame used."""
    return load_zsmod().zs(raw, dict_id, dicts)
