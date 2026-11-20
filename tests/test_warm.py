"""Warm-up order: the open scene first, neighbors in the same folder next."""

from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import FIELD_STATIC, KOKIRI_DYNAMIC, KOKIRI_STATIC, build_fixture
from ocarina_forge.warm import neighbor_bancs, queue_warm, run_warm, scene_model_names, warm_queue

CASTLE_STATIC = "Banc/HyruleCastle/HyruleCastle_Static.bcett.byml.zs"
DYNAMIC = "Banc/KokiriForest/KokiriForest_Child_Dynamic.bcett.byml.zs"


class _SceneList:
    """A scene list with one placement file and two files that have no actors."""

    def __init__(self):
        self.reads: list[str] = []

    def list_scenes(self):
        return [{"path": DYNAMIC}, {"path": KOKIRI_STATIC}, {"path": CASTLE_STATIC}]

    def read_byml(self, relative):
        self.reads.append(relative)
        if relative == DYNAMIC:
            return {"Actors": [{"Gyaml": "Obj_Tree"}, {"Gyaml": "Npc_Kokiri_F_01"}]}, None, b""
        return {"Climate": {}}, None, b""


def test_neighbors_stay_in_the_folder_before_the_rest_of_the_dump():
    scenes = [KOKIRI_STATIC, KOKIRI_DYNAMIC, FIELD_STATIC]
    assert neighbor_bancs(KOKIRI_STATIC, scenes) == [KOKIRI_DYNAMIC, FIELD_STATIC]
    queue = warm_queue(["Obj_Tree", "Npc_Kokiri_F_01"], ["Npc_Kokiri_F_01", "StartPos"], [KOKIRI_DYNAMIC])
    assert queue == [
        ("model", "Obj_Tree"),
        ("model", "Npc_Kokiri_F_01"),
        ("model", "StartPos"),
        ("scene", KOKIRI_DYNAMIC),
    ]


def test_warming_the_sample_twice_hits_the_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    dump = Dump(build_fixture(tmp_path / "dump"))
    assert "Npc_Kokiri_F_01" in scene_model_names(dump, KOKIRI_STATIC)
    cold = run_warm(dump, KOKIRI_STATIC)
    assert cold["misses"] >= 1
    assert cold["convertMs"] > 0
    warm = run_warm(dump, KOKIRI_STATIC)
    assert warm["misses"] == 0
    assert warm["hits"] == cold["misses"]
    assert warm["textureMs"] == 0
    out = capsys.readouterr().out
    assert "cache=hit" in out
    assert "warm done" in out
    assert "wall " in out
    assert warm["wallMs"] >= 0


def test_an_actorless_file_is_skipped_and_neighbors_are_parsed_later(monkeypatch, capsys):
    dump = _SceneList()
    started: list[list[str]] = []

    def describe_actor(_dump, name):
        started.append(list(_dump.reads))
        return {"status": "mesh", "timing": {"cache": "miss", "convertMs": 3, "textureMs": 1}}

    monkeypatch.setattr("ocarina_forge.models.describe_actor", describe_actor)
    assert scene_model_names(dump, KOKIRI_STATIC) == []
    dump.reads.clear()
    summary = run_warm(dump, DYNAMIC)
    assert summary["models"] == 2
    assert summary["misses"] == 2
    assert summary["wallMs"] >= 0
    assert started[0] == [DYNAMIC]
    assert KOKIRI_STATIC in dump.reads
    assert CASTLE_STATIC in dump.reads
    assert dump.reads.index(DYNAMIC) < dump.reads.index(KOKIRI_STATIC)
    out = capsys.readouterr().out
    assert "Obj_Tree cache=miss convert=3ms texture=1ms" in out
    assert KOKIRI_STATIC not in out
    only = _SceneList()
    run_warm(only, DYNAMIC, neighbors=False)
    assert only.reads == [DYNAMIC]
    queued = queue_warm(_SceneList(), DYNAMIC)
    assert queued[0] == ("model", "Obj_Tree")
    assert queued[1] == ("model", "Npc_Kokiri_F_01")
    assert ("scene", KOKIRI_STATIC) in queued
    assert ("scene", CASTLE_STATIC) in queued
