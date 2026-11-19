"""Prove the committed example against an invented dump. No game files."""

from __future__ import annotations

import tempfile
from pathlib import Path

from after_ocarina.config import load_mod_meta
from after_ocarina.errors import ModError
from after_ocarina.evflow import read_flow_bytes
from after_ocarina.fixture import EVENT_SETTING_REL, KEEPER_LABEL, KEEPER_TEXT, VANILLA, read_label, write_romfs
from after_ocarina.gamedata import load_gamedata_edits
from after_ocarina.paths import EVENTS_PATH, RSTB_REL, SCENES_PATH, TEXT_DIR
from after_ocarina.pipeline import build_mod
from after_ocarina.rstb import load_table, target_entry
from after_ocarina.rsdb import load_rsdb_edits
from after_ocarina.sources import load_flow_patches, load_text_edits
from after_ocarina.zsio import unpack


def run_self_test() -> None:
    meta = load_mod_meta()
    edits = load_text_edits(TEXT_DIR)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        _roomy(tmp, meta.id, meta.name, edits)
        _tight(tmp, meta.id, meta.name)
    print(_real_gamedata_roundtrip())
    print(
        "self-test passed: example line applied, untouched line kept, "
        "boot hook prepended, size table raised for every changed file"
    )


def _roomy(tmp: Path, mod_id: str, mod_name: str, edits) -> None:
    romfs = tmp / "roomy-dump"
    write_romfs(romfs, edits, slack=100_000)
    out = tmp / "roomy-build" / "romfs"
    game = tmp / "Longshot"
    game.mkdir()
    result = build_mod(
        romfs,
        mod_id=mod_id,
        mod_name=mod_name,
        text_dir=TEXT_DIR,
        events_path=EVENTS_PATH,
        scenes_path=SCENES_PATH,
        out_dir=out,
        mods_root=game / "mods",
        install=True,
    )
    if not result.resource_size_table:
        raise ModError("roomy fixture left the size table unchanged; a changed file has to raise its entry")
    if result.installed is None or not (result.installed / "romfs").is_dir():
        raise ModError("roomy fixture did not install mods/<id>/romfs")
    if (result.installed / "exefs").exists():
        raise ModError("install created an exefs directory; Longshot would ignore it and this build must not write one")
    installed_files = {path.relative_to(result.installed / "romfs").as_posix() for path in (result.installed / "romfs").rglob("*") if path.is_file()}
    from after_ocarina.castle import load_scene_logics
    from after_ocarina.saria import load_placement
    from after_ocarina.stones import load_stones

    patches = load_flow_patches()
    placement = load_placement()
    scenes = load_scene_logics()
    stones = load_stones()
    expected = (
        {edit.pack for edit in edits}
        | {patch.file for patch in patches}
        | {edit.file for edit in load_rsdb_edits() if edit.file}
        | {edit.file for edit in load_gamedata_edits()}
        | {EVENT_SETTING_REL}
    )
    if placement.place:
        expected.add(placement.files[0])
    if placement.logic:
        expected.add(placement.logic)
    if placement.schedule:
        expected.add(placement.schedule)
    for spec in scenes:
        for item in spec.deletes:
            expected.add(item.logic)
        if spec.door:
            expected.add(spec.door)
    for stone in stones.stones:
        expected.add(stone.banc)
    from after_ocarina.tapestry import load_tapestry, output_rels

    tapestry = load_tapestry()
    if tapestry is not None:
        expected.update(output_rels(tapestry))
    expected.add(RSTB_REL)
    if installed_files != expected:
        raise ModError(f"installed files were {sorted(installed_files)}")
    for patch in patches:
        _expect_patched_flow(out / patch.file, patch)
    _expect_reservations(romfs, out, result.files)
    for edit in edits:
        _expect_message(out / edit.pack, edit.msbt, edit.label, _editable(edit.text))
        _expect_message(out / edit.pack, edit.msbt, KEEPER_LABEL, KEEPER_TEXT)
        if read_label(out / edit.pack, edit.msbt, edit.label) == VANILLA:
            raise ModError(f"{edit.label} was left unchanged")
        if edit.label == "AtO_SariaLine":
            if not edit.msbt.endswith("Npc_Kokiri_Saria_Child.msbt"):
                raise ModError(f"{edit.label} is in {edit.msbt}")
            try:
                read_label(out / edit.pack, "EventFlowMsg/Dm_Kokiri_0060.msbt", "AtO_SariaLine")
            except KeyError:
                pass
            else:
                raise ModError("AtO_SariaLine is still in Dm_Kokiri_0060")
    for rel in result.files:
        if rel.endswith(".rstbl.byml.zs"):
            _expect_rsdb(out / rel)
    _expect_gamedata(out / load_gamedata_edits()[0].file)
    if placement.place:
        _expect_saria_place(out / placement.files[0])
    if placement.logic:
        _expect_logic(out / placement.logic)
    if placement.schedule:
        _expect_schedule(out / placement.schedule)
    for spec in scenes:
        for item in spec.deletes:
            if item.instance == "Npc_Hylia_Soldier_027_Child":
                _expect_guard(out / item.logic)
            elif item.open:
                _expect_gate(out / item.logic, item.instance, item.open)
            elif item.node == 6 and item.node_name == "ActorLogicAttentionActor":
                _expect_sword(out / item.logic)
            else:
                raise ModError(f"{item.logic} has no self-test for {item.instance or item.node_name}")
        if spec.door:
            _expect_temple_door(out / spec.door)
    _expect_display_stones(out, stones)
    if tapestry is not None:
        _expect_tapestry(romfs, out, tapestry)


def _tight(tmp: Path, mod_id: str, mod_name: str) -> None:
    text = tmp / "long-text"
    text.mkdir()
    pack = "Mals/USen.Product.110.sarc.zs"
    msbt = "EventFlowMsg/AllSage.msbt"
    label = "Talk_03"
    long_line = "Navi. " + ("searching " * 400)
    (text / "long.yaml").write_text(
        "edits:\n"
        f"  - pack: {pack}\n"
        f"    msbt: {msbt}\n"
        f"    label: {label}\n"
        f"    text: {long_line!r}\n",
        encoding="utf-8",
    )
    romfs = tmp / "tight-dump"
    edits = load_text_edits(text)
    write_romfs(romfs, edits, slack=0)
    before = load_table(romfs / RSTB_REL).size_for(pack)
    patches = load_flow_patches()
    flow_before = {patch.file: load_table(romfs / RSTB_REL).size_for(patch.file) for patch in patches}
    out = tmp / "tight-build" / "romfs"
    result = build_mod(
        romfs,
        mod_id=mod_id,
        mod_name=mod_name,
        text_dir=text,
        events_path=EVENTS_PATH,
        scenes_path=SCENES_PATH,
        out_dir=out,
        install=False,
    )
    if not result.resource_size_table:
        raise ModError("tight fixture did not grow the resource size table")
    table = load_table(out / RSTB_REL)
    after = table.size_for(pack)
    if before is None or after is None or after <= before:
        raise ModError(f"table entry did not grow ({before} -> {after})")
    if table.size_for("Other/untouched.file") != 800:
        raise ModError("growing one entry changed an unrelated entry")
    _expect_reservations(romfs, out, result.files)
    for patch in patches:
        _expect_patched_flow(out / patch.file, patch)
        if table.size_for(patch.file) == flow_before[patch.file]:
            raise ModError(f"{patch.file} size-table entry was left at {flow_before[patch.file]}")
    _expect_message(out / pack, msbt, label, long_line)
    for rel in result.files:
        if rel.endswith(".rstbl.byml.zs"):
            _expect_rsdb(out / rel)


