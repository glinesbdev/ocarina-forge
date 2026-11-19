"""Epilogue rumor talks, driven by the YAML rather than one function per NPC.

A rumor patch keeps the vanilla talk on the false side of
``HavePlayedEvent.TitleEventBase``. The true side asks
``EventQueryIsOnRecentTalkFlag``. Case 0, a repeat, plays the rumor line and
ends. Case 1, the first talk, looks at Link when this hook is the first thing
the talk does, plays the greeting, plays ``EvtTalkingL``, plays the rumor,
sets that NPC's flag, and steps the journal. Every step then shows the
banner. Vanilla runs the step first and the banner second. ``IsFirstStep``
is true only on Step05.

The flag just written is not queried again. The count starts at one and the
other three flags add one each, so a write this flow cannot see yet cannot
undercount. One, two, three, and four rumors are Step05, Step06, Step07, and
Step08. Step02, Step03, Step04, and Reset are never written.

Mido is the finale. He sets no rumor flag. Any of the four flags still false
is the nudge. All four true is the Lost Woods line, the done flag, the
completion banner, then ``Complete``.
"""

from __future__ import annotations

from dataclasses import dataclass

from evfl.actor import Actor
from evfl.common import ActorIdentifier, StringHolder
from evfl.entry_point import EntryPoint
from evfl.event import ActionEvent, Event, SubFlowEvent, SwitchEvent
from evfl.evfl import EventFlow
from evfl.flowchart import Flowchart
from evfl.util import make_index, make_rindex

from after_ocarina.errors import ModError
from after_ocarina.evflow import (
    GAMEDATA_ACTOR_PARAMS,
    PLAYER_EVENT_ACTOR_PARAMS,
    RECENT_FIRST_CASE,
    RECENT_QUERY,
    RECENT_REPEAT_CASE,
    STARTER_ACTOR,
    TALK_ACTION,
    STARTER_ACTOR_PARAMS,
    _action_event,
    _ensure_holder,
    _ensure_named_actor,
    _reachable,
    _talk_event,
    _write,
    make_params,
    retarget_next,
)
from after_ocarina.sources import TRAPPED_STEPS, FlowPatch, RumorSpec, RumorTalk

TITLE_FLAG = "HavePlayedEvent.TitleEventBase"
BOOL_QUERY = "EventQueryIsGameDataBool"
SET_BOOL = "EventTriggerSetGameDataBool"
CHALLENGE_ACTOR = "ChallengeSystemActor"
STEP_ACTION = "EventExecuteProgressChallengeStep"
SHOW_ACTION = "EventTriggerShowChallengeProgressUI"
PLAYER_ACTOR = "Player_Event"
GESTURE_ACTION = "EventPlayerPlayASForFlowchart"
GESTURE_PARAMS = (
    ("ASCommand", "EvtTalkingL"),
    ("IsWaitEnd", True),
    ("UseAnmDriven", False),
    ("IsAllSlot", False),
    ("KeepOneTimeAnm", False),
    ("ForceFadeFrame", -1.0),
    ("ClothWarpMode", 0),
)
RECENT = "AtO_RumorRecent"
INIT = "AtO_RumorInit"
GREET = "AtO_RumorGreet"
GESTURE = "AtO_RumorGesture"
LINE = "AtO_RumorLine"
REPEAT = "AtO_RumorRepeat"
FLAG = "AtO_RumorFlag"
# Step05's banner follows show_progress. Step06, Step07, and Step08 follow this
# flag: the step runs, then the Show. Set it false if those later banners double
# once the tapestry is on the wall. Step05 keeps its banner either way.
# Mido's Complete is separate. The Show runs before that step, and this flag
# does not remove it.
SHOW_LATER_STEPS = True
NUDGE = "AtO_RumorNudge"
FINALE = "AtO_RumorFinale"
AGAIN = "AtO_RumorAgain"
DONE = "AtO_RumorDone"
COMPLETE = "AtO_RumorComplete"


def show_event_name(step_name: str) -> str:
    """One banner event per step. Step05 is AtO_RumorShow05. Complete is AtO_RumorShowComplete."""
    if step_name in TRAPPED_STEPS or step_name == "Reset":
        raise ModError(f"{step_name} is not a banner step")
    if step_name == "Complete":
        return "AtO_RumorShowComplete"
    if step_name.startswith("Step") and step_name[4:].isdigit():
        return f"AtO_RumorShow{step_name[4:]}"
    raise ModError(f"{step_name} is not a banner step")


def _banner_for(spec: RumorSpec, step_name: str) -> bool:
    """Step05 follows ``show_progress``. Later steps follow ``SHOW_LATER_STEPS``.

    Complete has no banner after the step. The finale puts ``AtO_RumorShowComplete``
    before the step instead.
    """
    if step_name == "Step05":
        return spec.show_progress
    if step_name == "Complete":
        return False
    return SHOW_LATER_STEPS and spec.show_progress


