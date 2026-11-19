"""BYML version 7, little endian, as the Ocarina of Time remake writes it.

The node layout follows tools/byml.py in Kabosune/oot-remake-modding
(MIT, Copyright (c) 2026 KaboWabo/Kabosune). See LICENSE-oot-remake-modding.txt.
Hash maps (types 0x20 and 0x21) are read and written too, so a rebuilt banc
keeps nodes the plain dict writer would have dropped.

Python types:
    bool, str, float, int, list, dict  ->  bool, string, f32, s32, array, dictionary
    U32, S64, U64, F64, bytes          ->  u32, s64, u64, f64, binary
    Hash32, Hash64                     ->  hash maps keyed by u32 / u64
    None                               ->  null
"""

from __future__ import annotations

import struct

CONTAINERS = (0xC0, 0xC1, 0x20, 0x21)


class U32(int):
    pass


class S64(int):
    pass


class U64(int):
    pass


class F64(float):
    pass


class Hash32(dict):
    """A BYML hash map whose keys are 32-bit integers (node type 0x20)."""


class Hash64(dict):
    """A BYML hash map whose keys are 64-bit integers (node type 0x21)."""


def loads(data: bytes):
    if data[:2] != b"YB":
        raise ValueError("not a little-endian BYML (expected magic YB)")
    _version, keys_at, strings_at, root_at = struct.unpack_from("<H3I", data, 2)
    reader = _Reader(data)
    reader.keys = reader.table(keys_at) if keys_at else []
    reader.strings = reader.table(strings_at) if strings_at else []
    if not root_at:
        return None
    return reader.node(data[root_at], root_at)


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.keys: list[str] = []
        self.strings: list[str] = []

    def u24(self, at: int) -> int:
        return int.from_bytes(self.d[at : at + 3], "little")

    def table(self, at: int) -> list[str]:
        count = self.u24(at + 1)
        offsets = struct.unpack_from(f"<{count + 1}I", self.d, at + 4)
        out = []
        for i in range(count):
            start = at + offsets[i]
            end = self.d.index(b"\0", start)
            out.append(self.d[start:end].decode("utf-8"))
        return out

    def value(self, kind: int, raw: int):
        data = self.d
        if kind in CONTAINERS:
            return self.node(kind, raw)
        if kind == 0xA0:
            return self.strings[raw]
        if kind == 0xA1:
            size = struct.unpack_from("<I", data, raw)[0]
            return bytes(data[raw + 4 : raw + 4 + size])
        if kind == 0xD0:
            return bool(raw)
        if kind == 0xD1:
            return struct.unpack("<i", struct.pack("<I", raw))[0]
        if kind == 0xD2:
            return struct.unpack("<f", struct.pack("<I", raw))[0]
        if kind == 0xD3:
            return U32(raw)
        if kind == 0xD4:
            return S64(struct.unpack_from("<q", data, raw)[0])
        if kind == 0xD5:
            return U64(struct.unpack_from("<Q", data, raw)[0])
        if kind == 0xD6:
            return F64(struct.unpack_from("<d", data, raw)[0])
        if kind == 0xFF:
            return None
        raise ValueError(f"unknown BYML type {kind:#x}")

    def node(self, kind: int, at: int):
        data = self.d
        count = self.u24(at + 1)
        if kind == 0xC0:
            types = data[at + 4 : at + 4 + count]
            vals = at + 4 + ((count + 3) & ~3)
            return [
                self.value(types[i], struct.unpack_from("<I", data, vals + 4 * i)[0])
                for i in range(count)
            ]
        if kind == 0xC1:
            out: dict = {}
            for i in range(count):
                pos = at + 4 + 8 * i
                key = self.keys[self.u24(pos)]
                out[key] = self.value(data[pos + 3], struct.unpack_from("<I", data, pos + 4)[0])
            return out
        if kind in (0x20, 0x21):
            wide = kind == 0x21
            stride = 12 if wide else 8
            types_at = at + 4 + stride * count
            out = Hash64() if wide else Hash32()
            for i in range(count):
                pos = at + 4 + stride * i
                if wide:
                    key = struct.unpack_from("<Q", data, pos)[0]
                else:
                    key = struct.unpack_from("<I", data, pos)[0]
                raw = struct.unpack_from("<I", data, pos + stride - 4)[0]
                out[key] = self.value(data[types_at + i], raw)
            return out
        raise ValueError(f"unknown BYML container {kind:#x}")


def _kind(value) -> int:
    if value is None:
        return 0xFF
    if isinstance(value, bool):
        return 0xD0
    if isinstance(value, str):
        return 0xA0
    if isinstance(value, (bytes, bytearray)):
        return 0xA1
    if isinstance(value, U32):
        return 0xD3
    if isinstance(value, S64):
        return 0xD4
    if isinstance(value, U64):
        return 0xD5
    if isinstance(value, F64):
        return 0xD6
    if isinstance(value, int):
        return 0xD1
    if isinstance(value, float):
        return 0xD2
    if isinstance(value, Hash64):
        return 0x21
    if isinstance(value, Hash32):
        return 0x20
    if isinstance(value, list):
        return 0xC0
    if isinstance(value, dict):
        return 0xC1
    raise TypeError(f"can't store {type(value).__name__} in BYML")


def _collect(value, keys: set[str], strings: set[str]) -> None:
    if isinstance(value, (Hash32, Hash64)):
        for item in value.values():
            _collect(item, keys, strings)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"BYML dictionary key must be a string, got {type(key).__name__}")
            keys.add(key)
            _collect(item, keys, strings)
    elif isinstance(value, list):
        for item in value:
            _collect(item, keys, strings)
    elif isinstance(value, str):
        strings.add(value)