def _expect_reservations(romfs: Path, out: Path, rels: list[str]) -> None:
    before = load_table(romfs / RSTB_REL)
    after = load_table(out / RSTB_REL)
    for rel in rels:
        if not (romfs / rel).is_file():
            if after.size_for(rel) is None:
                raise ModError(f"{rel} has no resource size entry")
            continue
        old_raw, _old_id = unpack((romfs / rel).read_bytes(), {})
        new_raw, _new_id = unpack((out / rel).read_bytes(), {})
        entry = before.size_for(rel)
        want = None if entry is None else target_entry(entry, len(new_raw), old_unpacked=len(old_raw))
        got = after.size_for(rel)
        if want is None or got != want:
            raise ModError(f"{rel} entry is {got}, expected {want} (dump entry {entry})")


def _expect_tapestry(romfs: Path, out: Path, spec) -> None:
    import struct

    from after_ocarina.tapestry import (
        BOOT_PACK,
        MENU_TABLE,
        MODEL_INFO,
        TAPESTRY_ENTRY,
        TAPESTRY_PACK,
        _tools,
        model_rel,
    )

    byml, _zsmod, sarc_type = _tools()
    before = load_table(romfs / RSTB_REL)
    after = load_table(out / RSTB_REL)
    table_raw, _did = unpack((out / RSTB_REL).read_bytes(), {})
    n_hashed = struct.unpack_from("<I", table_raw, 14)[0]
    crcs = [struct.unpack_from("<I", table_raw, 22 + 8 * index)[0] for index in range(n_hashed)]
    if crcs != sorted(crcs):
        raise ModError("hashed resource entries are not sorted")
    for name in (spec.name, spec.name + "_Complete"):
        rel = model_rel(name)
        if after.size_for(rel) != TAPESTRY_ENTRY:
            raise ModError(f"{rel} entry is {after.size_for(rel)}, expected {TAPESTRY_ENTRY}")
        if before.size_for(rel) is not None:
            raise ModError(f"{rel} was already in the fixture table")
        raw, _model_id = unpack((out / rel).read_bytes(), {})
        if raw[:8] != b"FRES    " or len(raw) != 21184:
            raise ModError(f"{rel} is not the 21184-byte tapestry clone")
        if f"Obj_TapestryChallengeMenu.{name}".encode() not in raw:
            raise ModError(f"{rel} did not take the new model name")
        centre_x, centre_y = struct.unpack_from("<2f", raw, 0x4F0)
        if abs(centre_x - spec.cx) > 1e-5 or abs(centre_y - spec.cy) > 1e-5:
            raise ModError(f"{rel} centre is {(centre_x, centre_y)}, expected {(spec.cx, spec.cy)}")
    for path in (MENU_TABLE, MODEL_INFO):
        old = before.size_for(path)
        new = after.size_for(path)
        if old is None or new is None or new <= old:
            raise ModError(f"{path} entry did not grow ({old} -> {new})")
    boot, _boot_id = unpack((out / BOOT_PACK).read_bytes(), {})
    menu = byml.loads(sarc_type(boot).read(MENU_TABLE))
    row = menu["ChallengeMenuParamArray"][-1]
    if row.get("ChallengeShort") != spec.name:
        raise ModError(f"wall row is {row.get('ChallengeShort')!r}")
    if type(row.get("Category")) is not byml.U32:
        raise ModError(f"wall Category is {type(row.get('Category')).__name__}, expected U32")
    if abs(row["ActorPos"]["X"] - spec.cx) > 1e-5 or abs(row["ActorPos"]["Y"] - spec.cy) > 1e-5:
        raise ModError(f"wall ActorPos is {row['ActorPos']}")
    if row.get("CameraFovy") != 16.0:
        raise ModError("the cloned row did not keep CameraFovy")
    info_raw, _info_id = unpack((out / TAPESTRY_PACK).read_bytes(), {})
    models = byml.loads(sarc_type(info_raw).read(MODEL_INFO))["SubModels"]
    names = [item.get("FmdbName") for item in models[-2:]]
    if names != [spec.name, spec.name + "_Complete"]:
        raise ModError(f"SubModels ends with {names}")