def gate_name(entry: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in entry)
    return f"AtO_RumorGate_{slug}"


def apply_rumor(flow: EventFlow, patch: FlowPatch):
    """Insert the rumor split. Entry points that are not listed stay put."""
    from after_ocarina.evflow import HookNote

    spec = patch.rumor
    if spec is None:
        raise ModError(f"{patch.file}: rumor patch has no rumor block")
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{patch.file} has no flowchart")
    if any(event.name.startswith("AtO_Rumor") for event in flowchart.events):
        raise ModError(
            f"{patch.file}: already patched. An AtO_Rumor event is already in the flow. "
            "Build from a vanilla event flow."
        )
    prepared = [_prepare(flowchart, talk, patch.file) for talk in spec.talks]
    if spec.finale:
        made, recent = _finale_body(flowchart, spec)
    else:
        made, recent = _rumor_body(flowchart, spec)
    gates = [
        _bool_switch(flowchart, gate_name(item.talk.entry), TITLE_FLAG, recent, item.vanilla)
        for item in prepared
    ]
    flowchart.events.extend(made)
    flowchart.events.extend(gates)
    notes = []
    for item, gate in zip(prepared, gates):
        notes.append(_install(flowchart, item, gate, patch.file))
    return HookNote(
        file=patch.file,
        entry_points=tuple(entry.name for entry in flowchart.entry_points),
        hooked=prepared[0].talk.entry,
        explicit=True,
        original_event=prepared[0].vanilla.name,
        case_note=" ".join(notes),
        rumor=True,
    )


def verify_rumor(flow: EventFlow, patch: FlowPatch) -> None:
    """The re-read graph matches the YAML: vanilla case, repeat, first talk, and the step."""
    spec = patch.rumor
    if spec is None or flow.flowchart is None:
        raise ModError(f"{patch.file}: rumor verify has no flowchart")
    flowchart = flow.flowchart
    by_name = _by_name(flowchart, patch.file)
    _no_trapped_step(flowchart, patch.file)
    recent = by_name[RECENT]
    if not isinstance(recent.data, SwitchEvent):
        raise ModError(f"{patch.file}: {RECENT} is not a switch")
    if recent.data.actor.v is None or recent.data.actor.v.identifier.name != STARTER_ACTOR:
        raise ModError(f"{patch.file}: {RECENT} is not on {STARTER_ACTOR}")
    if str(recent.data.actor_query.v) != RECENT_QUERY:
        raise ModError(f"{patch.file}: {RECENT} calls {recent.data.actor_query.v}")
    _expect_starter(recent.data.actor.v, patch.file)
    if spec.finale:
        _verify_finale(flowchart, by_name, spec, patch.file)
    else:
        _verify_rumor_body(by_name, spec, patch.file)
    listed = {talk.entry for talk in spec.talks}
    for entry in flowchart.entry_points:
        target = entry.main_event.v.name if entry.main_event.v is not None else ""
        if entry.name not in listed and target.startswith("AtO_Rumor"):
            raise ModError(f"{patch.file}: {entry.name} was moved to {target}")
    for talk in spec.talks:
        _verify_hook(flowchart, by_name, talk, patch.file)


@dataclass
class _Prepared:
    talk: RumorTalk
    entry: EntryPoint
    vanilla: Event


def _prepare(flowchart, talk: RumorTalk, file_name: str) -> _Prepared:
    entry = _one_entry(flowchart, talk.entry, file_name)
    if talk.hook == "entry":
        vanilla = entry.main_event.v
        if vanilla is None or not vanilla.name:
            raise ModError(f"{file_name}: {talk.entry} does not start at a named event")
        return _Prepared(talk, entry, vanilla)
    if talk.hook == "next":
        after = _one_event(flowchart, talk.after, file_name)
        if not _reaches(entry, after.name):
            raise ModError(f"{file_name}: {talk.entry} does not reach {talk.after}")
        nxt = getattr(after.data, "nxt", None)
        vanilla = nxt.v if nxt is not None else None
        if vanilla is None or not vanilla.name:
            raise ModError(f"{file_name}: {talk.after} nxt does not point at a named event")
        if vanilla.name != talk.rejoin:
            raise ModError(f"{file_name}: {talk.after} next is {vanilla.name}, expected {talk.rejoin}")
        return _Prepared(talk, entry, vanilla)
    main = entry.main_event.v
    if main is None or not isinstance(main.data, SubFlowEvent) or main.data.entry_point_name != "Mask_All":
        found = main.name if main is not None else "nothing"
        kind = type(main.data).__name__ if main is not None else "missing"
        raise ModError(
            f"{file_name}: {talk.entry} starts at {found} ({kind}), not a Mask_All subflow. "
            "Set hook: next with after and rejoin once that event's name is known."
        )
    vanilla = main.data.nxt.v
    if vanilla is None or not vanilla.name:
        raise ModError(f"{file_name}: {talk.entry} Mask_All nxt does not point at a named event")
    return _Prepared(talk, entry, vanilla)


