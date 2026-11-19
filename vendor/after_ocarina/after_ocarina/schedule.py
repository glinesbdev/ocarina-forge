"""Give the epilogue Saria the sit and the cleared-save talk row.

Saria's shared AIScheduleParam picks the last ConditionalSchedule row whose
flag is true. The WarpDone row is the sit. Its animations are
``ActionSchedule[*].Setting`` (``ArrivalAS``, ``ResponseWaitAS``, and
``WaitArrivalAS`` are ``Act_SitChair_Wait``), not fields of the row. The
cleared-save talk row is ``ConditionFlag`` ``HavePlayedEvent.Dm_Kokiri_0150``.
Its ``EventEntry`` has an ``OnPlayerTalk`` whose ``EventName`` contains
``Npc_Kokiri_Saria_Child_C5C6_TT``. The InHome row shares that same
``OnPlayerTalk``, so the path is not unique. This appends a copy of the sit
row with ``ConditionFlag`` ``HavePlayedEvent.TitleEventBase`` and the
``Dm_Kokiri_0150`` row's ``EventEntry``. It is last, so it wins only after
the epilogue.

The file is plain little-endian BYML. byml.dumps of an unmodified load matches
the bytes it read. Both rows are found by ``ConditionFlag``. The talk row is
then checked for that ``OnPlayerTalk``. Neither lookup uses a fixed index.
"""

from __future__ import annotations

import copy
import sys

from after_ocarina.errors import ModError
from after_ocarina.paths import require_tools

SCHEDULE_REL = "Component/AIScheduleParam/Npc_Kokiri_Saria_Child.game__component__AIScheduleParam.bgyml"
SIT_FLAG = "Npc_Kokiri_Saria_Child_WarpDone"
TALK_FLAG = "HavePlayedEvent.Dm_Kokiri_0150"
TITLE_FLAG = "HavePlayedEvent.TitleEventBase"
SIT_AS = "Act_SitChair_Wait"
TALK_ROW = "Npc_Kokiri_Saria_Child_C5C6_TT"
_AS_FIELDS = ("ArrivalAS", "ResponseWaitAS", "WaitArrivalAS")
_LIST = "ConditionalSchedule"
_FLAG = "ConditionFlag"
_ENTRY = "EventEntry"
_EVENT_NAME = "EventName"
_PARENT = "Work/AISchedule/OnPlayerTalk/OnPlayerTalkBase.game__aischedule__OnPlayerTalk.gyml"
_ENTRY_TYPE = "game__aischedule__OnPlayerTalk"


def apply_schedule(data: bytes, label: str = SCHEDULE_REL) -> tuple[bytes, str]:
    """Return the patched schedule and a one-line report."""
    byml = _byml()
    if data[:2] != b"YB":
        raise ModError(f"{label}: not a little-endian BYML (header {data[:2]!r}).")
    try:
        root = byml.loads(data)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ModError(f"{label}: {exc}") from None
    if not isinstance(root, dict) or not isinstance(root.get(_LIST), list):
        keys = ", ".join(root) if isinstance(root, dict) else type(root).__name__
        raise ModError(f"{label}: no {_LIST} list. Keys: {keys}.")
    rows: list = root[_LIST]
    if any(isinstance(row, dict) and row.get(_FLAG) == TITLE_FLAG for row in rows):
        raise ModError(
            f"{label}: already patched. {_LIST} already has {_FLAG} {TITLE_FLAG}. "
            "Build from a vanilla dump of this file."
        )
    sit_index, sit = _one_row(rows, SIT_FLAG, label)
    _require_sit(sit, sit_index, label)
    talk_index, talk = _find_talk(rows, label)
    event_entry = talk[_ENTRY]
    added = copy.deepcopy(sit)
    added[_FLAG] = TITLE_FLAG
    added[_ENTRY] = copy.deepcopy(event_entry)
    rows.append(added)
    written = byml.dumps(root)
    names = _event_names(event_entry)
    note = (
        f"Saria schedule: appended {_LIST} entry {len(rows) - 1} "
        f"({_FLAG} {TITLE_FLAG}) copied from entry {sit_index} ({SIT_FLAG}). "
        f"ActionSchedule Setting keeps {SIT_AS}. "
        f"{_ENTRY} copied from entry {talk_index} ({talk.get(_FLAG)!r}, {_EVENT_NAME} {names[0]!r})."
    )
    return written, note


def sample_schedule_bytes() -> bytes:
    """A schedule shaped like the real Saria AIScheduleParam. It is not a game file.

    The sit row is at the vanilla index 3 and the talk row at index 5, but the
    patch does not use those indices. Row 6 is the InHome row, which shares the
    C5C6_TT OnPlayerTalk. Hash fields are uints. ``$parent`` and ``$type`` are
    real keys. One row has no ``EventEntry``.
    """
    byml = _byml()
    rows = [
        {
            "$parent": "Work/AISchedule/Other.gyml",
            "$type": "game__aischedule__Schedule",
            "ConditionFlag": "Always",
            "ActionSchedule": [_action(byml, "Act_Wait")],
        },
        {
            "ConditionFlag": "ChildMorning",
            "ActionSchedule": [_action(byml, "Act_Stand")],
            "EventEntry": [_talk("Npc_Kokiri_Saria_Child_Other")],
        },
        {"ConditionFlag": "ChildDay", "ActionSchedule": [_action(byml, "Act_Walk")]},
        _sit_row(byml),
        {"ConditionFlag": "Night"},
        {
            "ConditionFlag": TALK_FLAG,
            "EventEntry": [_talk(TALK_ROW)],
        },
        {
            "ConditionFlag": "Npc_Kokiri_Saria_Child_InHome_ChangeSchedule",
            "EventEntry": [
                _near("Npc_Kokiri_Saria_Child_C6_N"),
                _talk(TALK_ROW),
            ],
        },
    ]
    return byml.dumps({_LIST: rows, "BaseSchedule": "Npc_Kokiri_Saria_Child"})


