"""Apply text and event-flow sources to a RomFS dump and install the Longshot mod folder."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.evflow import HookNote, epilogue_runs, patch_flow_bytes
from after_ocarina.labels import add_created_labels
from after_ocarina.boolexp import load_bool_expressions
from after_ocarina.gamedata import apply_gamedata, load_gamedata_edits
from after_ocarina.paths import (
    BUILD_DIR,
    ROOT,
    RSTB_REL,
    ZSDIC_REL,
    require_tools,
)
from after_ocarina.pristine import assert_pristine
from after_ocarina.rsdb import RsdbEdit, add_subflows, apply_table, load_rsdb_edits, resolve_tables
from after_ocarina.wake import LINK_SLEEP_REL
from after_ocarina.rstb import ENTRY_MARGIN, load_table, rewrite_hashed, strip_zs, target_entry
from after_ocarina.tapestry import apply_tapestry, load_tapestry
from after_ocarina.sources import FlowPatch, TextEdit, load_flow_patches, load_pending_lists, load_text_edits
from after_ocarina.zsio import dicts_for, pack, unpack


@dataclass(frozen=True)
class BuildResult:
    mod_id: str
    mod_name: str
    out_dir: Path
    installed: Path | None
    files: list[str]
    resource_size_table: bool
    edits: list[TextEdit]
    flows: tuple[HookNote, ...] = ()
    placement_report: str = ""


def build_mod(
    romfs: Path,
    *,
    mod_id: str,
    mod_name: str,
    text_dir: Path,
    events_path: Path,
    scenes_path: Path,
    out_dir: Path,
    events_dir: Path | None = None,
    mods_root: Path | None = None,
    install: bool = False,
) -> BuildResult:
    require_tools()
    romfs = romfs.resolve()
    out_dir = out_dir.resolve()
    if not romfs.is_dir():
        raise ModError(f"romfs is not a directory: {romfs}")
    _refuse_inside_repo(romfs, "romfs")
    if install:
        if mods_root is None:
            raise ModError("install requested without longshot_mods")
        mods_root = mods_root.resolve()
        _refuse_inside_repo(mods_root, "longshot_mods")
    if out_dir == ROOT:
        raise ModError("refusing to use the repository root as the build directory")

    edits = load_text_edits(text_dir)
    load_pending_lists(events_path, scenes_path)
    patches = load_flow_patches(events_dir or events_path.parent)
    rsdb_edits = load_rsdb_edits()
    gamedata_edits = load_gamedata_edits()
    bool_expressions = load_bool_expressions()
    assert_pristine(romfs, edits, patches)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    written = _apply_text(romfs, edits, out_dir)
    flow_files, notes = _apply_flows(romfs, patches, out_dir)
    written.extend(flow_files)
    wake_flows: list[str] = []
    for note in notes:
        for name in note.wake_subflows:
            if name not in wake_flows:
                wake_flows.append(name)
    rsdb_edits = add_subflows(rsdb_edits, "DefaultBootEvent", wake_flows)
    rsdb_edits = resolve_tables(rsdb_edits, romfs, _unpacker(romfs))
    written.extend(_apply_rsdb(romfs, rsdb_edits, out_dir))
    written.extend(_apply_gamedata(romfs, gamedata_edits, out_dir, bool_expressions))
    placed, placement_report = _apply_placement(romfs, out_dir)
    stone_files, stone_report = _apply_stones(romfs, out_dir)
    castle_files, castle_report = _apply_castle(romfs, out_dir)
    written.extend(castle_files)
    if castle_report:
        placement_report = f"{placement_report}\n{castle_report}" if placement_report else castle_report
    written.extend(placed)
    written.extend(stone_files)
    if stone_report:
        placement_report = f"{placement_report}\n{stone_report}" if placement_report else stone_report
    tapestry_files, tapestry_raises, tapestry_new = _apply_tapestry(romfs, out_dir)
    written.extend(tapestry_files)
    table_written = _grow_table_if_needed(
        romfs, out_dir, written, raises=tapestry_raises, additions=tapestry_new
    )
    installed = None
    if install and mods_root is not None:
        installed = install_tree(out_dir, mods_root, mod_id)

    result = BuildResult(
        mod_id=mod_id,
        mod_name=mod_name,
        out_dir=out_dir,
        installed=installed,
        files=written,
        resource_size_table=table_written,
        edits=edits,
        flows=notes,
        placement_report=placement_report,
    )
    _write_manifest(result)
    return result


def install_tree(out_dir: Path, mods_root: Path, mod_id: str) -> Path:
    """Replace ``mods_root/mod_id`` with this build. Other mod folders are left alone."""
    if not mods_root.parent.is_dir():
        raise ModError(f"Parent of the mods folder does not exist: {mods_root.parent}")
    mods_root.mkdir(exist_ok=True)
    mod_folder = mods_root / mod_id
    if mod_folder.exists():
        shutil.rmtree(mod_folder)
    shutil.copytree(out_dir, mod_folder / "romfs")
    return mod_folder


def report(result: BuildResult) -> str:
    lines = [
        f"{result.mod_name} ({result.mod_id})",
        f"output: {result.out_dir}",
        f"files: {len(result.files)}",
        "resource size table: " + ("shipped" if result.resource_size_table else "unchanged, not shipped"),
    ]
    for note in result.flows:
        lines.append(note.text())
    if result.placement_report:
        lines.append(result.placement_report)
    if result.installed is not None:
        lines.append(f"installed: {result.installed}")
    lines.append("Stop Longshot if it is running, start it again, and check logs/longshot_log.txt for:")
    lines.append(f"  mods: {result.mod_id}:")
    for rel in result.files:
        lines.append(f"  mods: {rel} <- {result.mod_id}")
    if result.resource_size_table:
        lines.append(f"  mods: {RSTB_REL} <- {result.mod_id}")
        lines.append("  and, on the table's line, \"(with the button prompts' sizes merged in)\"")
    lines.append("A folder whose name starts with '_' or '-' is off. exefs patches are logged and not loaded.")
    return "\n".join(lines)


def _apply_text(romfs: Path, edits: list[TextEdit], out_dir: Path) -> list[str]:
    tools = require_tools()
    groups: dict[str, list[TextEdit]] = {}
    for edit in edits:
        groups.setdefault(edit.pack, []).append(edit)
    dictionary = romfs / ZSDIC_REL
    written = []
    for rel, items in groups.items():
        src = _romfs_file(romfs, rel)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        creates = [edit for edit in items if edit.create]
        replaces = [edit for edit in items if not edit.create]
        source = src
        temporary = None
        try:
            if creates:
                dicts = dicts_for(romfs)
                raw, dict_id = unpack(src.read_bytes(), dicts)
                raw = add_created_labels(raw, creates)
                for edit in creates:
                    print(f"{edit.msbt}:{edit.label}\n  added: {edit.text}")
                if replaces:
                    temporary = tempfile.NamedTemporaryFile(suffix=".zs")
                    temporary.write(pack(raw, dict_id, dicts))
                    temporary.flush()
                    source = Path(temporary.name)
                else:
                    dest.write_bytes(pack(raw, dict_id, dicts))
                    written.append(rel)
                    continue
            cmd = [sys.executable, str(tools / "msbtmod.py"), "set", str(source), str(dest)]
            cmd.extend(edit.msbt_argument() for edit in replaces)
            if dictionary.is_file():
                cmd.extend(["--dict", str(dictionary)])
            try:
                _run(cmd)
            except ModError as exc:
                hint = _missing_label_hint(src, replaces, dictionary)
                if hint and ("has no message" in str(exc) or "no file" in str(exc)):
                    raise ModError(f"{exc}\n{hint}") from None
                raise
        finally:
            if temporary is not None:
                temporary.close()
        written.append(rel)
    return written


def _apply_flows(romfs: Path, patches: list[FlowPatch], out_dir: Path) -> tuple[list[str], tuple[HookNote, ...]]:
    if not patches:
        return [], ()
    dicts = dicts_for(romfs)
    written = []
    notes = []
    bed_raw: bytes | None = None
    for patch in patches:
        src = _romfs_file(romfs, patch.file)
        bed = None
        if patch.custom_wake and epilogue_runs(patch):
            if bed_raw is None:
                bed_src = _romfs_file(romfs, LINK_SLEEP_REL)
                try:
                    bed_raw, _bed_id = unpack(bed_src.read_bytes(), dicts)
                except ModError as exc:
                    raise ModError(f"{LINK_SLEEP_REL}: {exc}") from None
            bed = bed_raw
        try:
            raw, dict_id = unpack(src.read_bytes(), dicts)
            patched, note = patch_flow_bytes(raw, patch, bed=bed)
        except ModError as exc:
            text = str(exc)
            if text.startswith(patch.file):
                raise
            raise ModError(f"{patch.file}: {text}") from None
        dest = out_dir / patch.file
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pack(patched, dict_id, dicts))
        written.append(patch.file)
        notes.append(note)
        print(note.text())
    return written, tuple(notes)


def _apply_gamedata(romfs: Path, edits, out_dir: Path, expressions=None) -> list[str]:
    expressions = list(expressions or [])
    if not edits and not expressions:
        return []
    dicts = dicts_for(romfs)
    by_file: dict[str, list] = {}
    by_expr: dict[str, list] = {}
    for edit in edits:
        by_file.setdefault(edit.file, []).append(edit)
    for edit in expressions:
        by_expr.setdefault(edit.file, []).append(edit)
    written = []
    for rel in list(dict.fromkeys([*by_file, *by_expr])):
        items = by_file.get(rel, [])
        exprs = by_expr.get(rel, [])
        src = _romfs_file(romfs, rel)
        try:
            raw, dict_id = unpack(src.read_bytes(), dicts)
            patched = apply_gamedata(raw, items, exprs)
        except ModError as exc:
            text = str(exc)
            if text.startswith(rel):
                raise
            raise ModError(f"{rel}: {text}") from None
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pack(patched, dict_id, dicts))
        written.append(rel)
        if items:
            print(f"{rel}: Navi_Ban ResetTypeValue {items[0].reset_from} -> {items[0].reset_to}")
        for expr in exprs:
            print(f"{rel}: {expr.name} AND NOT {expr.flag}")
    return written


def _apply_castle(romfs: Path, out_dir: Path) -> tuple[list[str], str]:
    from after_ocarina.castle import apply_delete, load_scene_logics
    from after_ocarina.logic import LOGIC_REL as DONOR_REL
    from after_ocarina.temple import apply_temple_door

    specs = load_scene_logics()
    dicts = dicts_for(romfs)
    donor_src = _romfs_file(romfs, DONOR_REL)
    donor_raw, _donor_id = unpack(donor_src.read_bytes(), dicts)
    written: list[str] = []
    notes: list[str] = []
    for spec in specs:
        for item in spec.deletes:
            src = _romfs_file(romfs, item.logic)
            blob = src.read_bytes()
            raw, dict_id = unpack(blob, dicts)
            patched, note = apply_delete(
                raw,
                item.logic,
                item.instance,
                donor=donor_raw,
                donor_label=DONOR_REL,
                open_instance=item.open,
                node_index=item.node,
                node_name=item.node_name,
            )
            compressed = blob[:4] == b"\x28\xb5\x2f\xfd"
            out_bytes = pack(patched, dict_id, dicts) if compressed else patched
            dest = out_dir / item.logic
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(out_bytes)
            print(note)
            written.append(item.logic)
            notes.append(note)
        if spec.door:
            src = _romfs_file(romfs, spec.door)
            blob = src.read_bytes()
            raw, dict_id = unpack(blob, dicts)
            patched, note = apply_temple_door(raw, spec.door, donor=donor_raw, donor_label=DONOR_REL)
            compressed = blob[:4] == b"\x28\xb5\x2f\xfd"
            out_bytes = pack(patched, dict_id, dicts) if compressed else patched
            dest = out_dir / spec.door
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(out_bytes)
            print(note)
            written.append(spec.door)
            notes.append(note)
    return written, "\n".join(notes)


def _apply_stones(romfs: Path, out_dir: Path) -> tuple[list[str], str]:
    from after_ocarina.stones import load_stones, place_stones

    spec = load_stones()
    dicts = dicts_for(romfs)
    template_src = _romfs_file(romfs, spec.template)
    template_raw, _template_id = unpack(template_src.read_bytes(), dicts)
    dests: dict[str, bytes] = {}
    dict_ids: dict[str, int | None] = {}
    blobs: dict[str, bytes] = {}
    for stone in spec.stones:
        if stone.banc in dests:
            continue
        src = _romfs_file(romfs, stone.banc)
        blob = src.read_bytes()
        raw, dict_id = unpack(blob, dicts)
        dests[stone.banc] = raw
        dict_ids[stone.banc] = dict_id
        blobs[stone.banc] = blob
    changed, note = place_stones(dests, template_raw, spec, romfs)
    written: list[str] = []
    for rel, raw in changed.items():
        blob = blobs[rel]
        compressed = blob[:4] == b"\x28\xb5\x2f\xfd"
        out_bytes = pack(raw, dict_ids[rel], dicts) if compressed else raw
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(out_bytes)
        written.append(rel)
    print(note)
    return written, note


def _apply_placement(romfs: Path, out_dir: Path) -> tuple[list[str], str]:
    from after_ocarina.logic import apply_logic
    from after_ocarina.saria import load_placement, place_actors

    spec = load_placement()
    written: list[str] = []
    notes: list[str] = []
    if spec.place:
        dicts = dicts_for(romfs)
        unpacked: dict[str, bytes] = {}
        dict_ids: dict[str, int | None] = {}
        for rel in spec.files:
            src = _romfs_file(romfs, rel)
            raw, dict_id = unpack(src.read_bytes(), dicts)
            unpacked[rel] = raw
            dict_ids[rel] = dict_id
        changed, report = place_actors(unpacked, spec)
        for rel, raw in changed.items():
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(pack(raw, dict_ids[rel], dicts))
            written.append(rel)
        if report:
            notes.append(report)
    if spec.logic:
        src = _romfs_file(romfs, spec.logic)
        blob = src.read_bytes()
        dicts = dicts_for(romfs)
        raw, dict_id = unpack(blob, dicts)
        patched, note = apply_logic(raw, spec.logic)
        compressed = blob[:4] == b"\x28\xb5\x2f\xfd"
        out_bytes = pack(patched, dict_id, dicts) if compressed else patched
        dest = out_dir / spec.logic
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(out_bytes)
        written.append(spec.logic)
        notes.append(note)
    if spec.schedule:
        from after_ocarina.schedule import apply_schedule

        src = _romfs_file(romfs, spec.schedule)
        blob = src.read_bytes()
        compressed = blob[:4] == b"\x28\xb5\x2f\xfd"
        if compressed:
            dicts = dicts_for(romfs)
            raw, dict_id = unpack(blob, dicts)
        else:
            raw, dict_id = blob, None
        patched, note = apply_schedule(raw, spec.schedule)
        out_bytes = pack(patched, dict_id, dicts_for(romfs)) if compressed else patched
        dest = out_dir / spec.schedule
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(out_bytes)
        written.append(spec.schedule)
        notes.append(note)
    return written, "\n".join(notes)


def _unpacker(romfs: Path):
    dicts = dicts_for(romfs)

    def unpack_file(path: Path) -> bytes:
        raw, _dict_id = unpack(path.read_bytes(), dicts)
        return raw

    return unpack_file


def _apply_rsdb(romfs: Path, edits: list[RsdbEdit], out_dir: Path) -> list[str]:
    if not edits:
        return []
    dicts = dicts_for(romfs)
    by_file: dict[str, list[RsdbEdit]] = {}
    for edit in edits:
        by_file.setdefault(edit.file, []).append(edit)
    written = []
    for rel, items in by_file.items():
        src = _romfs_file(romfs, rel)
        try:
            raw, dict_id = unpack(src.read_bytes(), dicts)
            patched = apply_table(raw, items)
        except ModError as exc:
            text = str(exc)
            if text.startswith(rel):
                raise
            raise ModError(f"{rel}: {text}") from None
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pack(patched, dict_id, dicts))
        written.append(rel)
        described = []
        for item in items:
            if item.subflows:
                described.append(f"{item.row} subflows")
            if item.clone_from:
                described.append(f"{item.row} cloned from {item.clone_from}")
            if item.delete_keys or item.clear_participants or (item.set_fields and not item.subflows and item.clone_from is None):
                described.append(f"{item.row} keys")
        print(f"{rel}: {', '.join(described)}")
    return written


def _apply_tapestry(romfs: Path, out_dir: Path) -> tuple[list[str], dict[str, int], dict[str, int]]:
    spec = load_tapestry()
    if spec is None:
        return [], {}, {}
    edit = apply_tapestry(romfs, out_dir, spec)
    return list(edit.files), edit.raises, edit.additions


def _grow_table_if_needed(
    romfs: Path,
    out_dir: Path,
    written: list[str],
    raises: dict[str, int] | None = None,
    additions: dict[str, int] | None = None,
) -> bool:
    extra = dict(raises or {})
    new_entries = dict(additions or {})
    table_path = romfs / RSTB_REL
    table = load_table(table_path)
    dicts = dicts_for(romfs)
    updates: dict[str, int] = {}
    added = {strip_zs(path) for path in new_entries}
    for rel in written:
        key = strip_zs(rel)
        if key in added:
            continue
        source = romfs / rel
        if not source.is_file():
            raise ModError(
                f"{rel} is not in the dump and was not listed as a new hashed entry. "
                "A missing entry is still an error unless the caller adds one on purpose."
            )
        _old_raw, _old_id = unpack(source.read_bytes(), dicts)
        new_raw, _new_id = unpack((out_dir / rel).read_bytes(), dicts)
        old_size = len(_old_raw)
        new_size = len(new_raw)
        entry = table.size_for(rel)
        if entry is None:
            raise ModError(
                f"{rel} has no hashed or named entry in the resource size table. "
                "An existing file is raised in place. A brand-new file has to be passed as a new hashed entry."
            )
        if new_raw == _old_raw:
            print(f"{rel}: unpacked {old_size} bytes, unchanged, table entry {entry} left")
            continue
        needed = target_entry(entry, new_size, ENTRY_MARGIN, old_unpacked=old_size)
        if needed is None:
            raise ModError(f"{rel}: changed, but the size-table entry was not raised")
        updates[key] = needed
        print(
            f"{rel}: unpacked {old_size} -> {new_size}, "
            f"table entry {entry} -> {needed} (reservation plus growth plus {ENTRY_MARGIN})"
        )
    for path, size in extra.items():
        old = table.size_for(path)
        print(f"{path}: table entry {old} -> {size} (reservation plus growth plus {ENTRY_MARGIN})")
    for path, size in new_entries.items():
        print(f"{strip_zs(path)}: new hashed entry {size}")
    updates.update(extra)
    if not updates and not new_entries:
        return False
    raw, dict_id = unpack(table_path.read_bytes(), dicts)
    edited = rewrite_hashed(raw, updates, new_entries)
    dest = out_dir / RSTB_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pack(edited, dict_id, dicts))
    return True


def _romfs_file(romfs: Path, rel: str) -> Path:
    path = (romfs / rel).resolve()
    if not path.is_relative_to(romfs):
        raise ModError(f"Path escapes the romfs: {rel}")
    if not path.is_file():
        raise ModError(f"Not in the dump: {rel}")
    return path


def _refuse_inside_repo(path: Path, key: str) -> None:
    if path == ROOT or _is_inside(path, ROOT):
        raise ModError(
            f"{key} points inside this repository ({path}). "
            "Keep the dump and the Longshot mods folder outside the repo so game-derived files are not committed."
        )


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _missing_label_hint(src: Path, items: list[TextEdit], dictionary: Path) -> str:
    msbt = sorted({item.msbt for item in items})
    lines = [
        "List that message file with:",
        "  "
        + " ".join(
            [
                sys.executable,
                str(require_tools() / "msbtmod.py"),
                "cat",
                str(src),
                msbt[0] if len(msbt) == 1 else "<msbt-path-inside-the-pack>",
            ]
            + (["--dict", str(dictionary)] if dictionary.is_file() else [])
        ),
        "Point the YAML edit at a label you can trigger in game, then build again.",
    ]
    return "\n".join(lines)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        detail = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
        raise ModError(detail or f"Command failed ({proc.returncode}): {cmd[0]}")
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")


def _write_manifest(result: BuildResult) -> None:
    payload = {
        "mod_id": result.mod_id,
        "mod_name": result.mod_name,
        "files": result.files,
        "resource_size_table": result.resource_size_table,
        "edits": [
            {"pack": edit.pack, "msbt": edit.msbt, "label": edit.label, "source": edit.source}
            for edit in result.edits
        ],
        "flows": [
            {"file": note.file, "hooked": note.hooked, "original_event": note.original_event}
            for note in result.flows
        ],
    }
    manifest = result.out_dir.parent / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def default_out_dir() -> Path:
    return BUILD_DIR
