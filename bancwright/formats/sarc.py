"""Little-endian SARC archives, the pack format inside actor packs and ZsDic.

Layout follows tools/zsmod.py in Kabosune/oot-remake-modding (MIT).
Names are sorted by the game's hash (h = h * 0x65 + byte). When `like` is the
original archive, each file keeps the alignment implied by its old offset.
"""

from __future__ import annotations

import struct


def name_hash(name: str) -> int:
    value = 0
    for byte in name.encode():
        value = (value * 0x65 + byte) & 0xFFFFFFFF
    return value


def _align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def _alignment(offset: int, cap: int = 0x2000) -> int:
    return min(offset & -offset, cap) if offset else cap


class Sarc:
    def __init__(self, data: bytes):
        if data[:4] != b"SARC":
            raise ValueError("not a SARC archive")
        if data[6:8] != b"\xff\xfe":
            raise ValueError("only little-endian SARC is supported")
        self.data = data
        self.data_at = struct.unpack_from("<I", data, 0x0C)[0]
        header = struct.unpack_from("<H", data, 4)[0]
        if data[header : header + 4] != b"SFAT":
            raise ValueError("SARC is missing its file table")
        count = struct.unpack_from("<H", data, header + 6)[0]
        nodes_at = header + 0x0C
        names_at = nodes_at + 16 * count + 8
        self.files: dict[str, tuple[int, int]] = {}
        for index in range(count):
            _file_hash, attrs, start, end = struct.unpack_from("<4I", data, nodes_at + 16 * index)
            if attrs & 0x01000000:
                at = names_at + (attrs & 0xFFFF) * 4
                name = data[at : data.index(b"\0", at)].decode()
            else:
                name = f"(unnamed {index})"
            self.files[name] = (self.data_at + start, self.data_at + end)

    def names(self) -> list[str]:
        return sorted(self.files)

    def read(self, name: str) -> bytes:
        start, end = self.files[name]
        return self.data[start:end]


def pack(files: dict[str, bytes], like: bytes | None = None) -> bytes:
    """Build a SARC. With `like`, files keep the alignment of the original."""
    if like is not None:
        if like[:4] != b"SARC" or like[6:8] != b"\xff\xfe":
            raise ValueError("only little-endian SARC is supported")
        old = Sarc(like)
        version = struct.unpack_from("<H", like, 0x10)[0]
    else:
        old = None
        version = 0x0100
    order = sorted(files, key=name_hash)
    names = bytearray()
    name_at: dict[str, int] = {}
    for name in order:
        name_at[name] = len(names)
        names += name.encode() + b"\0"
        names += b"\0" * (-len(names) % 4)
    count = len(order)
    sfat_at = 0x14
    names_at = sfat_at + 0x0C + 16 * count + 8
    data_at = _align(names_at + len(names), _alignment(old.data_at) if old else 4)
    body = bytearray()
    nodes = bytearray()
    for name in order:
        boundary = _alignment(old.files[name][0]) if old and name in old.files else 4
        body += b"\0" * (_align(data_at + len(body), boundary) - data_at - len(body))
        body += files[name]
        nodes += struct.pack("<4I", name_hash(name), 0x01000000 | (name_at[name] // 4), len(body) - len(files[name]), len(body))
    total = data_at + len(body)
    out = bytearray(b"SARC" + struct.pack("<HHIIHH", 0x14, 0xFEFF, total, data_at, version, 0))
    out += b"SFAT" + struct.pack("<HHI", 0x0C, count, 0x65) + nodes
    out += b"SFNT" + struct.pack("<HH", 8, 0) + names
    out += b"\0" * (data_at - len(out))
    out += body
    return bytes(out)