def _expect_boot_hook(path: Path) -> None:
    raw, _did = unpack(path.read_bytes(), {})
    flow = read_flow_bytes(raw)
    flowchart = flow.flowchart
    if flowchart is None or not flowchart.entry_points:
        raise ModError(f"{path.name} has no entry point after the patch")
    hooked = flowchart.entry_points[0]
    if hooked.main_event.v is None or hooked.main_event.v.name != "AtO_CheckGanon":
        raise ModError(f"{path.name} did not prepend AtO_CheckGanon")
    expected_entries = {
        "Later": "VanillaDone",
        "AtO_OrigBoot": "VanillaDone",
        "AtO_Wake": "AtO_WakeBan",
        "AirStartUP": "VanillaDone",
    }
    others = [entry for entry in flowchart.entry_points if entry is not hooked]
    for entry in others:
        if entry.main_event.v is None or expected_entries.get(entry.name) != entry.main_event.v.name:
            got = None if entry.main_event.v is None else entry.main_event.v.name
            raise ModError(f"{path.name} entry {entry.name} starts at {got}")
    by_name = {event.name: event for event in flowchart.events}
    if any(name.startswith("AtO_Debug") or name.startswith("AtO_GanonCase") or name.startswith("AtO_BoolCase") for name in by_name):
        raise ModError(f"{path.name} still ships a rupee diagnostic")
    ganon = by_name.get("AtO_CheckGanon")
    flag_query = by_name.get("AtO_CheckEpilogue")
    if ganon is None or flag_query is None:
        raise ModError(f"{path.name} is missing a query")
    if ganon.data.cases[2].v.name != "AtO_CheckEpilogue" or ganon.data.cases[0].v.name != "VanillaDone":
        raise ModError(f"{path.name} does not continue only on int case 2 (greater)")
    if ganon.data.cases[1].v.name != "VanillaDone":
        raise ModError(f"{path.name} int case 1 (equal) should rejoin")
    if flag_query.data.cases[1].v.name != "AtO_SetEpilogue" or flag_query.data.cases[0].v.name != "AtO_RebanNavi":
        raise ModError(f"{path.name} bool case 1 should be false and case 0 should re-ban Navi")
    set_flag = by_name.get("AtO_SetEpilogue")
    if set_flag is None or set_flag.data.params is None:
        raise ModError(f"{path.name} is missing the epilogue flag set")
    value = set_flag.data.params.data.get("Value")
    flag = set_flag.data.params.data.get("GameDataName")
    if value is not True or flag != "HavePlayedEvent.TitleEventBase":
        raise ModError(f"{path.name} set {flag} to {value!r}")
    if set_flag.data.nxt.v is None or set_flag.data.nxt.v.name != "AtO_GanonGuard":
        raise ModError(f"{path.name} does not ask about Light and Darkness before the original boot")
    guard = by_name.get("AtO_GanonGuard")
    complete = by_name.get("AtO_GanonComplete")
    if guard is None or complete is None:
        raise ModError(f"{path.name} is missing the Light and Darkness step")
    if guard.data.cases[1].v.name != "AtO_GanonComplete" or guard.data.cases[0].v.name != "AtO_CallOrigBoot":
        raise ModError(f"{path.name} BoolExp case 1 should complete Light and Darkness, and case 0 should skip it")
    if complete.data.nxt.v is None or complete.data.nxt.v.name != "AtO_CallOrigBoot":
        raise ModError(f"{path.name} does not run the original boot after Light and Darkness")
    if complete.data.params.data.get("IsIgnoreChallengeProgressUI") is not True:
        raise ModError(f"{path.name} would show a challenge banner during boot")
    call = by_name.get("AtO_CallOrigBoot")
    if call is None or call.data.res_flowchart_name != "" or call.data.entry_point_name != "AtO_OrigBoot":
        raise ModError(f"{path.name} self-subflow is not (self)::AtO_OrigBoot")
    if call.data.nxt.v is None or call.data.nxt.v.name != "AtO_Participate":
        raise ModError(f"{path.name} era switch does not follow the original boot")
    orig = next((entry for entry in flowchart.entry_points if entry.name == "AtO_OrigBoot"), None)
    if orig is None or orig.main_event.v is None or orig.main_event.v.name != "VanillaDone":
        raise ModError(f"{path.name} AtO_OrigBoot does not start at the original event")
    look = by_name.get("AtO_LookForward")
    if look is None or look.data.nxt.v is not None:
        raise ModError(f"{path.name} era switch does not end")
    banc = by_name["AtO_BancChange"].data.params.data
    if banc.get("BootEventName") != "AtO_Epilogue_Wake" or banc.get("NextPosition") != [0.0, 0.0, 0.0]:
        raise ModError(f"{path.name} banc change is {banc!r}")
    if "AtO_BanNavi" in by_name:
        raise ModError(f"{path.name} still bans Navi before the banc change")
    wake = by_name.get("AtO_WakeBan")
    if wake is None or wake.data.params is None or wake.data.params.data.get("GameDataName") != "Navi_Ban":
        raise ModError(f"{path.name} wake does not ban Navi")
    if wake.data.params.data.get("Value") is not True:
        raise ModError(f"{path.name} wake does not set Navi_Ban true")
    line = by_name.get("AtO_WakeLine")
    if line is None or line.data.params is None or line.data.params.data.get("MessageId") != "EventFlowMsg/Dm_Kokiri_0060:AtO_WakeLine":
        raise ModError(f"{path.name} wake line is missing")
    if line.data.params.data.get("IsAnonymous") is not True or line.data.nxt.v is None or line.data.nxt.v.name != "AtO_ControlCamera":
        raise ModError(f"{path.name} wake line does not return the camera")
    camera = by_name.get("AtO_ControlCamera")
    if camera is None or camera.data.nxt.v is None or camera.data.nxt.v.name != "AtO_CheckStoneForest":
        raise ModError(f"{path.name} camera return does not lead to the spiritual stones")
    spirit = by_name.get("AtO_CheckMedalSpirit")
    if spirit is None or spirit.data.cases[1].v is None or spirit.data.cases[1].v.name != "AtO_CheckOcarinaTime":
        raise ModError(f"{path.name} the last medallion does not continue to the ocarina swap")
    light = by_name.get("AtO_RemoveMedalLight")
    if light is None or light.data.params is None or light.data.params.data.get("Actor") != "Item_Medal_Light" or light.data.params.data.get("Num") != -1:
        raise ModError(f"{path.name} does not remove the Light Medallion")
    equipped = by_name.get("AtO_EquipOcarinaFairy")
    if equipped is None or equipped.data.nxt.v is None or equipped.data.nxt.v.name != "AtO_WaitEquipmentFrame":
        raise ModError(f"{path.name} equip write does not recreate the equipment")
    if equipped.data.params is None or equipped.data.params.data.get("Value") != "Item_Ocarina_Fairy":
        raise ModError(f"{path.name} does not equip the Fairy Ocarina")
    waited = by_name.get("AtO_WaitEquipment")
    if waited is None or waited.data.nxt.v is None or waited.data.nxt.v.name != "AtO_KeepCamera":
        raise ModError(f"{path.name} ocarina swap does not finish before the keep-state")
    reban = by_name.get("AtO_RebanNavi")
    if reban is None or reban.data.nxt.v is None or reban.data.nxt.v.name != "AtO_LoadCheckStoneForest":
        raise ModError(f"{path.name} later load does not clear the spiritual stones")
    load_spirit = by_name.get("AtO_LoadCheckMedalSpirit")
    if load_spirit is None or load_spirit.data.cases[1].v is None or load_spirit.data.cases[1].v.name != "AtO_LoadCheckOcarinaTime":
        raise ModError(f"{path.name} a load with nothing to remove does not reach the ocarina swap")
    load_held_medal = by_name.get("AtO_LoadCheckMedalSpiritHeld")
    if load_held_medal is None or load_held_medal.data.cases[1].v is None or load_held_medal.data.cases[1].v.name != "AtO_LoadCheckOcarinaTimeHeld":
        raise ModError(f"{path.name} a load that removed a medallion does not share the ocarina autosave")
    load_held = by_name.get("AtO_LoadIdleHeldWaitEquipment")
    if load_held is None or load_held.data.nxt.v is None or load_held.data.nxt.v.name != "AtO_LoadAutosave":
        raise ModError(f"{path.name} a load that removed a stone does not share the ocarina autosave")
    load_equip = by_name.get("AtO_LoadEquipOcarinaFairy")
    load_wait = by_name.get("AtO_LoadWaitEquipment")
    load_save = by_name.get("AtO_LoadAutosave")
    if (
        load_equip is None
        or load_equip.data.nxt.v is None
        or load_equip.data.nxt.v.name != "AtO_LoadWaitEquipmentFrame"
        or load_wait is None
        or load_wait.data.nxt.v is None
        or load_wait.data.nxt.v.name != "AtO_LoadAutosave"
        or load_save is None
        or load_save.data.nxt.v is None
        or load_save.data.nxt.v.name != "VanillaDone"
    ):
        raise ModError(f"{path.name} later-load swap does not save before the original boot")
    idle = by_name.get("AtO_LoadIdleWaitEquipment")
    if idle is None or idle.data.nxt.v is None or idle.data.nxt.v.name != "VanillaDone":
        raise ModError(f"{path.name} a save that already lost Time still autosaves")
    if "AtO_AutosaveCaption" in by_name:
        raise ModError(f"{path.name} still has an autosave caption")
    kept = by_name.get("AtO_KeepCamera")
    if kept is None or str(kept.data.actor_action.v) != "EventTriggerSendKeepStateMessageToPlayerCamera":
        raise ModError(f"{path.name} is missing the camera keep-state")
    if kept.data.nxt.v is None or kept.data.nxt.v.name != "AtO_Autosave":
        raise ModError(f"{path.name} keep-state does not lead to the autosave")
    scale = None if kept.data.params is None else kept.data.params.data.get("AfterKeepEndInterpolateCountScale")
    frames = None if kept.data.params is None else kept.data.params.data.get("KeepManualModeFrame")
    if scale != 1.0 or frames != 120.0:
        raise ModError(f"{path.name} keep-state params are {scale!r}, {frames!r}")
    saved = by_name.get("AtO_Autosave")
    if saved is None or str(saved.data.actor_action.v) != "EventTriggerRequestAutoSave" or saved.data.nxt.v is not None:
        raise ModError(f"{path.name} autosave does not end the wake")
    for event in flowchart.events:
        data = event.data
        if data.__class__.__name__ == "SubFlowEvent" and "Navi" in f"{data.res_flowchart_name} {data.entry_point_name}":
            raise ModError(f"{path.name} still calls {data.res_flowchart_name}::{data.entry_point_name}")
    if hooked.variable_defs.get("SampleCount") is None or hooked.variable_defs["SampleCount"].value != 3:
        raise ModError(f"{path.name} dropped the entry point's variable")


