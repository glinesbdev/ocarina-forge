"""Read and patch BFEVFL event flows.

Patches are applied in memory with ``evfl``, written, and read back. The re-read
checks that each new parameter kept the type the YAML asked for (int, bool, or
str) and that every query case is wired. A query case that is left empty ends
the flow, which on the boot event would stop a save from loading.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from evfl.actor import Actor
from evfl.common import ActorIdentifier, StringHolder
from evfl.container import Container
from evfl.entry_point import EntryPoint, VariableDef
from evfl.event import ActionEvent, Event, ForkEvent, JoinEvent, ReturnEvent, SubFlowEvent, SubFlowSwitchEvent, SwitchEvent
from evfl.evfl import EventFlow
from evfl.flowchart import Flowchart
from evfl.util import make_index, make_rindex

from after_ocarina.errors import ModError
from after_ocarina.sources import BOOL_FALSE_CASE, CALLS, ORIG_CALL, FlowPatch, FlowStep
from after_ocarina.zsio import unpack

_GAMEDATA = "GameDataActor"

# Every GameDataActor in sampled real flows carries this container and
# concurrent_clips 1. Types are exact: 0 is an int, the three flags are bools,
# and the two "do not use" strings stay strings. Bool is written before int
# would be, because bool is a subclass of int.
GAMEDATA_ACTOR_PARAMS: tuple[tuple[str, object], ...] = (
    ("EventMemberCreateMethod", 0),
    ("IsBindNearestActor", False),
    ("IsGrounding", False),
    ("IsWorld", False),
    ("UseEventModelAnime", "使用しない"),
    ("UseEventAS", "使用しない"),
)
# Player_Event as every vanilla flow that uses it declares it (Npc_Goron_001_Child,
# Npc_Gerudo_001, Npc_Kokiri_Mido_Child, Npc_Hylia_Zelda_Child ...). The six
# generic keys alone make the event create a new Link double, and that request
# fails (hos_fatal abort in the event member state machine). ExceptionalBindType 4
# with BindTargetActorNameComponent "Player" binds the existing Link instead.
PLAYER_EVENT_ACTOR_PARAMS: tuple[tuple[str, object], ...] = GAMEDATA_ACTOR_PARAMS + (
    ("ExceptionalBindType", 4),
    ("BindTargetActorNameComponent", "Player"),
    ("EquipmentUser_Weapon", ""),
    ("EquipmentUser_Weapon_EquipmentState", 0),
    ("EquipmentUser_Shield", ""),
    ("EquipmentUser_Shield_EquipmentState", 0),
    ("EquipmentUser_Bow", ""),
    ("EquipmentUser_Bow_EquipmentState", 0),
    ("EquipmentUser_ArmorHead", ""),
    ("EquipmentUser_ArmorUpper", ""),
    ("EquipmentUser_ArmorLower", ""),
)
GAMEDATA_ACTIONS = (
    "EventTriggerSetGameDataBool",
    "EventTriggerAddOrSubGameDataInt",
)
GAMEDATA_QUERIES = (
    "EventQueryIsGameDataBool",
    "EventQueryIsGameDataInt",
)
# Inserted in front of the YAML steps when a patch sets debug_visible or debug_cases.
DEBUG_RUPEE_EVENT = "AtO_DebugRupee"
DEBUG_RUPEE_FLAG = "PlayerStatus.CurrentRupee"
# debug_cases: one int case and one bool case fire, so the rupee delta is a bit field.
GANON_CASE_RUPEES = {0: 2, 1: 4, 2: 8}
BOOL_CASE_RUPEES = {0: 16, 1: 32}
WAKE_LINE = "AtO_WakeLine"
AUTOSAVE_EVENT = "AtO_Autosave"
KEEP_CAMERA_EVENT = "AtO_KeepCamera"
SARIA_LINE = "AtO_SariaLine"
RECENT_EVENT = "AtO_RecentTalk"
INIT_TALK_EVENT = "AtO_InitTalk"
WAKE_MESSAGE_ID = "EventFlowMsg/Dm_Kokiri_0060:AtO_WakeLine"
SARIA_MESSAGE_ID = "EventFlowMsg/Npc_Kokiri_Saria_Child:AtO_SariaLine"
TEXT_ACTOR = "SystemTextNPC"
STARTER_ACTOR = "Npc_EventStarter"
TALK_ACTION = "EventTalk"
RECENT_QUERY = "EventQueryIsOnRecentTalkFlag"
INIT_FLOW = "InitTalk"
INIT_ENTRY = "InitTalk_KokiriOnly"
# Case 0 is a repeat talk and skips InitTalk. Case 1 is the first talk.
RECENT_REPEAT_CASE = 0
RECENT_FIRST_CASE = 1
AUTOSAVE_ACTION = "EventTriggerRequestAutoSave"
KEEP_CAMERA_ACTION = "EventTriggerSendKeepStateMessageToPlayerCamera"
KEEP_CAMERA_PARAMS: tuple[tuple[str, object], ...] = (
    ("AfterKeepEndInterpolateCountScale", 1.0),
    ("ChangeToManualModeAfterEvent", False),
    ("KeepManualModeFrame", 120.0),
    ("IsKeepWithRelativePos", False),
)


@dataclass(frozen=True)
class HookNote:
    file: str
    entry_points: tuple[str, ...]
    hooked: str
    explicit: bool
    original_event: str
    debug_event: str | None = None
    debug_cases: bool = False
    case_note: str | None = None
    epilogue: bool = False
    epilogue_skipped: bool = False
    orig_entry: str | None = None
    wake_subflows: tuple[str, ...] = ()
    wake_log: str | None = None
    epilogue_talk: bool = False
    courtyard: bool = False
    song_gate: bool = False
    rumor: bool = False
    ganon_complete: bool = False

    def text(self) -> str:
        names = ", ".join(self.entry_points) if self.entry_points else "(none)"
        if self.rumor:
            return f"{self.file}: entry points: {names}\n  {self.case_note}"
        if self.song_gate:
            return (
                f"{self.file}: entry points: {names}\n"
                f"  {self.original_event} leads to {SONG_GATE}. "
                f"Bool case 0 plays {SONG_LINE}, then {SONG_FADE}, and ends. "
                f"Case 1 continues to {self.case_note}. Entry points stay put."
            )
        if self.courtyard:
            return (
                f"{self.file}: entry points: {names}\n"
                f"  {self.hooked} still starts at {self.original_event}. {self.case_note}"
            )
        lines = [f"{self.file}: entry points: {names}"]
        if self.explicit:
            lines.append(f"  hooking {self.hooked}, then {self.original_event}")
        else:
            lines.append(
                f"  hooking {self.hooked}, the first entry point, then {self.original_event}. "
                "Set entry_point in the YAML if the load entry has another name."
            )
        if self.debug_cases:
            lines.append(
                "  debug_cases: +1, then int case 0/1/2 adds +2/+4/+8, then bool case 0/1 adds +16/+32, "
                f"then the bool is set true and {self.original_event} runs. "
                "ganon_cleared_cases and bool_false_case are not used for routing."
            )
        elif self.debug_event:
            lines.append(
                f"  debug_visible: {self.debug_event} adds 1 to {DEBUG_RUPEE_FLAG} "
                "before the gate, on every load"
            )
        if self.case_note:
            lines.append(f"  {self.case_note}")
        if self.epilogue_talk:
            lines.append(
                "  epilogue talk: TitleEventBase true (bool case 0) asks EventQueryIsOnRecentTalkFlag. "
                "Case 0 says the line. Case 1 calls InitTalk::InitTalk_KokiriOnly, then the line. "
                "TitleEventBase false runs the original event, including its mask check. "
                "NpcFairy_MovingFlag is false because Navi is banned."
            )
        if self.epilogue and self.orig_entry:
            lines.append(
                f"  epilogue: after the flag is set, (self)::{self.orig_entry} runs {self.original_event}, "
                "then the era switch, which ends the flow. "
                "A cleared save that already played it sets Navi_Ban, removes any Spiritual Stones and Sage Medallions still in the pouch, "
                "swaps the ocarina, recreates the equipment, and runs the original event. "
                "That load autosaves when the Ocarina of Time was still owned or a stone or medallion was removed."
            )
        if self.wake_log:
            lines.append(self.wake_log)
        elif self.epilogue_skipped:
            lines.append(
                "  epilogue is configured, and this debug load skips the era switch "
                "and rejoins the original event"
            )
        if self.ganon_complete:
            lines.append(
                "  Light and Darkness: AtO_GanonGuard asks Defeat_Ganon_IsAfter_Step09_Exp. "
                "Case 1 (not yet) runs AtO_GanonComplete, then joins the same event case 0 joins. "
                "IsIgnoreChallengeProgressUI is true."
            )
        return "\n".join(lines)


def make_params(pairs: tuple[tuple[str, object], ...] | list[tuple[str, object]]) -> Container:
    """Build a parameter container. Bool is checked before int, because bool is a subclass of int.

    A list or tuple of floats is written as evfl's float array. That is how a vanilla
    float3 is stored: evfl has no vec3 type, and it checks ``int`` before ``float``,
    so a list of ints would become an int array. An empty string is a real string.
    """
    container = Container()
    for name, value in pairs:
        if isinstance(value, bool):
            container.data[name] = value
        elif isinstance(value, int):
            container.data[name] = value
        elif isinstance(value, float):
            container.data[name] = value
        elif isinstance(value, str):
            container.data[name] = value
        elif isinstance(value, (list, tuple)):
            if not value or any(isinstance(item, bool) or not isinstance(item, float) for item in value):
                raise ModError(
                    f"param {name} must be a list of floats. evfl writes a list of ints as an int array, "
                    "and it has no separate vec3 type."
                )
            container.data[name] = [float(item) for item in value]
        else:
            raise ModError(f"param {name} is a {type(value).__name__}; event params here are int, bool, str, or float3")
    return container


# Every vanilla flow that declares Npc_EventStarter adds ExceptionalBindType 1 (bind the
# talking NPC). Without it the event creates its own member of that name, and
# InitTalk's turn-to-Link then runs on the wrong actor.
STARTER_ACTOR_PARAMS = GAMEDATA_ACTOR_PARAMS + (("ExceptionalBindType", 1),)


def epilogue_runs(patch: FlowPatch) -> bool:
    """The era switch runs only on a normal load. A debug load keeps the M2a rejoin."""
    return bool(patch.epilogue) and not patch.debug_visible and not patch.debug_cases


def effective_boot_name(patch: FlowPatch) -> str:
    """The banc change's boot event. The custom wake is AtO_Epilogue_Wake; the fallback is the vanilla bed event."""
    if patch.custom_wake:
        return patch.boot_event
    return patch.vanilla_boot_event


REBAN = "AtO_RebanNavi"
FALLBACK_BAN = "AtO_BanNavi"
POUCH_ACTOR = "PouchSystemActor"
POUCH_ACTION = "EventTriggerAddOrRemovePouchContent"
GAME_SYSTEM = "GameSystemActor"
UI_SYSTEM = "UISystemActor"
PLAYER_EVENT = "Player_Event"
WAIT_FRAME_ACTION = "EventWait"
SYNC_EQUIPMENT_ACTION = "EventTriggerSyncPlayerEquipmentAndPouch"
WAIT_EQUIPMENT_ACTION = "EventWaitCreationFinishedEquipment"
DELETE_EQUIPMENT_ACTION = "EventTriggerRequestDeletePlayerEquipmentFromPouch"
SET_INT_ACTION = "EventTriggerSetGameDataInt"
SET_STRING_ACTION = "EventTriggerSetGameDataString64"
OCARINA_TIME = "Item_Ocarina_Time"
OCARINA_FAIRY = "Item_Ocarina_Fairy"
ISGET_TIME = "IsGet.Item_Ocarina_Time"
ISGET_FAIRY = "IsGet.Item_Ocarina_Fairy"
STOCK_TIME = "PouchData.Item_Ocarina_Time.StockNum"
EQUIP_OCARINA = "PlayerStatus.EquipOcarina"
# One removal list. The stem is the event-name piece, so StoneForest and
# MedalForest do not collide. Nothing in a BoolExp, a logic module, an event
# flow, a banc, a bgyml, or an actor pack reads these IsGet flags as a
# condition. The Door of Time and the NPCs use Step_Get_StoneOf*. The Ganon
# barrier, the Rainbow Bridge, the Light Arrows, and Sheik do not read the
# medal flags. HavePlayedGetEvent is not written.
POUCH_REMOVALS = (
    ("StoneForest", "Item_Stone_Forest"),
    ("StoneFire", "Item_Stone_Fire"),
    ("StoneWater", "Item_Stone_Water"),
    ("MedalLight", "Item_Medal_Light"),
    ("MedalForest", "Item_Medal_Forest"),
    ("MedalFire", "Item_Medal_Fire"),
    ("MedalWater", "Item_Medal_Water"),
    ("MedalShadow", "Item_Medal_Shadow"),
    ("MedalSpirit", "Item_Medal_Spirit"),
)
# The wake chain and the later-load chain are separate events: each one's last
# action continues somewhere different (the autosave, or the original boot).
WAKE_OCARINA_PREFIX = "AtO_"
LOAD_OCARINA_PREFIX = "AtO_Load"
_OCARINA_LEAVES = (
    "CheckOcarinaTime",
    "RemoveOcarinaTime",
    "ClearOcarinaGet",
    "ClearOcarinaStock",
    "CheckOcarinaFairy",
    "GiveOcarinaFairy",
    "EquipOcarinaFairy",
)
_SYNC_REFRESH = ("WaitEquipmentFrame", "SyncEquipment", "WaitEquipment")
_DELETE_REFRESH = ("DeleteEquipment", "WaitEquipment")


def read_flow_bytes(data: bytes) -> EventFlow:
    flow = EventFlow()
    try:
        flow.read(data)
    except (AssertionError, ValueError, KeyError, IndexError, struct.error) as exc:
        raise ModError(f"could not read the event flow ({exc})") from None
    return flow


def read_zs_flow(data: bytes, dicts) -> tuple[EventFlow, int | None]:
    raw, dict_id = unpack(data, dicts)
    return read_flow_bytes(raw), dict_id


def patch_flow_bytes(raw: bytes, patch: FlowPatch, bed: bytes | None = None) -> tuple[bytes, HookNote]:
    """Return the patched BFEVFL and a note naming the entry point that was hooked.

    ``bed`` is the unpacked LinkSleepInBed flow. The custom wake copies its bed
    events from there. The library does not invent that flow.
    """
    if epilogue_runs(patch) and patch.custom_wake and bed is None:
        from after_ocarina.wake import LINK_SLEEP_REL

        raise ModError(
            f"{patch.file}: custom_wake needs {LINK_SLEEP_REL} from the dump. "
            "Set custom_wake: false to keep BedBootEvent_Alive."
        )
    flow = read_flow_bytes(raw)
    if patch.rumor is not None:
        from after_ocarina.rumor import apply_rumor, verify_rumor

        note = apply_rumor(flow, patch)
        written = _write(flow, patch.file)
        check = read_flow_bytes(written)
        verify_rumor(check, patch)
        if _write(check, patch.file) != written:
            raise ModError(f"{patch.file}: rewriting the patched flow changed the bytes")
        return written, note
    if patch.song_gate:
        note = apply_saria_song(flow, patch)
        written = _write(flow, patch.file)
        check = read_flow_bytes(written)
        _verify_saria_song(check, patch)
        if _write(check, patch.file) != written:
            raise ModError(f"{patch.file}: rewriting the patched flow changed the bytes")
        return written, note
    if patch.retarget_next:
        note = apply_courtyard(flow, patch)
        written = _write(flow, patch.file)
        check = read_flow_bytes(written)
        _verify_courtyard(check, patch)
        if _write(check, patch.file) != written:
            raise ModError(f"{patch.file}: rewriting the patched flow changed the bytes")
        return written, note
    note, before = prepend_hook(flow, patch, bed)
    written = _write(flow, patch.file)
    check = read_flow_bytes(written)
    _verify(check, patch, note, before, effective_steps(patch))
    # A second write of the bytes we just read must not drift. evfl's writer is deterministic.
    if _write(check, patch.file) != written:
        raise ModError(f"{patch.file}: rewriting the patched flow changed the bytes")
    return written, note