def _install(flowchart, item: _Prepared, gate: Event, file_name: str) -> str:
    talk = item.talk
    if talk.hook == "entry":
        item.entry.main_event = make_index(gate)
        return f"{talk.entry} starts at {gate.name}. Case 1 returns to {item.vanilla.name}."
    if talk.hook == "next":
        returned = retarget_next(flowchart, talk.after, gate)
        if returned != talk.rejoin:
            raise ModError(f"{file_name}: retarget moved {talk.after} off {returned}, not {talk.rejoin}")
        return f"{talk.after} still runs, then {gate.name}. Case 1 continues to {talk.rejoin}."
    item.entry.main_event.v.data.nxt = make_index(gate)
    return f"{talk.entry} still runs Mask_All, then {gate.name}. Case 1 continues to {item.vanilla.name}."


def _rumor_body(flowchart, spec: RumorSpec) -> tuple[list[Event], Event]:
    talk = spec.talks[0]
    made: list[Event] = []
    steps: dict[int, Event] = {}
    for count, step_name in spec.steps:
        event = _step(flowchart, f"AtO_Rumor{step_name}", spec.challenge, step_name)
        steps[count] = event
        made.append(event)
        if _banner_for(spec, step_name):
            made.append(_attach_banner(flowchart, event, spec.challenge, step_name))
    root, switches = _count_cascade(flowchart, spec, steps)
    made.extend(switches)
    flag = _set_flag(flowchart, FLAG, talk.flag, root)
    line = _line(flowchart, LINE, talk.rumor, spec.actor, flag)
    gesture = _gesture(flowchart, line)
    greet = _line(flowchart, GREET, talk.greet, spec.actor, gesture)
    repeat = _line(flowchart, REPEAT, talk.rumor, spec.actor, None)
    made.extend((flag, line, gesture, greet, repeat))
    first: Event = greet
    if not talk.skip_init:
        first = _init(spec, greet)
        made.append(first)
    recent = _recent(flowchart, repeat, first)
    made.append(recent)
    return made, recent


def _finale_body(flowchart, spec: RumorSpec) -> tuple[list[Event], Event]:
    talk = spec.talks[0]
    nudge = _line(flowchart, NUDGE, talk.nudge, spec.actor, None)
    complete = _step(flowchart, COMPLETE, spec.challenge, "Complete")
    show = _action_event(
        flowchart,
        CHALLENGE_ACTOR,
        SHOW_ACTION,
        show_event_name("Complete"),
        (("ChallengeName", spec.challenge), ("IsFirstStep", False)),
    )
    show.data.nxt = make_index(complete)
    tail: Event = show
    done = None
    if spec.done_flag:
        done = _set_flag(flowchart, DONE, spec.done_flag, show)
        tail = done
    finale = _line(flowchart, FINALE, talk.rumor, spec.actor, tail)
    gesture = _gesture(flowchart, finale)
    again = _line(flowchart, AGAIN, talk.rumor, spec.actor, None)
    first_root, first_switches = _flag_chain(flowchart, spec.flags, gesture, nudge, "AtO_RumorAsk_First")
    again_root, again_switches = _flag_chain(flowchart, spec.flags, again, nudge, "AtO_RumorAsk_Again")
    init = _init(spec, first_root)
    recent = _recent(flowchart, again_root, init)
    made = [nudge, show, complete, finale, gesture, again, init, recent, *first_switches, *again_switches]
    if done is not None:
        made.append(done)
    return made, recent


def _count_cascade(flowchart, spec: RumorSpec, steps: dict[int, Event]):
    talk = spec.talks[0]
    others = [flag for flag in spec.flags if flag != talk.flag]
    made: list[Event] = []
    memo: dict[tuple[int, int], Event] = {}

    def build(index: int, count: int) -> Event:
        key = (index, count)
        if key in memo:
            return memo[key]
        if index == len(others):
            memo[key] = steps[count]
            return steps[count]
        event = _bool_switch(
            flowchart,
            f"AtO_RumorCount_{index}_{count}",
            others[index],
            build(index + 1, count + 1),
            build(index + 1, count),
        )
        made.append(event)
        memo[key] = event
        return event

    return build(0, 1), made


def _flag_chain(flowchart, flags: tuple[str, ...], success: Event, miss: Event, prefix: str):
    made: list[Event] = []
    target = success
    for index in reversed(range(len(flags))):
        event = _bool_switch(flowchart, f"{prefix}_{index}", flags[index], target, miss)
        made.append(event)
        target = event
    made.reverse()
    return target, made


