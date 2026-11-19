"""Zstd frames that may need one of the game's dictionaries.

The dictionary pack is `Pack/ZsDic.pack.zs` in the user's dump (plain zstd,
then a SARC of `*.zsdic`). Frame dictionary ids used by version 1.1.1:

    1  zs          everything that is not one of the types below
    2  bkprb       baked probes
    3  bcett.byml  map placement
    4  pack        actor packs

`ZsDic.pack.zs` itself is compressed with no dictionary. These ids are from
the public romfs notes in Kabosune/oot-remake-modding.
"""

from __future__ import annotations

from pathlib import Path

import zstandard

from bancwright.errors import BancError
from bancwright.formats.sarc import Sarc

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# Extension hints for files we create, when the source frame is not available.
DICT_FOR_SUFFIX = (
    (".bcett.byml.zs", 3),
    (".pack.zs", 4),
    (".bkprb.zs", 2),
)


class DictSet:
    def __init__(self, by_id: dict[int, zstandard.ZstdCompressionDict], source: str):
        self.by_id = by_id
        self.source = source

    def __contains__(self, dict_id: int) -> bool:
        return dict_id in self.by_id

    @classmethod
    def load(cls, path: Path) -> "DictSet":
        if not path.is_file():
            raise BancError(
                f"No dictionary pack at {path}. Copy nothing by hand: point Bancwright at the "
                "dump root that contains romfs/Pack/ZsDic.pack.zs.",
                "dict_missing",
            )
        raw = path.read_bytes()
        try:
            if raw[:4] == ZSTD_MAGIC:
                raw = zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 30)
            archive = Sarc(raw)
        except (zstandard.ZstdError, ValueError) as exc:
            raise BancError(f"Could not read the dictionary pack {path}: {exc}", "dict_missing") from exc
        by_id: dict[int, zstandard.ZstdCompressionDict] = {}
        for name in archive.names():
            if name.endswith(".zsdic"):
                dictionary = zstandard.ZstdCompressionDict(archive.read(name))
                by_id[dictionary.dict_id()] = dictionary
        if not by_id:
            raise BancError(
                f"{path} unpacked, but it has no .zsdic files. Bancwright needs the game's ZsDic pack.",
                "dict_missing",
            )
        return cls(by_id, str(path))


def is_zstd(data: bytes) -> bool:
    return data[:4] == ZSTD_MAGIC


def decompress(data: bytes, dicts: DictSet | None) -> tuple[bytes, int | None]:
    """Return (payload, dictionary id). Dictionary id is None when `data` is not zstd, and 0 when the frame uses none."""
    if not is_zstd(data):
        return data, None
    dict_id = zstandard.get_frame_parameters(data).dict_id
    dictionary = None
    if dict_id:
        if dicts is None or dict_id not in dicts.by_id:
            where = dicts.source if dicts else "no dictionary pack was loaded"
            raise BancError(
                f"This file is zstd with dictionary id {dict_id}, which is not in {where}. "
                "Placement files (.bcett.byml.zs) need id 3 from romfs/Pack/ZsDic.pack.zs.",
                "dict_missing",
            )
        dictionary = dicts.by_id[dict_id]
    try:
        decoder = zstandard.ZstdDecompressor(dict_data=dictionary)
        return decoder.decompress(data, max_output_size=1 << 31), dict_id
    except zstandard.ZstdError as exc:
        raise BancError(f"zstd could not decompress this file (dictionary {dict_id}): {exc}", "zstd") from exc


def compress(data: bytes, dict_id: int | None, dicts: DictSet | None, level: int = 19) -> bytes:
    """Compress with the same dictionary id the game used. None writes the bytes unchanged."""
    if dict_id is None:
        return data
    dictionary = None
    if dict_id:
        if dicts is None or dict_id not in dicts.by_id:
            raise BancError(
                f"Cannot recompress: dictionary id {dict_id} is not in the loaded ZsDic pack. "
                "A .bcett.byml.zs file has to be written with dictionary 3 or the game will not load it.",
                "dict_missing",
            )
        dictionary = dicts.by_id[dict_id]
    compressor = zstandard.ZstdCompressor(
        level=level,
        dict_data=dictionary,
        write_checksum=False,
        write_content_size=True,
    )
    return compressor.compress(data)


def dict_id_for(relative_path: str) -> int:
    """The dictionary a newly written file of this name should use."""
    for suffix, dict_id in DICT_FOR_SUFFIX:
        if relative_path.endswith(suffix):
            return dict_id
    return 1