def prepend_hook(flow: EventFlow, patch: FlowPatch, bed: bytes | None = None) -> tuple[HookNote, dict[str, str | None]]:
    """Insert ``patch.steps`` in front of one entry point. Other entry points stay put."""
    if flow.timeline is not None:
        raise ModError(
            f"{patch.file} has a timeline as well as a flowchart. "
            "evfl cannot write both, so this file is left unchanged."
        )
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file} has no flowchart")
    names = [entry.name for entry in flowchart.entry_points]
    if len(names) != len(set(names)):
        raise ModError(f"{patch.file} has two entry points with the same name")
    if not names:
        raise ModError(f"{patch.file} has no entry points")
    if patch.entry_point is None:
        chosen = flowchart.entry_points[0]
        explicit = False
    else:
        matches = [entry for entry in flowchart.entry_points if entry.name == patch.entry_point]
        if not matches:
            raise ModError(
                f"{patch.file}: no entry point named {patch.entry_point}. "
                f"Entry points: {', '.join(names)}"
            )
        chosen = matches[0]
        explicit = True
    original = chosen.main_event.v
    if original is None or not original.name:
        raise ModError(
            f"{patch.file}: entry point {chosen.name} does not start at an event. "
            f"Entry points: {', '.join(names)}"
        )
    steps = effective_steps(patch)
    existing = {event.name for event in flowchart.events}
    extra_names: list[str] = []
    if epilogue_runs(patch):
        extra_names.append(ORIG_CALL)
        extra_names.append(REBAN)
        if not patch.custom_wake:
            extra_names.append(FALLBACK_BAN)
        extra_names.extend(step.name for step in patch.chain)
        if patch.orig_entry in names:
            raise ModError(f"{patch.file}: already has an entry point named {patch.orig_entry}")
    if patch.epilogue_talk:
        extra_names.extend((RECENT_EVENT, INIT_TALK_EVENT, SARIA_LINE))
    if (patch.wake_dialogue or patch.autosave) and not patch.custom_wake:
        raise ModError(f"{patch.file}: wake_dialogue and autosave need the custom wake")
    if epilogue_runs(patch) and patch.custom_wake:
        if patch.wake_dialogue:
            extra_names.append(WAKE_LINE)
        if patch.autosave:
            extra_names.append(KEEP_CAMERA_EVENT)
            extra_names.append(AUTOSAVE_EVENT)
        extra_names.extend(_stone_names(WAKE_OCARINA_PREFIX, patch, split=False))
        extra_names.extend(_ocarina_names(WAKE_OCARINA_PREFIX, patch, split=False))
    if epilogue_runs(patch):
        extra_names.extend(_stone_names(LOAD_OCARINA_PREFIX, patch, split=True))
        extra_names.extend(_ocarina_names(LOAD_OCARINA_PREFIX, patch, split=True))
    for name in [step.name for step in steps] + _probe_names(patch) + extra_names:
        if name in existing:
            raise ModError(f"{patch.file}: the flow already has an event named {name}")
    before = {
        entry.name: entry.main_event.v.name if entry.main_event.v is not None else None
        for entry in flowchart.entry_points
    }
    actor = _ensure_gamedata_actor(flowchart)
    step_actors = [_actor_for_step(flowchart, actor, step) for step in steps]
    created: list[Event] = []
    for step, target in zip(steps, step_actors, strict=True):
        spec = CALLS[step.call]
        event = Event()
        event.name = step.name
        if spec["kind"] == "query":
            event.data = SwitchEvent()
            event.data.actor = make_rindex(target)
            event.data.actor_query = make_rindex(_ensure_holder(target.queries, step.call))
            event.data.params = make_params(step.params)
        else:
            event.data = ActionEvent()
            event.data.actor = make_rindex(target)
            event.data.actor_action = make_rindex(_ensure_holder(target.actions, step.call))
            event.data.params = make_params(step.params)
            event.data.nxt = make_index(None)
        created.append(event)
    runs_epilogue = epilogue_runs(patch)
    load_swap = (
        _ocarina_swap(flowchart, LOAD_OCARINA_PREFIX, original, patch, autosave_after_swap=True)
        if runs_epilogue
        else []
    )
    load_held: list[Event] = []
    load_stones: list[Event] = []
    if runs_epilogue:
        load_autosave = next(event for event in load_swap if event.name == f"{LOAD_OCARINA_PREFIX}Autosave")
        load_remove = next(event for event in load_swap if event.name == f"{LOAD_OCARINA_PREFIX}RemoveOcarinaTime")
        load_held = _ocarina_after_stones(flowchart, LOAD_OCARINA_PREFIX, load_remove, load_autosave, patch)
        load_stones = _stone_removal(flowchart, LOAD_OCARINA_PREFIX, load_swap[0], load_held[0])
    reban_event = _navi_ban_event(actor, REBAN, load_stones[0]) if runs_epilogue else None
    talk_events = _saria_talk_events(flowchart) if patch.epilogue_talk else []
    recent_event = talk_events[0] if talk_events else None
    for index, step in enumerate(steps):
        spec = CALLS[step.call]
        data = created[index].data
        if spec["kind"] == "query":
            if patch.debug_cases and step.call in ("EventQueryIsGameDataInt", "EventQueryIsGameDataBool"):
                continue
            if not isinstance(data, SwitchEvent):
                raise ModError(f"{patch.file}: {step.name} was not built as a switch")
            if index + 1 < len(created):
                follower = created[index + 1]
            elif recent_event is not None:
                follower = recent_event
            else:
                raise ModError(f"{patch.file}: {step.name} is a query with nothing after it")
            continuing = continuing_cases(patch, step)
            rejoin = original
            if runs_epilogue and step.call == "EventQueryIsGameDataBool" and reban_event is not None:
                rejoin = reban_event
            cases = {}
            for case in spec["cases"]:
                target = follower if case in continuing else rejoin
                cases[case] = make_rindex(target)
            data.cases = cases
        else:
            # An action in the middle of the chain (the rupee bump) continues
            # to the next inserted event. The last action rejoins the original,
            # unless the era switch retargets it at the self-subflow below.
            nxt = created[index + 1] if index + 1 < len(created) else original
            data.nxt = make_index(nxt)
    probes: list[Event] = []
    if patch.debug_cases:
        probes = _attach_debug_cases(patch, steps, created, actor)
    era: list[Event] = []
    wake_subflows: tuple[str, ...] = ()
    wake_log = None
    if runs_epilogue:
        era = _attach_epilogue(flowchart, patch, created[-1], original, actor)
        if reban_event is not None:
            era.append(reban_event)
            era.extend(load_stones)
            era.extend(load_held)
            era.extend(load_swap)
        if patch.custom_wake:
            from after_ocarina.wake import attach_wake

            if bed is None:
                raise ModError(f"{patch.file}: custom_wake has no bed flow")
            built = attach_wake(flowchart, bed)
            era.extend(built.events)
            dialogue, save = _wake_tail(flowchart, patch)
            join = next(event for event in built.events if event.name == "AtO_Join")
            control = next(event for event in built.events if event.name == "AtO_ControlCamera")
            if dialogue is not None:
                join.data.nxt = make_index(dialogue)
                dialogue.data.nxt = make_index(control)
                era.append(dialogue)
            if save:
                for left, right in zip(save, save[1:]):
                    left.data.nxt = make_index(right)
                save[-1].data.nxt = make_index(None)
                era.extend(save)
            wake_swap = _ocarina_swap(
                flowchart, WAKE_OCARINA_PREFIX, save[0] if save else None, patch, autosave_after_swap=False
            )
            wake_stones = _stone_removal(flowchart, WAKE_OCARINA_PREFIX, wake_swap[0], None)
            control.data.nxt = make_index(wake_stones[0])
            era.extend(wake_stones)
            era.extend(wake_swap)
            wake_subflows = built.external_subflows
            wake_log = _wake_log(built)
            head = [event for event in (dialogue, control) if event is not None]
            parts = [" -> ".join(_event_call(event) for event in head)]
            parts.append(
                "pouch (each owned Spiritual Stone and Sage Medallion leaves, and its IsGet and StockNum clear)"
            )
            parts.append(
                "ocarina swap (Time owned: remove it, clear IsGet and StockNum, give Fairy if missing; "
                "always set EquipOcarina to Item_Ocarina_Fairy, then recreate the equipment)"
            )
            if save:
                parts.append(" -> ".join(_event_call(event) for event in save))
            wake_log = f"{wake_log}\n  wake tail: {' -> '.join(parts)}"
    _retarget_bool_exp_skips(patch, steps, created)
    flowchart.events.extend(created)
    flowchart.events.extend(probes)
    flowchart.events.extend(era)
    flowchart.events.extend(talk_events)
    chosen.main_event = make_rindex(created[0])
    note = HookNote(
        file=patch.file,
        entry_points=tuple(names),
        hooked=chosen.name,
        explicit=explicit,
        original_event=original.name,
        debug_event=DEBUG_RUPEE_EVENT if patch.debug_visible or patch.debug_cases else None,
        debug_cases=patch.debug_cases,
        case_note=_case_note(patch),
        epilogue=runs_epilogue,
        epilogue_skipped=bool(patch.epilogue) and not runs_epilogue,
        orig_entry=patch.orig_entry if patch.epilogue else None,
        wake_subflows=wake_subflows,
        wake_log=wake_log,
        epilogue_talk=patch.epilogue_talk,
        ganon_complete=any(step.name == "AtO_GanonComplete" for step in patch.steps),
    )
    return note, before


def format_flow(flow: EventFlow) -> str:
    """Entry points, then every event: kind, actor call, params, and where it goes next."""
    lines = [f"name: {flow.name}", f"version: 0x{int(flow.version):04x}"]
    if flow.timeline is not None:
        lines.append("timeline: yes")
    flowchart = flow.flowchart
    if flowchart is None:
        lines.append("flowchart: none")
        return "\n".join(lines) + "\n"
    lines.append(f"flowchart: {flowchart.name}")
    if not flowchart.actors:
        lines.append("actors: (none)")
    for actor in flowchart.actors:
        lines.append(f"actor {actor.identifier}  concurrent_clips={actor.concurrent_clips}")
        lines.append(f"  params: {_format_params(actor.params)}")
        lines.append("  actions: " + (", ".join(holder.v for holder in actor.actions) or "(none)"))
        lines.append("  queries: " + (", ".join(holder.v for holder in actor.queries) or "(none)"))
    lines.append("entry points:")
    if not flowchart.entry_points:
        lines.append("  (none)")
    for entry in flowchart.entry_points:
        target = entry.main_event.v.name if entry.main_event.v is not None else "<none>"
        lines.append(f"  {entry.name} -> {target}")
    lines.append(f"events: {len(flowchart.events)}")
    for index, event in enumerate(flowchart.events):
        lines.extend(_format_event(event, index))
    return "\n".join(lines) + "\n"


def make_sample_flow() -> EventFlow:
    """A tiny flowchart with two entry points and no GameDataActor.

    Tests and the invented dump use this. It is not a game file. The first entry
    point carries a variable so a patch has to leave entry-point data in place.
    """
    flow = EventFlow()
    flow.name = "SampleBoot"
    flowchart = Flowchart()
    flowchart.name = "SampleBoot"
    actor = Actor()
    actor.identifier = ActorIdentifier("EventSystemActor")
    actor.concurrent_clips = 1
    action = StringHolder("Demo_WaitFrame")
    actor.actions.append(action)
    flowchart.actors.append(actor)
    done = Event()
    done.name = "VanillaDone"
    done.data = ActionEvent()
    done.data.actor = make_rindex(actor)
    done.data.actor_action = make_rindex(action)
    done.data.params = make_params((("IsWaitFinish", True), ("Frame", 1)))
    done.data.nxt = make_index(None)
    flowchart.events.append(done)
    for name in ("Boot", "Later", "AirStartUP"):
        entry = EntryPoint(name)
        entry.main_event = make_index(done)
        entry.variable_defs["SampleCount"] = VariableDef.from_value(3)
        flowchart.entry_points.append(entry)
    flow.flowchart = flowchart
    return flow


ZELDA_GATE = "AtO_CheckZeldaTalk"
ZELDA_LINE = "AtO_ZeldaLine"
ZELDA_TALK_ENTRY = "Talk"
SONG_GATE = "AtO_CheckEpilogueSong"
SONG_LINE = "AtO_SariaSongLine"
SONG_FADE = "AtO_SariaSongFade"
SONG_ENTRY = "SariaSong"
SONG_ENTRY_EVENT = "Event125"
FADE_ACTOR = "CameraXLinkControl"
FADE_ACTION = "EventTriggerXLinkFadeByKey"


def sample_flow_bytes() -> bytes:
    return _write(make_sample_flow(), "sample flow")


def sample_talk_flow_bytes(entry_name: str = "HavePlayedEvent.Dm_Kokiri_0150_Talk") -> bytes:
    """The sample boot flow plus the cleared-save talk entry.

    On a cleared save the schedule's last true row is
    ``HavePlayedEvent.Dm_Kokiri_0150``, and that row enters
    ``Npc_Kokiri_Saria_Child`` at ``HavePlayedEvent.Dm_Kokiri_0150_Talk``.
    The boot sample keeps Boot, Later, and AirStartUP so the boot hook's
    fixture stays separate.
    """
    flow = make_sample_flow()
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError("sample talk flow has no flowchart")
    if any(entry.name == entry_name for entry in flowchart.entry_points):
        return _write(flow, "sample talk flow")
    done = flowchart.events[0]
    entry = EntryPoint(entry_name)
    entry.main_event = make_index(done)
    entry.variable_defs["SampleCount"] = VariableDef.from_value(3)
    flowchart.entry_points.append(entry)
    return _write(flow, "sample talk flow")


def sample_zelda_flow_bytes() -> bytes:
    """Zelda's courtyard shape, small enough for the tests.

    ``Talk`` starts at Event99. Event99 is a same-file subflow whose
    flowchart name is empty and whose entry point is ``Mask_All``. Its nxt
    is Event6, a ``GameDataActor`` bool-expression query, not a talk. Event5,
    the first ``Npc_EventStarter.EventTalk`` reachable from Event6, is the
    parameter source. Other talks exist and are not that source. Built for
    the tests. It is not a game file.
    """
    flow = EventFlow()
    flow.name = "Npc_Hylia_Zelda_Child"
    flowchart = Flowchart()
    flowchart.name = flow.name
    starter = Actor()
    starter.identifier = ActorIdentifier(STARTER_ACTOR)
    starter.concurrent_clips = 1
    starter.params = make_params(GAMEDATA_ACTOR_PARAMS)
    talk_action = StringHolder(TALK_ACTION)
    starter.actions.append(talk_action)
    flowchart.actors.append(starter)
    gamedata = Actor()
    gamedata.identifier = ActorIdentifier("GameDataActor")
    gamedata.concurrent_clips = 1
    gamedata.params = make_params(GAMEDATA_ACTOR_PARAMS)
    bool_exp = StringHolder("EventQueryIsGameDataBoolExp")
    gamedata.queries.append(bool_exp)
    flowchart.actors.append(gamedata)
    system = Actor()
    system.identifier = ActorIdentifier("EventSystemActor")
    system.concurrent_clips = 1
    wait = StringHolder("Demo_WaitFrame")
    system.actions.append(wait)
    flowchart.actors.append(system)

    def wait_event(name: str, nxt: Event | None) -> Event:
        event = Event()
        event.name = name
        event.data = ActionEvent()
        event.data.actor = make_rindex(system)
        event.data.actor_action = make_rindex(wait)
        event.data.params = make_params((("IsWaitFinish", True), ("Frame", 1)))
        event.data.nxt = make_index(nxt)
        return event

    def talk_event(name: str, message_id: str, *, anonymous: bool) -> Event:
        event = Event()
        event.name = name
        event.data = ActionEvent()
        event.data.actor = make_rindex(starter)
        event.data.actor_action = make_rindex(talk_action)
        event.data.params = make_params(_talk_params(message_id, anonymous=anonymous, maintain=True))
        event.data.nxt = make_index(None)
        return event

    event5 = talk_event("Event5", "EventFlowMsg/Npc_Hylia_Zelda_Child:Talk_01", anonymous=False)
    event50 = talk_event("Event50", "EventFlowMsg/Npc_Hylia_Zelda_Child:Talk_50", anonymous=True)
    event2 = wait_event("Event2", event5)
    event55 = wait_event("Event55", None)
    event6 = Event()
    event6.name = "Event6"
    event6.data = SwitchEvent()
    event6.data.actor = make_rindex(gamedata)
    event6.data.actor_query = make_rindex(bool_exp)
    event6.data.params = make_params((("GameDataName", "DoorOfTime_IsAfter_Step01_Exp"), ("Index", -1)))
    event6.data.cases = {0: make_rindex(event2), 1: make_rindex(event55)}
    event99 = Event()
    event99.name = "Event99"
    event99.data = SubFlowEvent()
    event99.data.res_flowchart_name = ""
    event99.data.entry_point_name = "Mask_All"
    event99.data.params = None
    event99.data.nxt = make_index(event6)
    done = wait_event("VanillaDone", None)
    flowchart.events.extend((event99, event6, event2, event5, event55, event50, done))
    entry_events = {
        "Talk": event99,
        "Zelda_CanbeStopped_Impa_01": done,
        "Zelda_CanbeStopped_Impa_02": done,
        "Sub_CanbeStopped_Impa_Camera": done,
        "AboutFireStone": done,
        "AboutWaterStone": done,
        "Mask_Talk": event50,
        "Mask_All": done,
        "InterestTalk_Zelda_Child": done,
        "InterestTalk_Zelda_Child_ChangeTheTopic": done,
        "aboutGeneral": done,
        "ThatsAll": done,
        "Intro": done,
    }
    for name, event in entry_events.items():
        entry = EntryPoint(name)
        entry.main_event = make_index(event)
        flowchart.entry_points.append(entry)
    flow.flowchart = flowchart
    return _write(flow, "zelda sample")