def _bool_switch(flowchart, name: str, flag: str, when_true: Event, when_false: Event) -> Event:
    actor = _ensure_named_actor(flowchart, "GameDataActor", [SET_BOOL], [BOOL_QUERY])
    event = Event()
    event.name = name
    event.data = SwitchEvent()
    event.data.actor = make_rindex(actor)
    event.data.actor_query = make_rindex(_ensure_holder(actor.queries, BOOL_QUERY))
    event.data.params = make_params((("GameDataName", flag), ("Index", -1)))
    event.data.cases = {0: make_rindex(when_true), 1: make_rindex(when_false)}
    return event


def _line(flowchart, name: str, message: str, actor: str, nxt: Event | None) -> Event:
    event = _talk_event(flowchart, name, message, anonymous=False, actor_name=actor, maintain=True)
    event.data.nxt = make_index(nxt)
    return event


def _ensure_player_event(flowchart) -> None:
    """Declare Player_Event with the vanilla container when the flow has none.

    The six generic keys make the event create a new Link double, and that request aborts.
    """
    if any(actor.identifier.name == PLAYER_ACTOR and actor.identifier.sub_name == "" for actor in flowchart.actors):
        return
    actor = Actor()
    actor.identifier = ActorIdentifier(PLAYER_ACTOR)
    actor.concurrent_clips = 1
    actor.params = make_params(PLAYER_EVENT_ACTOR_PARAMS)
    flowchart.actors.append(actor)


def _banner_after(step: Event, show: Event) -> None:
    """The step runs, then the banner. The banner keeps whatever used to follow the step."""
    followed = step.data.nxt.v if step.data.nxt is not None else None
    show.data.nxt = make_index(followed)
    step.data.nxt = make_index(show)


def _attach_banner(flowchart, step: Event, challenge: str, step_name: str) -> Event:
    """Step, then Show, then the step's old successor. IsFirstStep is true only for Step05."""
    if step_name in TRAPPED_STEPS or step_name == "Reset":
        raise ModError(f"{step_name} is not a banner step")
    show = _action_event(
        flowchart,
        CHALLENGE_ACTOR,
        SHOW_ACTION,
        show_event_name(step_name),
        (("ChallengeName", challenge), ("IsFirstStep", step_name == "Step05")),
    )
    _banner_after(step, show)
    return show


def _gesture(flowchart, nxt: Event) -> Event:
    _ensure_player_event(flowchart)
    event = _action_event(flowchart, PLAYER_ACTOR, GESTURE_ACTION, GESTURE, GESTURE_PARAMS)
    event.data.nxt = make_index(nxt)
    return event


def _set_flag(flowchart, name: str, flag: str, nxt: Event) -> Event:
    event = _action_event(
        flowchart,
        "GameDataActor",
        SET_BOOL,
        name,
        (("GameDataName", flag), ("Value", True), ("Index", -1)),
    )
    event.data.nxt = make_index(nxt)
    return event


def _step(flowchart, name: str, challenge: str, step_name: str) -> Event:
    return _action_event(
        flowchart,
        CHALLENGE_ACTOR,
        STEP_ACTION,
        name,
        (
            ("ChallengeName", challenge),
            ("StepName", step_name),
            ("IsWaitEnd", True),
            ("IsIgnoreCompletedChallenge", False),
            ("IsIgnoreChallengeProgressUI", False),
        ),
    )


def _init(spec: RumorSpec, nxt: Event) -> Event:
    if spec.init is None:
        raise ModError("rumor init is missing")
    event = Event()
    event.name = INIT
    event.data = SubFlowEvent()
    event.data.res_flowchart_name = spec.init.flow
    event.data.entry_point_name = spec.init.entry
    event.data.params = make_params(spec.init.params) if spec.init.params else None
    event.data.nxt = make_index(nxt)
    return event


def _recent(flowchart, on_repeat: Event, on_first: Event) -> Event:
    starter = _ensure_named_actor(
        flowchart, STARTER_ACTOR, [TALK_ACTION], [RECENT_QUERY], STARTER_ACTOR_PARAMS
    )
    event = Event()
    event.name = RECENT
    event.data = SwitchEvent()
    event.data.actor = make_rindex(starter)
    event.data.actor_query = make_rindex(_ensure_holder(starter.queries, RECENT_QUERY))
    event.data.params = None
    event.data.cases = {
        RECENT_REPEAT_CASE: make_rindex(on_repeat),
        RECENT_FIRST_CASE: make_rindex(on_first),
    }
    return event


