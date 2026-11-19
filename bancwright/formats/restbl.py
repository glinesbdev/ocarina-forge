"""RESTBL version 1, the resource size table.

`System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs`
on game 1.1.1. The table is plain zstd (no dictionary). Layout, from the
public romfs notes:

    0x00  "RESTBL"
    0x06  version, u32, 1
    0x0A  name size, u32 (160 in the shipping table)
    0x0E  hashed entry count, u32
    0x12  named entry count, u32
    0x16  hashed entries: CRC32 of the path (u32), size (u32), sorted by CRC
          named entries: path padded to name size, then size (u32)

The path is the romfs path without `.zs`. The size is not the file size. It is
the memory the game sets aside to load the file: a multiple of 8, and larger
than the unpacked file.

Rules Bancwright applies when a rewritten BYML is installed (public notes,
plus the rule Longshot measured for parameter files):

* Never lower an entry.
* At least unpacked size + 512, rounded up to a multiple of 8. Rewritten BYML
  is a little larger than Nintendo's for the same data, and the shipping
  entries for placement files sit a few hundred bytes above the file.
* If the unpacked file grew, also at least
  `old entry + 8 * bytes grown + 4096`, rounded up to 8. Growing a BYML's
  entry in proportion to the file was not enough: list elements cost more on
  the heap than in the file. Longshot uses 8 bytes of entry per byte grown,
  plus 4 KiB.
"""

from __future__ import annotations

import struct
import zlib

import zstandard

from bancwright.errors import BancError

MAGIC = b"RESTBL"
HEADER = 22


def align8(size: int) -> int:
    return (size + 7) & ~7


def entry_for_byml(old_entry: int, old_unpacked: int | None, new_unpacked: int) -> int:
    """The entry to store for a rewritten BYML. See the module docstring."""
    floor = align8(new_unpacked + 512)
    if old_entry and old_unpacked is not None and new_unpacked > old_unpacked:
        growth = new_unpacked - old_unpacked
        floor = max(floor, align8(old_entry + 8 * growth + 4096))
    if old_entry:
        return max(old_entry, floor)
    return floor


def _crc(path: str) -> int:
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def canonical_path(relative: str) -> str:
    text = relative.replace("\\", "/")
    if text.endswith(".zs"):
        text = text[:-3]
    return text


class ResourceSizeTable:
    def __init__(self, data: bytes):
        if data[:6] != MAGIC or struct.unpack_from("<I", data, 6)[0] != 1:
            raise BancError(
                "The resource size table is not a RESTBL version 1 file. "
                "Expected System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable "
                "(possibly still .zs).",
                "rstb",
            )
        self.name_size, hashed_count, named_count = struct.unpack_from("<III", data, 10)
        expected = HEADER + 8 * hashed_count + (self.name_size + 4) * named_count
        if len(data) != expected or self.name_size <= 0:
            raise BancError(
                f"The resource size table has an unexpected size ({len(data)} bytes for "
                f"{hashed_count} hashed and {named_count} named entries).",
                "rstb",
            )
        self.hashed: dict[int, int] = {}
        for index in range(hashed_count):
            crc, size = struct.unpack_from("<II", data, HEADER + 8 * index)
            self.hashed[crc] = size
        self.named: list[tuple[str, int]] = []
        named_at = HEADER + 8 * hashed_count
        stride = self.name_size + 4
        for index in range(named_count):
            pos = named_at + stride * index
            raw = data[pos : pos + self.name_size]
            path = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
            size = struct.unpack_from("<I", data, pos + self.name_size)[0]
            self.named.append((path, size))

    def get(self, relative: str) -> int | None:
        path = canonical_path(relative)
        for name, size in self.named:
            if name == path:
                return size
        return self.hashed.get(_crc(path))

    def raise_to(self, relative: str, size: int) -> tuple[int, int]:
        """Raise the entry for `relative` to at least `size`. Returns (old, new).

        A path the table does not have is inserted. A CRC that already belongs
        to a different path is stored as a named entry, which is how the game
        keeps both files when the hash collides.
        """
        path = canonical_path(relative)
        want = align8(size)
        for index, (name, old) in enumerate(self.named):
            if name == path:
                new = max(old, want)
                self.named[index] = (name, new)
                return old, new
        crc = _crc(path)
        if crc in self.hashed:
            old = self.hashed[crc]
            new = max(old, want)
            self.hashed[crc] = new
            return old, new
        self.hashed[crc] = want
        return 0, want

    def to_bytes(self) -> bytes:
        ordered = sorted(self.hashed.items())
        out = bytearray(MAGIC)
        out += struct.pack("<IIII", 1, self.name_size, len(ordered), len(self.named))
        for crc, size in ordered:
            out += struct.pack("<II", crc, size)
        for path, size in self.named:
            raw = path.encode("utf-8")
            if len(raw) > self.name_size:
                raise BancError(
                    f"Resource path is longer than the table's name field ({self.name_size}): {path}",
                    "rstb",
                )
            out += raw + b"\0" * (self.name_size - len(raw))
            out += struct.pack("<I", size)
        return bytes(out)


def load_table(data: bytes) -> ResourceSizeTable:
    if data[:4] == b"\x28\xb5\x2f\xfd":
        try:
            data = zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 28)
        except zstandard.ZstdError as exc:
            raise BancError(f"Could not decompress the resource size table: {exc}", "rstb") from exc
    return ResourceSizeTable(data)


def save_table(table: ResourceSizeTable, level: int = 19) -> bytes:
    raw = table.to_bytes()
    packed = zstandard.ZstdCompressor(level=level, write_content_size=True).compress(raw)
    check = zstandard.ZstdDecompressor().decompress(packed, max_output_size=1 << 28)
    if check != raw:
        raise BancError("The recompressed resource size table did not round-trip.", "rstb")
    return packed
