"""Warm-up order: the open scene first, neighbors in the same folder next."""

from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import FIELD_STATIC, KOKIRI_DYNAMIC, KOKIRI_STATIC, build_fixture
from ocarina_forge.warm import neighbor_bancs, run_warm, scene_model_names, warm_queue


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