def sample_saria_song_flow_bytes() -> bytes:
    """The Saria's Song telepathy shape, small enough for the tests.

    ``SariaSong`` starts at Event125. Event26's nxt is Event155, a child
    query with two dummy cases. Event19 is the system-message parameter
    source. Event122 fades the telepathy sparkle. Built for the tests. It
    is not a game file.
    """
    flow = EventFlow()
    flow.name = "Dm_SY_Ocarina_SariaSong"
    flowchart = Flowchart()
    flowchart.name = flow.name
    text = Actor()
    text.identifier = ActorIdentifier(TEXT_ACTOR)
    text.concurrent_clips = 1
    text.params = make_params(GAMEDATA_ACTOR_PARAMS)
    talk = StringHolder(TALK_ACTION)
    text.actions.append(talk)
    flowchart.actors.append(text)
    gamedata = Actor()
    gamedata.identifier = ActorIdentifier("GameDataActor")
    gamedata.concurrent_clips = 1
    gamedata.params = make_params(GAMEDATA_ACTOR_PARAMS)
    flowchart.actors.append(gamedata)
    camera = Actor()
    camera.identifier = ActorIdentifier(FADE_ACTOR)
    camera.concurrent_clips = 1
    camera.params = make_params(GAMEDATA_ACTOR_PARAMS)
    fade_action = StringHolder(FADE_ACTION)
    camera.actions.append(fade_action)
    flowchart.actors.append(camera)

    def action(actor: Actor, holder: StringHolder, name: str, params, nxt: Event | None) -> Event:
        event = Event()
        event.name = name
        event.data = ActionEvent()
        event.data.actor = make_rindex(actor)
        event.data.actor_action = make_rindex(holder)
        event.data.params = None if params is None else make_params(params)
        event.data.nxt = make_index(nxt)
        return event

    child = action(text, talk, "Event154", (("MessageId", "child"),), None)
    adult = action(text, talk, "Event42", (("MessageId", "adult"),), None)
    event155 = Event()
    event155.name = "Event155"
    event155.data = SwitchEvent()
    event155.data.actor = make_rindex(gamedata)
    event155.data.actor_query = make_rindex(_ensure_holder(gamedata.queries, "EventQueryPlayerIsChild"))
    event155.data.params = make_params((("GameDataName", "PlayerStatus.IsChild"), ("Index", -1)))
    event155.data.cases = {0: make_rindex(child), 1: make_rindex(adult)}
    event26 = action(text, talk, "Event26", (("ASName", "Look"),), event155)
    event125 = action(text, talk, "Event125", (("ASName", "Participate"),), event26)
    event19 = action(
        text,
        talk,
        "Event19",
        (
            ("EndDialogOption", 0),
            ("ASName", ""),
            ("IsAnimeDriven", False),
            ("IsPlayASAllSlot", False),
            ("ChoiceDefaultNo", 0),
            ("ChoiceCancelNo", 0),
            ("IsNotBecomeSpeaker", False),
            ("ChangeMstxtForActor", False),
            ("IsAnonymous", False),
            ("NotPlayLipSyncAnim", False),
            ("IsNotOpenIfSkipedMostRecent", False),
            ("ChoiceLabel1", 0),
            ("ChoiceLabel2", 1),
            ("ChoiceLabel3", 3),
            ("ChoiceLabel4", 4),
            ("ChoiceNumber", 0),
            ("MaintainPerformanceState", 0),
            ("MessageId", "EventFlowMsg/Dm_SY_Ocarina:Talk_103"),
            ("IsCloseDialog", True),
            ("IsOverrideStyle", True),
            ("OverrideStyle", 0),
        ),
        None,
    )
    event122 = action(
        camera,
        fade_action,
        "Event122",
        (("ELinkKey", "Dm_SY_Ocarina_Talk_SariaSong"), ("SLinkKey", ""), ("IsKill", False)),
        None,
    )
    flowchart.events.extend((event125, event26, event155, child, adult, event19, event122))
    entry = EntryPoint(SONG_ENTRY)
    entry.main_event = make_index(event125)
    flowchart.entry_points.append(entry)
    flow.flowchart = flowchart
    return _write(flow, "saria song sample")


def ganon_probe_name(case: int) -> str:
    return f"AtO_GanonCase{case}"


def bool_probe_name(case: int) -> str:
    return f"AtO_BoolCase{case}"


def continuing_cases(patch: FlowPatch, step: FlowStep) -> set[int]:
    """Cases of this query that continue to the next step. The rest rejoin the original event."""
    if step.call == "EventQueryIsGameDataInt" and patch.ganon_cleared_cases is not None:
        return set(patch.ganon_cleared_cases)
    if step.call == "EventQueryIsGameDataBool":
        if patch.bool_false_case is not None:
            return {patch.bool_false_case}
        if step.continue_case is not None:
            return {step.continue_case}
        return {BOOL_FALSE_CASE}
    if step.continue_case is None:
        raise ModError(f"{patch.file}: {step.name} has no continue case")
    return {step.continue_case}


def effective_steps(patch: FlowPatch) -> tuple:
    """YAML steps, with the +1 rupee in front when a debug flag is set."""
    if not patch.debug_visible and not patch.debug_cases:
        return patch.steps
    reserved = {DEBUG_RUPEE_EVENT}
    if patch.debug_cases:
        reserved.update(ganon_probe_name(case) for case in GANON_CASE_RUPEES)
        reserved.update(bool_probe_name(case) for case in BOOL_CASE_RUPEES)
    clash = sorted(reserved.intersection(step.name for step in patch.steps))
    if clash:
        raise ModError(f"{patch.file}: {', '.join(clash)} is reserved for the debug events")
    debug = FlowStep(
        name=DEBUG_RUPEE_EVENT,
        call="EventTriggerAddOrSubGameDataInt",
        params=(
            ("GameDataName", DEBUG_RUPEE_FLAG),
            ("Index", -1),
            ("Value", 1),
        ),
        continue_case=None,
    )
    return (debug, *patch.steps)


def _probe_names(patch: FlowPatch) -> list[str]:
    if not patch.debug_cases:
        return []
    return [ganon_probe_name(case) for case in GANON_CASE_RUPEES] + [
        bool_probe_name(case) for case in BOOL_CASE_RUPEES
    ]


def _case_note(patch: FlowPatch) -> str | None:
    parts = []
    if patch.ganon_cleared_cases is not None:
        shown = ", ".join(str(case) for case in patch.ganon_cleared_cases)
        parts.append(f"ganon_cleared_cases: [{shown}]")
    if patch.bool_false_case is not None:
        parts.append(f"bool_false_case: {patch.bool_false_case}")
    if not parts:
        return None
    return "normal gate would continue on " + "; ".join(parts)


def _rupee_action(actor: Actor, name: str, amount: int, nxt: Event) -> Event:
    event = Event()
    event.name = name
    event.data = ActionEvent()
    event.data.actor = make_rindex(actor)
    event.data.actor_action = make_rindex(_ensure_holder(actor.actions, "EventTriggerAddOrSubGameDataInt"))
    event.data.params = make_params(
        (
            ("GameDataName", DEBUG_RUPEE_FLAG),
            ("Index", -1),
            ("Value", amount),
        )
    )
    event.data.nxt = make_index(nxt)
    return event


def _attach_debug_cases(patch: FlowPatch, steps: tuple, created: list[Event], actor: Actor) -> list[Event]:
    """Point the gate's query cases at their own rupee adds, then on to the next switch or the set."""
    queries = [(index, step) for index, step in enumerate(steps) if CALLS[step.call]["kind"] == "query"]
    set_index = queries[1][0] + 1 if len(queries) > 1 else -1
    if (
        len(queries) < 2
        or queries[0][1].call != "EventQueryIsGameDataInt"
        or queries[1][1].call != "EventQueryIsGameDataBool"
        or queries[1][0] != queries[0][0] + 1
        or set_index >= len(steps)
        or steps[set_index].call != "EventTriggerSetGameDataBool"
    ):
        raise ModError(
            f"{patch.file}: debug_cases needs the +1 rupee, then EventQueryIsGameDataInt, "
            "then EventQueryIsGameDataBool, then EventTriggerSetGameDataBool"
        )
    int_index = queries[0][0]
    bool_index = queries[1][0]
    int_event = created[int_index]
    bool_event = created[bool_index]
    set_event = created[set_index]
    probes: list[Event] = []
    int_cases = {}
    for case, amount in GANON_CASE_RUPEES.items():
        probe = _rupee_action(actor, ganon_probe_name(case), amount, bool_event)
        probes.append(probe)
        int_cases[case] = make_rindex(probe)
    bool_cases = {}
    for case, amount in BOOL_CASE_RUPEES.items():
        probe = _rupee_action(actor, bool_probe_name(case), amount, set_event)
        probes.append(probe)
        bool_cases[case] = make_rindex(probe)
    if not isinstance(int_event.data, SwitchEvent) or not isinstance(bool_event.data, SwitchEvent):
        raise ModError(f"{patch.file}: debug_cases queries were not built as switches")
    int_event.data.cases = int_cases
    bool_event.data.cases = bool_cases
    return probes


def _ensure_gamedata_actor(flowchart) -> Actor:
    found = [
        actor
        for actor in flowchart.actors
        if actor.identifier.name == _GAMEDATA and actor.identifier.sub_name == ""
    ]
    if len(found) > 1:
        raise ModError("flowchart has more than one GameDataActor")
    if found:
        return found[0]
    actor = Actor()
    actor.identifier = ActorIdentifier(_GAMEDATA)
    actor.concurrent_clips = 1
    actor.params = make_params(GAMEDATA_ACTOR_PARAMS)
    for name in GAMEDATA_ACTIONS:
        actor.actions.append(StringHolder(name))
    for name in GAMEDATA_QUERIES:
        actor.queries.append(StringHolder(name))
    flowchart.actors.append(actor)
    return actor


def _attach_epilogue(flowchart, patch: FlowPatch, last_gate: Event, original: Event, gamedata: Actor) -> list[Event]:
    """Point the flag set at a same-file subflow, then the era switch, which ends.

    The new entry point starts at the event the hooked entry used to start at, so
    the subflow runs the vanilla boot and returns into the chain. An empty
    flowchart name is how evfl stores a ``(self)::Entry`` call. A call into another
    file stores that flowchart's name and its entry point name. Actors that are
    already in the file keep their params; only a missing action is added. An
    actor this file does not have is created with the standard six params and
    concurrent_clips 1.
    """
    needed: dict[str, list[str]] = {}
    for step in patch.chain:
        if step.subflow is not None:
            continue
        bucket = needed.setdefault(step.actor, [])
        if step.call not in bucket:
            bucket.append(step.call)
    actors: dict[str, Actor] = {}
    for name, actions in needed.items():
        if name == _GAMEDATA:
            for action in actions:
                _ensure_holder(gamedata.actions, action)
            actors[name] = gamedata
        else:
            actors[name] = _ensure_named_actor(flowchart, name, actions)
    chain: list[Event] = []
    for step in patch.chain:
        event = Event()
        event.name = step.name
        if step.subflow is not None:
            event.data = SubFlowEvent()
            event.data.res_flowchart_name = step.subflow
            event.data.entry_point_name = step.entry_point or ""
            event.data.params = None
            event.data.nxt = make_index(None)
        else:
            target = actors[step.actor]
            event.data = ActionEvent()
            event.data.actor = make_rindex(target)
            event.data.actor_action = make_rindex(_ensure_holder(target.actions, step.call))
            params = step.params
            if step.call == "EventReserveBancChange":
                if not any(key == "BootEventName" for key, _value in params):
                    raise ModError(f"{patch.file}: {step.name} has no BootEventName")
                params = tuple(
                    (key, effective_boot_name(patch) if key == "BootEventName" else value) for key, value in params
                )
            event.data.params = make_params(params) if params else None
            event.data.nxt = make_index(None)
        chain.append(event)
    for index, event in enumerate(chain):
        event.data.nxt = make_index(chain[index + 1] if index + 1 < len(chain) else None)
    # The custom wake sets Navi_Ban after the banc change, so the reset cannot clear it.
    # The vanilla bed event does not, so the fallback sets it here.
    first = chain[0]
    prelude: list[Event] = []
    if not patch.custom_wake:
        ban = _navi_ban_event(gamedata, FALLBACK_BAN, first)
        prelude.append(ban)
        first = ban
    call = Event()
    call.name = ORIG_CALL
    call.data = SubFlowEvent()
    # Empty flowchart name is evfl's (self):: form. LinkSleepInBed uses that for
    # BedBootEvent_NoLoading. This tree has no dump of that file to compare bytes.
    call.data.res_flowchart_name = ""
    call.data.entry_point_name = patch.orig_entry
    call.data.params = None
    call.data.nxt = make_index(first)
    if not isinstance(last_gate.data, ActionEvent):
        raise ModError(f"{patch.file}: the gate does not end on an action, so it cannot call the original boot")
    last_gate.data.nxt = make_index(call)
    entry = EntryPoint(patch.orig_entry)
    entry.main_event = make_index(original)
    flowchart.entry_points.append(entry)
    return [call, *prelude, *chain]


def _refresh_leaves(delete: bool) -> tuple[str, ...]:
    return _DELETE_REFRESH if delete else _SYNC_REFRESH


def _stone_names(prefix: str, patch: FlowPatch, *, split: bool) -> list[str]:
    names: list[str] = []
    for index, (stem, _item) in enumerate(POUCH_REMOVALS):
        names.append(f"{prefix}Check{stem}")
        if split and index:
            names.append(f"{prefix}Check{stem}Held")
        names.append(f"{prefix}Remove{stem}")
        names.append(f"{prefix}Clear{stem}Get")
        names.append(f"{prefix}Clear{stem}Stock")
    if split:
        names.append(f"{prefix}CheckOcarinaTimeHeld")
        names.append(f"{prefix}EquipOcarinaIdleHeld")
        names.extend(f"{prefix}IdleHeld{leaf}" for leaf in _refresh_leaves(patch.ocarina_delete_equipment))
    return names


def _ocarina_names(prefix: str, patch: FlowPatch, *, split: bool) -> list[str]:
    leaves = _refresh_leaves(patch.ocarina_delete_equipment)
    names = [f"{prefix}{leaf}" for leaf in (*_OCARINA_LEAVES, *leaves)]
    if split:
        names.append(f"{prefix}EquipOcarinaIdle")
        names.extend(f"{prefix}Idle{leaf}" for leaf in leaves)
        names.append(f"{prefix}Autosave")
    return names


def _event_call(event: Event) -> str:
    data = event.data
    if isinstance(data, SwitchEvent) and data.actor.v is not None and data.actor_query.v is not None:
        return f"{data.actor.v.identifier.name}?{data.actor_query.v}"
    if isinstance(data, ActionEvent) and data.actor.v is not None and data.actor_action.v is not None:
        return f"{data.actor.v.identifier.name}.{data.actor_action.v}"
    return event.name


def _pouch_params(actor_name: str, num: int) -> tuple[tuple[str, object], ...]:
    """The parameter set from Dm_Kokiri_0155's give and Goto_HyruleCastle's removal.

    ``IsEquipDIrect`` is the vanilla spelling. ``IsGetEvent`` false skips the
    get cutscene. Num 1 gives, Num -1 removes.
    """
    return (
        ("Num", num),
        ("IsEquipDIrect", False),
        ("IsSetMessageTargetActorName", True),
        ("Actor", actor_name),
        ("IsGetEvent", False),
    )