def _expect_saria_place(path: Path) -> None:
    from after_ocarina.gamedata import _byml

    raw, _did = unpack(path.read_bytes(), {})
    actors = _byml().loads(raw)["Actors"]
    forest = [actor for actor in actors if actor.get("Gyaml") == "Npc_Kokiri_Saria_Child"]
    if len(forest) != 3:
        raise ModError(f"{path.name} has {len(forest)} forest Saria actors")
    clone = next(actor for actor in forest if actor.get("Name") == "OpeningSaria_AtO")
    original = next(actor for actor in forest if actor.get("Name") == "OpeningSaria")
    if clone["Presence"].get("GameDataName") != "HavePlayedEvent.TitleEventBase" or clone["Presence"].get("IsInverted") is not False:
        raise ModError(f"{path.name} clone presence is {clone['Presence']!r}")
    if original["Presence"].get("GameDataName") != "HavePlayedEvent.Dm_Kokiri_0060" or original["Presence"].get("IsInverted") is not True:
        raise ModError(f"{path.name} changed the opening Saria")
    if int(clone["Hash"]) == int(original["Hash"]) or clone.get("Dynamic") != original.get("Dynamic"):
        raise ModError(f"{path.name} did not copy her idle onto a new actor")
    if any(actor.get("Gyaml") == "Npc_Kokiri_Saria_Child_SacredForestMeadow" for actor in actors):
        raise ModError(f"{path.name} cloned the meadow Saria")


def _expect_logic(path: Path) -> None:
    from ainb import AINB

    from after_ocarina.logic import (
        ARRAY_INDEX,
        BOOL_OUTPUT,
        CREATE_PARAM,
        DATA_BOOL,
        DELETE_PARAM,
        GET_BOOL,
        HOLDER_INSTANCE,
        INSTANCE_PARAM,
        MULTI_PARAM,
        OR_NAME,
        TITLE_EVENT,
    )

    data = path.read_bytes()
    if data[:4] != b"AIB ":
        raise ModError(f"{path.name} was compressed; the logic module ships uncompressed")
    file = AINB.from_binary(data, reader_name=path.name)
    holder = next(
        node
        for node in file.nodes
        if any(
            prop.name == INSTANCE_PARAM and prop.default_value == HOLDER_INSTANCE
            for prop in node.properties.string_properties
        )
    )
    create = next(param for param in holder.params.bool_inputs if param.name == CREATE_PARAM)
    if create.source.src_output_index != 1:
        raise ModError(f"{path.name} Logic_Create reads output {create.source.src_output_index}")
    or_index = create.source.src_node_index
    or_node = file.nodes[or_index]
    if or_node.name != OR_NAME:
        raise ModError(f"{path.name} Logic_Create points at {or_node.name!r}")
    multi = next(param for param in or_node.params.bool_inputs if param.name == MULTI_PARAM)
    holds = [
        node
        for node in file.nodes
        if node.name == GET_BOOL
        and TITLE_EVENT
        in [prop.default_value for prop in node.properties.string_properties if prop.name == DATA_BOOL]
        + [param.default_value for param in node.params.string_inputs if param.name == DATA_BOOL]
    ]
    if len(holds) != 1:
        raise ModError(f"{path.name} has {len(holds)} {GET_BOOL} nodes for {TITLE_EVENT}")
    hold = holds[0]
    sources = [(source.src_node_index, source.src_output_index) for source in multi.source]
    if len(sources) != 2 or file.nodes[sources[0][0]].name != "OperateAND" or sources[0][1] != 1 or sources[1] != (hold.index, 0):
        raise ModError(f"{path.name} epilogue OR reads {sources}")
    and_index = sources[0][0]
    names = [prop.default_value for prop in hold.properties.string_properties if prop.name == DATA_BOOL]
    names.extend(param.default_value for param in hold.params.string_inputs if param.name == DATA_BOOL)
    if names != [TITLE_EVENT]:
        raise ModError(f"{path.name} {DATA_BOOL} is {names!r}")
    if not hold.params.bool_outputs or hold.params.bool_outputs[0].name != BOOL_OUTPUT:
        raise ModError(f"{path.name} {GET_BOOL} output is not {BOOL_OUTPUT}")
    if not any(param.name == ARRAY_INDEX for param in hold.params.int_inputs):
        raise ModError(f"{path.name} {GET_BOOL} dropped {ARRAY_INDEX}")
    if int(hold.flags) & 0x20 == 0:
        raise ModError(f"{path.name} dropped the unknown flag bit on {GET_BOOL}")
    plugs = [plug.node_index for plug in or_node.child_plugs]
    if plugs != [and_index]:
        raise ModError(f"{path.name} {OR_NAME} plug list is {plugs}, expected the AND")
    delete_param = next(
        param
        for node in file.nodes
        for param in node.params.bool_inputs
        if param.name == DELETE_PARAM and isinstance(param.source, list)
    )
    if delete_param.source[0].src_node_index != or_index:
        raise ModError(f"{path.name} standing Saria still deletes on the AND alone")
    if or_index not in holder.queries or and_index in holder.queries:
        raise ModError(f"{path.name} holder queries are {holder.queries}")
    if not holder.child_plugs or holder.child_plugs[0].node_index != or_index:
        raise ModError(f"{path.name} did not retarget the holder plug")


