"""Add an MSBT label before msbtmod replaces its text.

``msbtmod set`` only edits a label that is already in the file. A ``create``
edit is inserted here first. The bucket is the usual MSBT hash. Every label
already in the file is checked against that hash, so a wrong constant cannot
reshuffle the vanilla lines.
"""

from __future__ import annotations

import struct
import sys

from after_ocarina.errors import ModError
from after_ocarina.paths import require_tools
from after_ocarina.sources import TextEdit


def label_hash(label: str, groups: int) -> int:
    """MSBT bucket. hash = hash * 0x492 + byte, then modulo the group count."""
    value = 0
    for byte in label.encode("utf-8"):
        value = (value * 0x492 + byte) & 0xFFFFFFFF
    return value % groups


def add_created_labels(sarc_bytes: bytes, edits: list[TextEdit]) -> bytes:
    """Return the SARC with each ``create`` edit's label appended.

    ``new_file`` inserts the msbt when the pack does not have it. The new file
    has one label group, so every label hashes into that group. A file that is
    already in the pack is left on the add-a-label path.
    """
    msbtmod, zstool, zsmod = _tools()
    sarc = zstool.Sarc(sarc_bytes)
    files = {name: sarc.read(name) for name in sarc.names()}
    created = _create_missing_files(files, edits, msbtmod)
    parsed = {}
    for edit in edits:
        if not edit.create or (edit.msbt, edit.label) in created:
            continue
        if edit.msbt not in files:
            raise ModError(f"no file {edit.msbt!r} in the pack")
        message = parsed.setdefault(edit.msbt, msbtmod.Msbt(files[edit.msbt]))
        if edit.label in message.labels:
            raise ModError(f"{edit.msbt} already has message {edit.label!r}")
        body = edit.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        _add_label(message, edit.label, msbtmod.from_text(body))
    for name, message in parsed.items():
        files[name] = message.pack()
    return zsmod.sarc_pack(files, sarc_bytes)


def _create_missing_files(files: dict, edits: list[TextEdit], msbtmod) -> set[tuple[str, str]]:
    grouped: dict[str, list[TextEdit]] = {}
    for edit in edits:
        if edit.new_file:
            grouped.setdefault(edit.msbt, []).append(edit)
    created: set[tuple[str, str]] = set()
    for msbt, items in grouped.items():
        if msbt in files:
            continue
        if any(not edit.create for edit in items):
            raise ModError(f"{msbt}: new_file needs create: true")
        messages = []
        for edit in items:
            body = edit.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
            messages.append((edit.label, msbtmod.from_text(body)))
            created.add((edit.msbt, edit.label))
        from after_ocarina.fixture import build_msbt

        files[msbt] = build_msbt(messages)
    return created


def _add_label(message, label: str, raw: bytes) -> None:
    body = dict(message.sections)[b"LBL1"]
    groups = struct.unpack_from("<I", body, 0)[0]
    if groups <= 0:
        raise ModError("MSBT label table has no groups")
    stored = []
    for index in range(groups):
        count, off = struct.unpack_from("<II", body, 4 + 8 * index)
        cursor = off
        for _ in range(count):
            length = body[cursor]
            name = body[cursor + 1:cursor + 1 + length].decode()
            if label_hash(name, groups) != index:
                raise ModError(
                    f"MSBT label {name!r} is in group {index}, not the hash group; not adding a label"
                )
            stored.append((index, name, message.labels[name]))
            cursor += 5 + length
    stored.append((label_hash(label, groups), label, len(message.texts)))
    message.texts.append(raw)
    message.labels[label] = len(message.texts) - 1
    rebuilt = _label_body(groups, stored)
    for section in message.sections:
        if section[0] == b"LBL1":
            section[1] = rebuilt


def _label_body(groups: int, entries: list[tuple[int, str, int]]) -> bytes:
    buckets: list[list[tuple[str, int]]] = [[] for _ in range(groups)]
    for index, name, text_index in entries:
        buckets[index].append((name, text_index))
    directory = bytearray(struct.pack("<I", groups))
    blob = bytearray()
    for bucket in buckets:
        directory += struct.pack("<II", len(bucket), 4 + 8 * groups + len(blob))
        for name, text_index in bucket:
            encoded = name.encode("utf-8")
            blob += bytes([len(encoded)]) + encoded + struct.pack("<I", text_index)
    return bytes(directory + blob)


def _tools():
    tools = str(require_tools())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import msbtmod
    import zsmod
    import zstool

    return msbtmod, zstool, zsmod