def _equipment_refresh(flowchart, prefix: str, nxt: Event | None, delete: bool) -> list[Event]:
    """Rebuild the holder after ``PlayerStatus.EquipOcarina`` is written.

    The default block is the Npc_Dream / DreamBossStage shape: wait one frame,
    sync the pouch onto the equipment, then wait until those actors exist.
    ``delete`` uses LinkSleepInBed's delete-then-wait instead. The new actions
    are declared on the actor of that name already in the file. A file that
    does not have the actor gets one, which is how the sample flow is built.
    """
    if delete:
        steps = (
            ("DeleteEquipment", UI_SYSTEM, DELETE_EQUIPMENT_ACTION, None),
            ("WaitEquipment", PLAYER_EVENT, WAIT_EQUIPMENT_ACTION, (("IsWaitEnd", True),)),
        )
    else:
        steps = (
            (
                "WaitEquipmentFrame",
                GAME_SYSTEM,
                WAIT_FRAME_ACTION,
                (("IsWaitEnd", True), ("Frames", 1)),
            ),
            ("SyncEquipment", UI_SYSTEM, SYNC_EQUIPMENT_ACTION, None),
            ("WaitEquipment", PLAYER_EVENT, WAIT_EQUIPMENT_ACTION, (("IsWaitEnd", True),)),
        )
    made = [
        _action_event(flowchart, actor_name, call, f"{prefix}{leaf}", params)
        for leaf, actor_name, call, params in steps
    ]
    for left, right in zip(made, made[1:]):
        left.data.nxt = make_index(right)
    made[-1].data.nxt = make_index(nxt)
    return made


def _equip_fairy(flowchart, name: str, nxt: Event | None) -> Event:
    gamedata = _ensure_gamedata_actor(flowchart)
    event = Event()
    event.name = name
    event.data = ActionEvent()
    event.data.actor = make_rindex(gamedata)
    event.data.actor_action = make_rindex(_ensure_holder(gamedata.actions, SET_STRING_ACTION))
    event.data.params = make_params((("GameDataName", EQUIP_OCARINA), ("Value", OCARINA_FAIRY), ("Index", -1)))
    event.data.nxt = make_index(nxt)
    return event


def _ocarina_swap(flowchart, prefix: str, nxt: Event | None, patch: FlowPatch, *, autosave_after_swap: bool) -> list[Event]:
    """Remove the Ocarina of Time when it is owned, then equip the Fairy Ocarina.

    Case 0 of a bool query is true. The Time check's false case skips the
    removal and still sets ``PlayerStatus.EquipOcarina``. Fairy is added only
    when Time was owned and Fairy is not. ``HavePlayedGetEvent`` is not written.
    Both cases recreate the equipment after the string write. On the later-load
    path the true case also autosaves, so the two cases cannot share that tail.
    """
    gamedata = _ensure_gamedata_actor(flowchart)
    pouch = _ensure_named_actor(flowchart, POUCH_ACTOR, [POUCH_ACTION])
    delete = patch.ocarina_delete_equipment
    autosave = None
    idle_equip = None
    idle_refresh: list[Event] = []
    if autosave_after_swap:
        autosave = _action_event(flowchart, GAME_SYSTEM, AUTOSAVE_ACTION, f"{prefix}Autosave", None)
        autosave.data.nxt = make_index(nxt)
        refresh = _equipment_refresh(flowchart, prefix, autosave, delete)
        idle_refresh = _equipment_refresh(flowchart, f"{prefix}Idle", nxt, delete)
        idle_equip = _equip_fairy(flowchart, f"{prefix}EquipOcarinaIdle", idle_refresh[0])
    else:
        refresh = _equipment_refresh(flowchart, prefix, nxt, delete)
    names = {leaf: f"{prefix}{leaf}" for leaf in _OCARINA_LEAVES}
    events = {leaf: Event() for leaf in _OCARINA_LEAVES}
    for leaf, event in events.items():
        event.name = names[leaf]

    def link_action(leaf: str, actor: Actor, call: str, params, follower: Event | None) -> None:
        event = events[leaf]
        event.data = ActionEvent()
        event.data.actor = make_rindex(actor)
        event.data.actor_action = make_rindex(_ensure_holder(actor.actions, call))
        event.data.params = make_params(params)
        event.data.nxt = make_index(follower)

    def link_bool(leaf: str, game_data: str, when_true: Event, when_false: Event) -> None:
        event = events[leaf]
        event.data = SwitchEvent()
        event.data.actor = make_rindex(gamedata)
        event.data.actor_query = make_rindex(_ensure_holder(gamedata.queries, "EventQueryIsGameDataBool"))
        event.data.params = make_params((("GameDataName", game_data), ("Index", -1)))
        event.data.cases = {0: make_rindex(when_true), 1: make_rindex(when_false)}

    link_action(
        "RemoveOcarinaTime",
        pouch,
        POUCH_ACTION,
        _pouch_params(OCARINA_TIME, -1),
        events["ClearOcarinaGet"],
    )
    link_action(
        "ClearOcarinaGet",
        gamedata,
        "EventTriggerSetGameDataBool",
        (("GameDataName", ISGET_TIME), ("Index", -1), ("Value", False)),
        events["ClearOcarinaStock"],
    )
    link_action(
        "ClearOcarinaStock",
        gamedata,
        SET_INT_ACTION,
        (("GameDataName", STOCK_TIME), ("Index", -1), ("Value", 0)),
        events["CheckOcarinaFairy"],
    )
    link_action(
        "GiveOcarinaFairy",
        pouch,
        POUCH_ACTION,
        _pouch_params(OCARINA_FAIRY, 1),
        events["EquipOcarinaFairy"],
    )
    link_action(
        "EquipOcarinaFairy",
        gamedata,
        SET_STRING_ACTION,
        (("GameDataName", EQUIP_OCARINA), ("Value", OCARINA_FAIRY), ("Index", -1)),
        refresh[0],
    )
    link_bool("CheckOcarinaFairy", ISGET_FAIRY, events["EquipOcarinaFairy"], events["GiveOcarinaFairy"])
    without_time = idle_equip if idle_equip is not None else events["EquipOcarinaFairy"]
    link_bool("CheckOcarinaTime", ISGET_TIME, events["RemoveOcarinaTime"], without_time)
    built = [events[leaf] for leaf in _OCARINA_LEAVES]
    built.extend(refresh)
    if autosave is not None:
        built.append(autosave)
    if idle_equip is not None:
        built.append(idle_equip)
        built.extend(idle_refresh)
    return built


def _stone_removal(flowchart, prefix: str, clean_done: Event, held_done: Event | None) -> list[Event]:
    """Drop each owned pouch item in ``POUCH_REMOVALS``, then continue.

    Case 0 is true. An item that is not owned skips its removal. ``held_done``
    is the later-load path that already removed one item, so the idle ocarina
    tail can share the existing autosave. The wake passes None: every path
    ends on ``clean_done``, and the wake autosave is still the last event.
    """
    split = held_done is not None
    gamedata = _ensure_gamedata_actor(flowchart)
    _ensure_named_actor(flowchart, POUCH_ACTOR, [POUCH_ACTION])
    made: list[Event] = []
    checks: dict[tuple[str, bool], Event] = {}
    removes: dict[str, Event] = {}
    tails: dict[str, Event] = {}

    def keep(event: Event) -> Event:
        made.append(event)
        return event

    for index, (stem, item) in enumerate(POUCH_REMOVALS):
        lanes = (False, True) if split and index else (False,)
        for held in lanes:
            event = Event()
            event.name = f"{prefix}Check{stem}" + ("Held" if held else "")
            event.data = SwitchEvent()
            event.data.actor = make_rindex(gamedata)
            event.data.actor_query = make_rindex(_ensure_holder(gamedata.queries, "EventQueryIsGameDataBool"))
            event.data.params = make_params((("GameDataName", f"IsGet.{item}"), ("Index", -1)))
            event.data.cases = {}
            checks[(stem, held)] = keep(event)
        remove = _action_event(
            flowchart, POUCH_ACTOR, POUCH_ACTION, f"{prefix}Remove{stem}", _pouch_params(item, -1)
        )
        clear_get = _action_event(
            flowchart,
            "GameDataActor",
            "EventTriggerSetGameDataBool",
            f"{prefix}Clear{stem}Get",
            (("GameDataName", f"IsGet.{item}"), ("Index", -1), ("Value", False)),
        )
        clear_stock = _action_event(
            flowchart,
            "GameDataActor",
            SET_INT_ACTION,
            f"{prefix}Clear{stem}Stock",
            (("GameDataName", f"PouchData.{item}.StockNum"), ("Index", -1), ("Value", 0)),
        )
        remove.data.nxt = make_index(clear_get)
        clear_get.data.nxt = make_index(clear_stock)
        removes[stem] = keep(remove)
        keep(clear_get)
        tails[stem] = keep(clear_stock)

    def onward(index: int, held: bool) -> Event:
        if index >= len(POUCH_REMOVALS):
            if held and split:
                return held_done
            return clean_done
        stem = POUCH_REMOVALS[index][0]
        return checks[(stem, True)] if held and split else checks[(stem, False)]

    for index, (stem, _item) in enumerate(POUCH_REMOVALS):
        lanes = (False, True) if split and index else (False,)
        for held in lanes:
            check = checks[(stem, held)]
            check.data.cases = {0: make_rindex(removes[stem]), 1: make_rindex(onward(index + 1, held))}
        tails[stem].data.nxt = make_index(onward(index + 1, split))
    return made


def _ocarina_after_stones(flowchart, prefix: str, remove: Event, autosave: Event, patch: FlowPatch) -> list[Event]:
    """The load's second ocarina check, used after a pouch item was removed.

    Owning Time still enters the shared removal, which already autosaves.
    A save that already lost Time equips Fairy and then uses that same autosave.
    """
    gamedata = _ensure_gamedata_actor(flowchart)
    refresh = _equipment_refresh(flowchart, f"{prefix}IdleHeld", autosave, patch.ocarina_delete_equipment)
    idle = _equip_fairy(flowchart, f"{prefix}EquipOcarinaIdleHeld", refresh[0])
    check = Event()
    check.name = f"{prefix}CheckOcarinaTimeHeld"
    check.data = SwitchEvent()
    check.data.actor = make_rindex(gamedata)
    check.data.actor_query = make_rindex(_ensure_holder(gamedata.queries, "EventQueryIsGameDataBool"))
    check.data.params = make_params((("GameDataName", ISGET_TIME), ("Index", -1)))
    check.data.cases = {0: make_rindex(remove), 1: make_rindex(idle)}
    return [check, idle, *refresh]


def _navi_ban_event(actor: Actor, name: str, nxt: Event) -> Event:
    event = Event()
    event.name = name
    event.data = ActionEvent()
    event.data.actor = make_rindex(actor)
    event.data.actor_action = make_rindex(_ensure_holder(actor.actions, "EventTriggerSetGameDataBool"))
    event.data.params = make_params(
        (
            ("GameDataName", "Navi_Ban"),
            ("Index", -1),
            ("Value", True),
        )
    )
    event.data.nxt = make_index(nxt)
    return event


def _wake_log(built) -> str:
    by_name = {event.name: event for event in built.events}
    lines = ["  wake copies params from LinkSleepInBed:"]
    for name in ("AtO_Camera", "AtO_Stretch"):
        event = by_name.get(name)
        keys = list(event.data.params.data) if event is not None and event.data.params is not None else []
        lines.append(f"    {name}: {', '.join(keys) or '(none)'}")
    for event in built.events:
        if not event.name.startswith("AtO_Heal"):
            continue
        data = event.data
        actor = data.actor.v.identifier.name if isinstance(data, ActionEvent) and data.actor.v is not None else "?"
        call = data.actor_action.v if isinstance(data, ActionEvent) else "?"
        keys = list(data.params.data) if isinstance(data, ActionEvent) and data.params is not None else []
        lines.append(f"    {event.name} {actor}.{call}: {', '.join(keys) or '(none)'}")
    air = by_name.get("AtO_Air")
    if air is not None and isinstance(air.data, SubFlowEvent):
        called = air.data.res_flowchart_name or "(self)"
        lines.append(f"  wake keeps {called}::{air.data.entry_point_name}")
    if built.external_subflows:
        lines.append("  wake subflows added to DefaultBootEvent: " + ", ".join(built.external_subflows))
    if built.omitted:
        lines.append("  bed events left out of the wake: " + ", ".join(built.omitted))
    return "\n".join(lines)


def _wake_tail(flowchart, patch: FlowPatch) -> tuple[Event | None, list[Event]]:
    """The narration, then the keep-state and the autosave.

    The narration is linked before the camera is given back. The save pair is
    linked after that camera call, and the save is the last event. OpeningEvent,
    Saria's Event32, and ForestTemple_Gimmick Event326 use that same ending:
    keep-state, ``EventTriggerRequestAutoSave``, then the flow ends. No caption
    follows the save.
    """
    dialogue = _talk_event(flowchart, WAKE_LINE, WAKE_MESSAGE_ID, anonymous=True) if patch.wake_dialogue else None
    save: list[Event] = []
    if patch.autosave:
        save.append(
            _action_event(flowchart, "EventCamera", KEEP_CAMERA_ACTION, KEEP_CAMERA_EVENT, KEEP_CAMERA_PARAMS)
        )
        save.append(_action_event(flowchart, "GameSystemActor", AUTOSAVE_ACTION, AUTOSAVE_EVENT, None))
    return dialogue, save


def _action_event(flowchart, actor_name: str, action: str, event_name: str, params) -> Event:
    actor = _ensure_named_actor(flowchart, actor_name, [action])
    event = Event()
    event.name = event_name
    event.data = ActionEvent()
    event.data.actor = make_rindex(actor)
    event.data.actor_action = make_rindex(_ensure_holder(actor.actions, action))
    event.data.params = None if params is None else make_params(params)
    event.data.nxt = make_index(None)
    return event


def _talk_event(
    flowchart,
    name: str,
    message_id: str,
    *,
    anonymous: bool,
    actor_name: str = TEXT_ACTOR,
    maintain: bool = False,
) -> Event:
    actor = _ensure_named_actor(flowchart, actor_name, [TALK_ACTION])
    event = Event()
    event.name = name
    event.data = ActionEvent()
    event.data.actor = make_rindex(actor)
    event.data.actor_action = make_rindex(_ensure_holder(actor.actions, TALK_ACTION))
    event.data.params = make_params(_talk_params(message_id, anonymous=anonymous, maintain=maintain))
    event.data.nxt = make_index(None)
    return event


def _saria_talk_events(flowchart) -> list[Event]:
    """Recent-talk split, then Npc_EventStarter.EventTalk.

    Case 0 of EventQueryIsOnRecentTalkFlag plays the line. Case 1 calls
    InitTalk::InitTalk_KokiriOnly first. NpcFairy_MovingFlag is false: InitTalk
    forks a Navi look-at, and this epilogue has Navi banned. That look-at was
    not shown to be a no-op.
    """
    line = _talk_event(
        flowchart,
        SARIA_LINE,
        SARIA_MESSAGE_ID,
        anonymous=False,
        actor_name=STARTER_ACTOR,
        maintain=True,
    )
    starter = _ensure_named_actor(flowchart, STARTER_ACTOR, [TALK_ACTION], [RECENT_QUERY])
    init = Event()
    init.name = INIT_TALK_EVENT
    init.data = SubFlowEvent()
    init.data.res_flowchart_name = INIT_FLOW
    init.data.entry_point_name = INIT_ENTRY
    init.data.params = make_params(
        (("Arg_Turn", 4), ("Arg_Greeting", 4), ("NpcFairy_MovingFlag", False))
    )
    init.data.nxt = make_index(line)
    recent = Event()
    recent.name = RECENT_EVENT
    recent.data = SwitchEvent()
    recent.data.actor = make_rindex(starter)
    recent.data.actor_query = make_rindex(_ensure_holder(starter.queries, RECENT_QUERY))
    recent.data.params = None
    recent.data.cases = {
        RECENT_REPEAT_CASE: make_rindex(line),
        RECENT_FIRST_CASE: make_rindex(init),
    }
    return [recent, init, line]


def retarget_case(flowchart, event_name: str, case: int, target: Event) -> str:
    """Point one case of an existing switch at ``target``.

    Returns the event that case used to reach. Saria's epilogue talk does not
    call this. Event4 case 0, the repeat talk, never reaches the mask check, so
    retargeting only that check would leave repeat talks on the vanilla line.
    Zelda's courtyard edit can use it when one existing edge is the right hook.
    """
    matches = [event for event in flowchart.events if event.name == event_name]
    if len(matches) != 1:
        raise ModError(f"expected one event named {event_name}, found {len(matches)}")
    data = matches[0].data
    if not isinstance(data, SwitchEvent) or case not in data.cases:
        found = sorted(data.cases) if isinstance(data, SwitchEvent) else []
        raise ModError(f"{event_name} has no case {case}. Cases: {found or 'not a switch'}.")
    previous = data.cases[case].v
    data.cases[case] = make_rindex(target)
    if previous is None or not previous.name:
        raise ModError(f"{event_name} case {case} did not point at an event")
    return previous.name