def _expect_schedule(path: Path) -> None:
    from after_ocarina.schedule import SIT_AS, SIT_FLAG, TALK_FLAG, TALK_ROW, TITLE_FLAG, _byml, _event_names

    data = path.read_bytes()
    if data[:4] == b"\x28\xb5\x2f\xfd":
        raise ModError(f"{path.name} was compressed; the schedule ships uncompressed")
    if data[:2] != b"YB":
        raise ModError(f"{path.name} is not a little-endian BYML")

    rows = _byml().loads(data)["ConditionalSchedule"]
    if rows[-1].get("ConditionFlag") != TITLE_FLAG:
        raise ModError(f"{path.name} last flag is {rows[-1].get('ConditionFlag')!r}")
    schedule = rows[-1].get("ActionSchedule")
    setting = schedule[0].get("Setting") if isinstance(schedule, list) and schedule and isinstance(schedule[0], dict) else None
    if not isinstance(setting, dict) or any(setting.get(name) != SIT_AS for name in ("ArrivalAS", "ResponseWaitAS", "WaitArrivalAS")):
        raise ModError(f"{path.name} epilogue ActionSchedule Setting is {setting!r}")
    names = _event_names(rows[-1].get("EventEntry"))
    if not any(TALK_ROW in name for name in names):
        raise ModError(f"{path.name} epilogue EventName is {names!r}")
    flags = [row.get("ConditionFlag") for row in rows]
    if flags.count(TITLE_FLAG) != 1 or SIT_FLAG not in flags or TALK_FLAG not in flags:
        raise ModError(f"{path.name} flags are {flags}")


def _expect_saria_talk(path: Path) -> None:
    raw, _did = unpack(path.read_bytes(), {})
    flow = read_flow_bytes(raw)
    flowchart = flow.flowchart
    if flowchart is None:
        raise ModError(f"{path.name} has no flowchart")
    by_name = {event.name: event for event in flowchart.events}
    check = by_name.get("AtO_CheckEpilogueTalk")
    if check is None:
        raise ModError(f"{path.name} is missing the epilogue talk gate")
    cases = {case: link.v.name if link.v is not None else None for case, link in check.data.cases.items()}
    if cases.get(0) != "AtO_RecentTalk" or cases.get(1) != "VanillaDone":
        raise ModError(f"{path.name} talk cases are {cases}")
    recent = by_name.get("AtO_RecentTalk")
    if recent is None or recent.data.actor.v is None or recent.data.actor.v.identifier.name != "Npc_EventStarter":
        raise ModError(f"{path.name} recent-talk query is not on Npc_EventStarter")
    recent_cases = {case: link.v.name if link.v is not None else None for case, link in recent.data.cases.items()}
    if recent_cases.get(0) != "AtO_SariaLine" or recent_cases.get(1) != "AtO_InitTalk":
        raise ModError(f"{path.name} recent-talk cases are {recent_cases}")
    init = by_name.get("AtO_InitTalk")
    if (
        init is None
        or init.data.res_flowchart_name != "InitTalk"
        or init.data.entry_point_name != "InitTalk_KokiriOnly"
        or init.data.nxt.v is None
        or init.data.nxt.v.name != "AtO_SariaLine"
    ):
        raise ModError(f"{path.name} is missing InitTalk::InitTalk_KokiriOnly")
    fairy = None if init.data.params is None else init.data.params.data.get("NpcFairy_MovingFlag")
    if fairy is not False:
        raise ModError(f"{path.name} NpcFairy_MovingFlag is {fairy!r}")
    talk = by_name.get("AtO_SariaLine")
    if talk is None or talk.data.params is None:
        raise ModError(f"{path.name} is missing Saria's line")
    if talk.data.actor.v is None or talk.data.actor.v.identifier.name != "Npc_EventStarter":
        raise ModError(f"{path.name} Saria line is not on Npc_EventStarter")
    if talk.data.params.data.get("MessageId") != "EventFlowMsg/Npc_Kokiri_Saria_Child:AtO_SariaLine":
        raise ModError(f"{path.name} Saria message is {talk.data.params.data.get('MessageId')!r}")
    if talk.data.params.data.get("IsAnonymous") is not False or talk.data.nxt.v is not None:
        raise ModError(f"{path.name} Saria line should be spoken by her and then end")
    if talk.data.params.data.get("MaintainPerformanceState") != 0:
        raise ModError(f"{path.name} Saria line is missing MaintainPerformanceState 0")
    hooked = next(entry for entry in flowchart.entry_points if entry.name == "HavePlayedEvent.Dm_Kokiri_0150_Talk")
    named = [flowchart.events[index].name for index in hooked._sub_flow_event_indices]
    if "AtO_InitTalk" not in named:
        raise ModError(f"{path.name} talk entry subflow table is missing AtO_InitTalk")
    entries = {entry.name: entry.main_event.v.name if entry.main_event.v is not None else None for entry in flowchart.entry_points}
    if entries.get("Later") != "VanillaDone" or entries.get("AirStartUP") != "VanillaDone":
        raise ModError(f"{path.name} moved an entry point that is not the talk: {entries}")
    if entries.get("HavePlayedEvent.Dm_Kokiri_0150_Talk") != "AtO_CheckEpilogueTalk":
        raise ModError(f"{path.name} did not hook HavePlayedEvent.Dm_Kokiri_0150_Talk: {entries}")
    if entries.get("Boot") != "VanillaDone":
        raise ModError(f"{path.name} moved Boot: {entries}")


