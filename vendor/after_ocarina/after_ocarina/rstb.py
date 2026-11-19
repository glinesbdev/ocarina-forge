"""Decide when a rebuilt file needs a larger resource-size-table entry.

``tools/rstb_set.py`` can raise a hashed entry that is already in the table.
The build writes the table itself so it can also insert a new hashed entry.
See docs/tools.md for the format.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from after_ocarina.errors import ModError


def strip_zs(romfs_rel: str) -> str:
    """RomFS path in the form the table uses: forward slashes, no ``.zs`` suffix."""
    path = romfs_rel.replace("\\", "/")
    if path.endswith(".zs"):
        path = path[: -len(".zs")]
    return path


def align8(size: int) -> int:
    return (size + 7) & ~7


# Added to the dump's in-memory reservation whenever the shipped bytes differ.
# The reservation is not the file size: Saria's schedule is entry 14448 for a
# 3316-byte file. Comparing the new file with that entry always says it fits,
# and the game then ignores the edit.
ENTRY_MARGIN = 1024


def target_entry(
    current: int,
    new_unpacked: int,
    margin: int = ENTRY_MARGIN,
    *,
    old_unpacked: int | None = None,
) -> int | None:
    """Return the entry to write.

    A changed file (``old_unpacked`` given) always raises the reservation to
    ``align8(current + max(0, new_unpacked - old_unpacked) + margin)``. The
    file-size comparison is not used as a reason to keep ``current``.

    With no previous file, the old rule stays: ``align8(new_unpacked + margin)``
    when that is larger than ``current``, otherwise None. A path with no entry
    at all is still refused by the build; this function cannot invent one.
    """
    if min(current, new_unpacked, margin) < 0 or (old_unpacked is not None and old_unpacked < 0):
        raise ValueError("sizes must be non-negative")
    if old_unpacked is None:
        needed = align8(new_unpacked + margin)
        if needed <= current:
            return None
        return needed
    growth = max(0, new_unpacked - old_unpacked)
    return align8(current + growth + margin)


def decide_growth(old_unpacked: int, new_unpacked: int, entry: int, room: int = 64) -> int | None:
    """Earlier slack-preserving rule. The build uses :func:`target_entry` instead.

    Kept so the difference stays testable: this one preserves the dump's unused
    bytes when it grows, and treats ``room`` as the minimum spare bytes.
    """
    if min(old_unpacked, new_unpacked, entry, room) < 0:
        raise ValueError("sizes must be non-negative")
    if new_unpacked + room <= entry:
        return None
    slack = entry - old_unpacked
    if slack < room:
        slack = room
    needed = align8(new_unpacked + slack)
    if needed <= entry:
        return None
    return needed


class ResourceTable:
    """Hashed and named entries from an uncompressed RESTBL version 1 file."""

    def __init__(self, data: bytes):
        if data[:6] != b"RESTBL" or struct.unpack_from("<I", data, 6)[0] != 1:
            raise ModError("Resource size table is not a RESTBL version 1 file")
        self.name_size, n_hashed, n_named = struct.unpack_from("<III", data, 10)
        expect = 22 + 8 * n_hashed + (self.name_size + 4) * n_named
        if len(data) != expect:
            raise ModError(
                f"Resource size table size is {len(data)} bytes; RESTBL header describes {expect}"
            )
        self.hashed: dict[int, int] = {}
        for index in range(n_hashed):
            crc, size = struct.unpack_from("<II", data, 22 + 8 * index)
            self.hashed[crc] = size
        self.named: dict[str, int] = {}
        base = 22 + 8 * n_hashed
        stride = self.name_size + 4
        for index in range(n_named):
            chunk = data[base + index * stride : base + (index + 1) * stride]
            name = chunk[: self.name_size].split(b"\0", 1)[0].decode("utf-8", "replace")
            size = struct.unpack_from("<I", chunk, self.name_size)[0]
            self.named[name] = size

    def size_for(self, romfs_rel: str) -> int | None:
        key = strip_zs(romfs_rel)
        if key in self.named:
            return self.named[key]
        return self.hashed.get(zlib.crc32(key.encode()) & 0xFFFFFFFF)


def load_table_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard

        data = zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 28)
    return data


def load_table(path: Path) -> ResourceTable:
    if not path.is_file():
        raise ModError(f"Resource size table is not in the dump: {path}")
    return ResourceTable(load_table_bytes(path))


def rewrite_hashed(data: bytes, updates: dict[str, int], additions: dict[str, int]) -> bytes:
    """Return a RESTBL with hashed sizes replaced and new hashed entries inserted.

    Paths are RomFS paths with or without ``.zs``. The CRC32 is of the path
    without that suffix. An update has to already exist and cannot shrink.
    An addition has to be absent from both the hashed list and the named list.
    Sizes are aligned up to 8. Named entries are copied unchanged, and the
    hashed list stays sorted by CRC32.
    """
    if data[:6] != b"RESTBL" or struct.unpack_from("<I", data, 6)[0] != 1:
        raise ModError("Resource size table is not a RESTBL version 1 file")
    name_size, n_hashed, n_named = struct.unpack_from("<III", data, 10)
    expect = 22 + 8 * n_hashed + (name_size + 4) * n_named
    if len(data) != expect:
        raise ModError(f"Resource size table size is {len(data)} bytes; RESTBL header describes {expect}")
    hashed: dict[int, int] = {}
    for index in range(n_hashed):
        crc, size = struct.unpack_from("<II", data, 22 + 8 * index)
        hashed[crc] = size
    named: set[str] = set()
    base = 22 + 8 * n_hashed
    stride = name_size + 4
    for index in range(n_named):
        chunk = data[base + index * stride : base + (index + 1) * stride]
        named.add(chunk[:name_size].split(b"\0", 1)[0].decode("utf-8", "replace"))
    tail = data[base:]
    overlap = sorted(set(updates) & set(additions))
    if overlap:
        raise ModError(
            "resource size table lists a path as both a raise and a new entry: " + ", ".join(overlap)
        )

    def aligned(path: str, size: int) -> int:
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ModError(f"{path}: resource size must be a non-negative integer")
        return align8(size)

    def crc_of(path: str) -> tuple[str, int]:
        key = strip_zs(path)
        if key in named:
            raise ModError(f"{key}: is a named entry. This build only adds or raises hashed entries.")
        return key, zlib.crc32(key.encode()) & 0xFFFFFFFF

    for path, size in updates.items():
        key, crc = crc_of(path)
        if crc not in hashed:
            raise ModError(f"{key}: not in the table's hashed entries")
        new = aligned(key, size)
        old = hashed[crc]
        if new < old:
            raise ModError(f"{key}: {new} is below the game's {old}")
        hashed[crc] = new
    for path, size in additions.items():
        key, crc = crc_of(path)
        if crc in hashed:
            raise ModError(
                f"{key}: already has a hashed entry. A new entry is only for a path the table does not have."
            )
        hashed[crc] = aligned(key, size)
    header = bytearray(data[:22])
    struct.pack_into("<I", header, 14, len(hashed))
    body = b"".join(struct.pack("<II", crc, hashed[crc]) for crc in sorted(hashed))
    return bytes(header) + body + tail