def _string_table(names: set[str]) -> tuple[bytes, dict]:
    ordered = sorted(names, key=lambda text: text.encode("utf-8"))
    count = len(ordered)
    head = 4 + 4 * (count + 1)
    offsets: list[int] = []
    blob = bytearray()
    for text in ordered:
        offsets.append(head + len(blob))
        blob += text.encode("utf-8") + b"\0"
    offsets.append(head + len(blob))
    out = bytes([0xC2]) + count.to_bytes(3, "little")
    out += struct.pack(f"<{count + 1}I", *offsets) + bytes(blob)
    out += b"\0" * (-len(out) % 4)
    return out, {text: index for index, text in enumerate(ordered)}


def _sig(value):
    kind = _kind(value)
    if isinstance(value, (Hash32, Hash64)):
        return (kind, tuple((key, _sig(value[key])) for key in value))
    if isinstance(value, dict):
        return (kind, tuple((key, _sig(item)) for key, item in value.items()))
    if isinstance(value, list):
        return (kind, tuple(_sig(item) for item in value))
    if kind == 0xD2:
        return (kind, struct.pack("<f", value))
    if kind == 0xA1:
        return (kind, bytes(value))
    return (kind, value)


def dumps(root, version: int = 7) -> bytes:
    keys: set[str] = set()
    strings: set[str] = set()
    _collect(root, keys, strings)
    out = bytearray(16)
    if keys:
        keys_at = len(out)
        table, key_index = _string_table(keys)
        out += table
    else:
        keys_at = 0
        key_index = {}
    if strings:
        strings_at = len(out)
        table, string_index = _string_table(strings)
        out += table
    else:
        strings_at = 0
        string_index = {}

    def align(boundary: int) -> None:
        out.extend(b"\0" * (-len(out) % boundary))

    def scalar(value) -> int:
        kind = _kind(value)
        if kind == 0xA0:
            return string_index[value]
        if kind == 0xD0:
            return int(value)
        if kind == 0xD1:
            return struct.unpack("<I", struct.pack("<i", int(value)))[0]
        if kind == 0xD2:
            return struct.unpack("<I", struct.pack("<f", float(value)))[0]
        if kind == 0xD3:
            return int(value) & 0xFFFFFFFF
        if kind == 0xFF:
            return 0
        raise AssertionError(kind)

    # Identical containers and 64-bit values are stored once, matching Nintendo's files.
    seen: dict = {}

    def big(value) -> int:
        kind = _kind(value)
        signature = (kind, bytes(value) if kind == 0xA1 else value)
        if signature in seen:
            return seen[signature]
        align(8 if kind != 0xA1 else 4)
        at = len(out)
        if kind == 0xD4:
            out.extend(struct.pack("<q", int(value)))
        elif kind == 0xD5:
            out.extend(struct.pack("<Q", int(value)))
        elif kind == 0xD6:
            out.extend(struct.pack("<d", float(value)))
        else:
            raw = bytes(value)
            out.extend(struct.pack("<I", len(raw)) + raw)
        seen[signature] = at
        return at

    def container(value) -> int:
        signature = _sig(value)
        if signature in seen:
            return seen[signature]
        align(4)
        at = len(out)
        seen[signature] = at
        if isinstance(value, list):
            count = len(value)
            types = bytes(_kind(item) for item in value)
            out.extend(bytes([0xC0]) + count.to_bytes(3, "little") + types + b"\0" * (-count % 4))
            vals_at = len(out)
            out.extend(b"\0" * (4 * count))
            items = list(enumerate(value))
            stride = 4
        elif isinstance(value, (Hash32, Hash64)):
            wide = isinstance(value, Hash64)
            ordered = sorted(value)
            count = len(ordered)
            code = 0x21 if wide else 0x20
            out.extend(bytes([code]) + count.to_bytes(3, "little"))
            for key in ordered:
                if wide:
                    out.extend(struct.pack("<QI", key, 0))
                else:
                    out.extend(struct.pack("<II", key, 0))
            out.extend(bytes(_kind(value[key]) for key in ordered) + b"\0" * (-count % 4))
            vals_at = at + 4 + (8 if wide else 4)
            stride = 12 if wide else 8
            items = [(index, value[key]) for index, key in enumerate(ordered)]
        else:
            ordered = sorted(value, key=lambda key: key_index[key])
            count = len(ordered)
            out.extend(bytes([0xC1]) + count.to_bytes(3, "little"))
            for key in ordered:
                out.extend(key_index[key].to_bytes(3, "little") + bytes([_kind(value[key])]) + b"\0\0\0\0")
            vals_at = at + 8
            stride = 8
            items = [(index, value[key]) for index, key in enumerate(ordered)]
        for index, item in items:
            kind = _kind(item)
            if kind in CONTAINERS:
                raw = container(item)
            elif kind in (0xD4, 0xD5, 0xD6, 0xA1):
                raw = big(item)
            else:
                raw = scalar(item)
            struct.pack_into("<I", out, vals_at + stride * index, raw)
        return at

    root_at = container(root) if root is not None else 0
    align(4)
    struct.pack_into("<2sH3I", out, 0, b"YB", version, keys_at, strings_at, root_at)
    return bytes(out)


def same(left, right) -> bool:
    """Equal values and equal types. Floats are compared as stored f32 bits."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    if type(left) is float:
        return struct.pack("<f", left) == struct.pack("<f", right)
    return left == right