def _verify_rumor_body(by_name: dict[str, Event], spec: RumorSpec, file_name: str) -> None:
    talk = spec.talks[0]
    recent = by_name[RECENT]
    repeat = _case(recent, RECENT_REPEAT_CASE, file_name)
    first = _case(recent, RECENT_FIRST_CASE, file_name)
    if repeat.name != REPEAT or _nxt_name(repeat) is not None:
        raise ModError(f"{file_name}: a repeat does not end on the rumor line")
    _check_talk(repeat, talk.rumor, spec.actor, file_name)
    if talk.skip_init:
        if INIT in by_name:
            raise ModError(f"{file_name}: InitTalk already ran before this hook")
        greet = first
    else:
        if first.name != INIT:
            raise ModError(f"{file_name}: the first talk does not call InitTalk")
        _check_init(first, spec, file_name)
        greet = first.data.nxt.v
    if greet is None or greet.name != GREET:
        raise ModError(f"{file_name}: the first talk does not greet")
    _check_talk(greet, talk.greet, spec.actor, file_name)
    gesture = greet.data.nxt.v
    _check_gesture(gesture, file_name)
    line = gesture.data.nxt.v
    if line is None or line.name != LINE:
        raise ModError(f"{file_name}: the gesture does not continue to the rumor")
    _check_talk(line, talk.rumor, spec.actor, file_name)
    flag = line.data.nxt.v
    if flag is None or flag.name != FLAG:
        raise ModError(f"{file_name}: the rumor does not set {talk.flag}")
    _check_flag(flag, talk.flag, file_name)
    others = [name for name in spec.flags if name != talk.flag]
    root = flag.data.nxt.v
    for size in range(len(others) + 1):
        truths = set(others[:size])
        leaf = _walk_bools(root, others, truths, file_name)
        count = 1 + size
        step_name = dict(spec.steps)[count]
        if leaf is None or leaf.name != f"AtO_Rumor{step_name}":
            got = leaf.name if leaf is not None else None
            raise ModError(f"{file_name}: {count} rumors reach {got}, expected {step_name}")
        _check_step(leaf, spec.challenge, step_name, file_name)
        _expect_banner(leaf, step_name, spec, file_name)
    reached = {event.name for event in _reachable(repeat)}
    journal = {FLAG, *(f"AtO_Rumor{name}" for _count, name in spec.steps)}
    journal.update(show_event_name(name) for _count, name in spec.steps)
    if reached.intersection(journal):
        raise ModError(f"{file_name}: a repeat reaches the flag or the journal")


def _verify_finale(flowchart, by_name: dict[str, Event], spec: RumorSpec, file_name: str) -> None:
    talk = spec.talks[0]
    recent = by_name[RECENT]
    again_root = _case(recent, RECENT_REPEAT_CASE, file_name)
    init = _case(recent, RECENT_FIRST_CASE, file_name)
    if init.name != INIT:
        raise ModError(f"{file_name}: Mido's first talk does not call InitTalk")
    _check_init(init, spec, file_name)
    first_root = init.data.nxt.v
    nudge = by_name[NUDGE]
    _check_talk(nudge, talk.nudge, spec.actor, file_name)
    if _nxt_name(nudge) is not None:
        raise ModError(f"{file_name}: the nudge does not end")
    again = by_name[AGAIN]
    _check_talk(again, talk.rumor, spec.actor, file_name)
    if _nxt_name(again) is not None:
        raise ModError(f"{file_name}: a repeat of the Lost Woods line does not end")
    gesture = by_name[GESTURE]
    _check_gesture(gesture, file_name)
    finale = gesture.data.nxt.v
    if finale is None or finale.name != FINALE:
        raise ModError(f"{file_name}: the gesture does not continue to Mido's rumor")
    _check_talk(finale, talk.rumor, spec.actor, file_name)
    complete = by_name[COMPLETE]
    _check_step(complete, spec.challenge, "Complete", file_name)
    _expect_banner(complete, "Complete", spec, file_name)
    show_name = show_event_name("Complete")
    show = by_name.get(show_name)
    if show is None or _nxt_name(show) != COMPLETE:
        raise ModError(f"{file_name}: {show_name} does not run before Complete")
    _check_show(show, spec.challenge, file_name, first=False)
    if spec.done_flag:
        done = finale.data.nxt.v
        if done is None or done.name != DONE or _nxt_name(done) != show_name:
            raise ModError(f"{file_name}: the Lost Woods line does not set {spec.done_flag} before the completion banner")
        _check_flag(done, spec.done_flag, file_name)
    elif _nxt_name(finale) != show_name:
        raise ModError(f"{file_name}: the Lost Woods line does not show the completion banner")
    if FLAG in by_name:
        raise ModError(f"{file_name}: Mido sets a rumor flag")
    _expect_chain(first_root, spec.flags, gesture, nudge, file_name)
    _expect_chain(again_root, spec.flags, again, nudge, file_name)
    if any(event.name == INIT for event in _reachable(again_root)):
        raise ModError(f"{file_name}: a repeat calls InitTalk")
    for entry in flowchart.entry_points:
        if entry.name in {talk.entry for talk in spec.talks}:
            names = [flowchart.events[index].name for index in entry._sub_flow_event_indices]
            if INIT not in names:
                raise ModError(f"{file_name}: {entry.name} subflow table is missing {INIT}")


