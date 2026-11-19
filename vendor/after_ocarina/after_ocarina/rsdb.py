"""Edit RSDB tables: append subflow names, clone a row, or edit keys on a row.

``tools/byml.py`` round-trips the table. Rows are a ``__RowId``-sorted list.
A subflow edit that has no ``SubFlowList`` is an error. A clone copies one row,
gives it a new ``__RowId``, and overwrites keys that already exist. It does not
invent a key. A ``set`` without ``clone`` or ``subflows`` edits the row that is
already there: ``set`` may add a key, and ``delete`` removes one.
``clear_participants`` empties ``Participants`` on every step. Step names and
``__RowId`` are not rewritten. ``Category`` is stored as a u32. A ``find_row``
edit has no file until ``resolve_tables`` finds the table that contains that
``__RowId``. When several tables have the row, the file whose name starts with
``EventSetting`` is the one that is cloned.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.paths import RSDB_DIR, require_tools
from after_ocarina.sources import _mapping, _rel_path


@dataclass(frozen=True)
class RsdbEdit:
    file: str
    row: str
    subflows: tuple[str, ...]
    source: str
    clone_from: str | None = None
    set_fields: tuple[tuple[str, object], ...] = ()
    find_row: str | None = None
    delete_keys: tuple[str, ...] = ()
    clear_participants: bool = False


def load_rsdb_edits(directory: Path | None = None) -> list[RsdbEdit]:
    directory = directory or RSDB_DIR
    if not directory.is_dir():
        raise ModError(f"Missing RSDB source directory: {directory}")
    files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    edits: list[RsdbEdit] = []
    seen: dict[tuple[str, str], str] = {}
    for path in files:
        for edit in _edits_in(path):
            key = (edit.file or edit.find_row or "", edit.row)
            previous = seen.get(key)
            if previous is not None:
                raise ModError(f"{edit.file or edit.find_row} row {edit.row} is edited by both {previous} and {path.name}")
            seen[key] = path.name
            edits.append(edit)
    return edits


def add_subflows(edits: list[RsdbEdit], row: str, names: list[str]) -> list[RsdbEdit]:
    """Append flowchart names to the subflow edit for ``row``. A clone copies the list afterwards."""
    if not names:
        return edits
    out: list[RsdbEdit] = []
    found = False
    for edit in edits:
        if edit.row == row and edit.subflows and edit.clone_from is None and edit.find_row is None:
            merged = list(edit.subflows)
            for name in names:
                if name not in merged:
                    merged.append(name)
            out.append(replace(edit, subflows=tuple(merged)))
            found = True
        else:
            out.append(edit)
    if not found:
        raise ModError(f"no SubFlowList edit for {row} to add {', '.join(names)}")
    return out


def resolve_tables(edits: list[RsdbEdit], romfs: Path, unpack_file) -> list[RsdbEdit]:
    """Fill in the file for each ``find_row`` edit. ``unpack_file(path)`` returns BYML bytes."""
    pending = [edit for edit in edits if edit.find_row]
    if not pending:
        return edits
    matches: dict[str, list[str]] = {edit.find_row or "": [] for edit in pending}
    unreadable = 0
    byml = _byml()
    for path in sorted(romfs.rglob("*.rstbl.byml.zs")):
        try:
            root = byml.loads(unpack_file(path))
        except Exception:
            unreadable += 1
            continue
        rows = _row_list_or_none(root)
        if rows is None:
            continue
        ids = {row.get("__RowId") for row in rows if isinstance(row, dict)}
        rel = path.relative_to(romfs).as_posix()
        for name in matches:
            if name in ids:
                matches[name].append(rel)
    resolved = []
    for edit in edits:
        if not edit.find_row:
            resolved.append(edit)
            continue
        found = matches[edit.find_row]
        chosen = _prefer_event_setting(found)
        if chosen is None:
            shown = ", ".join(found) if found else "no table"
            extra = f" {unreadable} table(s) could not be read." if unreadable else ""
            raise ModError(
                f"row {edit.find_row} for {edit.row} was found in {shown}.{extra} "
                "Need one table, or one whose file name starts with EventSetting."
            )
        print(f"event setting: row {edit.find_row} is in {chosen}")
        resolved.append(replace(edit, file=chosen))
    return resolved


def _prefer_event_setting(found: list[str]) -> str | None:
    """One match is used as-is. Several matches keep the EventSetting file."""
    if len(found) == 1:
        return found[0]
    preferred = [rel for rel in found if Path(rel).name.startswith("EventSetting")]
    if len(preferred) == 1:
        return preferred[0]
    return None


def note_bed_row_also_present(data: bytes) -> bytes:
    """Add the bed row the real GameAnalyzedEventInfo carries, without EventSetting keys."""
    byml = _byml()
    root = byml.loads(data)
    if any(isinstance(row, dict) and row.get("__RowId") == "BedBootEvent_Alive" for row in root):
        return data
    root.append({"__RowId": "BedBootEvent_Alive", "IsPlayerWarpFirstFrame": False})
    root.sort(key=lambda row: row["__RowId"])
    return byml.dumps(root)


def apply_table(data: bytes, edits: list[RsdbEdit]) -> bytes:
    """Append subflow names, edit keys on existing rows, then clone. Rows are sorted by ``__RowId``."""
    if not edits:
        return data
    byml = _byml()
    label = edits[0].file or edits[0].row
    try:
        root = byml.loads(data)
    except Exception as exc:
        raise ModError(f"{label}: could not read the RSDB table ({exc})") from None
    rows = _row_list(root, label)
    for edit in edits:
        if edit.subflows:
            _apply_subflows(rows, edit)
    for edit in edits:
        if _is_update(edit):
            _apply_update(rows, edit)
    for edit in edits:
        if edit.clone_from:
            _apply_clone(rows, edit)
    rows.sort(key=lambda row: row["__RowId"])
    try:
        written = byml.dumps(root)
        again = byml.loads(written)
    except Exception as exc:
        hint = ""
        if "hash-map" in str(exc):
            hint = " The table has a hash-map node, which byml.py cannot write."
        raise ModError(f"{label}: could not write the RSDB table ({exc}).{hint}") from None
    if not byml.same(root, again):
        raise ModError(f"{label}: rewriting the RSDB table changed it")
    return written


def sample_table(edits: list[RsdbEdit]) -> bytes:
    """A tiny table. Edited rows start with SubFlowList [Common]. Clone targets are absent."""
    rows: dict[str, dict] = {
        "Common": {"__RowId": "Common", "SubFlowList": ["Common"]},
        "Zelda": {"__RowId": "Zelda", "SubFlowList": ["Common"]},
    }
    for edit in edits:
        if edit.find_row:
            continue
        if edit.subflows:
            row = rows.setdefault(edit.row, {"__RowId": edit.row, "SubFlowList": ["Common"]})
            row.setdefault("SubFlowList", ["Common"])
        if edit.clone_from:
            source = rows.setdefault(edit.clone_from, {"__RowId": edit.clone_from, "SubFlowList": ["Common"]})
            source.setdefault("SubFlowList", ["Common"])
            for key, value in edit.set_fields:
                if key not in source:
                    source[key] = _placeholder(value)
        if _is_update(edit):
            row = rows.setdefault(edit.row, {"__RowId": edit.row})
            row.setdefault("Steps", ["NotReady", "Ready", "Reset", "Step01", "Step02", "Step05", "Complete"])
            row.setdefault("Category", _byml().U32(460120899))
            for key in edit.delete_keys:
                row.setdefault(key, False)
    ordered = sorted(rows.values(), key=lambda row: row["__RowId"])
    return _byml().dumps(ordered)


def sample_event_setting() -> bytes:
    """Stand-in for the EventSetting table. The real path is found by the BedBootEvent_Alive row."""
    rows = [
        {
            "__RowId": "Alive",
            "FlowchartFileName": "Work/Event/EventFlow/Other.evfc",
            "EntryPointName": "Alive",
        },
        {
            "__RowId": "BedBootEvent_Alive",
            "FlowchartFileName": "Work/Event/EventFlow/LinkSleepInBed.evfc",
            "EntryPointName": "BedBootEvent",
            "PlayerPos": "LinkBed",
            "NPCTalkNoPause": ["Npc_Kokiri014"],
            "IgnoreDuckingOnFlowchart": True,
        },
    ]
    rows.sort(key=lambda row: row["__RowId"])
    return _byml().dumps(rows)


def _edits_in(path: Path) -> list[RsdbEdit]:
    data = _mapping(path)
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
        extra = set(row) - {"file", "row", "subflows", "clone", "find_row", "set", "delete", "clear_participants"}
        if extra:
            raise ModError(f"{path.name}: edit {index} has unknown keys: {', '.join(sorted(extra))}")
        where = f"{path.name}: edit {index}"
        name = row.get("row")
        if not isinstance(name, str) or not name:
            raise ModError(f"{where} row must be a non-empty string")
        find_row = row.get("find_row")
        clone_from = row.get("clone")
        has_file = "file" in row
        if find_row is not None and has_file:
            raise ModError(f"{where}: find_row looks the file up; do not set file as well")
        if find_row is not None and clone_from is not None:
            raise ModError(f"{where}: find_row clones that row; do not also set clone")
        if not isinstance(find_row, str) and find_row is not None:
            raise ModError(f"{where} find_row must be a string")
        if find_row is not None and not find_row:
            raise ModError(f"{where} find_row must be a non-empty string")
        if clone_from is not None and (not isinstance(clone_from, str) or not clone_from):
            raise ModError(f"{where} clone must be the source row id")
        subflows: tuple[str, ...] = ()
        if "subflows" in row:
            subflows = tuple(_subflow_names(row.get("subflows"), where))
        set_fields = _set_fields(row.get("set"), where) if "set" in row else ()
        delete_keys = _delete_keys(row.get("delete"), where) if "delete" in row else ()
        clear_participants = row.get("clear_participants", False)
        if not isinstance(clear_participants, bool):
            raise ModError(f"{where} clear_participants must be a bool")
        if find_row or clone_from:
            if delete_keys:
                raise ModError(f"{where}: a clone has no delete list")
            if clear_participants:
                raise ModError(f"{where}: a clone does not clear participants")
            if subflows:
                raise ModError(f"{where}: a clone has no subflows list; it copies the source row")
            if not set_fields:
                raise ModError(f"{where}: set must name the keys to change on the cloned row")
            rel = ""
            if clone_from and not find_row:
                rel = _rel_path(row.get("file"), path, index, "file", suffix=".rstbl.byml.zs")
            edits.append(
                RsdbEdit(
                    file=rel,
                    row=name,
                    subflows=(),
                    source=path.name,
                    clone_from=clone_from or find_row,
                    set_fields=set_fields,
                    find_row=find_row,
                )
            )
            continue
        rel = _rel_path(row.get("file"), path, index, "file", suffix=".rstbl.byml.zs")
        if delete_keys or clear_participants or ("set" in row and "subflows" not in row):
            if subflows:
                raise ModError(f"{where}: an in-place edit has no subflows list")
            if not set_fields and not delete_keys and not clear_participants:
                raise ModError(f"{where}: set or delete must name a key")
            edits.append(
                RsdbEdit(
                    file=rel,
                    row=name,
                    subflows=(),
                    source=path.name,
                    set_fields=set_fields,
                    delete_keys=delete_keys,
                    clear_participants=clear_participants,
                )
            )
            continue
        if not subflows:
            raise ModError(f"{where} subflows must be a non-empty list")
        edits.append(RsdbEdit(file=rel, row=name, subflows=subflows, source=path.name))
    return edits


def _subflow_names(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ModError(f"{where} subflows must be a non-empty list")
    names = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ModError(f"{where} subflow names must be non-empty strings")
        if item in names:
            raise ModError(f"{where} repeats {item}")
        names.append(item)
    return names


def _delete_keys(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ModError(f"{where} delete must be a non-empty list")
    keys = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ModError(f"{where} delete names must be non-empty strings")
        if item in keys:
            raise ModError(f"{where} repeats {item}")
        if item in ("Steps", "__RowId"):
            raise ModError(f"{where}: {item} stays on the row")
        keys.append(item)
    return tuple(keys)


def _is_update(edit: RsdbEdit) -> bool:
    return bool(edit.delete_keys) or edit.clear_participants or (
        bool(edit.set_fields) and not edit.subflows and edit.clone_from is None and edit.find_row is None
    )


def _set_fields(value: object, where: str) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, dict) or not value:
        raise ModError(f"{where} set must be a mapping")
    fields = []
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ModError(f"{where} set keys must be strings")
        if isinstance(item, bool) or isinstance(item, int) or isinstance(item, str):
            fields.append((key, item))
            continue
        raise ModError(f"{where} set {key} must be a bool, int, or string")
    return tuple(fields)


def _row_list(root: object, label: str) -> list:
    found = _row_list_or_none(root)
    if found is None:
        raise ModError(f"{label}: RSDB root is not a list of __RowId rows")
    return found


def _row_list_or_none(root: object) -> list | None:
    if isinstance(root, list):
        return root
    if isinstance(root, dict):
        for value in root.values():
            if isinstance(value, list) and value and isinstance(value[0], dict) and "__RowId" in value[0]:
                return value
    return None


def _apply_subflows(rows: list, edit: RsdbEdit) -> None:
    row = _one_row(rows, edit.file, edit.row)
    flows = row.get("SubFlowList")
    if not isinstance(flows, list):
        raise ModError(f"{edit.file}: row {edit.row} has no SubFlowList. Keys: {', '.join(sorted(row))}")
    if any(not isinstance(item, str) for item in flows):
        raise ModError(f"{edit.file}: row {edit.row} SubFlowList is not a list of strings")
    for name in edit.subflows:
        if name not in flows:
            flows.append(name)


def _apply_update(rows: list, edit: RsdbEdit) -> None:
    """Set keys, adding one that is missing, and delete keys that are present. ``Steps`` stays."""
    row = _one_row(rows, edit.file, edit.row)
    for key in edit.delete_keys:
        if key not in row:
            raise ModError(f"{edit.file}: row {edit.row} has no {key} to delete. Keys: {', '.join(sorted(row))}")
        del row[key]
    for key, value in edit.set_fields:
        if key in ("Steps", "__RowId"):
            raise ModError(f"{edit.file}: row {edit.row} does not rewrite {key}")
        if key == "Category":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ModError(f"{edit.file}: Category must be an integer")
            value = _byml().U32(value)
        row[key] = value
    if edit.clear_participants:
        steps = row.get("Steps")
        if not isinstance(steps, list):
            raise ModError(f"{edit.file}: row {edit.row} has no Steps list to clear participants from")
        for step in steps:
            if isinstance(step, dict) and step.get("Participants"):
                step["Participants"] = []


def _apply_clone(rows: list, edit: RsdbEdit) -> None:
    source_name = edit.clone_from or ""
    source = _one_row(rows, edit.file, source_name)
    if any(isinstance(row, dict) and row.get("__RowId") == edit.row for row in rows):
        raise ModError(f"{edit.file}: row {edit.row} already exists")
    cloned = copy.deepcopy(source)
    cloned["__RowId"] = edit.row
    for key, value in edit.set_fields:
        if key not in cloned:
            raise ModError(
                f"{edit.file}: row {source_name} has no {key}. Keys: {', '.join(sorted(cloned))}"
            )
        cloned[key] = value
    rows.append(cloned)


def _one_row(rows: list, label: str, row_id: str) -> dict:
    for row in rows:
        if not isinstance(row, dict) or "__RowId" not in row:
            keys = ", ".join(sorted(row)) if isinstance(row, dict) else "(not a mapping)"
            raise ModError(f"{label}: a row has no __RowId. Keys on that row: {keys}")
    matches = [row for row in rows if row["__RowId"] == row_id]
    if len(matches) != 1:
        shown = ", ".join(str(row.get("__RowId")) for row in rows[:40])
        raise ModError(f"{label}: expected one row __RowId {row_id!r}. Rows: {shown}")
    return matches[0]


def _placeholder(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return 0
    return ""


def _byml():
    tools = str(require_tools())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import byml

    return byml
