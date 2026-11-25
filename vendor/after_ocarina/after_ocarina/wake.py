"""Build the epilogue wake from LinkSleepInBed's bed-boot events.

The chain's shape is the stage-2 wake: ban Navi, fade, the child bed camera,
AirStartUP, then the stretch / fade-in fork. Parameter containers are copied
from ``BedBootEvent`` so the camera, the stretch, and the heals are the vanilla
ones. The Navi warp is skipped. ``EventQueryPlayerIsChild`` and
``EventQueryCheckPlayerState`` are followed on case 0 (child, on foot), so the
adult camera and the horse dismount are not copied. Anything past a dream-world
event is not copied.
``BedBootEvent`` calls ``DefaultBootEvent::AirStartUP``. That entry already exists
on the boot flow, so the wake copies that subflow call as it is and does not
add a second ``AirStartUP``.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from evfl.actor import Actor
from evfl.common import ActorIdentifier, StringHolder
from evfl.container import Container
from evfl.entry_point import EntryPoint
from evfl.event import ActionEvent, Event, ForkEvent, JoinEvent, SubFlowEvent, SwitchEvent
from evfl.evfl import EventFlow
from evfl.flowchart import Flowchart
from evfl.util import make_index, make_rindex

from after_ocarina.errors import ModError

LINK_SLEEP_REL = "Event/EventFlow/LinkSleepInBed.bfevfl.zs"
BED_ENTRY = "BedBootEvent"
AIR_ENTRY = "AirStartUP"
WAKE_ENTRY = "AtO_Wake"
WAKE_BAN = "AtO_WakeBan"

_FADE_OUT = "EventEventFadeOut"
_CAMERA = "EventMoveToTargetPosCamera"
_TO_PLAYER = "EventChangeTemporarilyToPlayerCamera"
_PLAY_AS = "EventPlayerPlayASForFlowchart"
_WAIT_FADE = "EventWaitCloseLoadingFade"
_FADE_IN = "EventEventFadeIn"
_HEART = "EventTriggerSetDisplayHeartGauge"
_HEART_STABLE = "EventWaitHeartGaugeStable"


@dataclass(frozen=True)
class WakeBuild:
    events: tuple[Event, ...]
    external_subflows: tuple[str, ...]
    # Source calls that were not copied. The wake uses the stage-2 order.
    omitted: tuple[str, ...] = ()


def attach_wake(flowchart, bed: bytes) -> WakeBuild:
    """Add ``AtO_Wake`` to ``flowchart``. ``AirStartUP`` has to already be an entry there."""
    source = _read(bed)
    source_chart = source.flowchart
    if source_chart is None:
        raise ModError(f"{LINK_SLEEP_REL} has no flowchart")
    entries = {entry.name: entry for entry in source_chart.entry_points}
    if BED_ENTRY not in entries or entries[BED_ENTRY].main_event.v is None:
        have = ", ".join(entries) or "(none)"
        raise ModError(f"{LINK_SLEEP_REL} has no entry point {BED_ENTRY}. Entry points: {have}")
    host = {entry.name: entry for entry in flowchart.entry_points}
    if AIR_ENTRY not in host or host[AIR_ENTRY].main_event.v is None:
        have = ", ".join(host) or "(none)"
        raise ModError(
            f"the boot flow has no entry point {AIR_ENTRY}. "
            f"{BED_ENTRY} calls that entry on this flowchart. Entry points: {have}"
        )
    if WAKE_ENTRY in host:
        raise ModError(f"the boot flow already has an entry point named {WAKE_ENTRY}")

    prefix, fork = _until_fork(entries[BED_ENTRY].main_event.v)
    if fork is None or not isinstance(fork.data, ForkEvent):
        raise ModError(f"{BED_ENTRY} has no fork before the dream tail. Calls: {_call_list(prefix)}")
    fade = _require_call(prefix, _FADE_OUT)
    camera = _require_call(prefix, _CAMERA)
    release = _require_call(prefix, _TO_PLAYER)
    air_call = _require_subflow(prefix, AIR_ENTRY)
    branches = []
    for link in fork.data.forks:
        if link.v is None:
            raise ModError(f"{BED_ENTRY} fork has an empty branch")
        linear, _stopped = _until_join(link.v, fork.data.join.v)
        branches.append(linear)
    stretch_branch = _branch_with(branches, _PLAY_AS)
    fade_branch = _branch_with(branches, _WAIT_FADE)
    stretch = _require_call(stretch_branch, _PLAY_AS)
    wait_fade = _require_call(fade_branch, _WAIT_FADE)
    fade_in = _require_call(fade_branch, _FADE_IN)
    heart = _require_call(fade_branch, _HEART)
    stable = _require_call(fade_branch, _HEART_STABLE)
    heart_at = fade_branch.index(heart)
    stable_at = fade_branch.index(stable)
    if stable_at <= heart_at:
        raise ModError(f"{BED_ENTRY} shows the heart gauge after waiting for it")
    heals = fade_branch[heart_at + 1 : stable_at]
    if not heals:
        raise ModError(
            f"{BED_ENTRY} has nothing between {_HEART} and {_HEART_STABLE}. "
            f"That branch is: {_call_list(fade_branch)}"
        )
    after = _after_join(fork.data.join.v)
    control = _require_call(after, _TO_PLAYER)
    if control is release:
        raise ModError(f"{BED_ENTRY} has only one {_TO_PLAYER}")

    made: list[Event] = []

    def add(event: Event) -> Event:
        made.append(event)
        return event

    ban = add(_action(flowchart, WAKE_BAN, "GameDataActor", "EventTriggerSetGameDataBool", (
        ("GameDataName", "Navi_Ban"),
        ("Index", -1),
        ("Value", True),
    )))
    fade_event = add(_cloned(flowchart, "AtO_FadeOut", fade))
    camera_event = add(_cloned(flowchart, "AtO_Camera", camera))
    release_event = add(_cloned(flowchart, "AtO_ReleaseCamera", release))
    air = add(_cloned(flowchart, "AtO_Air", air_call))
    stretch_event = add(_cloned(flowchart, "AtO_Stretch", stretch))
    fade_chain = [add(_cloned(flowchart, "AtO_WaitFade", wait_fade))]
    fade_chain.append(add(_cloned(flowchart, "AtO_FadeIn", fade_in)))
    fade_chain.append(add(_cloned(flowchart, "AtO_Heart", heart)))
    for index, heal in enumerate(heals):
        fade_chain.append(add(_cloned(flowchart, f"AtO_Heal{index}", heal)))
    fade_chain.append(add(_cloned(flowchart, "AtO_HeartStable", stable)))
    join = add(_join("AtO_Join"))
    control_event = add(_cloned(flowchart, "AtO_ControlCamera", control))
    fork_event = add(_fork("AtO_Fork", join, [stretch_event, fade_chain[0]]))

    _link_linear([ban, fade_event, camera_event, release_event, air, fork_event])
    _link_linear([stretch_event], _branch_end(stretch, fork.data.join.v, join))
    _link_linear(fade_chain, _branch_end(stable, fork.data.join.v, join))
    join.data.nxt = make_index(control_event)
    control_event.data.nxt = make_index(None)

    wake = EntryPoint(WAKE_ENTRY)
    wake.main_event = make_index(ban)
    flowchart.entry_points.append(wake)

    external = []
    for event in made:
        data = event.data
        if isinstance(data, SubFlowEvent) and data.res_flowchart_name:
            if data.res_flowchart_name not in external:
                external.append(data.res_flowchart_name)
        if _mentions_navi(event):
            raise ModError(f"the wake copied a Navi event ({event.name})")
    used = {
        id(fade), id(camera), id(release), id(air_call), id(stretch),
        id(wait_fade), id(fade_in), id(heart), id(stable), id(control),
    }
    used.update(id(event) for event in heals)
    left_out = [_one_call(event) for event in (*prefix, *stretch_branch, *fade_branch, *after) if id(event) not in used]
    return WakeBuild(
        events=tuple(made),
        external_subflows=tuple(external),
        omitted=tuple(left_out),
    )


def sample_bed_bytes() -> bytes:
    """A stand-in for LinkSleepInBed. The extra ``Copied`` params prove the containers were copied."""
    flow = EventFlow()
    flow.name = "LinkSleepInBed"
    chart = Flowchart()
    chart.name = "LinkSleepInBed"
    actors = {
        "UISystemActor": [
            _FADE_OUT,
            _WAIT_FADE,
            _FADE_IN,
            _HEART,
            _HEART_STABLE,
            "HealLifeMax",
            "HealMPMax",
        ],
        "EventCamera": [_CAMERA, _TO_PLAYER],
        "Player_Event": [_PLAY_AS, "EventPlayerHorseGetOff", "EventLookAtObject"],
        "GameDataActor": ["EventTriggerSetGameDataBool"],
    }
    made_actors = {}
    for name, actions in actors.items():
        actor = Actor()
        actor.identifier = ActorIdentifier(name)
        actor.concurrent_clips = 1
        for action in actions:
            actor.actions.append(StringHolder(action))
        chart.actors.append(actor)
        made_actors[name] = actor
    player = made_actors["Player_Event"]
    for query in ("EventQueryPlayerIsChild", "EventQueryCheckPlayerState"):
        player.queries.append(StringHolder(query))

    def action(name, actor, call, params):
        event = Event()
        event.name = name
        event.data = ActionEvent()
        event.data.actor = make_rindex(made_actors[actor])
        event.data.actor_action = make_rindex(
            next(holder for holder in made_actors[actor].actions if holder.v == call)
        )
        event.data.params = _fill(params)
        event.data.nxt = make_index(None)
        return event

    fade = action("Fade", "UISystemActor", _FADE_OUT, (
        ("Color", 0), ("IsWaitEnd", True), ("Length", 3), ("IsNoPlayAnime", True), ("Copied", 1),
    ))
    camera = action("Camera", "EventCamera", _CAMERA, (
        ("AtVector", (0.0, 0.9, 0.0)),
        ("PosVector", (1.4, 0.95, 2.45)),
        ("Fovy", 30.0),
        ("Copied", 2),
    ))
    release = action("Release", "EventCamera", _TO_PLAYER, (
        ("IsWaitEnd", False), ("EnableControle", False), ("Copied", 3),
    ))
    air = Event()
    air.name = "CallAir"
    air.data = SubFlowEvent()
    air.data.res_flowchart_name = "DefaultBootEvent"
    air.data.entry_point_name = AIR_ENTRY
    air.data.params = None
    air.data.nxt = make_index(None)
    stretch = action("Stretch", "Player_Event", _PLAY_AS, (
        ("ASName", "EvtStretch"), ("IsWaitEnd", True), ("Copied", 4),
    ))
    wait = action("Wait", "UISystemActor", _WAIT_FADE, (("Copied", 5),))
    fade_in = action("FadeIn", "UISystemActor", _FADE_IN, (
        ("IsNoPlayAnime", False), ("Color", 0), ("Length", 1), ("IsWaitEnd", True), ("Copied", 6),
    ))
    heart = action("Heart", "UISystemActor", _HEART, (("IsDisplayHeartGauge", True), ("Copied", 7)))
    heal_life = action("HealLife", "UISystemActor", "HealLifeMax", (("Copied", 8),))
    heal_magic = action("HealMagic", "UISystemActor", "HealMPMax", (("Copied", 81),))
    stable = action("Stable", "UISystemActor", _HEART_STABLE, (("Copied", 9),))
    control = action("Control", "EventCamera", _TO_PLAYER, (
        ("EnableControle", True), ("IsWaitEnd", False), ("Copied", 10),
    ))
    navi = Event()
    navi.name = "NaviWarp"
    navi.data = SubFlowEvent()
    navi.data.res_flowchart_name = "NaviEventCommon"
    navi.data.entry_point_name = "NaviWarp_PlayerHomePos_Choice"
    navi.data.params = None
    navi.data.nxt = make_index(None)
    adult = action("AdultCamera", "EventCamera", _CAMERA, (("Fovy", 99.0), ("Copied", 99)))
    horse = action("Horse", "Player_Event", "EventPlayerHorseGetOff", (("Copied", 77),))
    look = action("LookAt", "Player_Event", "EventLookAtObject", (("Copied", 12),))

    def switch(name, query, params, cases):
        event = Event()
        event.name = name
        event.data = SwitchEvent()
        event.data.actor = make_rindex(player)
        event.data.actor_query = make_rindex(next(holder for holder in player.queries if str(holder.v) == query))
        event.data.params = _fill(params) if params else None
        event.data.cases = {case: make_rindex(target) for case, target in cases.items()}
        return event

    is_child = switch("IsChild", "EventQueryPlayerIsChild", (), {0: camera, 1: adult})
    on_foot = switch("OnFoot", "EventQueryCheckPlayerState", (("State", 1),), {0: air, 1: horse})
    dream = action("DreamTail", "GameDataActor", "EventTriggerSetGameDataBool", (
        ("GameDataName", "Sequence_IsInDreamWorld"), ("Index", -1), ("Value", False),
    ))
    join = Event()
    join.name = "Join"
    join.data = JoinEvent()
    fork = Event()
    fork.name = "Fork"
    fork.data = ForkEvent()
    fork.data.join = make_rindex(join)
    fork.data.forks = [make_rindex(stretch), make_rindex(wait)]

    # FadeOut -> NaviWarp -> IsChild (case 0 camera, case 1 adult) -> OnFoot
    # (case 0 AirStartUP, case 1 horse dismount) -> fork -> control camera -> look -> dream.
    fade.data.nxt = make_index(navi)
    navi.data.nxt = make_index(is_child)
    camera.data.nxt = make_index(release)
    release.data.nxt = make_index(on_foot)
    horse.data.nxt = make_index(air)
    air.data.nxt = make_index(fork)
    stretch.data.nxt = make_index(join)
    _link_linear([wait, fade_in, heart, heal_life, heal_magic, stable], join)
    join.data.nxt = make_index(control)
    control.data.nxt = make_index(look)
    look.data.nxt = make_index(dream)

    for event in (
        fade, navi, is_child, camera, release, adult, on_foot, horse, air,
        stretch, wait, fade_in, heart, heal_life, heal_magic, stable, control, look, dream, join, fork,
    ):
        chart.events.append(event)
    bed = EntryPoint(BED_ENTRY)
    bed.main_event = make_index(fade)
    chart.entry_points.append(bed)
    flow.flowchart = chart
    return _write(flow)


def _cloned(flowchart, name: str, source: Event) -> Event:
    event = Event()
    event.name = name
    data = source.data
    if isinstance(data, ActionEvent):
        actor_name = data.actor.v.identifier.name if data.actor.v is not None else ""
        call = str(data.actor_action.v)
        event.data = ActionEvent()
        actor = _actor(flowchart, actor_name, call)
        event.data.actor = make_rindex(actor)
        event.data.actor_action = make_rindex(_holder(actor.actions, call))
        event.data.params = _copy_params(data.params)
        event.data.nxt = make_index(None)
    elif isinstance(data, SubFlowEvent):
        event.data = SubFlowEvent()
        event.data.res_flowchart_name = data.res_flowchart_name
        event.data.entry_point_name = data.entry_point_name
        event.data.params = _copy_params(data.params)
        event.data.nxt = make_index(None)
    elif isinstance(data, ForkEvent):
        event.data = ForkEvent()
    elif isinstance(data, JoinEvent):
        event.data = JoinEvent()
    elif isinstance(data, SwitchEvent):
        actor_name = data.actor.v.identifier.name if data.actor.v is not None else ""
        call = str(data.actor_query.v)
        event.data = SwitchEvent()
        actor = _actor(flowchart, actor_name, call, query=True)
        event.data.actor = make_rindex(actor)
        event.data.actor_query = make_rindex(_holder(actor.queries, call))
        event.data.params = _copy_params(data.params)
    else:
        raise ModError(f"cannot copy {source.name} ({type(data).__name__})")
    return event


def _action(flowchart, name, actor_name, call, params) -> Event:
    event = Event()
    event.name = name
    event.data = ActionEvent()
    actor = _actor(flowchart, actor_name, call)
    event.data.actor = make_rindex(actor)
    event.data.actor_action = make_rindex(_holder(actor.actions, call))
    event.data.params = _fill(params)
    event.data.nxt = make_index(None)
    return event


def _join(name) -> Event:
    event = Event()
    event.name = name
    event.data = JoinEvent()
    return event


def _fork(name, join, branches) -> Event:
    event = Event()
    event.name = name
    event.data = ForkEvent()
    event.data.join = make_rindex(join)
    event.data.forks = [make_rindex(branch) for branch in branches]
    return event


def _actor(flowchart, name: str, call: str, query: bool = False) -> Actor:
    found = [
        actor
        for actor in flowchart.actors
        if actor.identifier.name == name and actor.identifier.sub_name == ""
    ]
    if len(found) > 1:
        raise ModError(f"flowchart has more than one {name}")
    if found:
        actor = found[0]
    else:
        from after_ocarina.evflow import GAMEDATA_ACTOR_PARAMS, make_params

        actor = Actor()
        actor.identifier = ActorIdentifier(name)
        actor.concurrent_clips = 1
        actor.params = make_params(GAMEDATA_ACTOR_PARAMS)
        flowchart.actors.append(actor)
    if query:
        _holder(actor.queries, call)
    else:
        _holder(actor.actions, call)
    return actor


def _holder(holders, name: str) -> StringHolder:
    for holder in holders:
        if holder.v == name:
            return holder
    holder = StringHolder(name)
    holders.append(holder)
    return holder


def _until_fork(start: Event | None):
    linear = []
    event = start
    seen: set[int] = set()
    while event is not None and id(event) not in seen:
        event = _skip_navi(event)
        if event is None or _is_dream(event):
            break
        seen.add(id(event))
        if isinstance(event.data, ForkEvent):
            return linear, event
        if isinstance(event.data, JoinEvent):
            break
        linear.append(event)
        event = _nxt(event)
    return linear, None


def _until_join(start: Event | None, join: Event | None):
    linear = []
    event = start
    seen: set[int] = set()
    while event is not None and event is not join and id(event) not in seen:
        event = _skip_navi(event)
        if event is None or event is join or _is_dream(event):
            break
        seen.add(id(event))
        if isinstance(event.data, (ForkEvent, JoinEvent)):
            break
        linear.append(event)
        event = _nxt(event)
    return linear, event


def _after_join(join: Event | None):
    if join is None or not isinstance(join.data, JoinEvent):
        return []
    linear, _fork = _until_fork(join.data.nxt.v)
    return linear


def _branch_with(branches, call: str):
    found = [branch for branch in branches if any(_is_call(event, call) for event in branch)]
    if len(found) != 1:
        shown = " | ".join(_call_list(branch) for branch in branches)
        raise ModError(f"{BED_ENTRY} fork does not have one branch calling {call}. Branches: {shown}")
    return found[0]


def _require_call(events, call: str) -> Event:
    found = [event for event in events if _is_call(event, call)]
    if len(found) != 1:
        raise ModError(f"{BED_ENTRY} needs one {call}. Found {len(found)} in: {_call_list(events)}")
    return found[0]


def _require_subflow(events, entry: str) -> Event:
    found = [
        event
        for event in events
        if isinstance(event.data, SubFlowEvent) and event.data.entry_point_name == entry
    ]
    if len(found) != 1:
        shown = ", ".join(
            f"{event.data.res_flowchart_name or '(self)'}::{event.data.entry_point_name}"
            for event in events
            if isinstance(event.data, SubFlowEvent)
        )
        raise ModError(
            f"{BED_ENTRY} needs one subflow into {entry}. Found {len(found)}. "
            f"Subflows: {shown or '(none)'}. Calls: {_call_list(events)}"
        )
    return found[0]


def _branch_end(source_tail: Event, source_join: Event | None, our_join: Event):
    nxt = _nxt(source_tail)
    if source_join is not None and nxt is source_join:
        return our_join
    return None


def _link_linear(events, final=None) -> None:
    for index, event in enumerate(events):
        nxt = events[index + 1] if index + 1 < len(events) else final
        if not hasattr(event.data, "nxt"):
            if index + 1 < len(events):
                raise ModError(f"{event.name} cannot point at the next event")
            continue
        event.data.nxt = make_index(nxt)


def _is_call(event: Event, call: str) -> bool:
    return isinstance(event.data, ActionEvent) and str(event.data.actor_action.v) == call


def _is_navi(event: Event) -> bool:
    data = event.data
    if isinstance(data, SubFlowEvent):
        text = f"{data.res_flowchart_name} {data.entry_point_name}"
        return "Navi" in text
    return "Navi" in event.name


def _is_dream(event: Event) -> bool:
    if "Dream" in event.name:
        return True
    data = event.data
    if isinstance(data, SubFlowEvent) and "Dream" in f"{data.res_flowchart_name} {data.entry_point_name}":
        return True
    if isinstance(data, ActionEvent) and data.params is not None:
        for value in data.params.data.values():
            if isinstance(value, str) and "Dream" in value:
                return True
    return False


def _mentions_navi(event: Event) -> bool:
    if _is_navi(event):
        return True
    data = event.data
    if isinstance(data, ActionEvent) and data.params is not None:
        for key, value in data.params.data.items():
            if key == "GameDataName" and value == "Navi_Ban":
                continue
            if isinstance(value, str) and "Navi" in value:
                return True
    return False


def _skip_navi(event: Event | None) -> Event | None:
    seen: set[int] = set()
    while event is not None and id(event) not in seen and _is_navi(event):
        seen.add(id(event))
        event = _nxt(event)
    return event


# Case 0 of these queries is the child, on-foot bed. The other case is the adult
# camera or the horse dismount, and a dream switch is left alone so the walk stops.
_CASE0_QUERIES = frozenset({"EventQueryPlayerIsChild", "EventQueryCheckPlayerState"})


def _nxt(event: Event | None) -> Event | None:
    if event is None:
        return None
    data = event.data
    if isinstance(data, (ActionEvent, SubFlowEvent, JoinEvent)):
        return data.nxt.v
    if isinstance(data, SwitchEvent):
        query = str(data.actor_query.v) if data.actor_query.v is not None else ""
        if query in _CASE0_QUERIES:
            case = data.cases.get(0)
            target = case.v if case is not None else None
            if target is None:
                raise ModError(f"{event.name} {query} has no case 0")
            return target
    return None


def _one_call(event: Event) -> str:
    return _call_list([event])


def _call_list(events) -> str:
    names = []
    for event in events:
        data = event.data
        if isinstance(data, ActionEvent):
            names.append(str(data.actor_action.v))
        elif isinstance(data, SubFlowEvent):
            names.append(f"{data.res_flowchart_name or '(self)'}::{data.entry_point_name}")
        elif isinstance(data, SwitchEvent):
            names.append(str(data.actor_query.v) if data.actor_query.v is not None else "SwitchEvent")
        else:
            names.append(type(data).__name__)
    return ", ".join(names) or "(none)"


def _copy_params(params: Container | None) -> Container | None:
    if params is None or not params.data:
        return None
    clone = Container()
    for key, value in params.data.items():
        clone.data[key] = list(value) if isinstance(value, list) else value
    return clone


def _fill(pairs) -> Container:
    from after_ocarina.evflow import make_params

    return make_params(pairs)


def _read(data: bytes) -> EventFlow:
    flow = EventFlow()
    try:
        flow.read(data)
    except (AssertionError, ValueError, KeyError, IndexError, struct.error) as exc:
        raise ModError(f"{LINK_SLEEP_REL}: could not read the bed flow ({exc})") from None
    return flow


def _write(flow: EventFlow) -> bytes:
    buffer = io.BytesIO()
    if not flow.write(buffer):
        raise ModError("could not write the sample bed flow")
    return buffer.getvalue()