def _expect_chain(root: Event | None, flags: tuple[str, ...], success: Event, miss: Event, file_name: str) -> None:
    """A false flag ends at the nudge. The chain does not keep counting after that."""
    event = root
    for flag in flags:
        if event is None or not isinstance(event.data, SwitchEvent) or _params(event).get("GameDataName") != flag:
            raise ModError(f"{file_name}: the finale cascade does not query {flag}")
        if _case(event, 1, file_name).name != miss.name:
            raise ModError(f"{file_name}: a missing {flag} does not nudge")
        event = _case(event, 0, file_name)
    if event is None or event.name != success.name:
        got = event.name if event is not None else None
        raise ModError(f"{file_name}: all four rumors reach {got}, expected {success.name}")


def _verify_hook(flowchart, by_name: dict[str, Event], talk: RumorTalk, file_name: str) -> None:
    gate = by_name[gate_name(talk.entry)]
    if not isinstance(gate.data, SwitchEvent):
        raise ModError(f"{file_name}: {gate.name} is not the epilogue query")
    if _params(gate).get("GameDataName") != TITLE_FLAG or _params(gate).get("Index") != -1:
        raise ModError(f"{file_name}: {gate.name} does not read {TITLE_FLAG}")
    if _case(gate, 0, file_name).name != RECENT:
        raise ModError(f"{file_name}: {gate.name} case 0 is not the rumor")
    vanilla = _case(gate, 1, file_name)
    if vanilla.name.startswith("AtO_Rumor"):
        raise ModError(f"{file_name}: {gate.name} case 1 enters the rumor")
    entry = _one_entry(flowchart, talk.entry, file_name)
    if INIT in by_name:
        names = [flowchart.events[index].name for index in entry._sub_flow_event_indices]
        if INIT not in names:
            raise ModError(f"{file_name}: {talk.entry} subflow table is missing {INIT}")
    if talk.hook == "entry":
        if entry.main_event.v is None or entry.main_event.v.name != gate.name:
            raise ModError(f"{file_name}: {talk.entry} does not start at {gate.name}")
        return
    if talk.hook == "next":
        after = by_name[talk.after]
        if _nxt_name(after) != gate.name:
            raise ModError(f"{file_name}: {talk.after} does not lead to {gate.name}")
        if vanilla.name != talk.rejoin:
            raise ModError(f"{file_name}: {gate.name} case 1 is {vanilla.name}, expected {talk.rejoin}")
        if entry.main_event.v is not None and entry.main_event.v.name == gate.name:
            raise ModError(f"{file_name}: {talk.entry} was moved off its vanilla start")
        return
    main = entry.main_event.v
    if main is None or not isinstance(main.data, SubFlowEvent) or main.data.entry_point_name != "Mask_All":
        raise ModError(f"{file_name}: {talk.entry} no longer starts at Mask_All")
    if main.data.nxt.v is None or main.data.nxt.v.name != gate.name:
        raise ModError(f"{file_name}: {talk.entry} Mask_All does not lead to {gate.name}")


def _check_init(event: Event, spec: RumorSpec, file_name: str) -> None:
    if spec.init is None or not isinstance(event.data, SubFlowEvent):
        raise ModError(f"{file_name}: {INIT} is not a subflow")
    if event.data.res_flowchart_name != spec.init.flow or event.data.entry_point_name != spec.init.entry:
        raise ModError(
            f"{file_name}: {INIT} calls {event.data.res_flowchart_name}::{event.data.entry_point_name}"
        )
    got = _params(event)
    if got != dict(spec.init.params):
        raise ModError(f"{file_name}: {INIT} params are {got}")


def _check_talk(event: Event, message: str, actor: str, file_name: str) -> None:
    if not isinstance(event.data, ActionEvent):
        raise ModError(f"{file_name}: {event.name} is not a talk")
    if event.data.actor.v is None or event.data.actor.v.identifier.name != actor:
        raise ModError(f"{file_name}: {event.name} is not spoken by {actor}")
    if str(event.data.actor_action.v) != TALK_ACTION:
        raise ModError(f"{file_name}: {event.name} is not {TALK_ACTION}")
    params = _params(event)
    for key, value in (
        ("MessageId", message),
        ("IsWaitEnd", True),
        ("IsCloseDialog", True),
        ("EndDialogOption", 0),
        ("ChoiceNumber", 0),
        ("IsOverrideStyle", False),
        ("MaintainPerformanceState", 0),
        ("IsAnonymous", False),
    ):
        if params.get(key) != value:
            raise ModError(f"{file_name}: {event.name} {key} is {params.get(key)!r}")


