"""The behavior graph keeps dump ids and shows undecoded nodes on the raw layer."""

import pytest

from ocarina_forge.actors import format_hash
from ocarina_forge.behavior import (
    FADO_AI,
    friendly_name,
    load_actor_graph,
    make_op,
    mint_id,
    sample_ai_bytes,
    sample_flow_bytes,
    sample_schedule_bytes,
)
from ocarina_forge.dump import Dump
from ocarina_forge.errors import BancError
from ocarina_forge.fixtures import FADO, build_fixture
from ocarina_forge.formats.byml import dumps
from ocarina_forge.formats.sarc import pack


def test_fado_graph_is_stable_and_keeps_the_undecoded_node(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    graph = load_actor_graph(dump, gyaml="Npc_Kokiri_F_01")
    again = load_actor_graph(dump, gyaml="Npc_Kokiri_F_01")
    assert [node["id"] for node in graph["nodes"]] == [node["id"] for node in again["nodes"]]
    assert [edge["id"] for edge in graph["edges"]] == [edge["id"] for edge in again["edges"]]
    assert graph["ops"] == []
    assert graph["readOnly"] is True
    by_raw = {node["raw"]: node for node in graph["nodes"]}
    flag = by_raw["HoldGameDataGetBool"]
    assert flag["name"] == "Read flag"
    assert flag["known"] is True
    assert flag["decoded"] is True
    assert any(field["value"] == "Kokiri_FadoMet" for field in flag["fields"])
    spawn = by_raw["ActorLogicSimpleActor"]
    assert spawn["name"] == "Spawn actor"
    assert any(edge["from"].startswith(flag["id"]) and edge["to"].startswith(spawn["id"]) for edge in graph["edges"])
    mystery = by_raw["MysteryNode"]
    assert mystery["name"] == "Mystery Node"
    assert mystery["known"] is False
    assert mystery["decoded"] is True
    assert any(node["decoded"] is False and node["raw"].startswith("node ") for node in graph["nodes"])
    assert any(node["decoded"] is False and "broken" in node["raw"] for node in graph["nodes"])
    assert any(node["name"] == "When ChildDay" for node in graph["nodes"])
    assert any(node["name"] == "Play Act_Wait" for node in graph["nodes"])
    assert any(node["name"] == "Talk Npc_Kokiri_F_01_Talk" for node in graph["nodes"])
    assert any(node["raw"] == "row 2" and node["decoded"] is False for node in graph["nodes"])
    assert any(node["name"] == "Enter OnPlayerTalk" for node in graph["nodes"])
    assert any(node["name"] == "Talk" and node["kind"] == "event" for node in graph["nodes"])
    kinds = {source["kind"] for source in graph["sources"]}
    assert kinds == {"ainb", "schedule", "event"}
    assert any(source["path"] == FADO_AI for source in graph["sources"])
    assert f"pack Pack/Actor/Npc_Kokiri_F_01.pack.zs" in "\n".join(graph["trace"])


def test_a_second_draw_does_not_renumber_and_ops_use_those_ids(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    graph = load_actor_graph(dump, banc="Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs", actor_id=format_hash(FADO))
    assert graph["gyaml"] == "Npc_Kokiri_F_01"
    taken = {node["id"] for node in graph["nodes"]} | {pin["id"] for node in graph["nodes"] for pin in node["pins"]}
    taken |= {edge["id"] for edge in graph["edges"]}
    minted = mint_id("node", taken)
    assert minted not in taken
    assert mint_id("node", taken) == minted
    flag = next(node for node in graph["nodes"] if node["raw"] == "HoldGameDataGetBool")
    edited = make_op("set field", id=flag["id"], field="DataBool", value="Kokiri_FadoLeft")
    assert edited["id"] == flag["id"]
    added = make_op("add node", id=minted, graph=graph["sources"][0]["id"], name="Wait", type="Demo_WaitFrame")
    assert added["op"] == "add node"
    linked = make_op("connect", id="edge:1", **{"from": flag["pins"][0]["id"], "to": minted})
    assert linked["op"] == "connect"
    assert graph["ops"] == []
    try:
        make_op("replace file", id="nope")
    except ValueError as exc:
        assert "add node" in str(exc)
    else:
        raise AssertionError("a whole-file replacement is not a graph op")


def test_a_missing_actor_names_close_packs(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    with pytest.raises(BancError) as caught:
        load_actor_graph(dump, gyaml="Npc_Kokiri_F")
    message = str(caught.value)
    assert "Pack/Actor/Npc_Kokiri_F.pack.zs" in message
    assert "Npc_Kokiri_F_01" in message
    assert "Close matches" in message


def test_retail_npc_files_live_beside_the_pack(tmp_path):
    """Saria's AI, schedule, and talk flow are not path strings in a generic param.

    After the Ocarina reads the logic module under Logic/, the AIScheduleParam
    beside Component/, and Event/EventFlow/<actor>.bfevfl.zs. The pack's AIInfo
    names the program, and the talk component names the flow as an .evfc.
    """
    dump = Dump(build_fixture(tmp_path / "dump"))
    gyaml = "Npc_Kokiri_Saria_Child"
    param = dumps(
        {
            "$type": "engine__actor__ActorParam",
            "$parent": "Work/Actor/Npc/Npc_Base.engine__actor__ActorParam.gyml",
            "Name": gyaml,
        }
    )
    info = dumps(
        {
            "$type": "ai__AIInfo",
            "$parent": "Work/AI/Npc/Npc_Base.ai__AI.gyml",
            "AIProgram": f"AI/{gyaml}.work.ainb",
        }
    )
    talk = dumps(
        {
            "$type": "game__component__EventComponent",
            "FlowchartFileName": f"Work/Event/EventFlow/{gyaml}.evfc",
        }
    )
    archive = pack(
        {
            f"Actor/{gyaml}.engine__actor__ActorParam.bgyml": param,
            f"Component/AIInfo/{gyaml}.ai__AIInfo.bgyml": info,
            f"Component/Event/{gyaml}.game__component__EventComponent.bgyml": talk,
        }
    )
    romfs = dump.romfs
    (romfs / "Pack" / "Actor" / f"{gyaml}.pack.zs").write_bytes(archive)
    (romfs / "AI").mkdir(parents=True, exist_ok=True)
    (romfs / "AI" / f"{gyaml}.work.ainb").write_bytes(sample_ai_bytes())
    schedule = romfs / "Component" / "AIScheduleParam" / f"{gyaml}.game__component__AIScheduleParam.bgyml"
    schedule.parent.mkdir(parents=True, exist_ok=True)
    schedule.write_bytes(sample_schedule_bytes())
    flow = romfs / "Event" / "EventFlow" / f"{gyaml}.bfevfl.zs"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_bytes(sample_flow_bytes())
    logic = romfs / "Logic" / f"KokiriForest_Child_Level_{gyaml}_a766.logic.module.ainb"
    logic.parent.mkdir(parents=True, exist_ok=True)
    logic.write_bytes(sample_ai_bytes())

    graph = load_actor_graph(dump, gyaml=gyaml)
    kinds = {source["kind"] for source in graph["sources"]}
    assert kinds == {"ainb", "schedule", "event"}
    paths = {source["path"] for source in graph["sources"]}
    assert f"AI/{gyaml}.work.ainb" in paths
    assert f"Component/AIScheduleParam/{gyaml}.game__component__AIScheduleParam.bgyml" in paths
    assert f"Event/EventFlow/{gyaml}.bfevfl.zs" in paths
    assert f"Logic/KokiriForest_Child_Level_{gyaml}_a766.logic.module.ainb" in paths
    text = "\n".join(graph["trace"])
    assert f"pack Pack/Actor/{gyaml}.pack.zs" in text
    assert "Component/AIInfo/" in text
    assert "AIProgram=" in text
    assert "FlowchartFileName=" in text
    assert "scan Logic/" in text

    with pytest.raises(BancError) as caught:
        load_actor_graph(dump, gyaml="Npc_Saria")
    assert "Npc_Kokiri_Saria_Child" in str(caught.value)


def test_an_actor_without_logic_is_an_empty_graph(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    graph = load_actor_graph(dump, gyaml="Npc_Kokiri_M_01")
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert "no AI graph" in graph["note"]


def test_friendly_names_leave_unknown_types_readable():
    assert friendly_name("HoldGameDataGetBool") == ("Read flag", True)
    assert friendly_name("") == ("Undecoded", False)