def retarget_next(flowchart, event_name: str, target: Event) -> str:
    """Point one event's ``nxt`` at ``target`` and return the event it used to reach.

    This moves an action or subflow edge. It does not move a switch case, and it
    does not move an entry point. Zelda's courtyard talk uses it on Event99,
    after Mask_All, so the mask subflow still runs.
    """
    matches = [event for event in flowchart.events if event.name == event_name]
    if len(matches) != 1:
        names = ", ".join(event.name for event in flowchart.events) or "none"
        raise ModError(f"expected one event named {event_name}, found {len(matches)}. Events: {names}.")
    data = matches[0].data
    nxt = getattr(data, "nxt", None)
    if nxt is None:
        raise ModError(f"{event_name} has no nxt. It is not an action or a subflow.")
    previous = nxt.v
    data.nxt = make_index(target)
    if previous is None or not previous.name:
        raise ModError(f"{event_name} nxt did not point at a named event")
    return previous.name


def apply_courtyard(flow: EventFlow, patch: FlowPatch) -> HookNote:
    """Keep Event99's Mask_All call, then split on TitleEventBase.

    Case 0, the flag true, plays a new EventTalk and ends. Case 1 continues
    to the event Event99 already reached. Entry points stay where they are.
    """
    if patch.epilogue or patch.epilogue_talk:
        raise ModError(f"{patch.file}: the courtyard retarget does not use the boot or Saria talk gates")
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file} has no flowchart")
    if any(event.name in (ZELDA_GATE, ZELDA_LINE) for event in flowchart.events):
        raise ModError(
            f"{patch.file}: already patched. {ZELDA_GATE} or {ZELDA_LINE} is already in the flow. "
            "Build from a vanilla event flow."
        )
    matches = [event for event in flowchart.events if event.name == patch.retarget_next]
    if len(matches) != 1:
        names = ", ".join(event.name for event in flowchart.events) or "none"
        raise ModError(
            f"{patch.file}: expected one event named {patch.retarget_next}, found {len(matches)}. Events: {names}."
        )
    nxt = getattr(matches[0].data, "nxt", None)
    previous = nxt.v if nxt is not None else None
    if previous is None or not previous.name:
        raise ModError(f"{patch.file}: {patch.retarget_next} nxt does not point at a named event")
    if previous.name != patch.rejoin_event:
        raise ModError(
            f"{patch.file}: {patch.retarget_next} next is {previous.name}, expected {patch.rejoin_event}."
        )
    talk_matches = [entry for entry in flowchart.entry_points if entry.name == patch.talk_entry]
    reaching = [
        entry.name
        for entry in flowchart.entry_points
        if entry.main_event.v is not None and any(event.name == patch.retarget_next for event in _reachable(entry.main_event.v))
    ]
    listed = ", ".join(entry.name for entry in flowchart.entry_points) or "none"
    if len(talk_matches) != 1:
        raise ModError(
            f"{patch.file}: expected one entry named {patch.talk_entry!r}, found {len(talk_matches)}. "
            f"Entries that reach {patch.retarget_next}: {', '.join(reaching) or 'none'}. All: {listed}."
        )
    talk_entry = talk_matches[0]
    if talk_entry.name not in reaching:
        raise ModError(
            f"{patch.file}: {talk_entry.name} does not reach {patch.retarget_next}. "
            f"Entries that do: {', '.join(reaching) or 'none'}."
        )
    source = _talk_params_source(previous, patch.file)
    line = Event()
    line.name = ZELDA_LINE
    line.data = ActionEvent()
    line.data.actor = make_rindex(source.data.actor.v)
    line.data.actor_action = make_rindex(source.data.actor_action.v)
    line.data.params = _copied_message(source.data.params, patch.message_id, patch.file)
    line.data.nxt = make_index(None)
    gamedata = _ensure_gamedata_actor(flowchart)
    query = _ensure_holder(gamedata.queries, "EventQueryIsGameDataBool")
    gate = Event()
    gate.name = ZELDA_GATE
    gate.data = SwitchEvent()
    gate.data.actor = make_rindex(gamedata)
    gate.data.actor_query = make_rindex(query)
    gate.data.params = make_params((("GameDataName", "HavePlayedEvent.TitleEventBase"), ("Index", -1)))
    gate.data.cases = {0: make_rindex(line), 1: make_rindex(previous)}
    flowchart.events.extend((gate, line))
    returned = retarget_next(flowchart, patch.retarget_next, gate)
    if returned != patch.rejoin_event:
        raise ModError(f"{patch.file}: retarget moved {patch.retarget_next} off {returned}, not {patch.rejoin_event}")
    return HookNote(
        file=patch.file,
        entry_points=tuple(entry.name for entry in flowchart.entry_points),
        hooked=talk_entry.name,
        explicit=True,
        original_event=patch.retarget_next,
        case_note=(
            f"{patch.retarget_next} still runs Mask_All, then {ZELDA_GATE}. "
            f"Bool case 0 plays {ZELDA_LINE}. Case 1 continues to {patch.rejoin_event}."
        ),
        courtyard=True,
    )


def _talk_params_source(previous: Event, label: str) -> Event:
    """The EventTalk whose parameters the new line copies.

    Event6 is a bool-expression query. The first ``Npc_EventStarter.EventTalk``
    reachable from it is Event5. A talk that the rejoin never reaches is left
    alone, even when the flowchart has several.
    """
    if _is_starter_talk(previous):
        return previous
    for event in _reachable(previous):
        if _is_starter_talk(event):
            return event
    raise ModError(
        f"{label}: {previous.name} reaches no {STARTER_ACTOR}.{TALK_ACTION} to copy."
    )


def _is_starter_talk(event: Event) -> bool:
    data = event.data
    if not isinstance(data, ActionEvent) or data.actor.v is None or data.actor_action.v is None:
        return False
    actor = data.actor.v.identifier.name
    action = data.actor_action.v
    action_name = action.v if hasattr(action, "v") else action
    return actor == STARTER_ACTOR and action_name == TALK_ACTION


def _copied_message(params: Container | None, message_id: str, label: str) -> Container:
    import copy

    if params is None or not getattr(params, "data", None) or "MessageId" not in params.data:
        raise ModError(f"{label}: the vanilla EventTalk has no MessageId to copy")
    container = Container()
    container.data = copy.deepcopy(dict(params.data))
    container.data["MessageId"] = message_id
    return container


def _verify_courtyard(flow: EventFlow, patch: FlowPatch) -> None:
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file}: patched flow has no flowchart")
    by_name = {event.name: event for event in flowchart.events}
    event99 = by_name.get(patch.retarget_next)
    gate = by_name.get(ZELDA_GATE)
    line = by_name.get(ZELDA_LINE)
    if event99 is None or gate is None or line is None:
        raise ModError(f"{patch.file}: courtyard events did not survive the rewrite")
    if not isinstance(event99.data, SubFlowEvent) or event99.data.entry_point_name != "Mask_All":
        got = event99.data.entry_point_name if isinstance(event99.data, SubFlowEvent) else type(event99.data).__name__
        raise ModError(f"{patch.file}: {patch.retarget_next} entry point is {got!r}, expected Mask_All")
    if event99.data.nxt.v is None or event99.data.nxt.v.name != ZELDA_GATE:
        raise ModError(f"{patch.file}: {patch.retarget_next} nxt is not {ZELDA_GATE}")
    if not isinstance(gate.data, SwitchEvent) or gate.data.actor.v is None:
        raise ModError(f"{patch.file}: {ZELDA_GATE} is not a query")
    if gate.data.actor.v.identifier.name != "GameDataActor":
        raise ModError(f"{patch.file}: {ZELDA_GATE} is not on GameDataActor")
    query = gate.data.actor_query.v
    query_name = query.v if query is not None and hasattr(query, "v") else query
    if query_name != "EventQueryIsGameDataBool":
        raise ModError(f"{patch.file}: {ZELDA_GATE} query is {query_name!r}")
    if gate.data.params is None or gate.data.params.data.get("GameDataName") != "HavePlayedEvent.TitleEventBase":
        raise ModError(f"{patch.file}: {ZELDA_GATE} does not read HavePlayedEvent.TitleEventBase")
    if gate.data.params.data.get("Index") != -1:
        raise ModError(f"{patch.file}: {ZELDA_GATE} Index is {gate.data.params.data.get('Index')!r}")
    cases = {case: link.v.name if link.v is not None else None for case, link in gate.data.cases.items()}
    if cases.get(0) != ZELDA_LINE or cases.get(1) != patch.rejoin_event:
        raise ModError(f"{patch.file}: courtyard cases are {cases}")
    if not isinstance(line.data, ActionEvent) or line.data.nxt.v is not None:
        raise ModError(f"{patch.file}: {ZELDA_LINE} does not end the talk")
    if line.data.actor.v is None or line.data.actor.v.identifier.name != STARTER_ACTOR:
        raise ModError(f"{patch.file}: {ZELDA_LINE} is not {STARTER_ACTOR}.{TALK_ACTION}")
    if line.data.params is None or line.data.params.data.get("MessageId") != patch.message_id:
        raise ModError(f"{patch.file}: {ZELDA_LINE} message is {line.data.params.data.get('MessageId') if line.data.params else None!r}")
    rejoin = by_name.get(patch.rejoin_event)
    if rejoin is None:
        raise ModError(f"{patch.file}: {patch.rejoin_event} is missing after the rewrite")
    source_event = _talk_params_source(rejoin, patch.file)
    if not isinstance(source_event.data, ActionEvent) or source_event.data.params is None:
        raise ModError(f"{patch.file}: {source_event.name} has no EventTalk parameters")
    copied = dict(line.data.params.data)
    source = dict(source_event.data.params.data)
    copied.pop("MessageId", None)
    source.pop("MessageId", None)
    if copied != source:
        raise ModError(f"{patch.file}: {ZELDA_LINE} params are not a copy of {source_event.name}")
    talk = [entry for entry in flowchart.entry_points if entry.name == patch.talk_entry]
    if len(talk) != 1 or talk[0].main_event.v is None or talk[0].main_event.v.name != patch.retarget_next:
        raise ModError(f"{patch.file}: the courtyard entry no longer starts at {patch.retarget_next}")
    named = [flowchart.events[index].name for index in talk[0]._sub_flow_event_indices]
    if patch.retarget_next not in named:
        raise ModError(f"{patch.file}: {talk[0].name} subflow table is missing {patch.retarget_next}")
    for entry in flowchart.entry_points:
        if entry.name == patch.talk_entry:
            continue
        target = entry.main_event.v.name if entry.main_event.v is not None else ""
        if target in (ZELDA_GATE, ZELDA_LINE):
            raise ModError(f"{patch.file}: {entry.name} was moved to {target}")


def apply_saria_song(flow: EventFlow, patch: FlowPatch) -> HookNote:
    """After the look-at, show one system line on the epilogue and then fade the sparkle.

    Case 0, the flag true, copies Event19's talk and Event122's fade, then ends.
    Case 1 continues to Event155, the vanilla child query. ``SariaSong`` stays
    on Event125.
    """
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file} has no flowchart")
    if any(event.name in (SONG_GATE, SONG_LINE, SONG_FADE) for event in flowchart.events):
        raise ModError(
            f"{patch.file}: already patched. {SONG_GATE}, {SONG_LINE}, or {SONG_FADE} is already in the flow. "
            "Build from a vanilla event flow."
        )
    event26 = _one_named(flowchart, patch.from_event, patch.file)
    event155 = _one_named(flowchart, patch.rejoin_event, patch.file)
    event19 = _one_named(flowchart, patch.talk_source, patch.file)
    event122 = _one_named(flowchart, patch.fade_source, patch.file)
    nxt = getattr(event26.data, "nxt", None)
    previous = nxt.v if nxt is not None else None
    if previous is None or not previous.name:
        raise ModError(f"{patch.file}: {patch.from_event} nxt does not point at a named event")
    if previous.name != patch.rejoin_event:
        raise ModError(
            f"{patch.file}: {patch.from_event} next is {previous.name}, expected {patch.rejoin_event}."
        )
    _require_call(event19, TEXT_ACTOR, TALK_ACTION, patch.file)
    _require_call(event122, FADE_ACTOR, FADE_ACTION, patch.file)
    gamedata = _ensure_gamedata_actor(flowchart)
    query = _ensure_holder(gamedata.queries, "EventQueryIsGameDataBool")
    fade = Event()
    fade.name = SONG_FADE
    fade.data = ActionEvent()
    fade.data.actor = make_rindex(event122.data.actor.v)
    fade.data.actor_action = make_rindex(event122.data.actor_action.v)
    fade.data.params = _copied_container(event122.data.params, patch.file, patch.fade_source)
    fade.data.nxt = make_index(None)
    line = Event()
    line.name = SONG_LINE
    line.data = ActionEvent()
    line.data.actor = make_rindex(event19.data.actor.v)
    line.data.actor_action = make_rindex(event19.data.actor_action.v)
    line.data.params = _copied_message(event19.data.params, patch.message_id, patch.file)
    line.data.nxt = make_index(fade)
    gate = Event()
    gate.name = SONG_GATE
    gate.data = SwitchEvent()
    gate.data.actor = make_rindex(gamedata)
    gate.data.actor_query = make_rindex(query)
    gate.data.params = make_params((("GameDataName", "HavePlayedEvent.TitleEventBase"), ("Index", -1)))
    gate.data.cases = {0: make_rindex(line), 1: make_rindex(event155)}
    flowchart.events.extend((gate, line, fade))
    returned = retarget_next(flowchart, patch.from_event, gate)
    if returned != patch.rejoin_event:
        raise ModError(f"{patch.file}: retarget moved {patch.from_event} off {returned}, not {patch.rejoin_event}")
    return HookNote(
        file=patch.file,
        entry_points=tuple(entry.name for entry in flowchart.entry_points),
        hooked=SONG_ENTRY,
        explicit=True,
        original_event=patch.from_event,
        case_note=patch.rejoin_event,
        song_gate=True,
    )


def _one_named(flowchart, name: str, label: str) -> Event:
    matches = [event for event in flowchart.events if event.name == name]
    if len(matches) != 1:
        names = ", ".join(event.name for event in flowchart.events) or "none"
        raise ModError(f"{label}: expected one event named {name}, found {len(matches)}. Events: {names}.")
    return matches[0]


def _require_call(event: Event, actor_name: str, action_name: str, label: str) -> None:
    data = event.data
    if not isinstance(data, ActionEvent) or data.actor.v is None or data.actor_action.v is None:
        raise ModError(f"{label}: {event.name} is not an action")
    actor = data.actor.v.identifier.name
    action = str(data.actor_action.v)
    if actor != actor_name or action != action_name:
        raise ModError(f"{label}: {event.name} calls {actor}.{action}, expected {actor_name}.{action_name}")


def _copied_container(params: Container | None, label: str, source_name: str) -> Container:
    import copy

    if params is None or not getattr(params, "data", None):
        raise ModError(f"{label}: {source_name} has no parameters to copy")
    container = Container()
    container.data = copy.deepcopy(dict(params.data))
    return container


