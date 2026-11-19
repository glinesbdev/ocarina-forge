"""Load the human-editable mod sources under src/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from after_ocarina.errors import ModError
from after_ocarina.paths import EVENTS_DIR, EVENTS_PATH, SCENES_PATH, TEXT_DIR

# GameDataActor calls the boot hook is allowed to emit. Param order is the order
# written into a new container. Query cases are every branch the game has: a
# missing case ends the flow, so the patcher wires each one.
#
# Param names are the ones taken from real flows for M2a. evflags.py only reads
# GameDataName (or GameData). EventTriggerAddOrSubGameDataInt with
# GameDataName/Index/Value added exactly Value rupees in game. The int query's
# parameters are GameDataName, Value, and Index. The bool query's are
# GameDataName and Index. EventQueryIsGameDataBoolExp takes GameDataName only.
# The boot hook uses it for Defeat_Ganon_IsAfter_Step09_Exp.
#
# Case numbers were measured on a cleared save (rupees 218 -> 259 -> 284):
# int query against Value: case 0 less, case 1 equal, case 2 greater.
# bool query: case 0 true, case 1 false. A bool query that names no continue
# case continues on BOOL_FALSE_CASE.
INT_LESS_CASE = 0
INT_EQUAL_CASE = 1
INT_GREATER_CASE = 2
BOOL_TRUE_CASE = 0
BOOL_FALSE_CASE = 1
CALLS: dict[str, dict] = {
    "EventQueryIsGameDataInt": {
        "kind": "query",
        "cases": (INT_LESS_CASE, INT_EQUAL_CASE, INT_GREATER_CASE),
        "params": (("GameDataName", "str"), ("Value", "int"), ("Index", "int")),
    },
    "EventQueryIsGameDataBool": {
        "kind": "query",
        "cases": (BOOL_TRUE_CASE, BOOL_FALSE_CASE),
        "params": (("GameDataName", "str"), ("Index", "int")),
    },
    "EventTriggerSetGameDataBool": {
        "kind": "action",
        "cases": (),
        "params": (("GameDataName", "str"), ("Index", "int"), ("Value", "bool")),
    },
    "EventTriggerAddOrSubGameDataInt": {
        "kind": "action",
        "cases": (),
        "params": (("GameDataName", "str"), ("Index", "int"), ("Value", "int")),
    },
    "EventQueryIsGameDataBoolExp": {
        "kind": "query",
        "cases": (BOOL_TRUE_CASE, BOOL_FALSE_CASE),
        "params": (("GameDataName", "str"),),
    },
    "EventExecuteProgressChallengeStep": {
        "kind": "action",
        "actor": "ChallengeSystemActor",
        "cases": (),
        "params": (
            ("ChallengeName", "str"),
            ("StepName", "str"),
            ("IsWaitEnd", "bool"),
            ("IsIgnoreCompletedChallenge", "bool"),
            ("IsIgnoreChallengeProgressUI", "bool"),
        ),
    },
}


def _action(actor: str, params: tuple[tuple[str, str], ...]) -> dict:
    return {"kind": "action", "actor": actor, "cases": (), "params": params}


# Era-switch calls. ``float3`` is three floats: evfl writes that as a float array
# (there is no vec3 type). ``text`` allows an empty string, which EventReserveBancChange
# uses for EventStarterName. A call with no params is stored with no container.
EPILOGUE_CALLS: dict[str, dict] = {
    "EventTriggerParticipateEvent": _action("Player_Event", ()),
    "EventTriggerSetGameDataBool": _action(
        "GameDataActor",
        CALLS["EventTriggerSetGameDataBool"]["params"],
    ),
    "EventTrigger_SetTime": _action(
        "EventWorldManagerController",
        (("TimeMin", "int"), ("TimeHour", "int")),
    ),
    "EventTriggerSoundSetSunSongInfo": _action("SoundSystemActor", (("NextTimeDivision", "int"),)),
    "EventTriggerBancRequestBeforehandLoad": _action(
        "GameSystemActor",
        (("BancPath", "str"), ("IsTargetRetry", "bool")),
    ),
    "EventReserveBancChange": _action(
        "GameSystemActor",
        (
            ("IsWaitEnd", "bool"),
            ("IsStartOnEvent", "bool"),
            ("NextBanc", "str"),
            ("NextStartPos", "str"),
            ("NextPosition", "float3"),
            ("NextStartRot", "float3"),
            ("FadePattern", "int"),
            ("BootEventName", "str"),
            ("EventStarterName", "text"),
            ("IsItemWarp", "bool"),
        ),
    ),
    "EventTriggerPlayerRequestLookAtTheFrontLock": _action(
        "Player_Event",
        (("IsImmediate", "bool"),),
    ),
}

# AtO_OrigBoot is the new entry point that starts at the event the hooked entry
# used to start at. AtO_CallOrigBoot is the same-file subflow that runs it.
ORIG_ENTRY = "AtO_OrigBoot"
ORIG_CALL = "AtO_CallOrigBoot"


@dataclass(frozen=True)
class TextEdit:
    pack: str
    msbt: str
    label: str
    text: str
    source: str
    note: str = ""
    # True adds the label. False replaces one that is already in the file.
    create: bool = False
    # True creates the msbt when the pack does not have it yet. The label is still created.
    new_file: bool = False

    def msbt_argument(self) -> str:
        """One ``file:label=text`` argument for tools/msbtmod.py set.

        ``create`` is handled before this runs: the label is added, then this
        argument replaces it. A missing label without ``create`` still stops
        the build.
        """
        body = self.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        return f"{self.msbt}:{self.label}={body}"


def load_text_edits(directory: Path | None = None) -> list[TextEdit]:
    directory = directory or TEXT_DIR
    if not directory.is_dir():
        raise ModError(f"Missing text source directory: {directory}")
    files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    if not files:
        raise ModError(f"No YAML text edits in {directory}")
    edits: list[TextEdit] = []
    seen: dict[tuple[str, str, str], str] = {}
    for path in files:
        for edit in _edits_in(path):
            key = (edit.pack, edit.msbt, edit.label)
            previous = seen.get(key)
            if previous is not None:
                raise ModError(
                    f"Duplicate text edit {edit.pack}:{edit.msbt}:{edit.label} in {previous} and {path.name}"
                )
            seen[key] = path.name
            edits.append(edit)
    return edits


def load_pending_lists(events_path: Path | None = None, scenes_path: Path | None = None) -> None:
    """Reject event and scene records until those milestones can apply them."""
    _expect_empty_list(events_path or EVENTS_PATH, "flows", "M2")
    _expect_empty_list(scenes_path or SCENES_PATH, "reroutes", "M3")


def _edits_in(path: Path) -> list[TextEdit]:
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
        extra = set(row) - {"pack", "msbt", "label", "text", "note", "create", "new_file"}
        if extra:
            raise ModError(f"{path.name}: edit {index} has unknown keys: {', '.join(sorted(extra))}")
        pack = _rel_path(row.get("pack"), path, index, "pack", suffix=".zs")
        msbt = _rel_path(row.get("msbt"), path, index, "msbt", suffix=".msbt")
        label = row.get("label")
        text = row.get("text")
        if not isinstance(label, str) or not label or any(ch in label for ch in ":\n\r"):
            raise ModError(f"{path.name}: edit {index} label must be a single line without ':'")
        if not isinstance(text, str) or text == "":
            raise ModError(f"{path.name}: edit {index} text must be a non-empty string")
        note = row.get("note", "")
        if not isinstance(note, str):
            raise ModError(f"{path.name}: edit {index} note must be a string")
        create = False
        if "create" in row:
            if not isinstance(row.get("create"), bool):
                raise ModError(f"{path.name}: edit {index} create must be true or false")
            create = row["create"]
        new_file = False
        if "new_file" in row:
            if not isinstance(row.get("new_file"), bool):
                raise ModError(f"{path.name}: edit {index} new_file must be true or false")
            new_file = row["new_file"]
        if new_file and not create:
            raise ModError(f"{path.name}: edit {index} new_file needs create: true")
        edits.append(
            TextEdit(
                pack=pack,
                msbt=msbt,
                label=label,
                text=text,
                source=path.name,
                note=note,
                create=create,
                new_file=new_file,
            )
        )
    return edits


def _expect_empty_list(path: Path, key: str, milestone: str) -> None:
    if not path.is_file():
        raise ModError(f"Missing {path}")
    data = _mapping(path)
    if set(data) != {key}:
        raise ModError(f"{path.name}: expected only the key {key!r}")
    rows = data[key]
    if not isinstance(rows, list):
        raise ModError(f"{path.name}: {key} must be a list")
    if rows:
        noun = "entry" if len(rows) == 1 else "entries"
        raise ModError(
            f"{path.name}: {key} has {len(rows)} {noun}, and {milestone} does not apply them yet. "
            "The boot hook lives in m2a-boot-hook.yaml. Leave this list empty."
        )


def _mapping(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ModError(f"{path.name}: the file must contain a mapping")
    return loaded


@dataclass(frozen=True)
class FlowStep:
    name: str
    call: str
    params: tuple[tuple[str, object], ...]
    continue_case: int | None
    # GameDataActor for the gate. Chain steps name their actor. A subflow leaves this empty.
    actor: str = "GameDataActor"
    # Set together for a call into another flowchart. Empty subflow is not used;
    # the same-file call is generated, and evfl stores it as an empty flowchart name.
    subflow: str | None = None
    entry_point: str | None = None


# Adult Zelda's AI compares these. A rumor step must not write them.
TRAPPED_STEPS = frozenset({"Step02", "Step03", "Step04", "Reset"})
RUMOR_STEPS = ((1, "Step05"), (2, "Step06"), (3, "Step07"), (4, "Step08"))


@dataclass(frozen=True)
class RumorInit:
    flow: str
    entry: str
    params: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class RumorTalk:
    entry: str
    hook: str
    after: str
    rejoin: str
    greet: str
    rumor: str
    nudge: str
    flag: str
    skip_init: bool


@dataclass(frozen=True)
class RumorSpec:
    actor: str
    challenge: str
    show_progress: bool
    finale: bool
    done_flag: str
    init: RumorInit | None
    flags: tuple[str, ...]
    steps: tuple[tuple[int, str], ...]
    talks: tuple[RumorTalk, ...]


@dataclass(frozen=True)
class FlowPatch:
    file: str
    entry_point: str | None
    steps: tuple[FlowStep, ...]
    source: str
    # When true, the patcher inserts an unconditional +1 rupee before the steps.
    debug_visible: bool = False
    # When true, every query case adds a distinct rupee amount, then the bool is set.
    # Replaces debug_visible. The normal gate below is not used for routing.
    debug_cases: bool = False
    # Normal gate: these int-query cases continue. None means the step's continue_case.
    ganon_cleared_cases: tuple[int, ...] | None = None
    # Normal gate: this bool-query case continues. That is the false case, which is 1.
    # None uses the step's continue_case, or BOOL_FALSE_CASE when the step omits it.
    bool_false_case: int | None = None
    # Stage flag. True runs the original boot, then ``chain``. False is the M2a gate.
    epilogue: bool = False
    chain: tuple[FlowStep, ...] = ()
    orig_entry: str = ORIG_ENTRY
    # True builds AtO_Wake from LinkSleepInBed and boots that. False keeps BedBootEvent_Alive
    # and sets Navi_Ban before the banc change.
    custom_wake: bool = False
    boot_event: str = "AtO_Epilogue_Wake"
    vanilla_boot_event: str = "BedBootEvent_Alive"
    # True shows SystemTextNPC.EventTalk after the wake camera returns.
    wake_dialogue: bool = False
    # True calls the keep-state message and EventTriggerRequestAutoSave.
    # The save is the last event in the wake.
    autosave: bool = False
    # False recreates equipment the way Npc_Dream does: wait one frame, sync the
    # pouch, wait until the equipment actors exist. True uses LinkSleepInBed's
    # delete-then-wait instead. The sync is the one that ships.
    ocarina_delete_equipment: bool = False
    # True gates one talk. Bool case 0 runs the recent-talk split. Case 1 rejoins.
    epilogue_talk: bool = False
    # Point this event's nxt at a new TitleEventBase query. Empty means unused.
    # The entry points stay put. rejoin_event is the nxt that was already there.
    retarget_next: str = ""
    rejoin_event: str = ""
    talk_entry: str = ""
    message_id: str = ""
    # True splits Saria's Song after the look-at. Case 0 plays a system line and
    # fades the telepathy sparkle. Case 1 continues to the vanilla child query.
    song_gate: bool = False
    from_event: str = ""
    talk_source: str = ""
    fade_source: str = ""
    # Epilogue rumors. None means this patch is not a rumor.
    rumor: RumorSpec | None = None


def load_flow_patches(directory: Path | None = None) -> list[FlowPatch]:
    """Load ``src/events/*.yaml`` except ``edits.yaml``, which stays an empty placeholder."""
    directory = directory or EVENTS_DIR
    if not directory.is_dir():
        raise ModError(f"Missing event source directory: {directory}")
    files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    patches: list[FlowPatch] = []
    seen: dict[str, str] = {}
    for path in files:
        if path.name == EVENTS_PATH.name:
            continue
        for patch in _patches_in(path):
            previous = seen.get(patch.file)
            if previous is not None:
                raise ModError(f"{patch.file} is patched by both {previous} and {path.name}")
            seen[patch.file] = path.name
            patches.append(patch)
    return patches


def _patches_in(path: Path) -> list[FlowPatch]:
    data = _mapping(path)
    unknown = set(data) - {"patches"}
    if unknown:
        raise ModError(f"{path.name}: unknown keys: {', '.join(sorted(unknown))}")
    rows = data.get("patches")
    if not isinstance(rows, list) or not rows:
        raise ModError(f"{path.name}: patches must be a non-empty list")
    patches = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ModError(f"{path.name}: patch {index} must be a mapping")
        extra = set(row) - {
            "file",
            "entry_point",
            "steps",
            "debug_visible",
            "debug_cases",
            "ganon_cleared_cases",
            "bool_false_case",
            "epilogue",
            "chain",
            "orig_entry",
            "custom_wake",
            "boot_event",
            "vanilla_boot_event",
            "wake_dialogue",
            "autosave",
            "ocarina_delete_equipment",
            "epilogue_talk",
            "retarget_next",
            "rejoin_event",
            "talk_entry",
            "message_id",
            "song_gate",
            "from_event",
            "talk_source",
            "fade_source",
            "rumor",
        }
        if extra:
            raise ModError(f"{path.name}: patch {index} has unknown keys: {', '.join(sorted(extra))}")
        rel = _rel_path(row.get("file"), path, index, "file", suffix=".bfevfl.zs")
        where = f"{path.name}: patch {index}"
        entry_point = _optional_name(row.get("entry_point", None), where, "entry_point")
        debug_visible = _optional_bool(row, "debug_visible", where)
        debug_cases = _optional_bool(row, "debug_cases", where)
        if debug_visible and debug_cases:
            raise ModError(f"{where}: debug_cases replaces debug_visible; set only one")
        ganon_cleared_cases = None
        if "ganon_cleared_cases" in row:
            ganon_cleared_cases = _case_list(row.get("ganon_cleared_cases"), f"{where} ganon_cleared_cases", (0, 1, 2))
        bool_false_case = None
        if "bool_false_case" in row:
            bool_false_case = _as_int(row.get("bool_false_case"), f"{where} bool_false_case")
            if bool_false_case not in (0, 1):
                raise ModError(f"{where} bool_false_case must be 0 or 1")
        retarget_next = row.get("retarget_next") or ""
        rejoin_event = row.get("rejoin_event") or ""
        talk_entry = row.get("talk_entry") or ""
        message_id = row.get("message_id") or ""
        song_gate = _optional_bool(row, "song_gate", where)
        from_event = row.get("from_event") or ""
        talk_source = row.get("talk_source") or ""
        fade_source = row.get("fade_source") or ""
        if "rumor" in row:
            stray = set(row) - {"file", "rumor"}
            if stray:
                raise ModError(f"{where}: rumor leaves {', '.join(sorted(stray))} unset")
        rumor = _rumor_spec(row.get("rumor"), where) if "rumor" in row else None
        if retarget_next and not isinstance(retarget_next, str):
            raise ModError(f"{where}: retarget_next must be an event name")
        if song_gate and retarget_next:
            raise ModError(f"{where}: song_gate does not use retarget_next")
        if rumor is not None:
            steps = []
        elif retarget_next:
            for key, value in (
                ("rejoin_event", rejoin_event),
                ("talk_entry", talk_entry),
                ("message_id", message_id),
            ):
                if not isinstance(value, str) or not value:
                    raise ModError(f"{where}: retarget_next needs {key}")
            if not message_id.endswith(":AtO_ZeldaLine"):
                raise ModError(f"{where}: message_id must end with :AtO_ZeldaLine")
            if "steps" in row:
                raise ModError(f"{where}: retarget_next leaves the steps list empty")
            steps = []
        elif song_gate:
            for key, value in (
                ("from_event", from_event),
                ("rejoin_event", rejoin_event),
                ("talk_source", talk_source),
                ("fade_source", fade_source),
                ("message_id", message_id),
            ):
                if not isinstance(value, str) or not value:
                    raise ModError(f"{where}: song_gate needs {key}")
            from_event = _call_name(from_event, where, "from_event")
            talk_source = _call_name(talk_source, where, "talk_source")
            fade_source = _call_name(fade_source, where, "fade_source")
            rejoin_event = _call_name(rejoin_event, where, "rejoin_event")
            if not message_id.endswith(":AtO_SariaSongLine"):
                raise ModError(f"{where}: message_id must end with :AtO_SariaSongLine")
            if "steps" in row:
                raise ModError(f"{where}: song_gate leaves the steps list empty")
            if talk_entry:
                raise ModError(f"{where}: song_gate does not use talk_entry")
            steps = []
        else:
            stray = [
                key
                for key in ("rejoin_event", "talk_entry", "message_id", "from_event", "talk_source", "fade_source")
                if row.get(key)
            ]
            if stray:
                raise ModError(
                    f"{where}: {', '.join(stray)} need retarget_next or song_gate"
                )
            steps = _steps(row.get("steps"), path, index, allow_query_end=row.get("epilogue_talk") is True)
        epilogue = _optional_bool(row, "epilogue", where)
        custom_wake = _optional_bool(row, "custom_wake", where)
        if custom_wake and not epilogue:
            raise ModError(f"{where}: custom_wake needs epilogue: true")
        wake_dialogue = _optional_bool(row, "wake_dialogue", where)
        autosave = _optional_bool(row, "autosave", where)
        ocarina_delete_equipment = _optional_bool(row, "ocarina_delete_equipment", where)
        if (wake_dialogue or autosave) and not custom_wake:
            raise ModError(f"{where}: wake_dialogue and autosave need custom_wake: true")
        epilogue_talk = _optional_bool(row, "epilogue_talk", where)
        if epilogue_talk and (epilogue or debug_visible or debug_cases):
            raise ModError(f"{where}: epilogue_talk does not run the era switch or a debug probe")
        if epilogue_talk and bool_false_case is not None:
            raise ModError(f"{where}: epilogue_talk continues on case 0. Leave bool_false_case unset")
        if retarget_next and (epilogue or epilogue_talk or custom_wake or debug_visible or debug_cases):
            raise ModError(f"{where}: retarget_next does not run the boot hook, the Saria talk, or a debug probe")
        if retarget_next and (entry_point is not None or bool_false_case is not None):
            raise ModError(f"{where}: retarget_next leaves the entry point and bool_false_case unset")
        if song_gate and (epilogue or epilogue_talk or custom_wake or debug_visible or debug_cases):
            raise ModError(f"{where}: song_gate does not run the boot hook, the Saria talk, or a debug probe")
        if song_gate and (entry_point is not None or bool_false_case is not None):
            raise ModError(f"{where}: song_gate leaves the entry point and bool_false_case unset")
        if epilogue_talk and (
            len(steps) != 1 or steps[0].call != "EventQueryIsGameDataBool" or steps[0].continue_case != 0
        ):
            raise ModError(
                f"{where}: epilogue_talk needs one EventQueryIsGameDataBool with continue_case 0 (true)"
            )
        boot_event = "AtO_Epilogue_Wake"
        if "boot_event" in row:
            boot_event = _call_name(row.get("boot_event"), where, "boot_event")
        vanilla_boot_event = "BedBootEvent_Alive"
        if "vanilla_boot_event" in row:
            vanilla_boot_event = _call_name(row.get("vanilla_boot_event"), where, "vanilla_boot_event")
        orig_entry = ORIG_ENTRY
        if "orig_entry" in row:
            orig_entry = _call_name(row.get("orig_entry"), where, "orig_entry")
        if epilogue:
            chain = _chain(row.get("chain"), path, index, {step.name for step in steps}, orig_entry)
        elif "chain" in row:
            raise ModError(f"{where}: chain is the era switch; set epilogue: true or remove the chain")
        else:
            chain = []
        patches.append(
            FlowPatch(
                file=rel,
                entry_point=entry_point,
                steps=tuple(steps),
                source=path.name,
                debug_visible=debug_visible,
                debug_cases=debug_cases,
                ganon_cleared_cases=ganon_cleared_cases,
                bool_false_case=bool_false_case,
                epilogue=epilogue,
                chain=tuple(chain),
                orig_entry=orig_entry,
                custom_wake=custom_wake,
                boot_event=boot_event,
                vanilla_boot_event=vanilla_boot_event,
                wake_dialogue=wake_dialogue,
                autosave=autosave,
                ocarina_delete_equipment=ocarina_delete_equipment,
                epilogue_talk=epilogue_talk,
                retarget_next=retarget_next,
                rejoin_event=rejoin_event,
                talk_entry=talk_entry,
                message_id=message_id,
                song_gate=song_gate,
                from_event=from_event,
                talk_source=talk_source,
                fade_source=fade_source,
                rumor=rumor,
            )
        )
    return patches


def _rumor_spec(value: object, where: str) -> RumorSpec:
    """One NPC file. ``finale`` is Mido. Every other file is one rumor and one flag."""
    if not isinstance(value, dict):
        raise ModError(f"{where}: rumor must be a mapping")
    extra = set(value) - {
        "actor",
        "challenge",
        "show_progress",
        "finale",
        "done_flag",
        "init",
        "flags",
        "steps",
        "talks",
    }
    if extra:
        raise ModError(f"{where}: rumor has unknown keys: {', '.join(sorted(extra))}")
    actor = _call_name(value.get("actor"), where, "actor")
    challenge = _call_name(value.get("challenge"), where, "challenge")
    finale = _optional_bool(value, "finale", where)
    show_progress = _optional_bool(value, "show_progress", where)
    if finale and show_progress:
        raise ModError(f"{where}: the finale does not show the first-rumor banner")
    done_flag = value.get("done_flag") or ""
    if done_flag:
        done_flag = _call_name(done_flag, where, "done_flag")
    if done_flag and not finale:
        raise ModError(f"{where}: done_flag is the finale's marker")
    flags = _name_list(value.get("flags"), where, "flags", 4)
    if done_flag in flags:
        raise ModError(f"{where}: done_flag is also a rumor flag")
    if finale:
        if "steps" in value:
            raise ModError(f"{where}: the finale steps to Complete. Leave steps unset")
        steps = ()
    else:
        steps = _rumor_steps(value.get("steps"), where)
    init = _rumor_init(value.get("init"), where) if "init" in value else None
    talks = _rumor_talks(value.get("talks"), where, finale=finale, flags=flags)
    needs_init = any(not talk.skip_init for talk in talks)
    if needs_init and init is None:
        raise ModError(f"{where}: a first talk needs init")
    if not needs_init and init is not None:
        raise ModError(f"{where}: init already ran. Leave init unset")
    return RumorSpec(
        actor=actor,
        challenge=challenge,
        show_progress=show_progress,
        finale=finale,
        done_flag=done_flag,
        init=init,
        flags=flags,
        steps=steps,
        talks=talks,
    )


def _rumor_steps(value: object, where: str) -> tuple[tuple[int, str], ...]:
    if not isinstance(value, dict) or not value:
        raise ModError(f"{where}: steps must map 1, 2, 3, and 4 to Step05, Step06, Step07, and Step08")
    got = []
    for key, item in value.items():
        if isinstance(key, bool) or not isinstance(key, int):
            raise ModError(f"{where}: step counts must be integers")
        if not isinstance(item, str) or not item:
            raise ModError(f"{where}: step {key} must be a step name")
        if item in TRAPPED_STEPS:
            raise ModError(f"{where}: {item} is compared by adult Zelda. Use Step05 through Step08")
        got.append((key, item))
    if tuple(sorted(got)) != RUMOR_STEPS:
        raise ModError(f"{where}: steps must be 1 Step05, 2 Step06, 3 Step07, 4 Step08")
    return RUMOR_STEPS


def _rumor_init(value: object, where: str) -> RumorInit:
    if not isinstance(value, dict):
        raise ModError(f"{where}: init must be a mapping")
    extra = set(value) - {"flow", "entry", "params"}
    if extra:
        raise ModError(f"{where}: init has unknown keys: {', '.join(sorted(extra))}")
    flow = _call_name(value.get("flow"), where, "init flow")
    entry = _call_name(value.get("entry"), where, "init entry")
    params = value.get("params", {})
    if not isinstance(params, dict):
        raise ModError(f"{where}: init params must be a mapping")
    pairs = []
    for key, item in params.items():
        if not isinstance(key, str) or not key:
            raise ModError(f"{where}: init param names must be strings")
        if isinstance(item, bool) or isinstance(item, int) or isinstance(item, str):
            pairs.append((key, item))
            continue
        raise ModError(f"{where}: init param {key} must be a bool, int, or string")
    return RumorInit(flow=flow, entry=entry, params=tuple(pairs))


def _rumor_talks(value: object, where: str, *, finale: bool, flags: tuple[str, ...]) -> tuple[RumorTalk, ...]:
    if not isinstance(value, list) or not value:
        raise ModError(f"{where}: talks must be a non-empty list")
    if not finale and len(value) != 1:
        raise ModError(f"{where}: a rumor file has one talk. The finale may have several")
    talks = []
    entries: set[str] = set()
    for index, row in enumerate(value, start=1):
        spot = f"{where} talk {index}"
        if not isinstance(row, dict):
            raise ModError(f"{spot} must be a mapping")
        extra = set(row) - {"entry", "hook", "after", "rejoin", "greet", "rumor", "nudge", "flag", "skip_init"}
        if extra:
            raise ModError(f"{spot} has unknown keys: {', '.join(sorted(extra))}")
        entry = _call_name(row.get("entry"), spot, "entry")
        if entry in entries:
            raise ModError(f"{spot} repeats {entry}")
        entries.add(entry)
        hook = row.get("hook")
        if hook not in ("entry", "next", "mask"):
            raise ModError(f"{spot} hook must be entry, next, or mask")
        after = row.get("after") or ""
        rejoin = row.get("rejoin") or ""
        if after:
            after = _call_name(after, spot, "after")
        if rejoin:
            rejoin = _call_name(rejoin, spot, "rejoin")
        if hook == "next" and (not after or not rejoin):
            raise ModError(f"{spot}: hook next needs after and rejoin")
        if hook != "next" and (after or rejoin):
            raise ModError(f"{spot}: {hook} does not take after or rejoin")
        skip_init = _optional_bool(row, "skip_init", spot)
        greet = row.get("greet") or ""
        rumor = _message_id(row.get("rumor"), spot, "rumor")
        nudge = row.get("nudge") or ""
        flag = row.get("flag") or ""
        if finale:
            if skip_init or flag or greet:
                raise ModError(f"{spot}: the finale has a nudge and a rumor, and it calls InitTalk itself")
            nudge = _message_id(nudge, spot, "nudge")
        else:
            if nudge:
                raise ModError(f"{spot}: nudge is the finale's line")
            greet = _message_id(greet, spot, "greet")
            flag = _call_name(flag, spot, "flag")
            if flag not in flags:
                raise ModError(f"{spot}: {flag} is not one of the four rumor flags")
        talks.append(
            RumorTalk(
                entry=entry,
                hook=hook,
                after=after,
                rejoin=rejoin,
                greet=greet,
                rumor=rumor,
                nudge=nudge,
                flag=flag,
                skip_init=skip_init,
            )
        )
    if finale:
        first = talks[0]
        for talk in talks[1:]:
            if talk.nudge != first.nudge or talk.rumor != first.rumor:
                raise ModError(f"{where}: finale talks share one nudge and one rumor")
    return tuple(talks)


def _message_id(value: object, where: str, key: str) -> str:
    if not isinstance(value, str) or value.count(":") != 1 or not value.startswith("EventFlowMsg/"):
        raise ModError(f"{where} {key} must be EventFlowMsg/<file>:<label>")
    label = value.split(":", 1)[1]
    if not label.startswith("AtO_") or not label.replace("_", "").isalnum():
        raise ModError(f"{where} {key} label must start with AtO_")
    return value


def _name_list(value: object, where: str, key: str, size: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise ModError(f"{where} {key} must be {size} names")
    names = []
    for item in value:
        names.append(_call_name(item, where, key))
    if len(set(names)) != len(names):
        raise ModError(f"{where} {key} repeats a name")
    return tuple(names)


def _steps(value: object, path: Path, patch_index: int, *, allow_query_end: bool = False) -> list[FlowStep]:
    where = f"{path.name}: patch {patch_index}"
    if not isinstance(value, list) or not value:
        raise ModError(f"{where} steps must be a non-empty list")
    steps: list[FlowStep] = []
    names: set[str] = set()
    for index, row in enumerate(value, start=1):
        step_where = f"{where} step {index}"
        if not isinstance(row, dict):
            raise ModError(f"{step_where} must be a mapping")
        extra = set(row) - {"name", "call", "params", "continue_case", "actor"}
        if extra:
            raise ModError(f"{step_where} has unknown keys: {', '.join(sorted(extra))}")
        name = _call_name(row.get("name"), step_where, "name")
        if name in names:
            raise ModError(f"{step_where} reuses the event name {name}")
        names.add(name)
        call = row.get("call")
        if call not in CALLS:
            allowed = ", ".join(sorted(CALLS))
            raise ModError(f"{step_where} call must be one of: {allowed}")
        spec = CALLS[call]
        continue_case = row.get("continue_case", None)
        if spec["kind"] == "query":
            if continue_case is None and call == "EventQueryIsGameDataBool":
                # False is case 1. Omitting continue_case continues on that case.
                case = None
            elif continue_case is None:
                raise ModError(f"{step_where} needs continue_case (one of {list(spec['cases'])})")
            else:
                case = _as_int(continue_case, f"{step_where} continue_case")
                if case not in spec["cases"]:
                    raise ModError(f"{step_where} continue_case must be one of {list(spec['cases'])}")
        else:
            if continue_case is not None:
                raise ModError(f"{step_where} is an action and cannot take continue_case")
            case = None
        params = _params(row.get("params"), spec["params"], step_where)
        expected_actor = spec.get("actor", "GameDataActor")
        if expected_actor != "GameDataActor" and "actor" not in row:
            raise ModError(f"{step_where} needs actor: {expected_actor}")
        if "actor" in row:
            actor_name = _call_name(row.get("actor"), step_where, "actor")
        else:
            actor_name = expected_actor
        if actor_name != expected_actor:
            raise ModError(f"{step_where} actor must be {expected_actor}")
        steps.append(FlowStep(name=name, call=call, params=params, continue_case=case, actor=actor_name))
    if CALLS[steps[-1].call]["kind"] != "action" and not allow_query_end:
        raise ModError(f"{where}: the last step must be an action, so the chain can rejoin the original event")
    return steps


def _params(value: object, shape: tuple[tuple[str, str], ...], where: str) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, dict):
        raise ModError(f"{where} params must be a mapping")
    expected = {name for name, _kind in shape}
    if set(value) != expected:
        missing = expected - set(value)
        extra = set(value) - expected
        detail = []
        if missing:
            detail.append("missing " + ", ".join(sorted(missing)))
        if extra:
            detail.append("unknown " + ", ".join(sorted(extra)))
        raise ModError(f"{where} params: {'; '.join(detail)}")
    pairs = []
    for name, kind in shape:
        raw = value[name]
        if kind == "int":
            typed: object = _as_int(raw, f"{where} params.{name}")
        elif kind == "bool":
            typed = _as_bool(raw, f"{where} params.{name}")
        elif kind == "str":
            typed = _as_str(raw, f"{where} params.{name}")
        elif kind == "text":
            typed = _as_text(raw, f"{where} params.{name}")
        elif kind == "float3":
            typed = _as_float3(raw, f"{where} params.{name}")
        else:
            raise ModError(f"{where} params.{name} has an unknown type {kind}")
        pairs.append((name, typed))
    return tuple(pairs)


def _chain(
    value: object,
    path: Path,
    patch_index: int,
    gate_names: set[str],
    orig_entry: str,
) -> list[FlowStep]:
    """Epilogue steps. A ``subflow`` step calls another flowchart; every other step is an action."""
    where = f"{path.name}: patch {patch_index} chain"
    if not isinstance(value, list) or not value:
        raise ModError(f"{where} must be a non-empty list")
    steps: list[FlowStep] = []
    names = set(gate_names)
    names.add(ORIG_CALL)
    if orig_entry in names:
        raise ModError(f"{where}: orig_entry {orig_entry} is already an event name")
    for index, row in enumerate(value, start=1):
        step_where = f"{where} step {index}"
        if not isinstance(row, dict):
            raise ModError(f"{step_where} must be a mapping")
        if "subflow" in row:
            extra = set(row) - {"name", "subflow", "entry"}
            if extra:
                raise ModError(f"{step_where} has unknown keys: {', '.join(sorted(extra))}")
            name = _unique_name(row.get("name"), step_where, names)
            flowchart = _call_name(row.get("subflow"), step_where, "subflow")
            entry = _call_name(row.get("entry"), step_where, "entry")
            steps.append(
                FlowStep(
                    name=name,
                    call="subflow",
                    params=(),
                    continue_case=None,
                    actor="",
                    subflow=flowchart,
                    entry_point=entry,
                )
            )
            continue
        extra = set(row) - {"name", "actor", "call", "params"}
        if extra:
            raise ModError(f"{step_where} has unknown keys: {', '.join(sorted(extra))}")
        name = _unique_name(row.get("name"), step_where, names)
        call = row.get("call")
        if call not in EPILOGUE_CALLS:
            allowed = ", ".join(sorted(EPILOGUE_CALLS))
            raise ModError(f"{step_where} call must be one of: {allowed}")
        spec = EPILOGUE_CALLS[call]
        if row.get("actor") != spec["actor"]:
            raise ModError(f"{step_where} actor must be {spec['actor']}")
        if not spec["params"]:
            if "params" in row and row.get("params") not in (None, {}):
                raise ModError(f"{step_where} takes no parameters")
            params: tuple[tuple[str, object], ...] = ()
        else:
            params = _params(row.get("params"), spec["params"], step_where)
        steps.append(
            FlowStep(name=name, call=call, params=params, continue_case=None, actor=spec["actor"])
        )
    if steps[-1].subflow is not None:
        raise ModError(f"{where}: the last step must be an action, so the era switch can end")
    return steps


def _unique_name(value: object, where: str, names: set[str]) -> str:
    name = _call_name(value, where, "name")
    if name in names:
        raise ModError(f"{where} reuses the event name {name}")
    names.add(name)
    return name


def _optional_bool(row: dict, key: str, where: str) -> bool:
    if key not in row:
        return False
    return _as_bool(row.get(key), f"{where} {key}")


def _case_list(value: object, where: str, allowed: tuple[int, ...]) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ModError(f"{where} must be a non-empty list")
    cases: list[int] = []
    for item in value:
        case = _as_int(item, where)
        if case not in allowed:
            raise ModError(f"{where} must be one of {list(allowed)}")
        if case in cases:
            raise ModError(f"{where} repeats {case}")
        cases.append(case)
    return tuple(cases)


def _optional_name(value: object, where: str, key: str) -> str | None:
    if value is None:
        return None
    return _call_name(value, where, key)


def _call_name(value: object, where: str, key: str) -> str:
    """An event or entry-point name. Entry points may contain dots, as in HavePlayedEvent.Dm_Kokiri_0150_Talk."""
    body = value.replace("_", "").replace(".", "") if isinstance(value, str) else ""
    if not isinstance(value, str) or not body.isalnum() or not value[0].isalpha() or value.startswith(".") or value.endswith("."):
        raise ModError(f"{where} {key} must be a letter followed by letters, digits, underscores, or dots")
    return value


def _as_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModError(f"{where} must be an integer")
    return value


def _as_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ModError(f"{where} must be true or false")
    return value


def _as_str(value: object, where: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ModError(f"{where} must be a non-empty string")
    return value


def _as_text(value: object, where: str) -> str:
    """A string parameter that may be empty. EventReserveBancChange's EventStarterName is empty."""
    if not isinstance(value, str):
        raise ModError(f"{where} must be a string")
    return value


def _as_float3(value: object, where: str) -> tuple[float, float, float]:
    """Three floats. evfl stores this as a float array; a list of ints would be the wrong type."""
    if not isinstance(value, list) or len(value) != 3:
        raise ModError(f"{where} must be a list of three numbers")
    numbers = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ModError(f"{where} must be a list of three numbers")
        numbers.append(float(item))
    return (numbers[0], numbers[1], numbers[2])


def _rel_path(value: object, path: Path, index: int, key: str, suffix: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModError(f"{path.name}: item {index} {key} must be a relative path")
    parts = Path(value).parts
    if "\\" in value or value.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ModError(f"{path.name}: item {index} {key} must be a relative forward-slash path")
    if not value.endswith(suffix):
        raise ModError(f"{path.name}: item {index} {key} must end with {suffix}")
    return value