def _expect_rsdb(path: Path) -> None:
    from after_ocarina.rsdb import _byml

    raw, _did = unpack(path.read_bytes(), {})
    root = _byml().loads(raw)
    ids = [row["__RowId"] for row in root]
    if ids != sorted(ids):
        raise ModError(f"{path.name} rows are not __RowId-sorted: {ids}")
    if any(row["__RowId"] == "DefaultBootEvent" for row in root):
        row = next(row for row in root if row["__RowId"] == "DefaultBootEvent")
        want = ["Common", "ReplaceEquipmentForEraChange", "DefaultBootEvent"]
        if row.get("SubFlowList") != want:
            raise ModError(f"{path.name} SubFlowList is {row.get('SubFlowList')!r}")
        wake = next(row for row in root if row["__RowId"] == "AtO_Epilogue_Wake")
        if wake.get("SubFlowList") != want or wake.get("IsPlayerWarpFirstFrame") is not True:
            raise ModError(f"{path.name} wake row is {wake!r}")
        if row.get("IsPlayerWarpFirstFrame") is not False:
            raise ModError(f"{path.name} changed the source warp flag")
    if any(row["__RowId"] == "BedBootEvent_Alive" and "EntryPointName" in row for row in root):
        bed = next(row for row in root if row["__RowId"] == "BedBootEvent_Alive")
        wake = next(row for row in root if row["__RowId"] == "AtO_Epilogue_Wake")
        if bed.get("EntryPointName") != "BedBootEvent" or bed.get("PlayerPos") != "LinkBed":
            raise ModError(f"{path.name} changed the vanilla bed row")
        if wake.get("EntryPointName") != "AtO_Wake" or wake.get("PlayerPos") != "LinkBed":
            raise ModError(f"{path.name} wake event setting is {wake!r}")
        if wake.get("FlowchartFileName") != "Work/Event/EventFlow/DefaultBootEvent.evfc":
            raise ModError(f"{path.name} flowchart is {wake.get('FlowchartFileName')!r}")
    challenge = "Work/Challenge/EscapeFromCrumblingGanonCastle.game__challenge__Challenge.gyml"
    if any(row["__RowId"] == challenge for row in root):
        row = next(row for row in root if row["__RowId"] == challenge)
        if "IsListAdventureLog" in row or row.get("IsCompleteTarget") is not True:
            raise ModError(f"{path.name} journal row is {row!r}")
        if type(row.get("Category")).__name__ != "U32" or int(row["Category"]) != 1200733368:
            raise ModError(f"{path.name} Category is {row.get('Category')!r}")
        if "Step02" not in row.get("Steps", []) or row.get("Steps") != [
            "NotReady",
            "Ready",
            "Reset",
            "Step01",
            "Step02",
            "Step05",
            "Complete",
        ]:
            raise ModError(f"{path.name} rewrote Steps: {row.get('Steps')!r}")


def _expect_patched_flow(path: Path, patch) -> None:
    if patch.rumor is not None:
        _expect_rumor(path, patch)
    elif patch.song_gate:
        _expect_saria_song(path)
    elif patch.epilogue_talk:
        _expect_saria_talk(path)
    elif patch.retarget_next:
        _expect_zelda_talk(path)
    else:
        _expect_boot_hook(path)


def _expect_rumor(path: Path, patch) -> None:
    from after_ocarina.rumor import verify_rumor

    raw, _did = unpack(path.read_bytes(), {})
    verify_rumor(read_flow_bytes(raw), patch)


def _expect_saria_song(path: Path) -> None:
    from after_ocarina.evflow import _verify_saria_song

    raw, _did = unpack(path.read_bytes(), {})
    flow = read_flow_bytes(raw)
    patch = next(item for item in load_flow_patches() if item.song_gate)
    _verify_saria_song(flow, patch)


def _expect_zelda_talk(path: Path) -> None:
    from after_ocarina.evflow import _verify_courtyard

    raw, _did = unpack(path.read_bytes(), {})
    flow = read_flow_bytes(raw)
    patch = next(item for item in load_flow_patches() if item.retarget_next)
    _verify_courtyard(flow, patch)


def _expect_guard(path: Path) -> None:
    from after_ocarina.logic import CREATE_PARAM, DATA_BOOL, DELETE_PARAM, GET_BOOL, TITLE_EVENT, _field_values, _instance_names, _load

    file = _load(path.read_bytes(), path.name)
    guards = [
        node
        for node in file.nodes
        if any(name == "Npc_Hylia_Soldier_027_Child" or name.startswith("Npc_Hylia_Soldier_027_Child") for name in _instance_names(node))
    ]
    if len(guards) != 1:
        raise ModError(f"{path.name} has {len(guards)} lost-child guards")
    delete = next(param for param in guards[0].params.bool_inputs if param.name == DELETE_PARAM)
    source = delete.source.src_node_index
    if source < 0 or file.nodes[source].name != GET_BOOL or _field_values(file.nodes[source], DATA_BOOL) != [TITLE_EVENT]:
        raise ModError(f"{path.name} did not delete the lost-child guard on {TITLE_EVENT}")
    create = next(param for param in guards[0].params.bool_inputs if param.name == CREATE_PARAM)
    if create.source.src_node_index >= 0 or create.default_value is not True:
        raise ModError(f"{path.name} changed Logic_Create")


def _expect_display_stones(out: Path, spec) -> None:
    from after_ocarina.gamedata import _byml
    from after_ocarina.logic import TITLE_EVENT
    from after_ocarina.stones import _HASHES

    byml = _byml()
    for stone in spec.stones:
        raw, _did = unpack((out / stone.banc).read_bytes(), {})
        actors = byml.loads(raw)["Actors"]
        found = [actor for actor in actors if actor.get("Name") == stone.name]
        if len(found) != 1:
            raise ModError(f"{stone.banc} has {len(found)} {stone.name}")
        actor = found[0]
        if actor.get("Gyaml") != stone.gyaml:
            raise ModError(f"{stone.name} gyaml is {actor.get('Gyaml')!r}")
        if "Links" in actor:
            raise ModError(f"{stone.name} still has Market links")
        presence = actor.get("Presence")
        if presence.get("GameDataName") != TITLE_EVENT or presence.get("IsInverted") is not False or presence.get("IsLazy") is not False:
            raise ModError(f"{stone.name} presence is {presence!r}")
        if int(actor["Hash"]) != _HASHES[stone.gyaml] or int(actor["Phive"]["Placement"]["ID"]) != int(actor["Hash"]):
            raise ModError(f"{stone.name} hash is {actor.get('Hash')!r}")
        translate = actor.get("Translate")
        y = stone.translate[1] + stone.y_offset
        want = (stone.translate[0], y, stone.translate[2])
        if not isinstance(translate, list) or len(translate) != 3 or any(abs(got - expected) > 0.001 for got, expected in zip(translate, want)):
            raise ModError(f"{stone.name} is at {translate!r}, expected {want}")
        rotate = actor.get("Rotate")
        want_rotate = stone.rotate if stone.rotate is not None else (0.0, 1.5, 0.0)
        if not isinstance(rotate, list) or len(rotate) != 3 or any(abs(got - expected) > 0.001 for got, expected in zip(rotate, want_rotate)):
            raise ModError(f"{stone.name} rotation is {rotate!r}, expected {want_rotate}")