def _check_gesture(event: Event | None, file_name: str) -> None:
    if event is None or event.name != GESTURE or not isinstance(event.data, ActionEvent):
        raise ModError(f"{file_name}: the line is not followed by {GESTURE}")
    if event.data.actor.v is None or event.data.actor.v.identifier.name != PLAYER_ACTOR:
        raise ModError(f"{file_name}: {GESTURE} is not on {PLAYER_ACTOR}")
    if str(event.data.actor_action.v) != GESTURE_ACTION:
        raise ModError(f"{file_name}: {GESTURE} calls {event.data.actor_action.v}")
    params = _params(event)
    for key, value in GESTURE_PARAMS:
        if params.get(key) != value:
            raise ModError(f"{file_name}: {GESTURE} {key} is {params.get(key)!r}")


def _check_flag(event: Event, flag: str, file_name: str) -> None:
    if str(event.data.actor_action.v) != SET_BOOL:
        raise ModError(f"{file_name}: {event.name} does not set a bool")
    params = _params(event)
    if params.get("GameDataName") != flag or params.get("Value") is not True or params.get("Index") != -1:
        raise ModError(f"{file_name}: {event.name} writes {params}")


def _check_step(event: Event, challenge: str, step_name: str, file_name: str) -> None:
    if event.data.actor.v is None or event.data.actor.v.identifier.name != CHALLENGE_ACTOR:
        raise ModError(f"{file_name}: {event.name} is not on {CHALLENGE_ACTOR}")
    if str(event.data.actor_action.v) != STEP_ACTION:
        raise ModError(f"{file_name}: {event.name} is not {STEP_ACTION}")
    params = _params(event)
    for key, value in (
        ("ChallengeName", challenge),
        ("StepName", step_name),
        ("IsWaitEnd", True),
        ("IsIgnoreCompletedChallenge", False),
        ("IsIgnoreChallengeProgressUI", False),
    ):
        if params.get(key) != value:
            raise ModError(f"{file_name}: {event.name} {key} is {params.get(key)!r}")
    if event.data.actor.v.params is None:
        raise ModError(f"{file_name}: {CHALLENGE_ACTOR} has no params")
    actor_params = event.data.actor.v.params.data
    for key, value in GAMEDATA_ACTOR_PARAMS:
        if actor_params.get(key) != value:
            raise ModError(f"{file_name}: {CHALLENGE_ACTOR} {key} is {actor_params.get(key)!r}")


def _expect_starter(actor, file_name: str) -> None:
    """The recent-talk actor binds the NPC. A flow that already declared it keeps that container."""
    params = actor.params.data if actor is not None and actor.params is not None else {}
    if params.get("ExceptionalBindType") != 1 or isinstance(params.get("ExceptionalBindType"), bool):
        raise ModError(
            f"{file_name}: {STARTER_ACTOR} ExceptionalBindType is {params.get('ExceptionalBindType')!r}. "
            "InitTalk turns the bound member, and an unbound starter is a different actor."
        )


def _expect_banner(step: Event, step_name: str, spec: RumorSpec, file_name: str) -> None:
    banner = step.data.nxt.v
    if not _banner_for(spec, step_name):
        if banner is not None and str(banner.name).startswith("AtO_RumorShow"):
            raise ModError(f"{file_name}: {step.name} shows a banner this step does not use")
        return
    expected = show_event_name(step_name)
    if banner is None or banner.name != expected:
        got = banner.name if banner is not None else None
        raise ModError(f"{file_name}: {step.name} continues to {got}, expected {expected}")
    _check_show(banner, spec.challenge, file_name, first=(step_name == "Step05"))
    if _nxt_name(banner) is not None:
        raise ModError(f"{file_name}: {banner.name} does not end the branch")


def _check_show(event: Event, challenge: str, file_name: str, *, first: bool) -> None:
    if str(event.data.actor_action.v) != SHOW_ACTION:
        raise ModError(f"{file_name}: {event.name} calls {event.data.actor_action.v}")
    params = _params(event)
    if params.get("ChallengeName") != challenge or params.get("IsFirstStep") is not first:
        raise ModError(f"{file_name}: {event.name} params are {params}")


def _walk_bools(event: Event | None, flags: list[str] | tuple[str, ...], truths: set[str], file_name: str) -> Event:
    for flag in flags:
        if event is None or not isinstance(event.data, SwitchEvent):
            raise ModError(f"{file_name}: the flag cascade ended before {flag}")
        if _params(event).get("GameDataName") != flag:
            raise ModError(f"{file_name}: expected a query of {flag}, found {_params(event).get('GameDataName')}")
        event = _case(event, 0 if flag in truths else 1, file_name)
    if event is None:
        raise ModError(f"{file_name}: the flag cascade has no leaf")
    return event


def _no_trapped_step(flowchart, file_name: str) -> None:
    for event in flowchart.events:
        if not event.name.startswith("AtO_Rumor"):
            continue
        step = _params(event).get("StepName")
        if step in TRAPPED_STEPS:
            raise ModError(f"{file_name}: {event.name} writes {step}. Adult Zelda compares that value.")