def _verify_saria_song(flow: EventFlow, patch: FlowPatch) -> None:
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file}: patched flow has no flowchart")
    by_name: dict[str, Event] = {}
    for event in flowchart.events:
        if event.name in by_name:
            raise ModError(f"{patch.file}: duplicate event name {event.name}")
        by_name[event.name] = event
    event26 = by_name.get(patch.from_event)
    gate = by_name.get(SONG_GATE)
    line = by_name.get(SONG_LINE)
    fade = by_name.get(SONG_FADE)
    event155 = by_name.get(patch.rejoin_event)
    event19 = by_name.get(patch.talk_source)
    event122 = by_name.get(patch.fade_source)
    if None in (event26, gate, line, fade, event155, event19, event122):
        raise ModError(f"{patch.file}: Saria's Song events did not survive the rewrite")
    assert event26 is not None and gate is not None and line is not None and fade is not None
    assert event155 is not None and event19 is not None and event122 is not None
    if event26.data.nxt.v is None or event26.data.nxt.v.name != SONG_GATE:
        raise ModError(f"{patch.file}: {patch.from_event} nxt is not {SONG_GATE}")
    if not isinstance(gate.data, SwitchEvent) or gate.data.actor.v is None:
        raise ModError(f"{patch.file}: {SONG_GATE} is not a query")
    if gate.data.actor.v.identifier.name != "GameDataActor":
        raise ModError(f"{patch.file}: {SONG_GATE} is not on GameDataActor")
    query = gate.data.actor_query.v
    query_name = query.v if query is not None and hasattr(query, "v") else query
    if query_name != "EventQueryIsGameDataBool":
        raise ModError(f"{patch.file}: {SONG_GATE} query is {query_name!r}")
    if gate.data.params is None or gate.data.params.data.get("GameDataName") != "HavePlayedEvent.TitleEventBase":
        raise ModError(f"{patch.file}: {SONG_GATE} does not read HavePlayedEvent.TitleEventBase")
    if gate.data.params.data.get("Index") != -1:
        raise ModError(f"{patch.file}: {SONG_GATE} Index is {gate.data.params.data.get('Index')!r}")
    cases = {case: link.v.name if link.v is not None else None for case, link in gate.data.cases.items()}
    if cases.get(0) != SONG_LINE or cases.get(1) != patch.rejoin_event or set(cases) != {0, 1}:
        raise ModError(f"{patch.file}: song cases are {cases}")
    _require_call(line, TEXT_ACTOR, TALK_ACTION, patch.file)
    if line.data.nxt.v is None or line.data.nxt.v.name != SONG_FADE:
        raise ModError(f"{patch.file}: {SONG_LINE} does not continue to {SONG_FADE}")
    if line.data.params is None or line.data.params.data.get("MessageId") != patch.message_id:
        got = None if line.data.params is None else line.data.params.data.get("MessageId")
        raise ModError(f"{patch.file}: {SONG_LINE} message is {got!r}")
    copied = dict(line.data.params.data)
    source = dict(event19.data.params.data)
    copied.pop("MessageId", None)
    source.pop("MessageId", None)
    if copied != source:
        raise ModError(f"{patch.file}: {SONG_LINE} params are not a copy of {patch.talk_source}")
    _require_call(fade, FADE_ACTOR, FADE_ACTION, patch.file)
    if fade.data.nxt.v is not None:
        raise ModError(f"{patch.file}: {SONG_FADE} does not end the flow")
    if fade.data.params is None or dict(fade.data.params.data) != dict(event122.data.params.data):
        raise ModError(f"{patch.file}: {SONG_FADE} params are not a copy of {patch.fade_source}")
    song = [entry for entry in flowchart.entry_points if entry.name == SONG_ENTRY]
    if len(song) != 1 or song[0].main_event.v is None or song[0].main_event.v.name != SONG_ENTRY_EVENT:
        got = None if not song or song[0].main_event.v is None else song[0].main_event.v.name
        raise ModError(f"{patch.file}: {SONG_ENTRY} starts at {got!r}, expected {SONG_ENTRY_EVENT}")
    for entry in flowchart.entry_points:
        target = entry.main_event.v.name if entry.main_event.v is not None else ""
        if target in (SONG_GATE, SONG_LINE, SONG_FADE):
            raise ModError(f"{patch.file}: {entry.name} was moved to {target}")
    descendants = {event.name for event in _reachable(event155)}
    if descendants.intersection((SONG_GATE, SONG_LINE, SONG_FADE)):
        raise ModError(f"{patch.file}: {patch.rejoin_event} now reaches the epilogue line")
    if not isinstance(event155.data, SwitchEvent):
        raise ModError(f"{patch.file}: {patch.rejoin_event} is no longer the vanilla query")


def _talk_params(message_id: str, *, anonymous: bool, maintain: bool = False) -> tuple[tuple[str, object], ...]:
    """EventTalk parameters. ``maintain`` adds Event5's MaintainPerformanceState.

    The other keys are the set already written for SystemTextNPC.EventTalk,
    including the spellings. Npc_EventStarter.EventTalk on Saria's Event5 adds
    MaintainPerformanceState 0.
    """
    pairs: list[tuple[str, object]] = [
        ("MessageId", message_id),
        ("IsCloseDialog", True),
        ("IsWaitEnd", True),
        ("EndDialogOption", 0),
        ("ChoiceNumber", 0),
        ("ChoiceLabel1", 0),
        ("ChoiceLabel2", 0),
        ("ChoiceLabel3", 0),
        ("ChoiceLabel4", 0),
        ("ChoiceDefaultNo", 0),
        ("ChoiceCancelNo", 0),
        ("IsNotBecomeSpeaker", anonymous),
        ("IsAnonymous", anonymous),
        ("ChangeMstxtForActor", False),
        ("IsAnimeDriven", False),
        ("IsPlayASAllSlot", False),
        ("NotPlayLipSyncAnim", False),
        ("IsOverrideStyle", False),
        ("OverrideStyle", 0),
        ("ASName", ""),
        ("IsNotOpenIfSkipedMostRecent", False),
    ]
    if maintain:
        pairs.append(("MaintainPerformanceState", 0))
    return tuple(pairs)


def _actor_for_step(flowchart, gamedata: Actor, step: FlowStep) -> Actor:
    """GameDataActor is the gate's actor. Any other step names the actor the YAML declares."""
    spec = CALLS[step.call]
    if step.actor == _GAMEDATA:
        holders = gamedata.queries if spec["kind"] == "query" else gamedata.actions
        _ensure_holder(holders, step.call)
        return gamedata
    actions = [step.call] if spec["kind"] == "action" else []
    queries = [step.call] if spec["kind"] == "query" else []
    return _ensure_named_actor(flowchart, step.actor, actions, queries)


def _retarget_bool_exp_skips(patch: FlowPatch, steps, created: list[Event]) -> None:
    """A BoolExp guard's other case skips the next action and joins whatever that action joins.

    The TitleEventBase query still rejoins the original boot, or Navi's re-ban. This one
    sits in the middle of the first-load chain, so the already-true case must not skip
    the era switch.
    """
    for index, step in enumerate(steps):
        if step.call != "EventQueryIsGameDataBoolExp":
            continue
        if index + 1 >= len(created):
            raise ModError(f"{patch.file}: {step.name} is a query with nothing after it")
        action = created[index + 1]
        if not isinstance(action.data, ActionEvent) or action.data.nxt is None or action.data.nxt.v is None:
            raise ModError(f"{patch.file}: {step.name} does not skip to the event after {steps[index + 1].name}")
        data = created[index].data
        if not isinstance(data, SwitchEvent):
            raise ModError(f"{patch.file}: {step.name} was not built as a switch")
        continuing = continuing_cases(patch, step)
        for case in CALLS[step.call]["cases"]:
            if case not in continuing:
                data.cases[case] = make_rindex(action.data.nxt.v)


def _bool_exp_rejoin(patch: FlowPatch, steps, index: int, original_name: str) -> str:
    """The event a BoolExp's non-continue case should name, matching :func:`_retarget_bool_exp_skips`."""
    if index + 2 < len(steps):
        return steps[index + 2].name
    if epilogue_runs(patch):
        return ORIG_CALL
    return original_name


def _ensure_named_actor(
    flowchart,
    name: str,
    actions: list[str],
    queries: list[str] | None = None,
    params: tuple | None = None,
) -> Actor:
    found = [
        actor
        for actor in flowchart.actors
        if actor.identifier.name == name and actor.identifier.sub_name == ""
    ]
    if len(found) > 1:
        raise ModError(f"flowchart has more than one {name}")
    if found:
        actor = found[0]
        for action in actions:
            _ensure_holder(actor.actions, action)
        for query in queries or []:
            _ensure_holder(actor.queries, query)
        return actor
    actor = Actor()
    actor.identifier = ActorIdentifier(name)
    actor.concurrent_clips = 1
    chosen = params
    if chosen is None and name == STARTER_ACTOR:
        # A talk line can declare the starter before the recent-talk query. Either
        # creation binds the NPC. An actor already in the file is returned above.
        chosen = STARTER_ACTOR_PARAMS
    if chosen is None:
        chosen = GAMEDATA_ACTOR_PARAMS
    actor.params = make_params(chosen)
    for action in actions:
        actor.actions.append(StringHolder(action))
    for query in queries or []:
        actor.queries.append(StringHolder(query))
    flowchart.actors.append(actor)
    return actor


def _ensure_holder(holders: list, name: str) -> StringHolder:
    for holder in holders:
        if holder.v == name:
            return holder
    holder = StringHolder(name)
    holders.append(holder)
    return holder


def _write(flow: EventFlow, label: str) -> bytes:
    buffer = io.BytesIO()
    try:
        ok = flow.write(buffer)
    except (AssertionError, ValueError, KeyError, IndexError, struct.error) as exc:
        raise ModError(f"{label}: evfl could not write the flow ({exc})") from None
    if not ok:
        raise ModError(f"{label}: evfl refused to write this flow")
    return buffer.getvalue()


def _verify(flow: EventFlow, patch: FlowPatch, note: HookNote, before: dict[str, str | None], steps) -> None:
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file}: patched flow has no flowchart")
    by_name: dict[str, Event] = {}
    for event in flowchart.events:
        if event.name in by_name:
            raise ModError(f"{patch.file}: duplicate event name {event.name}")
        by_name[event.name] = event
    entries = {entry.name: entry for entry in flowchart.entry_points}
    hooked = entries.get(note.hooked)
    if hooked is None or hooked.main_event.v is None or hooked.main_event.v.name != steps[0].name:
        raise ModError(f"{patch.file}: {note.hooked} does not start at {steps[0].name} after the rewrite")
    for name, target in before.items():
        if name == note.hooked:
            continue
        event = entries[name].main_event.v
        got = event.name if event is not None else None
        if got != target:
            raise ModError(f"{patch.file}: entry point {name} moved from {target} to {got}")
    for index, step in enumerate(steps):
        event = by_name.get(step.name)
        if event is None:
            raise ModError(f"{patch.file}: patched flow is missing {step.name}")
        spec = CALLS[step.call]
        if spec["kind"] == "query" and patch.debug_cases and step.call in (
            "EventQueryIsGameDataInt",
            "EventQueryIsGameDataBool",
        ):
            join = steps[index + 1].name
            amounts = GANON_CASE_RUPEES if step.call == "EventQueryIsGameDataInt" else BOOL_CASE_RUPEES
            name_for = ganon_probe_name if step.call == "EventQueryIsGameDataInt" else bool_probe_name
            _verify_debug_query(patch.file, step, event, by_name, join, amounts, name_for)
        elif spec["kind"] == "query":
            rejoin = note.original_event
            if epilogue_runs(patch) and step.call == "EventQueryIsGameDataBool":
                rejoin = REBAN
            if step.call == "EventQueryIsGameDataBoolExp":
                if index + 1 >= len(steps):
                    raise ModError(f"{patch.file}: {step.name} is a query with nothing after it")
                follower_name = steps[index + 1].name
                rejoin = _bool_exp_rejoin(patch, steps, index, note.original_event)
            elif index + 1 < len(steps):
                follower_name = steps[index + 1].name
            elif patch.epilogue_talk:
                follower_name = RECENT_EVENT
            else:
                raise ModError(f"{patch.file}: {step.name} is a query with nothing after it")
            _verify_query(
                patch.file,
                step,
                event,
                follower_name,
                rejoin,
                continuing_cases(patch, step),
            )
        else:
            if index + 1 < len(steps):
                nxt = steps[index + 1].name
            elif epilogue_runs(patch):
                nxt = ORIG_CALL
            else:
                nxt = note.original_event
            _verify_action(patch.file, step, event, nxt)
    if epilogue_runs(patch):
        _verify_epilogue(flow, patch, note, by_name, entries)
        _verify_reban(flow, patch, note, by_name)
        if patch.custom_wake:
            _verify_wake(flow, patch, by_name, entries)
    if patch.epilogue_talk:
        _verify_saria_talk(flow, patch, by_name, entries)


def _verify_query(label: str, step, event: Event, follower: str, original: str, continuing: set[int]) -> None:
    data = event.data
    if not isinstance(data, SwitchEvent):
        raise ModError(f"{label}: {step.name} was not written as a switch")
    _verify_actor(label, step.name, data.actor.v, step.actor)
    if str(data.actor_query.v) != step.call:
        raise ModError(f"{label}: {step.name} calls {data.actor_query.v}, not {step.call}")
    _verify_params(label, step, data.params)
    expected = {case: follower if case in continuing else original for case in CALLS[step.call]["cases"]}
    got = {case: (link.v.name if link.v is not None else None) for case, link in data.cases.items()}
    if got != expected:
        raise ModError(f"{label}: {step.name} cases are {got}, expected {expected}")


def _verify_debug_query(label: str, step, event: Event, by_name: dict, join: str, amounts: dict, name_for) -> None:
    data = event.data
    if not isinstance(data, SwitchEvent):
        raise ModError(f"{label}: {step.name} was not written as a switch")
    _verify_actor(label, step.name, data.actor.v)
    if str(data.actor_query.v) != step.call:
        raise ModError(f"{label}: {step.name} calls {data.actor_query.v}, not {step.call}")
    _verify_params(label, step, data.params)
    expected = {case: name_for(case) for case in CALLS[step.call]["cases"]}
    got = {case: (link.v.name if link.v is not None else None) for case, link in data.cases.items()}
    if got != expected:
        raise ModError(f"{label}: {step.name} cases are {got}, expected {expected}")
    for case, probe_name in expected.items():
        probe = by_name.get(probe_name)
        if probe is None:
            raise ModError(f"{label}: missing {probe_name}")
        probe_step = FlowStep(
            name=probe_name,
            call="EventTriggerAddOrSubGameDataInt",
            params=(
                ("GameDataName", DEBUG_RUPEE_FLAG),
                ("Index", -1),
                ("Value", amounts[case]),
            ),
            continue_case=None,
        )
        _verify_action(label, probe_step, probe, join)


def _verify_action(label: str, step, event: Event, original: str) -> None:
    data = event.data
    if not isinstance(data, ActionEvent):
        raise ModError(f"{label}: {step.name} was not written as an action")
    _verify_actor(label, step.name, data.actor.v, step.actor)
    if str(data.actor_action.v) != step.call:
        raise ModError(f"{label}: {step.name} calls {data.actor_action.v}, not {step.call}")
    _verify_params(label, step, data.params)
    if step.actor != _GAMEDATA:
        got = data.actor.v.params.data if data.actor.v is not None and data.actor.v.params is not None else {}
        for key, value in GAMEDATA_ACTOR_PARAMS:
            if got.get(key) != value:
                raise ModError(f"{label}: {step.actor} {key} is {got.get(key)!r}")
    nxt = data.nxt.v.name if data.nxt.v is not None else None
    if nxt != original:
        raise ModError(f"{label}: {step.name} goes to {nxt}, expected {original}")


def _verify_epilogue(flow: EventFlow, patch: FlowPatch, note: HookNote, by_name: dict, entries: dict) -> None:
    call = by_name.get(ORIG_CALL)
    if call is None or not isinstance(call.data, SubFlowEvent):
        raise ModError(f"{patch.file}: missing the self-subflow {ORIG_CALL}")
    if call.data.res_flowchart_name != "":
        raise ModError(
            f"{patch.file}: {ORIG_CALL} flowchart name is {call.data.res_flowchart_name!r}; "
            "a same-file call is an empty name"
        )
    if call.data.entry_point_name != patch.orig_entry:
        raise ModError(f"{patch.file}: {ORIG_CALL} enters {call.data.entry_point_name}, not {patch.orig_entry}")
    if call.data.params is not None and call.data.params.data:
        raise ModError(f"{patch.file}: {ORIG_CALL} should have no parameters")
    nxt = call.data.nxt.v.name if call.data.nxt.v is not None else None
    expect_first = patch.chain[0].name if patch.custom_wake else FALLBACK_BAN
    if nxt != expect_first:
        raise ModError(f"{patch.file}: {ORIG_CALL} goes to {nxt}, expected {expect_first}")
    if not patch.custom_wake:
        ban = by_name.get(FALLBACK_BAN)
        if ban is None or not isinstance(ban.data, ActionEvent):
            raise ModError(f"{patch.file}: missing {FALLBACK_BAN}")
        ban_step = FlowStep(
            name=FALLBACK_BAN,
            call="EventTriggerSetGameDataBool",
            params=(("GameDataName", "Navi_Ban"), ("Index", -1), ("Value", True)),
            continue_case=None,
        )
        _verify_action(patch.file, ban_step, ban, patch.chain[0].name)
    entry = entries.get(patch.orig_entry)
    if entry is None or entry.main_event.v is None or entry.main_event.v.name != note.original_event:
        got = None if entry is None or entry.main_event.v is None else entry.main_event.v.name
        raise ModError(f"{patch.file}: {patch.orig_entry} starts at {got}, expected {note.original_event}")
    for index, step in enumerate(patch.chain):
        event = by_name.get(step.name)
        if event is None:
            raise ModError(f"{patch.file}: patched flow is missing {step.name}")
        follower = patch.chain[index + 1].name if index + 1 < len(patch.chain) else None
        if step.subflow is not None:
            _verify_subflow(patch.file, step, event, follower)
        else:
            replace = None
            if step.call == "EventReserveBancChange":
                replace = {"BootEventName": effective_boot_name(patch)}
            _verify_chain_action(patch.file, step, event, follower, replace)
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file}: patched flow has no flowchart")
    hooked = entries[note.hooked]
    named = []
    for index in hooked._sub_flow_event_indices:
        if index < 0 or index >= len(flowchart.events):
            raise ModError(f"{patch.file}: {note.hooked} has a subflow index {index} past the event list")
        named.append(flowchart.events[index].name)
    required = [ORIG_CALL, *[step.name for step in patch.chain if step.subflow is not None]]
    missing = [name for name in required if name not in named]
    if missing:
        raise ModError(f"{patch.file}: {note.hooked} subflow table is missing {', '.join(missing)}")


