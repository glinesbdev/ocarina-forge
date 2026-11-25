"""Append ``AND NOT HavePlayedEvent.TitleEventBase`` to one BoolExp.

``DoorOfTime_IsAfter_Step2_2_Exp`` is child Zelda's presence expression. Her
banc Presence is that expression, negated, so she is absent once it is true.
A logic module cannot override Presence. Making the expression false during
the epilogue is what brings her back. A new game leaves the flag false, so
the expression stays on its vanilla result.

A BoolExp entry has ``Hash``, ``ResetTypeValue``, ``SaveFileIndex``, and
``Values``. There is no name and no ``RPN`` key. The hash is murmur3 of the
expression name. ``DoorOfTime_IsAfter_Step2_2_Exp`` is ``713412248``.
``Values`` is the RPN. ``[0, hash]`` pushes a Bool, ``[5]`` is NOT, and
``[3]`` is AND. The file also uses other opcodes. This build appends only
after some expression already contains the consecutive tokens
``[0, hash], [5], [3]``. The appended push uses the same integer type as
the numbers already in that expression's ``Values``.

``Dm_SY_Ocarina_SariaSong`` also reads this expression (Event13 and Event66).
On current main, in the epilogue, playing Saria's Song next to the sitting
Saria still runs the Sacred Grove telepathy. After this append the same
play is the check for whether that flow took the pre-Step2_2 branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.paths import GAMEDATA_DIR
from after_ocarina.saveinfo import flag_hash
from after_ocarina.sources import _mapping, _rel_path

NOT_CODE = 5
AND_CODE = 3
PUSH_CODE = 0
TITLE_EVENT = "HavePlayedEvent.TitleEventBase"


@dataclass(frozen=True)
class BoolExpEdit:
    file: str
    name: str
    flag: str
    flag_hash: int
    source: str


def load_bool_expressions(directory: Path | None = None) -> list[BoolExpEdit]:
    directory = directory or GAMEDATA_DIR
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    edits: list[BoolExpEdit] = []
    seen: dict[str, str] = {}
    for path in files:
        data = _mapping(path)
        if "expressions" not in data:
            continue
        unknown = set(data) - {"expressions"}
        if unknown:
            raise ModError(f"{path.name}: unknown keys: {', '.join(sorted(unknown))}")
        rows = data.get("expressions")
        if not isinstance(rows, list) or not rows:
            raise ModError(f"{path.name}: expressions must be a non-empty list")
        for index, row in enumerate(rows, start=1):
            edit = _expression(path, index, row)
            previous = seen.get(edit.name)
            if previous is not None:
                raise ModError(f"{edit.name} is edited by both {previous} and {path.name}")
            seen[edit.name] = path.name
            edits.append(edit)
    return edits


def _expression(path: Path, index: int, row: object) -> BoolExpEdit:
    where = f"{path.name}: expression {index}"
    if not isinstance(row, dict):
        raise ModError(f"{where} must be a mapping")
    extra = set(row) - {"file", "name", "flag", "flag_hash"}
    if extra:
        raise ModError(f"{where} has unknown keys: {', '.join(sorted(extra))}")
    rel = _rel_path(row.get("file"), path, index, "file", suffix=".byml.zs")
    name = row.get("name")
    flag = row.get("flag")
    hashed = row.get("flag_hash")
    if not isinstance(name, str) or not name:
        raise ModError(f"{where}: name must be a string")
    if not isinstance(flag, str) or not flag:
        raise ModError(f"{where}: flag must be a string")
    if isinstance(hashed, bool) or not isinstance(hashed, int):
        raise ModError(f"{where}: flag_hash must be an int")
    measured = flag_hash(flag)
    if int(hashed) != measured:
        raise ModError(f"{where}: flag_hash {hashed} is not murmur3 of {flag!r} ({measured})")
    return BoolExpEdit(file=rel, name=name, flag=flag, flag_hash=int(hashed), source=path.name)


def sample_bool_rows(expressions: list[BoolExpEdit], byml) -> list[dict]:
    """Hash-only BoolExp rows in the real shape: Hash, ResetTypeValue, SaveFileIndex, Values.

    One row contains ``[0, hash], [5], [3]`` so the structural opcode check can
    pass. The target row uses the measured Door of Time tokens, which are not
    that sequence. Built for the tests.
    """
    def row(name: str, values: list) -> dict:
        return {
            "Hash": byml.U32(flag_hash(name) & 0xFFFFFFFF),
            "ResetTypeValue": 9,
            "SaveFileIndex": 0,
            "Values": values,
        }

    rows = [
        row(
            "Sample_NotAnd_Exp",
            [[PUSH_CODE, byml.U32(11)], [PUSH_CODE, byml.U32(22)], [NOT_CODE], [AND_CODE]],
        ),
        row(
            "Sample_Enum_Exp",
            [[12, byml.U32(931730378)], [2, byml.U32(3039382493)], [8, byml.U32(1), byml.U32(2)]],
        ),
    ]
    for edit in expressions:
        rows.append(
            row(
                edit.name,
                [[12, byml.U32(931730378)], [2, byml.U32(3039382493)]],
            )
        )
    return rows


def append_expressions(root: dict, expressions: list[BoolExpEdit]) -> None:
    """Confirm the NOT/AND sequence, then append ``[0, flag], [5], [3]``."""
    section = _bool_section(root, expressions[0].file)
    named = [_named_rpn(entry, expressions[0].file) for entry in section]
    confirm_opcodes(named, expressions[0].file)
    for edit in expressions:
        found = _match(named, edit)
        if len(found) != 1:
            shown = ", ".join(name for _entry, name, _rpn in named[:8]) or "none"
            raise ModError(
                f"{edit.file}: expected one BoolExp with hash {flag_hash(edit.name) & 0xFFFFFFFF} "
                f"({edit.name}), found {len(found)}. Entries: {shown}."
            )
        _entry, _name, values = found[0]
        suffix = _suffix(values, edit.flag_hash)
        if _ends_with(values, suffix):
            raise ModError(
                f"{edit.file}: {edit.name} already ends with AND NOT {edit.flag}. "
                "Build from a vanilla GameDataList."
            )
        values.extend(suffix)


def confirm_opcodes(named: list[tuple[dict, str, list]], label: str) -> None:
    """Refuse unless a Bool push exists and some expression contains ``[0, x], [5], [3]``.

    Entries have no names, so a Not/And name scan cannot confirm the opcodes.
    Other opcodes are allowed. The consecutive sequence is what this file uses
    for ``(bool) AND NOT (bool)``.
    """
    saw_push = False
    saw_sequence = False
    shown: list[str] = []
    for _entry, name, values in named:
        if any(_is_push(token) for token in values):
            saw_push = True
        if _has_not_and(values):
            saw_sequence = True
        if len(shown) < 4:
            shown.append(f"{name} {values[-4:]}")
    if not saw_push:
        raise ModError(
            f"{label}: no BoolExp push token [{PUSH_CODE}, hash]. "
            f"Tails: {'; '.join(shown) or 'none'}. Refusing to append NOT TitleEventBase."
        )
    if not saw_sequence:
        raise ModError(
            f"{label}: no BoolExp contains [{PUSH_CODE}, hash], [{NOT_CODE}], [{AND_CODE}]. "
            f"Tails: {'; '.join(shown) or 'none'}. Refusing to append AND NOT TitleEventBase."
        )


def _bool_section(root: dict, label: str) -> list:
    data = root.get("Data")
    section = None
    keys: list[str] = []
    if isinstance(data, dict):
        keys = [str(key) for key in data]
        section = data.get("BoolExp")
    if section is None and isinstance(root, dict):
        section = root.get("BoolExp")
        keys = keys or [str(key) for key in root]
    if not isinstance(section, list) or not section:
        raise ModError(f"{label}: no BoolExp list. Data keys: {', '.join(keys) or 'none'}.")
    if not all(isinstance(entry, dict) for entry in section):
        raise ModError(f"{label}: BoolExp entries are not mappings.")
    return section


def _named_rpn(entry: dict, label: str) -> tuple[dict, str, list]:
    name = _entry_name(entry)
    hashed = entry.get("Hash")
    if name is None and (isinstance(hashed, bool) or not isinstance(hashed, int)):
        raise ModError(f"{label}: a BoolExp entry has no Hash. Keys: {sorted(entry)}.")
    shown = name or f"#{int(hashed)}"
    values = entry.get("Values", entry.get("RPN"))
    if "Values" not in entry and "RPN" not in entry:
        raise ModError(f"{label}: BoolExp {shown} has no Values or RPN. Keys: {sorted(entry)}.")
    if not isinstance(values, list) or not values or not all(isinstance(token, list) for token in values):
        raise ModError(f"{label}: BoolExp {shown} Values is not a list of lists.")
    return entry, shown, values


def _entry_name(entry: dict) -> str | None:
    for key in ("Name", "GameDataName"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _match(named: list[tuple[dict, str, list]], edit: BoolExpEdit) -> list[tuple[dict, str, list]]:
    wanted = flag_hash(edit.name) & 0xFFFFFFFF
    found = []
    for entry, name, values in named:
        hashed = entry.get("Hash")
        if isinstance(hashed, bool) or not isinstance(hashed, int):
            continue
        if int(hashed) == wanted:
            found.append((entry, name, values))
    return found


def _suffix(values: list, flag: int) -> list[list]:
    """``[0, flag], [5], [3]`` using the integer types already in this Values list."""
    code = _opcode_prototype(values)
    number = _number_prototype(values)
    return [
        [_like_int(code, PUSH_CODE), _like_int(number, flag)],
        [_like_int(code, NOT_CODE)],
        [_like_int(code, AND_CODE)],
    ]


def _opcode_prototype(values: list):
    for token in values:
        if len(token) == 1 and type(token[0]) is int:
            return token[0]
    for token in values:
        if token and type(token[0]) is int:
            return token[0]
    return 0


def _number_prototype(values: list):
    for token in values:
        for item in token[1:]:
            if _is_u32(item):
                return item
    for token in values:
        for item in token[1:]:
            if type(item) is int:
                return item
    return 0


def _like_int(prototype: object, value: int):
    if _is_u32(prototype):
        return _u32(value)
    if type(prototype) is int and -0x80000000 <= value <= 0x7FFFFFFF:
        return int(value)
    return _u32(value)


def _is_u32(value: object) -> bool:
    return type(value).__name__ == "U32" and isinstance(value, int)


def _has_not_and(values: list) -> bool:
    for index in range(len(values) - 2):
        push, negate, combine = values[index : index + 3]
        if (
            _is_push(push)
            and _is_operator(negate)
            and int(negate[0]) == NOT_CODE
            and _is_operator(combine)
            and int(combine[0]) == AND_CODE
        ):
            return True
    return False


def _u32(value: int):
    from after_ocarina.gamedata import _byml

    return _byml().U32(value)


def _ends_with(rpn: list, suffix: list[list]) -> bool:
    if len(rpn) < len(suffix):
        return False
    tail = rpn[-len(suffix) :]
    for got, want in zip(tail, suffix):
        if len(got) != len(want):
            return False
        if any(int(left) != int(right) for left, right in zip(got, want)):
            return False
    return True


def _is_push(token: list) -> bool:
    return len(token) == 2 and _is_int(token[0]) and int(token[0]) == PUSH_CODE and _is_int(token[1])


def _is_operator(token: list) -> bool:
    return len(token) == 1 and _is_int(token[0])


def _is_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int)