def _by_name(flowchart, file_name: str) -> dict[str, Event]:
    found: dict[str, Event] = {}
    for event in flowchart.events:
        if event.name in found:
            raise ModError(f"{file_name}: two events named {event.name}")
        found[event.name] = event
    return found


def _one_entry(flowchart, name: str, file_name: str) -> EntryPoint:
    matches = [entry for entry in flowchart.entry_points if entry.name == name]
    listed = ", ".join(entry.name for entry in flowchart.entry_points) or "none"
    if len(matches) != 1:
        raise ModError(f"{file_name}: expected one entry named {name!r}, found {len(matches)}. Entries: {listed}.")
    return matches[0]


def _one_event(flowchart, name: str, file_name: str) -> Event:
    matches = [event for event in flowchart.events if event.name == name]
    if len(matches) != 1:
        names = ", ".join(event.name for event in flowchart.events) or "none"
        raise ModError(f"{file_name}: expected one event named {name}, found {len(matches)}. Events: {names}.")
    return matches[0]


def _reaches(entry: EntryPoint, name: str) -> bool:
    return any(event.name == name for event in _reachable(entry.main_event.v))


def _case(event: Event, case: int, file_name: str) -> Event:
    link = event.data.cases.get(case)
    if link is None or link.v is None:
        raise ModError(f"{file_name}: {event.name} has no case {case}")
    return link.v


def _params(event: Event) -> dict:
    if event.data.params is None:
        return {}
    return event.data.params.data


def _nxt_name(event: Event) -> str | None:
    nxt = getattr(event.data, "nxt", None)
    if nxt is None or nxt.v is None:
        return None
    return nxt.v.name


def sample_rumor_flow_bytes(spec: RumorSpec) -> bytes:
    """A small flow with the hook each talk names. Built for the tests. It is not a game file."""
    flow = EventFlow()
    flow.name = "RumorSample"
    flowchart = Flowchart()
    flowchart.name = flow.name
    system = Actor()
    system.identifier = ActorIdentifier("EventSystemActor")
    system.concurrent_clips = 1
    system.params = make_params(GAMEDATA_ACTOR_PARAMS)
    wait = StringHolder("Demo_WaitFrame")
    system.actions.append(wait)
    flowchart.actors.append(system)
    events: list[Event] = []

    def action(name: str, nxt: Event | None) -> Event:
        event = Event()
        event.name = name
        event.data = ActionEvent()
        event.data.actor = make_rindex(system)
        event.data.actor_action = make_rindex(wait)
        event.data.params = make_params((("IsWaitFinish", True), ("Frame", 1)))
        event.data.nxt = make_index(nxt)
        events.append(event)
        return event

    def terminal(name: str) -> Event:
        found = [event for event in events if event.name == name]
        if found:
            return found[0]
        return action(name, None)

    entry_events: list[tuple[str, Event]] = []
    entry_hooks = [talk for talk in spec.talks if talk.hook == "entry"]
    if len(entry_hooks) > 1:
        raise ModError("the rumor sample has two entry hooks")
    for talk in spec.talks:
        if talk.hook == "entry" and spec.finale:
            # Mido's Talk is the default schedule fallback. On the dump it starts
            # at Event6, a BoolExp switch, not Mask_All.
            query = StringHolder("EventQueryIsGameDataBoolExp")
            system.queries.append(query)
            event = Event()
            event.name = "Event6"
            event.data = SwitchEvent()
            event.data.actor = make_rindex(system)
            event.data.actor_query = make_rindex(query)
            event.data.params = make_params((("GameDataName", "Meet_DekuTree_IsAfter_Step01_Exp"),))
            event.data.cases = {
                0: make_rindex(terminal("Event6True")),
                1: make_rindex(terminal("Event6False")),
            }
            events.append(event)
            entry_events.append((talk.entry, event))
        elif talk.hook == "entry":
            entry_events.append((talk.entry, action("Event4", None)))
        elif talk.hook == "next":
            rejoin = terminal(talk.rejoin)
            entry_events.append((talk.entry, action(talk.after, rejoin)))
        else:
            rejoin = action(f"EventVanilla_{talk.entry}", None)
            mask = Event()
            mask.name = f"EventMask_{talk.entry}"
            mask.data = SubFlowEvent()
            mask.data.res_flowchart_name = ""
            mask.data.entry_point_name = "Mask_All"
            mask.data.params = None
            mask.data.nxt = make_index(rejoin)
            events.append(mask)
            entry_events.append((talk.entry, mask))
    entry_events.append(("Later", action("VanillaDone", None)))
    if spec.finale:
        entry_events.append(("Near", action("VanillaNear", None)))
    flowchart.events.extend(events)
    for name, event in entry_events:
        entry = EntryPoint(name)
        entry.main_event = make_index(event)
        flowchart.entry_points.append(entry)
    flow.flowchart = flowchart
    return _write(flow, "rumor sample")