def _verify_subflow(label: str, step: FlowStep, event: Event, follower: str | None) -> None:
    data = event.data
    if not isinstance(data, SubFlowEvent):
        raise ModError(f"{label}: {step.name} was not written as a subflow")
    if data.res_flowchart_name != step.subflow or data.entry_point_name != step.entry_point:
        raise ModError(
            f"{label}: {step.name} calls {data.res_flowchart_name}::{data.entry_point_name}, "
            f"expected {step.subflow}::{step.entry_point}"
        )
    if data.params is not None and data.params.data:
        raise ModError(f"{label}: {step.name} should have no parameters")
    nxt = data.nxt.v.name if data.nxt.v is not None else None
    if nxt != follower:
        raise ModError(f"{label}: {step.name} goes to {nxt}, expected {follower}")


def _verify_chain_action(
    label: str, step: FlowStep, event: Event, follower: str | None, replace: dict | None = None
) -> None:
    data = event.data
    if not isinstance(data, ActionEvent):
        raise ModError(f"{label}: {step.name} was not written as an action")
    actor = data.actor.v
    if actor is None or actor.identifier.name != step.actor or actor.identifier.sub_name != "":
        got = None if actor is None else actor.identifier.name
        raise ModError(f"{label}: {step.name} is on {got}, expected {step.actor}")
    if str(data.actor_action.v) != step.call:
        raise ModError(f"{label}: {step.name} calls {data.actor_action.v}, not {step.call}")
    if not step.params:
        if data.params is not None and data.params.data:
            raise ModError(f"{label}: {step.name} should have no parameters")
    else:
        _verify_params(label, step, data.params, replace)
    nxt = data.nxt.v.name if data.nxt.v is not None else None
    if nxt != follower:
        raise ModError(f"{label}: {step.name} goes to {nxt}, expected {follower}")


def _require(by_name: dict, label: str, name: str) -> Event:
    event = by_name.get(name)
    if event is None:
        raise ModError(f"{label}: missing {name}")
    return event


def _verify_refresh(label: str, by_name: dict, prefix: str, terminal: str | None, delete: bool) -> str:
    """Return the first event of the recreate block. Its last event continues to ``terminal``."""
    last = f"{prefix}WaitEquipment"
    if delete:
        first = f"{prefix}DeleteEquipment"
        if f"{prefix}SyncEquipment" in by_name or f"{prefix}WaitEquipmentFrame" in by_name:
            raise ModError(f"{label}: the delete fallback still syncs equipment")
        _verify_chain_action(
            label,
            FlowStep(name=first, call=DELETE_EQUIPMENT_ACTION, params=(), continue_case=None, actor=UI_SYSTEM),
            _require(by_name, label, first),
            last,
        )
    else:
        first = f"{prefix}WaitEquipmentFrame"
        sync = f"{prefix}SyncEquipment"
        if f"{prefix}DeleteEquipment" in by_name:
            raise ModError(f"{label}: the sync block deletes equipment")
        frame = _require(by_name, label, first)
        _verify_chain_action(
            label,
            FlowStep(
                name=first,
                call=WAIT_FRAME_ACTION,
                params=(("IsWaitEnd", True), ("Frames", 1)),
                continue_case=None,
                actor=GAME_SYSTEM,
            ),
            frame,
            sync,
        )
        if isinstance(frame.data.params.data["Frames"], bool):
            raise ModError(f"{label}: {first} Frames is a bool, expected the int 1")
        _verify_chain_action(
            label,
            FlowStep(name=sync, call=SYNC_EQUIPMENT_ACTION, params=(), continue_case=None, actor=UI_SYSTEM),
            _require(by_name, label, sync),
            last,
        )
    _verify_chain_action(
        label,
        FlowStep(
            name=last,
            call=WAIT_EQUIPMENT_ACTION,
            params=(("IsWaitEnd", True),),
            continue_case=None,
            actor=PLAYER_EVENT,
        ),
        _require(by_name, label, last),
        terminal,
    )
    return first


def _verify_ocarina_swap(
    label: str, by_name: dict, prefix: str, terminal: str | None, patch: FlowPatch, *, autosave_after_swap: bool
) -> None:
    """The removal runs only while Time is owned. Equip always runs. Songs are untouched."""

    def named(leaf: str) -> Event:
        return _require(by_name, label, f"{prefix}{leaf}")

    check = named("CheckOcarinaTime")
    if not isinstance(check.data, SwitchEvent):
        raise ModError(f"{label}: {check.name} is not a query")
    if check.data.actor.v is None or check.data.actor.v.identifier.name != "GameDataActor":
        raise ModError(f"{label}: {check.name} is not on GameDataActor")
    if str(check.data.actor_query.v) != "EventQueryIsGameDataBool":
        raise ModError(f"{label}: {check.name} is not a bool query")
    params = check.data.params.data if check.data.params is not None else {}
    if params.get("GameDataName") != ISGET_TIME or params.get("Index") != -1:
        raise ModError(f"{label}: {check.name} does not read {ISGET_TIME}")
    if set(check.data.cases) != {0, 1}:
        raise ModError(f"{label}: {check.name} cases are {set(check.data.cases)}")
    if check.data.cases[0].v is None or check.data.cases[0].v.name != f"{prefix}RemoveOcarinaTime":
        raise ModError(f"{label}: owning Time does not remove it")
    without = f"{prefix}EquipOcarinaIdle" if autosave_after_swap else f"{prefix}EquipOcarinaFairy"
    if check.data.cases[1].v is None or check.data.cases[1].v.name != without:
        raise ModError(f"{label}: a save without Time does not go straight to the equip")

    remove = named("RemoveOcarinaTime")
    _verify_chain_action(
        label,
        FlowStep(
            name=remove.name,
            call=POUCH_ACTION,
            params=_pouch_params(OCARINA_TIME, -1),
            continue_case=None,
            actor=POUCH_ACTOR,
        ),
        remove,
        f"{prefix}ClearOcarinaGet",
    )

    clear_get = named("ClearOcarinaGet")
    _verify_action(
        label,
        FlowStep(
            name=clear_get.name,
            call="EventTriggerSetGameDataBool",
            params=(("GameDataName", ISGET_TIME), ("Index", -1), ("Value", False)),
            continue_case=None,
        ),
        clear_get,
        f"{prefix}ClearOcarinaStock",
    )
    if not isinstance(clear_get.data.params.data["Value"], bool):
        raise ModError(f"{label}: {clear_get.name} Value is not a bool")

    clear_stock = named("ClearOcarinaStock")
    _verify_action(
        label,
        FlowStep(
            name=clear_stock.name,
            call=SET_INT_ACTION,
            params=(("GameDataName", STOCK_TIME), ("Index", -1), ("Value", 0)),
            continue_case=None,
        ),
        clear_stock,
        f"{prefix}CheckOcarinaFairy",
    )
    if isinstance(clear_stock.data.params.data["Value"], bool):
        raise ModError(f"{label}: {clear_stock.name} Value is a bool, expected the int 0")

    fairy = named("CheckOcarinaFairy")
    if not isinstance(fairy.data, SwitchEvent) or str(fairy.data.actor_query.v) != "EventQueryIsGameDataBool":
        raise ModError(f"{label}: {fairy.name} is not a bool query")
    fairy_params = fairy.data.params.data if fairy.data.params is not None else {}
    if fairy_params.get("GameDataName") != ISGET_FAIRY:
        raise ModError(f"{label}: {fairy.name} does not read {ISGET_FAIRY}")
    if fairy.data.cases[0].v is None or fairy.data.cases[0].v.name != f"{prefix}EquipOcarinaFairy":
        raise ModError(f"{label}: an owned Fairy Ocarina is given again")
    if fairy.data.cases[1].v is None or fairy.data.cases[1].v.name != f"{prefix}GiveOcarinaFairy":
        raise ModError(f"{label}: a missing Fairy Ocarina is not given")

    give = named("GiveOcarinaFairy")
    _verify_chain_action(
        label,
        FlowStep(
            name=give.name,
            call=POUCH_ACTION,
            params=_pouch_params(OCARINA_FAIRY, 1),
            continue_case=None,
            actor=POUCH_ACTOR,
        ),
        give,
        f"{prefix}EquipOcarinaFairy",
    )

    equip = named("EquipOcarinaFairy")
    refresh_terminal = f"{prefix}Autosave" if autosave_after_swap else terminal
    first = _verify_refresh(label, by_name, prefix, refresh_terminal, patch.ocarina_delete_equipment)
    _verify_action(
        label,
        FlowStep(
            name=equip.name,
            call=SET_STRING_ACTION,
            params=(("GameDataName", EQUIP_OCARINA), ("Value", OCARINA_FAIRY), ("Index", -1)),
            continue_case=None,
        ),
        equip,
        first,
    )
    if autosave_after_swap:
        idle = _require(by_name, label, f"{prefix}EquipOcarinaIdle")
        idle_first = _verify_refresh(label, by_name, f"{prefix}Idle", terminal, patch.ocarina_delete_equipment)
        _verify_action(
            label,
            FlowStep(
                name=idle.name,
                call=SET_STRING_ACTION,
                params=(("GameDataName", EQUIP_OCARINA), ("Value", OCARINA_FAIRY), ("Index", -1)),
                continue_case=None,
            ),
            idle,
            idle_first,
        )
        saved = _require(by_name, label, f"{prefix}Autosave")
        _verify_chain_action(
            label,
            FlowStep(name=saved.name, call=AUTOSAVE_ACTION, params=(), continue_case=None, actor=GAME_SYSTEM),
            saved,
            terminal,
        )
    for leaf in _OCARINA_LEAVES:
        event = named(leaf)
        blob = ""
        if getattr(event.data, "params", None) is not None and event.data.params.data:
            blob = " ".join(str(value) for value in event.data.params.data.values())
        if "HavePlayedGetEvent" in blob or "Item_Melody" in blob:
            raise ModError(f"{label}: {event.name} touches a song or a get-event flag")


def _verify_stones(label: str, by_name: dict, prefix: str, clean_done: str, held_done: str | None) -> None:
    """Each owned pouch item is removed. A missing one is skipped. Songs and get-events stay."""
    split = held_done is not None

    def check_name(stem: str, held: bool) -> str:
        return f"{prefix}Check{stem}" + ("Held" if held else "")

    def onward_name(index: int, held: bool) -> str:
        if index >= len(POUCH_REMOVALS):
            if held and split:
                return held_done
            return clean_done
        stem = POUCH_REMOVALS[index][0]
        return check_name(stem, held and split)

    for index, (stem, item) in enumerate(POUCH_REMOVALS):
        lanes = (False, True) if split and index else (False,)
        for held in lanes:
            event = _require(by_name, label, check_name(stem, held))
            if not isinstance(event.data, SwitchEvent):
                raise ModError(f"{label}: {event.name} is not a query")
            params = event.data.params.data if event.data.params is not None else {}
            if params.get("GameDataName") != f"IsGet.{item}" or params.get("Index") != -1:
                raise ModError(f"{label}: {event.name} does not read IsGet.{item}")
            if set(event.data.cases) != {0, 1}:
                raise ModError(f"{label}: {event.name} cases are {set(event.data.cases)}")
            if event.data.cases[0].v is None or event.data.cases[0].v.name != f"{prefix}Remove{stem}":
                raise ModError(f"{label}: owning {item} does not remove it")
            if event.data.cases[1].v is None or event.data.cases[1].v.name != onward_name(index + 1, held):
                got = None if event.data.cases[1].v is None else event.data.cases[1].v.name
                raise ModError(f"{label}: {event.name} false goes to {got}, expected {onward_name(index + 1, held)}")
        remove = _require(by_name, label, f"{prefix}Remove{stem}")
        _verify_chain_action(
            label,
            FlowStep(
                name=remove.name,
                call=POUCH_ACTION,
                params=_pouch_params(item, -1),
                continue_case=None,
                actor=POUCH_ACTOR,
            ),
            remove,
            f"{prefix}Clear{stem}Get",
        )
        clear_get = _require(by_name, label, f"{prefix}Clear{stem}Get")
        _verify_action(
            label,
            FlowStep(
                name=clear_get.name,
                call="EventTriggerSetGameDataBool",
                params=(("GameDataName", f"IsGet.{item}"), ("Index", -1), ("Value", False)),
                continue_case=None,
            ),
            clear_get,
            f"{prefix}Clear{stem}Stock",
        )
        if not isinstance(clear_get.data.params.data["Value"], bool):
            raise ModError(f"{label}: {clear_get.name} Value is not a bool")
        clear_stock = _require(by_name, label, f"{prefix}Clear{stem}Stock")
        _verify_action(
            label,
            FlowStep(
                name=clear_stock.name,
                call=SET_INT_ACTION,
                params=(("GameDataName", f"PouchData.{item}.StockNum"), ("Index", -1), ("Value", 0)),
                continue_case=None,
            ),
            clear_stock,
            onward_name(index + 1, split),
        )
        if isinstance(clear_stock.data.params.data["Value"], bool):
            raise ModError(f"{label}: {clear_stock.name} Value is a bool, expected the int 0")
        for event in (remove, clear_get, clear_stock):
            blob = " ".join(str(value) for value in event.data.params.data.values())
            if "HavePlayedGetEvent" in blob or "Item_Melody" in blob:
                raise ModError(f"{label}: {event.name} touches a song or a get-event flag")


def _verify_reban(flow: EventFlow, patch: FlowPatch, note: HookNote, by_name: dict) -> None:
    event = by_name.get(REBAN)
    if event is None:
        raise ModError(f"{patch.file}: missing {REBAN}")
    step = FlowStep(
        name=REBAN,
        call="EventTriggerSetGameDataBool",
        params=(("GameDataName", "Navi_Ban"), ("Index", -1), ("Value", True)),
        continue_case=None,
    )
    _verify_action(patch.file, step, event, f"{LOAD_OCARINA_PREFIX}CheckStoneForest")
    _verify_stones(
        patch.file,
        by_name,
        LOAD_OCARINA_PREFIX,
        f"{LOAD_OCARINA_PREFIX}CheckOcarinaTime",
        f"{LOAD_OCARINA_PREFIX}CheckOcarinaTimeHeld",
    )
    _verify_ocarina_swap(
        patch.file, by_name, LOAD_OCARINA_PREFIX, note.original_event, patch, autosave_after_swap=True
    )
    held = _require(by_name, patch.file, f"{LOAD_OCARINA_PREFIX}CheckOcarinaTimeHeld")
    if not isinstance(held.data, SwitchEvent):
        raise ModError(f"{patch.file}: {held.name} is not a query")
    if held.data.cases[0].v is None or held.data.cases[0].v.name != f"{LOAD_OCARINA_PREFIX}RemoveOcarinaTime":
        raise ModError(f"{patch.file}: a pouch removal that still owns Time does not share the ocarina removal")
    if held.data.cases[1].v is None or held.data.cases[1].v.name != f"{LOAD_OCARINA_PREFIX}EquipOcarinaIdleHeld":
        raise ModError(f"{patch.file}: a pouch removal that already lost Time does not equip Fairy")
    _verify_refresh(
        patch.file, by_name, f"{LOAD_OCARINA_PREFIX}IdleHeld", f"{LOAD_OCARINA_PREFIX}Autosave", patch.ocarina_delete_equipment
    )
    idle_held = _require(by_name, patch.file, f"{LOAD_OCARINA_PREFIX}EquipOcarinaIdleHeld")
    _verify_action(
        patch.file,
        FlowStep(
            name=idle_held.name,
            call=SET_STRING_ACTION,
            params=(("GameDataName", EQUIP_OCARINA), ("Value", OCARINA_FAIRY), ("Index", -1)),
            continue_case=None,
        ),
        idle_held,
        f"{LOAD_OCARINA_PREFIX}IdleHeld" + ("DeleteEquipment" if patch.ocarina_delete_equipment else "WaitEquipmentFrame"),
    )
    del flow


