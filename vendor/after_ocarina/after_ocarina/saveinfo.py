"""Read flag values out of a Longshot ``progress.sav``.

The layout measured on a 1.1.1 clear save:

* 12-byte little-endian header: magic, revision ``2982123``, then a word that is
  the GameDataList ``SaveDataOffsetPos`` (95704 on the measured file), not where
  the pairs start
* 8-byte pairs from offset 40 to the last whole pair:
  murmur3_32 of the flag name (seed 0), then the value as an unsigned 32-bit int

Real files have a short tail after the pairs (4 bytes on a 134,108-byte save).
Bytes past the flag table hold strings and arrays, so the same hash can appear
again; the first pair is the flag. Enum values are the same hash of the value's
name. Bools are 0 or 1. ``caption.sav`` uses the same pair layout.
This module does not write saves.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.paths import GAMEDATA_REL
from after_ocarina.zsio import dicts_for, unpack

REVISION = 2_982_123
# Pairs always start here. Header word 2 is SaveDataOffsetPos and is only reported.
MEASURED_TABLE_OFFSET = 40
CAPTION_IS_CLEAR = "CaptionData.IsClear"
# murmur3_32("CaptionData.IsClear"), checked against a real caption.sav.
CAPTION_IS_CLEAR_HASH = 3_340_780_796

DEFAULT_FLAGS = (
    "Step_Defeat_Ganon",
    "DefeatedEnemyNum.Enemy_DungeonBoss_Ganon",
    "HavePlayedEvent.TitleEventBase",
    "Sequence_CurrentBanc",
    "Sequence_IsChildhood",
)

# Readings that were checked on a real clear save. Other flags are printed raw.
_INT_CLEAR = "DefeatedEnemyNum.Enemy_DungeonBoss_Ganon"
_BOOL_EPILOGUE = "HavePlayedEvent.TitleEventBase"


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 32-bit, x86 variant, the hash GameData uses for flag names."""
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    digest = seed & 0xFFFFFFFF
    blocks = len(data) // 4
    for index in range(blocks):
        part = int.from_bytes(data[index * 4 : index * 4 + 4], "little")
        part = (part * c1) & 0xFFFFFFFF
        part = ((part << 15) | (part >> 17)) & 0xFFFFFFFF
        part = (part * c2) & 0xFFFFFFFF
        digest ^= part
        digest = ((digest << 13) | (digest >> 19)) & 0xFFFFFFFF
        digest = (digest * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[blocks * 4 :]
    part = 0
    if len(tail) >= 3:
        part ^= tail[2] << 16
    if len(tail) >= 2:
        part ^= tail[1] << 8
    if tail:
        part ^= tail[0]
        part = (part * c1) & 0xFFFFFFFF
        part = ((part << 15) | (part >> 17)) & 0xFFFFFFFF
        part = (part * c2) & 0xFFFFFFFF
        digest ^= part
    digest ^= len(data)
    digest ^= digest >> 16
    digest = (digest * 0x85EBCA6B) & 0xFFFFFFFF
    digest ^= digest >> 13
    digest = (digest * 0xC2B2AE35) & 0xFFFFFFFF
    digest ^= digest >> 16
    return digest


def flag_hash(name: str) -> int:
    return murmur3_32(name.encode("utf-8"))


@dataclass(frozen=True)
class SaveFile:
    magic: int
    revision: int
    table_offset: int
    values: dict[int, int]
    duplicates: frozenset[int]


@dataclass(frozen=True)
class NameCatalog:
    """Strings harvested from a GameDataList, keyed by murmur3 of the string."""

    by_hash: dict[int, tuple[str, ...]]
    strings: frozenset[str]
    note: str


def parse_progress(data: bytes) -> SaveFile:
    if len(data) < 12:
        raise ModError(f"progress.sav is {len(data)} bytes; the header is 12")
    magic, revision, table_offset = struct.unpack_from("<III", data, 0)
    if revision != REVISION:
        raise ModError(
            f"progress.sav revision is {revision}, expected {REVISION}. "
            f"First 16 bytes: {data[:16].hex()}"
        )
    values: dict[int, int] = {}
    duplicates: set[int] = set()
    # Stop before a partial pair. ``len - 7`` is the first offset that cannot
    # hold 8 bytes, so a 4-byte tail is left unread.
    for offset in range(MEASURED_TABLE_OFFSET, len(data) - 7, 8):
        name_hash, value = struct.unpack_from("<II", data, offset)
        if name_hash in values:
            duplicates.add(name_hash)
            continue
        values[name_hash] = value
    return SaveFile(
        magic=magic,
        revision=revision,
        table_offset=table_offset,
        values=values,
        duplicates=frozenset(duplicates),
    )


def byml_strings(data: bytes) -> list[str]:
    """Every string in a BYML v7 string table, little-endian (``YB``) or big-endian (``BY``)."""
    if len(data) < 16:
        raise ValueError("BYML is shorter than a header")
    if data[:2] == b"YB":
        endian = "<"
    elif data[:2] == b"BY":
        endian = ">"
    else:
        raise ValueError(f"not a BYML file (magic {data[:2]!r})")
    version, _keys_at, strings_at, _root_at = struct.unpack_from(endian + "HIII", data, 2)
    if version != 7:
        raise ValueError(f"BYML version {version}, expected 7")
    if strings_at == 0:
        return []
    if strings_at + 4 > len(data) or data[strings_at] != 0xC2:
        raise ValueError("BYML string table is missing")
    count = int.from_bytes(data[strings_at + 1 : strings_at + 4], "little" if endian == "<" else "big")
    offsets = struct.unpack_from(endian + f"{count + 1}I", data, strings_at + 4)
    strings = []
    for index in range(count):
        start = strings_at + offsets[index]
        end = data.index(b"\0", start)
        strings.append(data[start:end].decode("utf-8"))
    return strings


def catalog_from_byml(data: bytes, note: str) -> NameCatalog:
    strings = byml_strings(data)
    by_hash: dict[int, list[str]] = {}
    for text in strings:
        by_hash.setdefault(flag_hash(text), []).append(text)
    return NameCatalog(
        by_hash={key: tuple(names) for key, names in by_hash.items()},
        strings=frozenset(strings),
        note=note,
    )


def load_gamedata_catalog(romfs: Path) -> NameCatalog:
    """Hash to names from the dump's GameDataList. Raises ModError when the file cannot be read."""
    path = romfs / GAMEDATA_REL
    if not path.is_file():
        raise ModError(f"no {GAMEDATA_REL} under {romfs}")
    raw, _dict_id = unpack(path.read_bytes(), dicts_for(romfs))
    try:
        return catalog_from_byml(raw, f"{len(byml_strings(raw))} strings from {GAMEDATA_REL}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ModError(f"{GAMEDATA_REL} is not a BYML v7 string table ({exc})") from None


def format_report(save: SaveFile, flags: list[str], catalog: NameCatalog | None) -> str:
    lines = [
        f"revision {save.revision}, pairs from offset {MEASURED_TABLE_OFFSET}, "
        f"header SaveDataOffsetPos {save.table_offset}, "
        f"magic 0x{save.magic:08x}, {len(save.values)} values",
    ]
    if save.table_offset != MEASURED_TABLE_OFFSET:
        lines.append(
            f"header word 2 is {save.table_offset} (GameDataList SaveDataOffsetPos), "
            f"not the pair-table start; pairs are read from offset {MEASURED_TABLE_OFFSET}, "
            "and the first hash match is kept"
        )
    if catalog is None:
        lines.append("GameDataList: not loaded; names below are the ones you asked for, hashed")
    else:
        lines.append(f"GameDataList: {catalog.note}")
    for name in flags:
        lines.append("")
        lines.extend(_flag_lines(save, name, catalog))
    return "\n".join(lines) + "\n"


def _flag_lines(save: SaveFile, name: str, catalog: NameCatalog | None) -> list[str]:
    name_hash = flag_hash(name)
    lines = [name, f"  hash 0x{name_hash:08x}"]
    if catalog is not None:
        listed = "yes" if name in catalog.strings else "no string match"
        lines.append(f"  in GameDataList: {listed}")
    if name_hash not in save.values:
        lines.append("  value: absent")
        return lines
    value = save.values[name_hash]
    lines.append(f"  value: {value}")
    reading = _reading(name, value)
    if reading:
        lines.append(f"  reading: {reading}")
    if catalog is not None and value not in (0, 1):
        match = catalog.by_hash.get(value)
        if match and len(match) == 1:
            lines.append(f"  value matches name: {match[0]}")
        elif match:
            lines.append("  value matches names: " + ", ".join(match))
    if name_hash in save.duplicates:
        lines.append("  duplicate hash later in this save; the first copy is shown")
    return lines


def format_caption(save: SaveFile) -> str:
    """``CaptionData.IsClear`` from a caption.sav that uses the same pair layout."""
    lines = [
        f"caption.sav: revision {save.revision}, pairs from offset {MEASURED_TABLE_OFFSET}, "
        f"header SaveDataOffsetPos {save.table_offset}",
    ]
    lines.extend(_flag_lines(save, CAPTION_IS_CLEAR, None))
    return "\n".join(lines) + "\n"


def _reading(name: str, value: int) -> str | None:
    if name == CAPTION_IS_CLEAR:
        if value == 0:
            return "bool false"
        if value == 1:
            return "bool true"
        return f"expected 0 or 1 for this bool, got {value}"
    if name == _INT_CLEAR:
        if value == 0:
            return "int 0, this save has not cleared the game"
        return f"int {value}, this save has cleared the game"
    if name == _BOOL_EPILOGUE:
        if value == 0:
            return "bool false, the boot hook has not set it"
        if value == 1:
            return "bool true, the boot hook has set it"
        return f"expected 0 or 1 for this bool, got {value}"
    return None
