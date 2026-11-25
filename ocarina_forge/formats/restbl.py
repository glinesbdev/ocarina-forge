"""RESTBL version 1, the resource size table.

`System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs`
on game 1.1.1. The table is plain zstd (no dictionary). Layout, checked
against After the Ocarina's `docs/game-formats.md`:

    0x00  "RESTBL"
    0x06  version, u32, 1
    0x0A  name size, u32 (160 in the shipping table)
    0x0E  hashed entry count, u32
    0x12  named entry count, u32
    0x16  hashed entries: CRC32 of the path (u32), size (u32), sorted by CRC
          named entries: path padded to name size, then size (u32)

The path is the romfs path without `.zs`. An uncompressed file keeps its
suffix, so `.ainb` and `.bgyml` keys are not stripped. The size is not the
file size. It is the memory the game sets aside to load the file: a multiple
of 8, and larger than the unpacked file.

The rule below is the one After the Ocarina verified in game
(`docs/game-formats.md` on that repo). An earlier Ocarina Forge build used a
public-notes guess (`unpacked + 512`, and `old + 8 * growth + 4096`). That
guess is not what the game accepted. Saria's schedule is entry 14448 for a
3316-byte file. Comparing the new file with that entry always says it still
fits, and she then stands with no dialogue. The entry that worked was
14448 + 1024.

When the shipped bytes differ from the dump:

* Raise the entry to ``align8(old entry + max(0, new size - old size) + 1024)``.
  The raise is not skipped because the new file still fits under the old entry.
* Never shrink an entry.
* A path with no entry is not invented here. A patch that adds a file (the
  tapestry models) passes the new hashed entry in explicitly. See
  ``after_ocarina.rstb.rewrite_hashed``.
"""

from __future__ import annotations

import struct
import zlib

import zstandard

from ocarina_forge.errors import BancError

MAGIC = b"RESTBL"
HEADER = 22


def align8(size: int) -> int:
    return (size + 7) & ~7


def entry_for_byml(old_entry: int, old_unpacked: int | None, new_unpacked: int) -> int:
    """The entry to store for a rewritten file that already has a table row.

    ``align8(old_entry + max(0, growth) + 1024)``. A missing entry raises
    ValueError; the caller has to refuse it or pass an explicit new row.
    """
    if old_entry <= 0:
        raise ValueError("no existing resource-size entry")
    growth = 0
    if old_unpacked is not None:
        growth = max(0, new_unpacked - old_unpacked)
    return align8(old_entry + growth + 1024)


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
