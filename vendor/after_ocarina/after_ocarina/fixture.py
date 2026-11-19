"""Invented RomFS used to prove the build without a game dump.

The bytes are a tiny MSBT inside a SARC, compressed with zstd and paired with
a RESTBL that names those paths. The sentences are written for the test. Nothing
here is copied from the game.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import zstandard

from after_ocarina.boolexp import load_bool_expressions
from after_ocarina.evflow import (
    sample_flow_bytes,
    sample_saria_song_flow_bytes,
    sample_talk_flow_bytes,
    sample_zelda_flow_bytes,
)
from after_ocarina.gamedata import load_gamedata_edits, sample_gamedata, to_big_endian
from after_ocarina.paths import RSTB_REL, require_tools
from after_ocarina.rsdb import load_rsdb_edits, note_bed_row_also_present, sample_event_setting, sample_table
from after_ocarina.rstb import strip_zs
from after_ocarina.sources import TextEdit, load_flow_patches
from after_ocarina.wake import LINK_SLEEP_REL, sample_bed_bytes

EVENT_SETTING_REL = "RSDB/EventSetting.Product.111.rstbl.byml.zs"

VANILLA = "Vanilla line before the mod."
KEEPER_LABEL = "PipelineKeeper"
KEEPER_TEXT = "Leave this sentence alone."


def _write_blob(dest: Path, rel: str, raw: bytes, sizes: dict[str, int], entries: dict[str, int], slack: int) -> None:
    blob = zstandard.ZstdCompressor(level=3, write_content_size=True).compress(raw)
    path = dest / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    sizes[rel] = len(raw)
    entries[strip_zs(rel)] = len(raw) + slack


def write_romfs(dest: Path, edits: list[TextEdit], *, slack: int) -> dict[str, int]:
    """Write a fake dump. Returns each pack's unpacked SARC size."""
    if not edits:
        raise ValueError("fixture needs at least one text edit")
    tools = str(require_tools())
    import sys

    if tools not in sys.path:
        sys.path.insert(0, tools)
    import zsmod

    by_pack: dict[str, list[TextEdit]] = {}
    for edit in edits:
        by_pack.setdefault(edit.pack, []).append(edit)

    sizes: dict[str, int] = {}
    entries: dict[str, int] = {"Other/untouched.file": 800}
    for pack, items in by_pack.items():
        by_msbt: dict[str, list[TextEdit]] = {}
        for edit in items:
            by_msbt.setdefault(edit.msbt, []).append(edit)
        files = {}
        for msbt, msbt_edits in by_msbt.items():
            messages = [(KEEPER_LABEL, _text(KEEPER_TEXT))]
            for edit in msbt_edits:
                if edit.create:
                    continue
                messages.append((edit.label, _text(VANILLA)))
            files[msbt] = build_msbt(messages)
        sarc = zsmod.sarc_pack(files)
        blob = zstandard.ZstdCompressor(level=3, write_content_size=True).compress(sarc)
        out = dest / pack
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
        sizes[pack] = len(sarc)
        entries[strip_zs(pack)] = len(sarc) + slack

    for patch in load_flow_patches():
        if patch.rumor is not None:
            from after_ocarina.rumor import sample_rumor_flow_bytes

            raw = sample_rumor_flow_bytes(patch.rumor)
        elif patch.song_gate:
            raw = sample_saria_song_flow_bytes()
        elif patch.retarget_next:
            raw = sample_zelda_flow_bytes()
        elif patch.epilogue_talk and patch.entry_point:
            raw = sample_talk_flow_bytes(patch.entry_point)
        else:
            raw = sample_flow_bytes()
        blob = zstandard.ZstdCompressor(level=3, write_content_size=True).compress(raw)
        flow_path = dest / patch.file
        flow_path.parent.mkdir(parents=True, exist_ok=True)
        flow_path.write_bytes(blob)
        sizes[patch.file] = len(raw)
        entries[strip_zs(patch.file)] = len(raw) + slack

    rsdb_edits = load_rsdb_edits()
    by_file: dict[str, list] = {}
    for edit in rsdb_edits:
        if not edit.file:
            continue
        by_file.setdefault(edit.file, []).append(edit)
    for rel, items in by_file.items():
        raw = sample_table(items)
        if Path(rel).name.startswith("GameAnalyzedEventInfo"):
            raw = note_bed_row_also_present(raw)
        _write_blob(dest, rel, raw, sizes, entries, slack)
    if any(edit.find_row for edit in rsdb_edits):
        _write_blob(dest, EVENT_SETTING_REL, sample_event_setting(), sizes, entries, slack)
    gamedata_edits = load_gamedata_edits()
    expressions = load_bool_expressions()
    by_gamedata: dict[str, list] = {}
    by_expression: dict[str, list] = {}
    for edit in gamedata_edits:
        by_gamedata.setdefault(edit.file, []).append(edit)
    for edit in expressions:
        by_expression.setdefault(edit.file, []).append(edit)
    for rel in list(dict.fromkeys([*by_gamedata, *by_expression])):
        _write_blob(
            dest,
            rel,
            to_big_endian(sample_gamedata(by_gamedata.get(rel, []), by_expression.get(rel, []))),
            sizes,
            entries,
            slack,
        )
    if any(patch.custom_wake and patch.epilogue for patch in load_flow_patches()):
        _write_blob(dest, LINK_SLEEP_REL, sample_bed_bytes(), sizes, entries, slack)
    from after_ocarina.saria import load_placement, sample_banc

    placement = load_placement()
    if placement.place:
        for rel, raw in sample_banc(placement).items():
            _write_blob(dest, rel, raw, sizes, entries, slack)
    if placement.logic:
        from after_ocarina.logic import sample_logic_bytes

        raw = sample_logic_bytes()
        logic_path = dest / placement.logic
        logic_path.parent.mkdir(parents=True, exist_ok=True)
        logic_path.write_bytes(raw)
        sizes[placement.logic] = len(raw)
        entries[strip_zs(placement.logic)] = len(raw) + slack
    if placement.schedule:
        from after_ocarina.schedule import sample_schedule_bytes

        raw = sample_schedule_bytes()
        schedule_path = dest / placement.schedule
        schedule_path.parent.mkdir(parents=True, exist_ok=True)
        schedule_path.write_bytes(raw)
        sizes[placement.schedule] = len(raw)
        entries[strip_zs(placement.schedule)] = len(raw) + slack
    from after_ocarina.castle import load_scene_logics, sample_logic_for
    from after_ocarina.errors import ModError
    from after_ocarina.temple import sample_temple_logic

    def sample_for(logic: str) -> bytes:
        try:
            return sample_logic_for(logic)
        except ModError:
            return sample_temple_logic(logic)

    for spec in load_scene_logics():
        paths = [item.logic for item in spec.deletes]
        if spec.door:
            paths.append(spec.door)
        for logic in paths:
            if logic in sizes:
                continue
            raw = sample_for(logic)
            logic_path = dest / logic
            logic_path.parent.mkdir(parents=True, exist_ok=True)
            logic_path.write_bytes(raw)
            sizes[logic] = len(raw)
            entries[strip_zs(logic)] = len(raw) + slack

    from after_ocarina.stones import load_stones, sample_market_banc, sample_town_banc

    stones = load_stones()
    market = sample_market_banc()
    market_path = dest / stones.template
    market_path.parent.mkdir(parents=True, exist_ok=True)
    market_path.write_bytes(market)
    for stone in stones.stones:
        raw = sample_town_banc("Spawn")
        logic_path = dest / stone.banc
        logic_path.parent.mkdir(parents=True, exist_ok=True)
        logic_path.write_bytes(raw)
        sizes[stone.banc] = len(raw)
        entries[strip_zs(stone.banc)] = len(raw) + slack
        pack = dest / "Pack" / "Actor" / f"{stone.gyaml}.pack.zs"
        pack.parent.mkdir(parents=True, exist_ok=True)
        pack.write_bytes(b"pack")
        model = dest / "Model" / f"{stone.gyaml}.bfres"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"model")

    from after_ocarina.tapestry import load_tapestry, reserved_entries, source_files

    tapestry = load_tapestry()
    if tapestry is not None:
        for rel, raw in source_files(tapestry).items():
            _write_blob(dest, rel, raw, sizes, entries, slack)
        entries.update(reserved_entries())

    table = dest / RSTB_REL
    table.parent.mkdir(parents=True, exist_ok=True)
    raw = make_restbl(entries)
    table.write_bytes(zstandard.ZstdCompressor(level=3, write_content_size=True).compress(raw))
    return sizes