def _sit_row(byml) -> dict:
    number = byml.U32
    return {
        "ActionSchedule": [
            {
                "ActionTimeDivision": number(3710534274),
                "ActionType": number(3152672504),
                "CommentHash": number(689506350),
                "Setting": {
                    "ArrivalAS": SIT_AS,
                    "EquipmentStateWhenWait": number(1627154745),
                    "EquipmentStateWhenWalk": number(1627154745),
                    "EventTurnTypeWhenWait": number(1419739951),
                    "EventTurnTypeWhenWalk": number(1419739951),
                    "IsTurnToTargetFromVeryClose": False,
                    "IsTurnToTerror": False,
                    "PostureTypeWhenWait": number(2884776663),
                    "PostureTypeWhenWalk": number(4135750958),
                    "ResponseDistance": 1,
                    "ResponseTypeToApproachActor": number(3069107721),
                    "ResponseWaitAS": SIT_AS,
                    "WaitASWhenVeryClose": "",
                    "WaitArrivalAS": SIT_AS,
                    "WaitBehaviorPostTalkTime": -1,
                },
            }
        ],
        "ConditionFlag": SIT_FLAG,
        "EventEntry": [_talk("Npc_Kokiri_Saria_Child_C3_T")],
    }


def _action(byml, action: str) -> dict:
    return {
        "ActionTimeDivision": byml.U32(1),
        "ActionType": byml.U32(2),
        "CommentHash": byml.U32(3),
        "Setting": {name: action for name in _AS_FIELDS},
    }


def _talk(name: str) -> dict:
    return _entry(_PARENT, _ENTRY_TYPE, name)


def _near(name: str) -> dict:
    """The InHome row's other entry. The parent string follows the OnPlayerTalk naming; the dump's parent was not recorded."""
    return _entry(
        "Work/AISchedule/OnNearPlayer/OnNearPlayerBase.game__aischedule__OnNearPlayer.gyml",
        "game__aischedule__OnNearPlayer",
        name,
    )


def _entry(parent: str, kind: str, name: str) -> dict:
    return {
        "$parent": parent,
        "$type": kind,
        "EventName": f"Work/Event/EventSetting/{name}.engine__event__EventSettingParam.gyml",
    }


def _one_row(rows: list, flag: str, label: str) -> tuple[int, dict]:
    found = [
        (index, row)
        for index, row in enumerate(rows)
        if isinstance(row, dict) and row.get(_FLAG) == flag
    ]
    listed = ", ".join(
        f"{index}:{row.get(_FLAG)!r}" if isinstance(row, dict) else f"{index}:{type(row).__name__}"
        for index, row in enumerate(rows)
    )
    if len(found) != 1:
        raise ModError(
            f"{label}: expected one {_LIST} entry with {_FLAG} {flag!r}, found {len(found)}. Rows: {listed or 'none'}."
        )
    return found[0]


def _require_sit(row: dict, index: int, label: str) -> None:
    schedule = row.get("ActionSchedule")
    if not isinstance(schedule, list) or not schedule:
        keys = ", ".join(row)
        raise ModError(f"{label}: {_LIST} entry {index} ({SIT_FLAG}) has no ActionSchedule list. Keys: {keys}.")
    for slot, item in enumerate(schedule):
        setting = item.get("Setting") if isinstance(item, dict) else None
        if not isinstance(setting, dict):
            raise ModError(
                f"{label}: {_LIST} entry {index} ({SIT_FLAG}) ActionSchedule[{slot}] has no Setting."
            )
        got = {name: setting.get(name) for name in _AS_FIELDS}
        if any(got[name] != SIT_AS for name in _AS_FIELDS):
            raise ModError(
                f"{label}: {_LIST} entry {index} ({SIT_FLAG}) ActionSchedule[{slot}].Setting "
                f"has {got}, expected each of {list(_AS_FIELDS)} to be {SIT_AS!r}."
            )


def _find_talk(rows: list, label: str) -> tuple[int, dict]:
    """The cleared-save row, by flag. C5C6_TT is also on the InHome row."""
    index, row = _one_row(rows, TALK_FLAG, label)
    entry = row.get(_ENTRY)
    if not isinstance(entry, list) or not _has_player_talk(entry):
        raise ModError(
            f"{label}: {_LIST} entry {index} ({TALK_FLAG}) {_ENTRY} has no {_ENTRY_TYPE} "
            f"whose {_EVENT_NAME} contains {TALK_ROW!r}. {_EVENT_NAME}s: {_event_names(entry) or '-'}."
        )
    return index, row


def _has_player_talk(entry: list) -> bool:
    for item in entry:
        if not isinstance(item, dict) or item.get("$type") != _ENTRY_TYPE:
            continue
        name = item.get(_EVENT_NAME)
        if isinstance(name, str) and TALK_ROW in name:
            return True
    return False


def _event_names(value: object) -> list[str]:
    """EventName strings only. A row with no EventEntry, and $parent paths, are ignored."""
    if isinstance(value, dict):
        found: list[str] = []
        for key, item in value.items():
            if key == _EVENT_NAME and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_event_names(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_event_names(item))
        return found
    return []


def _byml():
    tools = str(require_tools())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import byml

    return byml
