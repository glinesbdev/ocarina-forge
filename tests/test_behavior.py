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
    talk = next(node for node in graph["nodes"] if node["raw"] == "OnPlayerTalk")
    assert talk["name"] == "Talk"
    assert talk["entry"] is True
    assert any(node["name"] == "Talk" and node["kind"] == "event" and node["raw"] == "Demo_Talk" for node in graph["nodes"])
    opened = next(source for source in graph["sources"] if source["id"] == graph["open"])
    assert opened["path"] == FADO_AI
    assert any(entry["raw"] == "Root" for entry in opened["entries"])
    flow = next(source for source in graph["sources"] if source["kind"] == "event")
    assert any(entry["name"] == "Talk" and entry["raw"] == "OnPlayerTalk" for entry in flow["entries"])
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


def test_a_builtin_ainb_node_uses_its_type_and_child_plug():
    from ainb import AINB
    from ainb.blackboard import Blackboard
    from ainb.command import Command
    from ainb.node import ChildPlug, Node, NodeFlag, NodeType

    from ocarina_forge.behavior import _ainb

    graph = AINB()
    graph.version = 0x407
    graph.filename = "Seq"
    graph.category = "AI"
    graph.blackboard = Blackboard()
    graph.exists_section_0x6c = True
    sequence = Node(NodeType.Element_Sequential)
    sequence.flags = NodeFlag(0)
    child = Node(NodeType.UserDefined)
    child.name = "HoldGameDataGetBool"
    child.flags = NodeFlag(1)
    plug = ChildPlug()
    plug.node_index = 1
    plug.name = "then"
    sequence.child_plugs.append(plug)
    graph.nodes = [sequence, child]
    for index, node in enumerate(graph.nodes):
        node.index = index
    command = Command()
    command.name = "Root"
    command.guid = "11111111-1111-1111-1111-111111111111"
    command.root_node_index = 0
    graph.commands.append(command)
    built = _ainb("AI/Seq.ainb", graph.to_binary())
    sequence_node = next(node for node in built["nodes"] if node["type"] == "Element_Sequential")
    assert sequence_node["name"] == "Sequence"
    assert sequence_node["decoded"] is True
    assert sequence_node["raw"] == "Element_Sequential"
    assert any(edge["from"].startswith(sequence_node["id"]) for edge in built["edges"])


def test_build_time_paths_are_reference_only(tmp_path):
    from ocarina_forge.behavior import _reference_only, _try_reference

    dump = Dump(build_fixture(tmp_path / "dump"))
    assert _reference_only("Work/AI/Root/Npc_Kokiri_F_01.ain")
    assert _reference_only("Work/BSA/Npc_Kokiri_F_01/Brain.ainb")
    assert _reference_only("AI/Npc_Kokiri_F_01.root.ainb") is False
    found = []
    trace = []
    _try_reference(dump, found, set(), set(), trace, [], "ainb", "Work/AI/Root/Npc_Kokiri_F_01.ain", "AIProgram")
    _try_reference(dump, found, set(), set(), trace, [], "ainb", "Work/BSA/Npc_Kokiri_F_01/Brain.ainb", "Logic")
    text = "\n".join(trace)
    assert text.count("reference only") == 2
    assert "missing" not in text
    assert found == []


def test_behavior_audit_counts_unnamed_user_nodes(tmp_path):
    from ocarina_forge.behavior import audit_npc_behavior

    dump = Dump(build_fixture(tmp_path / "dump"))
    report = audit_npc_behavior(dump)
    assert report["packs"] >= 1
    assert report["undecoded"] >= 1
    labels = {row["type"] for row in report["undecodedTypes"]}
    assert "UserDefined" in labels
    assert "Element_Sequential" not in labels


def test_friendly_names_leave_unknown_types_readable():
    assert friendly_name("HoldGameDataGetBool") == ("Read flag", True)
    assert friendly_name("") == ("Undecoded", False)
