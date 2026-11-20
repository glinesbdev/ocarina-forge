"""The behavior graph keeps dump ids and shows undecoded nodes on the raw layer."""

from ocarina_forge.actors import format_hash
from ocarina_forge.behavior import (
    FADO_AI,
    friendly_name,
    load_actor_graph,
    make_op,
    mint_id,
)
from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import FADO, build_fixture


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


def test_an_actor_without_logic_is_an_empty_graph(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    graph = load_actor_graph(dump, gyaml="Npc_Kokiri_M_01")
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert "no AI graph" in graph["note"]


def test_friendly_names_leave_unknown_types_readable():
    assert friendly_name("HoldGameDataGetBool") == ("Read flag", True)
    assert friendly_name("") == ("Undecoded", False)