def _expect_sword(path: Path) -> None:
    from after_ocarina.logic import CREATE_PARAM, DATA_BOOL, DELETE_PARAM, GET_BOOL, TITLE_EVENT, _field_values, _load

    file = _load(path.read_bytes(), path.name)
    if len(file.nodes) <= 6 or file.nodes[6].name != "ActorLogicAttentionActor":
        raise ModError(f"{path.name} node 6 is not the Master Sword")
    sword = file.nodes[6]
    delete = next(param for param in sword.params.bool_inputs if param.name == DELETE_PARAM)
    source = delete.source.src_node_index
    if source < 0 or file.nodes[source].name != GET_BOOL or _field_values(file.nodes[source], DATA_BOOL) != [TITLE_EVENT]:
        raise ModError(f"{path.name} did not delete the Master Sword on {TITLE_EVENT}")
    create = next(param for param in sword.params.bool_inputs if param.name == CREATE_PARAM)
    if create.source.src_node_index >= 0 or create.default_value is not True:
        raise ModError(f"{path.name} changed the Master Sword Logic_Create")


def _expect_temple_door(path: Path) -> None:
    from after_ocarina.logic import CREATE_PARAM, DATA_BOOL, GET_BOOL, TITLE_EVENT, _field_values, _load, _named_input, _only_source
    from after_ocarina.temple import (
        AND_RESULT,
        CLOSE_PARAM,
        DOOR_INDEX,
        FIX_OR,
        NOT_OUTPUT,
        OPEN_PARAM,
        SIGNAL_NODE,
        SIGNAL_OUTPUT,
        SKIP_PARAM,
        STONE_INDICES,
        _eval_reader,
        reduced_signal,
    )

    file = _load(path.read_bytes(), path.name)
    if file.nodes[SIGNAL_NODE].name != FIX_OR:
        raise ModError(f"{path.name} node {SIGNAL_NODE} is {file.nodes[SIGNAL_NODE].name!r}")
    close_param = _named_input(file.nodes[DOOR_INDEX], CLOSE_PARAM, path.name, DOOR_INDEX)
    close = _only_source(close_param, path.name, DOOR_INDEX, CLOSE_PARAM)
    if close.src_node_index >= 0 or close_param.default_value is not True:
        raise ModError(f"{path.name} isClose is no longer constant true")
    open_from = _only_source(_named_input(file.nodes[DOOR_INDEX], OPEN_PARAM, path.name, DOOR_INDEX), path.name, DOOR_INDEX, OPEN_PARAM)
    gate = file.nodes[open_from.src_node_index]
    if gate.name != "OperateAND" or open_from.src_output_index != AND_RESULT:
        raise ModError(f"{path.name} isOpen reads {gate.name} output {open_from.src_output_index}")
    sources = [
        (item.src_node_index, item.src_output_index)
        for item in next(param for param in gate.params.bool_inputs if param.name == "BoolMulti").source
    ]
    if sources[0] != (SIGNAL_NODE, SIGNAL_OUTPUT):
        raise ModError(f"{path.name} AND does not start from node {SIGNAL_NODE} output {SIGNAL_OUTPUT} ({sources})")
    not_index, not_output = sources[1]
    if file.nodes[not_index].name != "OperateOR" or not_output != NOT_OUTPUT:
        raise ModError(f"{path.name} NOT is node {not_index} {file.nodes[not_index].name} output {not_output}")
    flag_sources = [
        (item.src_node_index, item.src_output_index)
        for item in next(param for param in file.nodes[not_index].params.bool_inputs if param.name == "BoolMulti").source
    ]
    if len(flag_sources) != 1 or file.nodes[flag_sources[0][0]].name != GET_BOOL:
        raise ModError(f"{path.name} OperateOR does not read only the flag ({flag_sources})")
    if _field_values(file.nodes[flag_sources[0][0]], DATA_BOOL) != [TITLE_EVENT]:
        raise ModError(f"{path.name} flag query is {_field_values(file.nodes[flag_sources[0][0]], DATA_BOOL)}")
    for index, name in [(DOOR_INDEX, SKIP_PARAM), *[(stone, CREATE_PARAM) for stone in STONE_INDICES]]:
        source = _only_source(_named_input(file.nodes[index], name, path.name, index), path.name, index, name)
        if (source.src_node_index, source.src_output_index) != (open_from.src_node_index, AND_RESULT):
            raise ModError(f"{path.name} node {index} {name} was not rewired to the AND")
        plugs = [plug for group in file.nodes[index]._plugs for plug in group if getattr(plug, "name", None) == name]
        if not plugs or plugs[0].node_index != open_from.src_node_index:
            raise ModError(f"{path.name} node {index} {name} plug still points at the vanilla signal")
        if SIGNAL_NODE in (file.nodes[index].queries or []):
            raise ModError(f"{path.name} node {index} still queries node {SIGNAL_NODE}")
        for vanilla in (False, True):
            got = _eval_reader(file.nodes, index, name, vanilla=vanilla, flag=False)
            if got != reduced_signal(vanilla, False):
                raise ModError(f"{path.name} node {index} {name} is {got} for signal {vanilla} with the flag false")
            if _eval_reader(file.nodes, index, name, vanilla=vanilla, flag=True) is not False:
                raise ModError(f"{path.name} node {index} {name} stays true when the flag is true")


