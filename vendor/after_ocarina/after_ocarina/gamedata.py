"""Change one GameDataList reset type, looked up by flag hash.

``Navi_Ban`` is hash 3472889777 (murmur3 of the name, the same hash progress.sav
uses). The array index in the file is not part of the edit. The source value
has to be the expected one; a file that was already edited fails instead of
being changed again.

The shipped list is big-endian (``BY``). A reset-only edit stays in place
with ``zsmod``, so the size stays the same and one byte of ``ResetTypeValue``
changes. A BoolExp append grows the file, so that edit is rewritten:
big-endian to little-endian, ``byml.py``, then back. A little-endian ``YB``
file is still rewritten with ``byml.py``.
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.paths import GAMEDATA_DIR, require_tools
from after_ocarina.saveinfo import flag_hash
from after_ocarina.sources import _mapping, _rel_path


@dataclass(frozen=True)
class GameDataEdit:
    file: str
    name: str
    hash: int
    reset_from: int
    reset_to: int
    source: str


def load_gamedata_edits(directory: Path | None = None) -> list[GameDataEdit]:
    directory = directory or GAMEDATA_DIR
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    edits: list[GameDataEdit] = []
    seen: dict[str, str] = {}
    for path in files:
        for edit in _edits_in(path):
            previous = seen.get(edit.name)
            if previous is not None:
                raise ModError(f"{edit.name} is edited by both {previous} and {path.name}")
            seen[edit.name] = path.name
            edits.append(edit)
    return edits


def apply_gamedata(data: bytes, edits: list[GameDataEdit], expressions: list | None = None) -> bytes:
    expressions = list(expressions or [])
    if not edits and not expressions:
        return data
    if data[:2] == b"BY" and not expressions:
        return _apply_big_endian(data, edits)
    label = edits[0].file if edits else expressions[0].file
    payload = to_little_endian(data) if data[:2] == b"BY" else data
    byml = _byml()
    try:
        root = byml.loads(payload)
    except Exception as exc:
        raise ModError(f"{label}: could not read GameDataList ({exc})") from None
    if expressions:
        from after_ocarina.boolexp import append_expressions

        append_expressions(root, expressions)
    for edit in edits:
        _apply_one(root, edit)
    try:
        written = byml.dumps(root)
        again = byml.loads(written)
    except Exception as exc:
        raise ModError(f"{label}: could not write GameDataList ({exc})") from None
    if not byml.same(root, again):
        label = edits[0].file if edits else expressions[0].file
        raise ModError(f"{label}: rewriting GameDataList changed it")
    if data[:2] == b"BY":
        return to_big_endian(written)
    return written


def sample_gamedata(edits: list[GameDataEdit], expressions: list | None = None) -> bytes:
    """A tiny list with the edited flag, a neighbor bool, and an int that also resets as 9."""
    byml = _byml()
    bools: list[dict] = [
        {"Hash": byml.U32(1), "ResetTypeValue": 9, "SaveFileIndex": 0},
    ]
    for edit in edits:
        bools.append(
            {
                "Hash": byml.U32(edit.hash),
                "ResetTypeValue": byml.U32(edit.reset_from),
                "SaveFileIndex": 0,
                "DefaultValue": False,
            }
        )
    bools.append({"Hash": byml.U32(99), "ResetTypeValue": 24, "SaveFileIndex": 1})
    root = {
        "Data": {
            "Bool": bools,
            "Int": [{"Hash": byml.U32(5), "ResetTypeValue": 9, "SaveFileIndex": 0}],
        }
    }
    if expressions:
        from after_ocarina.boolexp import sample_bool_rows

        root["Data"]["BoolExp"] = sample_bool_rows(expressions, byml)
    return byml.dumps(root)


def to_big_endian(data: bytes) -> bytes:
    """Turn a little-endian BYML into the big-endian layout the shipped list uses."""
    if data[:2] != b"YB":
        raise ValueError("not a little-endian BYML")
    return _swap_endian(data, source="<", dest=">", magic=b"BY")


def to_little_endian(data: bytes) -> bytes:
    """Turn a big-endian BYML back into the little-endian layout ``byml.py`` edits."""
    if data[:2] != b"BY":
        raise ValueError("not a big-endian BYML")
    return _swap_endian(data, source=">", dest="<", magic=b"YB")


def roundtrip_big_endian(data: bytes) -> bytes:
    """Rewrite a big-endian BYML with no edits and return big-endian bytes again.

    ``byml.py`` loads little-endian only. This is the path a BoolExp append
    uses, without the append. A real GameDataList should come back unchanged.
    """
    byml = _byml()
    root = byml.loads(to_little_endian(data))
    return to_big_endian(byml.dumps(root))


def _swap_endian(data: bytes, *, source: str, dest: str, magic: bytes) -> bytes:
    out = bytearray(data)
    out[0:2] = magic
    byte_order = "little" if source == "<" else "big"
    version, keys_at, strings_at, root_at = struct.unpack_from(source + "H3I", data, 2)
    struct.pack_into(dest + "H3I", out, 2, version, keys_at, strings_at, root_at)
    seen: set[int] = set()

    def swap_u24(at: int) -> int:
        count = int.from_bytes(data[at:at + 3], byte_order)
        out[at:at + 3] = count.to_bytes(3, "little" if dest == "<" else "big")
        return count

    def swap_u32(at: int) -> int:
        value = struct.unpack_from(source + "I", data, at)[0]
        struct.pack_into(dest + "I", out, at, value)
        return value

    def payload(kind: int, at: int) -> None:
        if at in seen:
            return
        seen.add(at)
        if kind == 0xA1:
            swap_u32(at)
        elif kind == 0xD4:
            struct.pack_into(dest + "q", out, at, struct.unpack_from(source + "q", data, at)[0])
        elif kind == 0xD5:
            struct.pack_into(dest + "Q", out, at, struct.unpack_from(source + "Q", data, at)[0])
        elif kind == 0xD6:
            struct.pack_into(dest + "d", out, at, struct.unpack_from(source + "d", data, at)[0])

    def descend(kind: int, raw: int) -> None:
        if kind in (0xC0, 0xC1, 0x20, 0x21):
            container(raw)
        elif kind in (0xA1, 0xD4, 0xD5, 0xD6):
            payload(kind, raw)

    def table(at: int) -> None:
        if not at or at in seen:
            return
        seen.add(at)
        count = swap_u24(at + 1)
        for index in range(count + 1):
            swap_u32(at + 4 + 4 * index)

    def container(at: int) -> None:
        if at in seen:
            return
        seen.add(at)
        kind = data[at]
        count = swap_u24(at + 1)
        if kind == 0xC0:
            values_at = at + 4 + ((count + 3) & ~3)
            for index in range(count):
                child = data[at + 4 + index]
                descend(child, swap_u32(values_at + 4 * index))
        elif kind == 0xC1:
            for index in range(count):
                entry = at + 4 + 8 * index
                key = int.from_bytes(data[entry:entry + 3], "little")
                out[entry:entry + 3] = key.to_bytes(3, "big")
                child = data[entry + 3]
                descend(child, swap_u32(entry + 4))
        else:
            raise ValueError(f"cannot byte-swap container type {kind:#x}")

    table(keys_at)
    table(strings_at)
    if root_at:
        container(root_at)
    return bytes(out)


def _edits_in(path: Path) -> list[GameDataEdit]:
    data = _mapping(path)
    if "edits" not in data and set(data) <= {"expressions"}:
        return []
    unknown = set(data) - {"edits"}
    if unknown:
        raise ModError(f"{path.name}: unknown keys: {', '.join(sorted(unknown))}")
    rows = data.get("edits")
    if not isinstance(rows, list) or not rows:
        raise ModError(f"{path.name}: edits must be a non-empty list")
    edits = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ModError(f"{path.name}: edit {index} must be a mapping")
        extra = set(row) - {"file", "name", "hash", "reset_from", "reset_to"}
        if extra:
            raise ModError(f"{path.name}: edit {index} has unknown keys: {', '.join(sorted(extra))}")
        rel = _rel_path(row.get("file"), path, index, "file", suffix=".byml.zs")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            raise ModError(f"{path.name}: edit {index} name must be a non-empty string")
        where = f"{path.name}: edit {index}"
        hashed = _as_int(row.get("hash"), f"{where} hash")
        if hashed < 0:
            raise ModError(f"{where} hash must be the murmur3 of {name}")
        if flag_hash(name) != hashed:
            raise ModError(
                f"{where}: hash {hashed} is not murmur3 of {name!r} ({flag_hash(name)}). "
                "Look the flag up by that hash, not by its array index."
            )
        reset_from = _as_int(row.get("reset_from"), f"{where} reset_from")
        reset_to = _as_int(row.get("reset_to"), f"{where} reset_to")
        edits.append(
            GameDataEdit(
                file=rel,
                name=name,
                hash=hashed,
                reset_from=reset_from,
                reset_to=reset_to,
                source=path.name,
            )
        )
    return edits


def _apply_big_endian(data: bytes, edits: list[GameDataEdit]) -> bytes:
    """Edit ``BY`` bytes in place. The file size does not change."""
    zsmod = _zsmod()
    out = data
    for edit in edits:
        matches = [
            path
            for path, hashed, label in _hash_entries(out, zsmod)
            if hashed == edit.hash and (label is None or label == edit.name)
        ]
        if len(matches) != 1:
            raise ModError(
                f"{edit.file}: expected one entry for {edit.name} (hash {edit.hash}), found {len(matches)}. "
                "The array index is not used."
            )
        reset_path = matches[0].rsplit("/", 1)[0] + "/ResetTypeValue"
        current = zsmod.byml_get(out, reset_path)
        if isinstance(current, bool) or not isinstance(current, int) or int(current) != edit.reset_from:
            raise ModError(
                f"{edit.file}: {edit.name} ResetTypeValue is {current!r}, expected {edit.reset_from}. "
                f"Refusing to change it. Restore the vanilla {edit.file}."
            )
        out = zsmod.byml_set(out, reset_path, str(edit.reset_to))
        written = zsmod.byml_get(out, reset_path)
        if int(written) != edit.reset_to:
            raise ModError(f"{edit.file}: {edit.name} ResetTypeValue is {written!r} after the edit")
    if len(out) != len(data):
        raise ModError(f"{edits[0].file}: the in-place edit changed the file size")
    return out


def _hash_entries(data: bytes, zsmod):
    """``(Data/<section>/<index>/Hash, hash, name or None)`` for every indexed flag."""
    reader = zsmod._Keys(data)
    _kind, slot = zsmod.byml_find(data, "Data")
    data_at = struct.unpack_from(reader.e + "I", data, slot)[0]
    if data[data_at] != 0xC1:
        raise ModError("GameDataList Data is not a dictionary")
    count = reader.u24(data_at + 1)
    found = []
    for index in range(count):
        entry = data_at + 4 + 8 * index
        name = reader.keys[reader.u24(entry)]
        if data[entry + 3] != 0xC0:
            continue
        section_at = struct.unpack_from(reader.e + "I", data, entry + 4)[0]
        section_count = reader.u24(section_at + 1)
        for item in range(section_count):
            path = f"Data/{name}/{item}/Hash"
            try:
                hashed = zsmod.byml_get(data, path)
            except (KeyError, ValueError):
                continue
            if isinstance(hashed, bool) or not isinstance(hashed, int):
                continue
            found.append((path, int(hashed), _entry_name(data, zsmod, path)))
    return found


def _entry_name(data: bytes, zsmod, hash_path: str):
    prefix = hash_path.rsplit("/", 1)[0]
    for key in ("Name", "GameDataName"):
        try:
            text = zsmod.byml_get(data, f"{prefix}/{key}")
        except KeyError:
            continue
        if isinstance(text, str):
            return text
    return None


def _apply_one(root: object, edit: GameDataEdit) -> None:
    matches = []
    for node in _walk(root):
        hashed = _as_int(node.get("Hash"), f"{edit.file} Hash")
        if hashed != edit.hash:
            continue
        label = node.get("Name", node.get("GameDataName"))
        if isinstance(label, str) and label != edit.name:
            continue
        matches.append(node)
    if len(matches) != 1:
        raise ModError(
            f"{edit.file}: expected one entry for {edit.name} (hash {edit.hash}), found {len(matches)}. "
            "The array index is not used."
        )
    node = matches[0]
    current = node.get("ResetTypeValue")
    if isinstance(current, bool) or not isinstance(current, int) or int(current) != edit.reset_from:
        raise ModError(
            f"{edit.file}: {edit.name} ResetTypeValue is {current!r}, expected {edit.reset_from}. "
            f"Refusing to change it. Restore the vanilla {edit.file}."
        )
    node["ResetTypeValue"] = type(current)(edit.reset_to)


def _walk(node: object):
    if isinstance(node, dict):
        if "Hash" in node and "ResetTypeValue" in node:
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _as_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModError(f"{where} must be an integer")
    return int(value)


def _byml():
    tools = str(require_tools())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import byml

    return byml


def _zsmod():
    _byml()
    import zsmod

    return zsmod