def _verify_wake(flow: EventFlow, patch: FlowPatch, by_name: dict, entries: dict) -> None:
    from after_ocarina.wake import AIR_ENTRY, WAKE_BAN, WAKE_ENTRY

    entry = entries.get(WAKE_ENTRY)
    if entry is None or entry.main_event.v is None or entry.main_event.v.name != WAKE_BAN:
        got = None if entry is None or entry.main_event.v is None else entry.main_event.v.name
        raise ModError(f"{patch.file}: {WAKE_ENTRY} starts at {got}, expected {WAKE_BAN}")
    if AIR_ENTRY not in entries or entries[AIR_ENTRY].main_event.v is None:
        raise ModError(f"{patch.file}: missing entry point {AIR_ENTRY}")
    ban = by_name.get(WAKE_BAN)
    step = FlowStep(
        name=WAKE_BAN,
        call="EventTriggerSetGameDataBool",
        params=(("GameDataName", "Navi_Ban"), ("Index", -1), ("Value", True)),
        continue_case=None,
    )
    _verify_action(patch.file, step, ban, "AtO_FadeOut")
    order = ["AtO_FadeOut", "AtO_Camera", "AtO_ReleaseCamera", "AtO_Air", "AtO_Fork"]
    event = by_name["AtO_FadeOut"]
    for name in order:
        if event is None or event.name != name:
            got = None if event is None else event.name
            raise ModError(f"{patch.file}: wake order broke at {got}, expected {name}")
        event = event.data.nxt.v if hasattr(event.data, "nxt") else None
    fork = by_name.get("AtO_Fork")
    if fork is None or not isinstance(fork.data, ForkEvent):
        raise ModError(f"{patch.file}: AtO_Fork is missing")
    forks = [link.v.name if link.v is not None else None for link in fork.data.forks]
    if forks != ["AtO_Stretch", "AtO_WaitFade"]:
        raise ModError(f"{patch.file}: AtO_Fork branches are {forks}")
    if fork.data.join.v is None or fork.data.join.v.name != "AtO_Join":
        raise ModError(f"{patch.file}: AtO_Fork does not join AtO_Join")
    control = by_name["AtO_ControlCamera"]
    if not isinstance(control.data, ActionEvent):
        raise ModError(f"{patch.file}: AtO_ControlCamera is not an action")
    join = by_name["AtO_Join"]
    if not isinstance(join.data, JoinEvent) or join.data.nxt.v is None:
        raise ModError(f"{patch.file}: AtO_Join does not continue")
    if patch.wake_dialogue:
        if join.data.nxt.v.name != WAKE_LINE:
            raise ModError(f"{patch.file}: AtO_Join goes to {join.data.nxt.v.name}, expected {WAKE_LINE}")
        spoken = by_name[WAKE_LINE]
        if not isinstance(spoken.data, ActionEvent) or spoken.data.nxt.v is None or spoken.data.nxt.v.name != "AtO_ControlCamera":
            raise ModError(f"{patch.file}: {WAKE_LINE} does not return the camera")
    elif join.data.nxt.v.name != "AtO_ControlCamera":
        raise ModError(f"{patch.file}: AtO_Join goes to {join.data.nxt.v.name}, expected AtO_ControlCamera")
    terminal = KEEP_CAMERA_EVENT if patch.autosave else None
    nxt = control.data.nxt.v
    expected_check = f"{WAKE_OCARINA_PREFIX}CheckStoneForest"
    if nxt is None or nxt.name != expected_check:
        got = None if nxt is None else nxt.name
        raise ModError(f"{patch.file}: AtO_ControlCamera goes to {got}, expected {expected_check}")
    _verify_stones(patch.file, by_name, WAKE_OCARINA_PREFIX, f"{WAKE_OCARINA_PREFIX}CheckOcarinaTime", None)
    _verify_ocarina_swap(
        patch.file, by_name, WAKE_OCARINA_PREFIX, terminal, patch, autosave_after_swap=False
    )
    expect: list[str] = []
    if patch.autosave:
        expect.append(KEEP_CAMERA_EVENT)
        expect.append(AUTOSAVE_EVENT)
    cursor = by_name[f"{WAKE_OCARINA_PREFIX}WaitEquipment"]
    for name in expect:
        nxt = cursor.data.nxt.v
        if nxt is None or nxt.name != name:
            got = None if nxt is None else nxt.name
            raise ModError(f"{patch.file}: {cursor.name} goes to {got}, expected {name}")
        cursor = nxt
    if cursor.data.nxt.v is not None:
        raise ModError(f"{patch.file}: the wake does not end after {cursor.name}")
    if patch.wake_dialogue:
        _verify_talk(
            patch.file,
            by_name[WAKE_LINE],
            WAKE_MESSAGE_ID,
            anonymous=True,
            ends=False,
        )
    if patch.autosave:
        saved = by_name[AUTOSAVE_EVENT]
        if not isinstance(saved.data, ActionEvent) or str(saved.data.actor_action.v) != AUTOSAVE_ACTION:
            raise ModError(f"{patch.file}: {AUTOSAVE_EVENT} is not {AUTOSAVE_ACTION}")
        if saved.data.actor.v is None or saved.data.actor.v.identifier.name != "GameSystemActor":
            raise ModError(f"{patch.file}: {AUTOSAVE_EVENT} is not on GameSystemActor")
        if saved.data.params is not None and saved.data.params.data:
            raise ModError(f"{patch.file}: {AUTOSAVE_EVENT} should have no parameters")
        kept = by_name[KEEP_CAMERA_EVENT]
        if not isinstance(kept.data, ActionEvent) or str(kept.data.actor_action.v) != KEEP_CAMERA_ACTION:
            raise ModError(f"{patch.file}: {KEEP_CAMERA_EVENT} is not {KEEP_CAMERA_ACTION}")
        if kept.data.actor.v is None or kept.data.actor.v.identifier.name != "EventCamera":
            raise ModError(f"{patch.file}: {KEEP_CAMERA_EVENT} is not on EventCamera")
        got = {} if kept.data.params is None else kept.data.params.data
        for key, value in KEEP_CAMERA_PARAMS:
            if got.get(key) != value or type(got.get(key)) is not type(value):
                raise ModError(f"{patch.file}: {KEEP_CAMERA_EVENT} {key} is {got.get(key)!r}")
        if saved.data.nxt.v is not None:
            raise ModError(f"{patch.file}: {AUTOSAVE_EVENT} is not the last wake event")
    air = by_name.get("AtO_Air")
    if air is None or not isinstance(air.data, SubFlowEvent) or air.data.entry_point_name != AIR_ENTRY:
        called = None if air is None or not isinstance(air.data, SubFlowEvent) else air.data.entry_point_name
        raise ModError(f"{patch.file}: AtO_Air enters {called}, expected {AIR_ENTRY}")
    for event in _reachable(entries[WAKE_ENTRY].main_event.v):
        data = event.data
        if isinstance(data, SubFlowEvent):
            text = f"{data.res_flowchart_name} {data.entry_point_name}"
            if "Navi" in text or "Dream" in text:
                raise ModError(f"{patch.file}: the wake still calls {event.name} ({text})")
        if "Dream" in event.name:
            raise ModError(f"{patch.file}: the wake still has {event.name}")
    hooked = entries[WAKE_ENTRY]
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file}: patched flow has no flowchart")
    named = []
    for index in hooked._sub_flow_event_indices:
        if index < 0 or index >= len(flowchart.events):
            raise ModError(f"{patch.file}: {WAKE_ENTRY} has a subflow index {index} past the event list")
        named.append(flowchart.events[index].name)
    if "AtO_Air" not in named:
        raise ModError(f"{patch.file}: {WAKE_ENTRY} subflow table is missing AtO_Air")


def _reachable(start: Event | None):
    seen: set[int] = set()
    stack = [start]
    while stack:
        event = stack.pop()
        if event is None or id(event) in seen:
            continue
        seen.add(id(event))
        yield event
        data = event.data
        if isinstance(data, (ActionEvent, SubFlowEvent, JoinEvent)):
            stack.append(data.nxt.v)
        elif isinstance(data, ForkEvent):
            stack.append(data.join.v)
            stack.extend(link.v for link in data.forks)
        elif isinstance(data, SwitchEvent):
            stack.extend(link.v for link in data.cases.values())


def _verify_saria_talk(flow: EventFlow, patch: FlowPatch, by_name: dict, entries: dict) -> None:
    recent = by_name.get(RECENT_EVENT)
    if recent is None or not isinstance(recent.data, SwitchEvent):
        raise ModError(f"{patch.file}: missing {RECENT_EVENT}")
    if recent.data.actor.v is None or recent.data.actor.v.identifier.name != STARTER_ACTOR:
        raise ModError(f"{patch.file}: {RECENT_EVENT} is not on {STARTER_ACTOR}")
    if str(recent.data.actor_query.v) != RECENT_QUERY:
        raise ModError(f"{patch.file}: {RECENT_EVENT} calls {recent.data.actor_query.v}")
    got = {case: link.v.name if link.v is not None else None for case, link in recent.data.cases.items()}
    if got.get(RECENT_REPEAT_CASE) != SARIA_LINE or got.get(RECENT_FIRST_CASE) != INIT_TALK_EVENT:
        raise ModError(f"{patch.file}: {RECENT_EVENT} cases are {got}")
    init = by_name.get(INIT_TALK_EVENT)
    if init is None or not isinstance(init.data, SubFlowEvent):
        raise ModError(f"{patch.file}: missing {INIT_TALK_EVENT}")
    if init.data.res_flowchart_name != INIT_FLOW or init.data.entry_point_name != INIT_ENTRY:
        raise ModError(
            f"{patch.file}: {INIT_TALK_EVENT} calls {init.data.res_flowchart_name}::{init.data.entry_point_name}"
        )
    params = {} if init.data.params is None else init.data.params.data
    if params.get("Arg_Turn") != 4 or params.get("Arg_Greeting") != 4 or params.get("NpcFairy_MovingFlag") is not False:
        raise ModError(f"{patch.file}: {INIT_TALK_EVENT} params are {params}")
    if init.data.nxt.v is None or init.data.nxt.v.name != SARIA_LINE:
        raise ModError(f"{patch.file}: {INIT_TALK_EVENT} does not reach {SARIA_LINE}")
    _verify_talk(
        patch.file,
        by_name[SARIA_LINE],
        SARIA_MESSAGE_ID,
        anonymous=False,
        actor_name=STARTER_ACTOR,
        check_container=False,
    )
    if by_name[SARIA_LINE].data.params.data.get("MaintainPerformanceState") != 0:
        raise ModError(f"{patch.file}: {SARIA_LINE} is missing MaintainPerformanceState 0")
    flowchart = flow.flowchart
    hooked = entries[patch.entry_point or ""]
    if flowchart is None or hooked is None:
        raise ModError(f"{patch.file}: cannot check the InitTalk subflow table")
    named = [flowchart.events[index].name for index in hooked._sub_flow_event_indices]
    if INIT_TALK_EVENT not in named:
        raise ModError(f"{patch.file}: {hooked.name} subflow table is missing {INIT_TALK_EVENT}")


def _verify_talk(
    label: str,
    event: Event,
    message_id: str,
    *,
    anonymous: bool,
    ends: bool = True,
    actor_name: str = TEXT_ACTOR,
    check_container: bool = True,
) -> None:
    data = event.data
    if not isinstance(data, ActionEvent):
        raise ModError(f"{label}: {event.name} was not written as an action")
    actor = data.actor.v
    if actor is None or actor.identifier.name != actor_name or actor.identifier.sub_name != "":
        got = None if actor is None else actor.identifier.name
        raise ModError(f"{label}: {event.name} is on {got}, expected {actor_name}")
    if str(data.actor_action.v) != TALK_ACTION:
        raise ModError(f"{label}: {event.name} calls {data.actor_action.v}, not {TALK_ACTION}")
    params = data.params.data if data.params is not None else {}
    if params.get("MessageId") != message_id:
        raise ModError(f"{label}: {event.name} message is {params.get('MessageId')!r}")
    if params.get("IsAnonymous") is not anonymous or params.get("IsNotBecomeSpeaker") is not anonymous:
        raise ModError(f"{label}: {event.name} speaker flags are not anonymous={anonymous}")
    if params.get("IsCloseDialog") is not True or params.get("IsWaitEnd") is not True:
        raise ModError(f"{label}: {event.name} does not wait for the box to close")
    if params.get("IsNotOpenIfSkipedMostRecent") is not False:
        raise ModError(f"{label}: {event.name} is missing IsNotOpenIfSkipedMostRecent")
    if ends and data.nxt.v is not None:
        raise ModError(f"{label}: {event.name} should end")
    if not ends and data.nxt.v is None:
        raise ModError(f"{label}: {event.name} should continue")
    if check_container:
        got = actor.params.data if actor.params is not None else {}
        for key, value in GAMEDATA_ACTOR_PARAMS:
            if got.get(key) != value:
                raise ModError(f"{label}: {actor_name} param {key} is {got.get(key)!r}")
    if TALK_ACTION not in [holder.v for holder in actor.actions]:
        raise ModError(f"{label}: {actor_name} does not declare {TALK_ACTION}")


def _verify_actor(label: str, step: str, actor, expected: str = _GAMEDATA) -> None:
    if actor is None or actor.identifier.name != expected or actor.identifier.sub_name != "":
        got = None if actor is None else actor.identifier.name
        raise ModError(f"{label}: {step} is on {got}, expected {expected}")


def _verify_params(label: str, step, params: Container | None, replace: dict | None = None) -> None:
    if params is None:
        raise ModError(f"{label}: {step.name} has no parameters after the rewrite")
    expected = dict(step.params)
    if replace:
        expected.update(replace)
    got = params.data
    if set(got) != set(expected):
        raise ModError(f"{label}: {step.name} parameters are {sorted(got)}, expected {sorted(expected)}")
    for name, value in expected.items():
        if not _same_value(value, got[name]):
            raise ModError(
                f"{label}: {step.name} {name} is {got[name]!r} ({_type_name(got[name])}), "
                f"expected {value!r} ({_type_name(value)})"
            )


def _same_value(expected: object, got: object) -> bool:
    if isinstance(expected, bool):
        return isinstance(got, bool) and got == expected
    if isinstance(expected, int):
        return isinstance(got, int) and not isinstance(got, bool) and got == expected
    if isinstance(expected, str):
        return isinstance(got, str) and not isinstance(got, bool) and got == expected
    if isinstance(expected, tuple) and expected and all(isinstance(item, float) for item in expected):
        return (
            isinstance(got, list)
            and len(got) == len(expected)
            and all(isinstance(item, float) and item == want for item, want in zip(got, expected))
        )
    return False


def _format_event(event: Event, index: int) -> list[str]:
    data = event.data
    lines = [f"[{index}] {event.name}  {_kind(data)}"]
    if isinstance(data, (ActionEvent, SwitchEvent)):
        actor = data.actor.v.identifier if data.actor.v is not None else "?"
        call = data.actor_action.v if isinstance(data, ActionEvent) else data.actor_query.v
        lines.append(f"    {_kind(data)}: {actor}.{call}")
        lines.append(f"    params: {_format_params(data.params)}")
    if isinstance(data, ActionEvent):
        lines.append(f"    next: {data.nxt.v.name if data.nxt.v is not None else '<end>'}")
    elif isinstance(data, SwitchEvent):
        if not data.cases:
            lines.append("    cases: (none)")
        for case in sorted(data.cases):
            target = data.cases[case].v
            lines.append(f"    case {case}: {target.name if target is not None else '?'}")
    elif isinstance(data, ForkEvent):
        lines.append(f"    join: {data.join.v.name if data.join.v is not None else '?'}")
        forks = ", ".join(link.v.name for link in data.forks if link.v is not None) or "(none)"
        lines.append(f"    forks: {forks}")
    elif isinstance(data, JoinEvent):
        lines.append(f"    next: {data.nxt.v.name if data.nxt.v is not None else '<end>'}")
    elif isinstance(data, (SubFlowEvent, SubFlowSwitchEvent)):
        flowchart_name = data.res_flowchart_name or "(self)"
        lines.append(f"    subflow: {flowchart_name}::{data.entry_point_name}")
        if isinstance(data, SubFlowEvent):
            lines.append(f"    next: {data.nxt.v.name if data.nxt.v is not None else '<end>'}")
        else:
            for case in sorted(data.cases):
                target = data.cases[case].v
                lines.append(f"    case {case}: {target.name if target is not None else '?'}")
        lines.append(f"    params: {_format_params(data.params)}")
    elif isinstance(data, ReturnEvent):
        lines.append(f"    value: {data.value}")
    return lines


def _format_params(params: Container | None) -> str:
    if params is None or not params.data:
        return "(none)"
    parts = []
    for name, value in params.data.items():
        parts.append(f"{name}={_format_literal(value)} ({_type_name(value)})")
    return ", ".join(parts)


def _format_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return repr(value)
    return repr(value)


def _type_name(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list) and value and all(isinstance(item, float) for item in value):
        return "float3" if len(value) == 3 else "float[]"
    return type(value).__name__


def _kind(data: object) -> str:
    return {
        ActionEvent: "action",
        SwitchEvent: "switch",
        ForkEvent: "fork",
        JoinEvent: "join",
        SubFlowEvent: "subflow",
        SubFlowSwitchEvent: "subflow-switch",
        ReturnEvent: "return",
    }.get(type(data), type(data).__name__)