def read_label(zs_path: Path, msbt: str, label: str) -> str:
    """Return one message in the editable form msbtmod prints."""
    tools = str(require_tools())
    import sys

    if tools not in sys.path:
        sys.path.insert(0, tools)
    import msbtmod
    import zsmod
    import zstool

    raw, _did = zsmod.unzs(zs_path.read_bytes(), {})
    parsed = msbtmod.Msbt(zstool.Sarc(raw).read(msbt))
    return msbtmod.to_text(parsed.texts[parsed.labels[label]])


def make_restbl(entries: dict[str, int], name_size: int = 160) -> bytes:
    hashed = sorted((zlib.crc32(path.encode()) & 0xFFFFFFFF, size) for path, size in entries.items())
    buf = bytearray(b"RESTBL")
    buf += struct.pack("<I", 1)
    buf += struct.pack("<III", name_size, len(hashed), 0)
    for crc, size in hashed:
        buf += struct.pack("<II", crc, size)
    return bytes(buf)


def build_msbt(messages: list[tuple[str, bytes]]) -> bytes:
    """Little-endian UTF-8 MSBT with one label group. ``messages`` are (label, bytes including NUL)."""
    labels = struct.pack("<I", 1) + struct.pack("<II", len(messages), 12)
    for index, (label, _text_bytes) in enumerate(messages):
        labels += bytes([len(label)]) + label.encode() + struct.pack("<I", index)
    count = len(messages)
    at = 4 + 4 * count
    offsets = []
    blob = b""
    for _label, text in messages:
        offsets.append(at + len(blob))
        blob += text
    txt = struct.pack("<I", count) + struct.pack("<%dI" % count, *offsets) + blob
    body = _section(b"LBL1", labels) + _section(b"TXT2", txt)
    head = (
        b"MsgStdBn"
        + b"\xff\xfe"
        + b"\0\0"
        + bytes([0, 3])
        + struct.pack("<H", 2)
        + b"\0\0"
        + struct.pack("<I", 0x20 + len(body))
        + b"\0" * 10
    )
    return head + body


def _section(magic: bytes, body: bytes) -> bytes:
    return magic + struct.pack("<I", len(body)) + b"\0" * 8 + body + b"\xab" * (-len(body) % 16)


def _text(value: str) -> bytes:
    return value.encode() + b"\0"