def _expect_gate(path: Path, closed: str, opened: str) -> None:
    from after_ocarina.logic import (
        CREATE_PARAM,
        DATA_BOOL,
        DELETE_PARAM,
        GET_BOOL,
        MULTI_PARAM,
        TITLE_EVENT,
        _field_values,
        _instance_names,
        _load,
        _named_input,
        _only_source,
    )

    file = _load(path.read_bytes(), path.name)
    names = [node.name for node in file.nodes[:4]]
    if names != [
        "ActorLogicSimpleActor",
        "HoldChallengeCurrentStepCheckOrder",
        "OperateOR",
        "ActorLogicSimpleActor",
    ]:
        raise ModError(f"{path.name} nodes start {names}")
    if "Step03" not in _field_values(file.nodes[1], "ChallengeStepName"):
        raise ModError(f"{path.name} step check is {_field_values(file.nodes[1], 'ChallengeStepName')}")
    closed_node = file.nodes[0]
    if _instance_names(closed_node) != [closed]:
        raise ModError(f"{path.name} closed gate is {_instance_names(closed_node)}")
    create = _only_source(_named_input(closed_node, CREATE_PARAM, path.name, 0), path.name, 0, CREATE_PARAM)
    if (create.src_node_index, create.src_output_index) != (2, 1):
        raise ModError(f"{path.name} closed {CREATE_PARAM} reads {(create.src_node_index, create.src_output_index)}")
    delete = _only_source(_named_input(closed_node, DELETE_PARAM, path.name, 0), path.name, 0, DELETE_PARAM)
    hold = delete.src_node_index
    if hold < 0 or file.nodes[hold].name != GET_BOOL or _field_values(file.nodes[hold], DATA_BOOL) != [TITLE_EVENT]:
        raise ModError(f"{path.name} did not delete the closed gate on {TITLE_EVENT}")
    open_node = file.nodes[3]
    if _instance_names(open_node) != [opened]:
        raise ModError(f"{path.name} open gate is {_instance_names(open_node)}")
    open_create = _only_source(_named_input(open_node, CREATE_PARAM, path.name, 3), path.name, 3, CREATE_PARAM)
    open_delete = _only_source(_named_input(open_node, DELETE_PARAM, path.name, 3), path.name, 3, DELETE_PARAM)
    or_index = open_create.src_node_index
    if file.nodes[or_index].name != "OperateOR" or open_create.src_output_index != 1:
        raise ModError(f"{path.name} open {CREATE_PARAM} is not the new OR True output")
    if (open_delete.src_node_index, open_delete.src_output_index) != (or_index, 0):
        raise ModError(f"{path.name} open {DELETE_PARAM} is not the new OR False output")
    multi = _named_input(file.nodes[or_index], MULTI_PARAM, path.name, or_index)
    sources = [(item.src_node_index, item.src_output_index) for item in multi.source]
    if sources != [(2, 0), (hold, 0)]:
        raise ModError(f"{path.name} open-gate OR reads {sources}")
    step = _named_input(file.nodes[2], MULTI_PARAM, path.name, 2)
    step_sources = [(item.src_node_index, item.src_output_index) for item in step.source]
    if step_sources != [(1, 2), (1, 0)]:
        raise ModError(f"{path.name} step OR changed to {step_sources}")


def _real_gamedata_roundtrip() -> str:
    """Re-encode the real GameDataList when a dump is configured. Skip otherwise."""
    from after_ocarina.config import load_user_config
    from after_ocarina.gamedata import roundtrip_big_endian
    from after_ocarina.paths import GAMEDATA_REL, ROOT
    from after_ocarina.zsio import dicts_for, unpack

    config = ROOT / "config.toml"
    if not config.is_file():
        return "gamedata round-trip: no config.toml, skipped"
    try:
        user = load_user_config(config)
    except ModError as exc:
        return f"gamedata round-trip: skipped ({exc})"
    romfs = user.pristine_romfs or user.romfs
    path = romfs / GAMEDATA_REL
    if not path.is_file():
        return "gamedata round-trip: dump not in this checkout, skipped"
    raw, _dict_id = unpack(path.read_bytes(), dicts_for(romfs))
    if raw[:2] != b"BY":
        raise ModError(f"{path.name} is not big-endian BYML")
    again = roundtrip_big_endian(raw)
    if again != raw:
        raise ModError(
            f"{path.name} round-trip changed the bytes ({len(raw)} -> {len(again)}). "
            "A BoolExp edit would rewrite more than the expression."
        )
    return f"gamedata round-trip: {path.name} unchanged ({len(raw)} bytes)"


def _expect_zelda_presence(root: dict, path: Path) -> None:
    from after_ocarina.saveinfo import flag_hash

    rows = root.get("Data", {}).get("BoolExp")
    if not isinstance(rows, list):
        raise ModError(f"{path.name} has no BoolExp list")
    wanted_hash = flag_hash("DoorOfTime_IsAfter_Step2_2_Exp")
    target = next((row for row in rows if int(row.get("Hash", -1)) == wanted_hash), None)
    if target is None:
        raise ModError(f"{path.name} is missing DoorOfTime_IsAfter_Step2_2_Exp")
    tail = target.get("Values", target.get("RPN", []))[-3:]
    wanted = flag_hash("HavePlayedEvent.TitleEventBase")
    if (
        len(tail) != 3
        or int(tail[0][0]) != 0
        or int(tail[0][1]) != wanted
        or [int(tail[1][0])] != [5]
        or [int(tail[2][0])] != [3]
    ):
        raise ModError(f"{path.name} BoolExp tail is {tail!r}")


def _expect_gamedata(path: Path) -> None:
    from after_ocarina.gamedata import _byml, _zsmod

    raw, _did = unpack(path.read_bytes(), {})
    if raw[:2] == b"BY":
        zsmod = _zsmod()
        if zsmod.byml_get(raw, "Data/Bool/1/Hash") != 3472889777:
            raise ModError(f"{path.name} did not keep the Navi_Ban hash")
        if zsmod.byml_get(raw, "Data/Bool/1/ResetTypeValue") != 8:
            raise ModError(f"{path.name} Navi_Ban reset type is not 8")
        if zsmod.byml_get(raw, "Data/Bool/0/ResetTypeValue") != 9 or zsmod.byml_get(raw, "Data/Int/0/ResetTypeValue") != 9:
            raise ModError(f"{path.name} changed a flag that is not Navi_Ban")
        if zsmod.byml_get(raw, "Data/Bool/2/ResetTypeValue") != 24:
            raise ModError(f"{path.name} changed the neighbor bool")
        from after_ocarina.gamedata import to_little_endian

        _expect_zelda_presence(_byml().loads(to_little_endian(raw)), path)
        return
    root = _byml().loads(raw)
    bools = root["Data"]["Bool"]
    changed = next(item for item in bools if int(item["Hash"]) == 3472889777)
    if int(changed["ResetTypeValue"]) != 8:
        raise ModError(f"{path.name} Navi_Ban reset type is {changed['ResetTypeValue']!r}")
    if int(bools[0]["ResetTypeValue"]) != 9 or root["Data"]["Int"][0]["ResetTypeValue"] != 9:
        raise ModError(f"{path.name} changed a flag that is not Navi_Ban")


def _expect_message(path: Path, msbt: str, label: str, expected: str) -> None:
    got = read_label(path, msbt, label)
    if got != expected:
        raise ModError(f"{msbt}:{label} is {got!r}, expected {expected!r}")


def _editable(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
